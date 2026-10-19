import json
import random
import builtins
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.engine import (
    MatchDependencyConflict,
    map_win_probability,
    match_key,
    predict_match,
    series_win_probability,
    simulate_playoffs,
    simulate_swiss,
    simulate_tournament,
)
from app.model import available_matches, build_model_snapshot
from app.providers import HltvProvider


def team(number: int) -> dict:
    return {
        "id": f"team-{number:02d}",
        "name": f"Team {number:02d}",
        "seed": number,
        "region": "demo",
        "elo_rating": 1740 - number * 8,
        "synthetic": False,
    }


def predictor(a, b, best_of):
    return predict_match(a, b, best_of)


def swiss_rules(entrants=16, advances=8, all_bo3=False):
    return {
        "entrants": entrants,
        "advances": advances,
        "wins_to_advance": 3,
        "losses_to_eliminate": 3,
        "max_rounds": 5,
        "opening_match_best_of": 3 if all_bo3 else 1,
        "decider_match_best_of": 3,
        "all_matches_best_of_three": all_bo3,
    }


def full_inputs():
    teams = [team(i) for i in range(1, 33)]
    return (
        {
            "stage1": teams[16:32],
            "stage2": teams[8:16],
            "stage3": teams[:8],
        },
        {"stage1": swiss_rules(), "stage2": swiss_rules(), "stage3": swiss_rules(all_bo3=True)},
    )


def test_match_probabilities_normalize_and_series_probability_is_sensible():
    a, b = team(1), team(2)
    bo1 = predict_match(a, b, 1)
    bo3 = predict_match(a, b, 3)
    assert bo1["team_a_probability"] + bo1["team_b_probability"] == pytest.approx(1.0)
    assert bo3["team_a_probability"] + bo3["team_b_probability"] == pytest.approx(1.0)
    assert bo3["team_a_probability"] > bo1["team_a_probability"] > 0.5
    assert map_win_probability(1500, 1500) == pytest.approx(0.5)
    assert series_win_probability(0.5, 5) == pytest.approx(0.5)


def test_swiss_updates_records_and_advances_exactly_eight():
    outcome = simulate_swiss([team(i) for i in range(1, 17)], swiss_rules(), "stage1", predictor, random.Random(13), True)
    assert len(outcome["advanced"]) == 8
    assert len(outcome["eliminated"]) == 8
    assert all(item["wins"] == 3 for item in outcome["advanced"])
    assert all(item["losses"] == 3 for item in outcome["eliminated"])
    assert len(outcome["rounds"]) <= 5
    assert all(round_["matches"] for round_ in outcome["rounds"])


def test_pairings_prevent_rematches_when_feasible():
    outcome = simulate_swiss([team(i) for i in range(1, 17)], swiss_rules(), "stage1", predictor, random.Random(73), True)
    seen = set()
    for round_ in outcome["rounds"]:
        for match in round_["matches"]:
            pair = frozenset((match["team_a_id"], match["team_b_id"]))
            assert pair not in seen
            seen.add(pair)
            assert not match["forced_rematch"]


def test_confirmed_completed_results_override_model_outcome():
    entrants = [team(i) for i in range(1, 17)]
    confirmed = {
        "id": "major-stage1-r1-001",
        "stage_slug": "stage1",
        "round_no": 1,
        "team_a_id": "team-01",
        "team_b_id": "team-16",
        "winner_id": "team-16",
        "score": "0:1",
        "status": "completed",
        "best_of": 1,
    }
    outcome = simulate_swiss(entrants, swiss_rules(), "stage1", predictor, random.Random(3), True, actual_matches=[confirmed])
    found = next(match for match in outcome["rounds"][0]["matches"] if match["id"] == match_key("stage1", 1, "team-01", "team-16"))
    assert found["winner_id"] == "team-16"
    assert found["outcome_source"] == "completed_result"
    assert found["score"] == "0:1"


def test_confirmed_generated_upset_is_locked_into_projection_and_every_simulation():
    entrants = [team(i) for i in range(1, 17)]
    key = match_key("stage1", 1, "team-01", "team-16")
    confirmed = {
        "id": key,
        "fixture_type": "generated",
        "stage_slug": "stage1",
        "round_no": 1,
        "team_a_id": "team-01",
        "team_b_id": "team-16",
        "best_of": 1,
        "status": "completed",
        "result_status": "confirmed",
        "confirmed_winner_id": "team-16",
        "result_source": "manual",
        "confirmed_score": "0:1",
    }
    first = simulate_swiss(entrants, swiss_rules(), "stage1", predictor, random.Random(11), True, actual_matches=[confirmed])
    second = simulate_swiss(entrants, swiss_rules(), "stage1", predictor, random.Random(11), True, actual_matches=[confirmed])
    for outcome in (first, second):
        match = next(item for item in outcome["rounds"][0]["matches"] if item["id"] == key)
        assert match["winner_id"] == "team-16"
        assert match["original_prediction"] is None
        standings = {item["id"]: item for item in outcome["rounds"][0]["standings"]}
        assert (standings["team-16"]["wins"], standings["team-16"]["losses"]) == (1, 0)
        assert (standings["team-01"]["wins"], standings["team-01"]["losses"]) == (0, 1)


def test_confirmed_playoff_upset_advances_actual_winner_to_semifinals():
    qualifiers = [team(i) for i in (1, 3, 4, 5, 7, 10, 12, 18)]
    initial = simulate_playoffs(qualifiers, predictor, random.Random(17), True)
    quarterfinal = initial["rounds"][0]["matches"][0]
    actual_winner = quarterfinal["team_b_id"] if quarterfinal["winner_id"] == quarterfinal["team_a_id"] else quarterfinal["team_a_id"]
    confirmed = {
        "id": quarterfinal["id"],
        "fixture_type": "generated",
        "stage_slug": "playoffs",
        "round_no": 1,
        "team_a_id": quarterfinal["team_a_id"],
        "team_b_id": quarterfinal["team_b_id"],
        "best_of": 3,
        "status": "completed",
        "result_status": "confirmed",
        "confirmed_winner_id": actual_winner,
        "result_source": "manual",
    }
    updated = simulate_playoffs(qualifiers, predictor, random.Random(17), True, actual_matches=[confirmed])
    assert updated["rounds"][0]["matches"][0]["winner_id"] == actual_winner
    semifinals = updated["rounds"][1]["matches"]
    assert any(actual_winner in (match["team_a_id"], match["team_b_id"]) for match in semifinals)
    assert updated["champion_id"] in {item["id"] for item in qualifiers}


def test_confirmed_official_result_outside_rebuilt_path_is_reported_as_dependency_conflict():
    entrants, rules = full_inputs()
    official_result = {
        "id": "official-stage1-round6-result",
        "fixture_type": "official",
        "stage_slug": "stage1",
        "round_no": 6,
        "team_a_id": "team-17",
        "team_b_id": "team-18",
        "status": "completed",
        "result_status": "confirmed",
        "winner_id": "team-17",
    }
    with pytest.raises(MatchDependencyConflict) as caught:
        simulate_tournament(entrants, rules, predictor, simulations=2, seed=17, actual_matches=[official_result])
    assert caught.value.conflicts[0]["id"] == official_result["id"]
    assert caught.value.conflicts[0]["fixture_type"] == "official"
    assert caught.value.conflicts[0]["can_invalidate"] is True


def test_scenario_override_changes_projected_winner():
    entrants = [team(i) for i in range(1, 17)]
    key = match_key("stage1", 1, "team-01", "team-16")
    outcome = simulate_swiss(entrants, swiss_rules(), "stage1", predictor, random.Random(4), True, {key: "team-16"})
    found = next(match for match in outcome["rounds"][0]["matches"] if match["id"] == key)
    assert found["winner_id"] == "team-16"
    assert found["outcome_source"] == "scenario_override"


def test_playoff_seeding_progression_and_single_champion():
    qualifiers = [team(i) for i in (1, 3, 4, 5, 7, 10, 12, 18)]
    result = simulate_playoffs(qualifiers, predictor, random.Random(17), True)
    quarterfinals = result["rounds"][0]["matches"]
    assert len(quarterfinals) == 4
    assert [(match["team_a_id"], match["team_b_id"]) for match in quarterfinals] == [
        ("team-01", "team-18"),
        ("team-05", "team-07"),
        ("team-03", "team-12"),
        ("team-04", "team-10"),
    ]
    assert len(result["rounds"][1]["matches"]) == 2
    assert len(result["rounds"][2]["matches"]) == 1
    assert result["champion_id"] in {item["id"] for item in qualifiers}


def test_full_tournament_progression_and_probability_normalization():
    entrants, rules = full_inputs()
    result = simulate_tournament(entrants, rules, predictor, simulations=20, seed=91)
    path = result["projected_path"]
    assert len(path["stages"]["stage1"]["advanced"]) == 8
    assert len(path["stages"]["stage2"]["advanced"]) == 8
    assert len(path["stages"]["stage3"]["advanced"]) == 8
    stage2_entrants = {team_id for match in path["stages"]["stage2"]["rounds"][0]["matches"] for team_id in (match["team_a_id"], match["team_b_id"])}
    assert len(stage2_entrants) == 16
    assert set(item["id"] for item in entrants["stage2"]).issubset(stage2_entrants)
    assert len(path["playoffs"]["rounds"][0]["matches"]) == 4
    assert len(path["playoffs"]["rounds"][1]["matches"]) == 2
    assert len(path["playoffs"]["rounds"][2]["matches"]) == 1
    assert path["playoffs"]["champion_id"]
    assert result["championship_probability_sum"] == pytest.approx(1.0)
    assert sum(item["stage1_advance"]["probability"] for item in result["probabilities"].values()) == pytest.approx(8.0)
    assert sum(item["stage2_advance"]["probability"] for item in result["probabilities"].values()) == pytest.approx(8.0)
    assert sum(item["playoffs"]["probability"] for item in result["probabilities"].values()) == pytest.approx(8.0)


def test_seed_makes_full_tournament_simulation_reproducible():
    entrants, rules = full_inputs()
    first = simulate_tournament(entrants, rules, predictor, simulations=8, seed=515)
    second = simulate_tournament(entrants, rules, predictor, simulations=8, seed=515)
    assert first == second


def test_missing_metrics_and_synthetic_data_are_disclosed():
    a = {"id": "demo-a", "name": "Demo A", "seed": 1, "synthetic": True}
    b = {"id": "demo-b", "name": "Demo B", "seed": 2}
    prediction = predict_match(a, b, 3, sample_counts={})
    assert prediction["rating_a"] == 1500
    assert prediction["reliability"] == "low"
    assert any("Synthetic" in warning for warning in prediction["warnings"])
    assert any("Limited historical" in warning for warning in prediction["warnings"])
    assert prediction["map_pool_used"] is False
    assert prediction["player_metrics_used"] is False


def test_historical_cutoff_requires_both_result_and_availability_by_cutoff():
    records = [
        {"id": "visible", "status": "completed", "winner_id": "a", "played_at": "2026-01-01T00:00:00Z", "available_at": "2026-01-02T00:00:00Z"},
        {"id": "late", "status": "completed", "winner_id": "a", "played_at": "2026-01-01T00:00:00Z", "available_at": "2026-02-01T00:00:00Z"},
        {"id": "future", "status": "completed", "winner_id": "b", "played_at": "2026-02-02T00:00:00Z", "available_at": "2026-02-02T00:00:00Z"},
        {"id": "scheduled", "status": "scheduled", "winner_id": None, "played_at": "2026-01-01T00:00:00Z", "available_at": "2026-01-01T00:00:00Z"},
    ]
    assert [row["id"] for row in available_matches(records, "2026-01-31T00:00:00Z")] == ["visible"]


def test_trained_model_stays_off_without_sufficient_history_and_uses_cutoff():
    teams = [team(1), team(2)]
    matches = [{"id": "one", "team_a_id": teams[0]["id"], "team_b_id": teams[1]["id"], "winner_id": teams[0]["id"], "status": "completed", "best_of": 3, "played_at": "2026-01-01T00:00:00Z", "available_at": "2026-01-02T00:00:00Z"}]
    snapshot = build_model_snapshot(teams, matches, "2026-01-03T00:00:00Z")
    assert snapshot["eligible_matches"] == 1
    assert snapshot["status"] == "baseline_only"
    hidden = build_model_snapshot(teams, matches, "2026-01-01T12:00:00Z")
    assert hidden["eligible_matches"] == 0
    assert hidden["ratings"][teams[0]["id"]] == teams[0]["elo_rating"]


def test_external_team_rating_is_not_used_before_its_record_availability():
    teams = [
        {**team(1), "updated_at": "2026-02-01T00:00:00Z"},
        {**team(2), "updated_at": "2026-02-01T00:00:00Z"},
    ]
    snapshot = build_model_snapshot(teams, [], "2026-01-31T23:59:59Z")
    assert snapshot["ratings"][teams[0]["id"]] == 1500
    available = build_model_snapshot(teams, [], "2026-02-02T00:00:00Z")
    assert available["ratings"][teams[0]["id"]] == teams[0]["elo_rating"]


def test_sufficient_history_trains_and_result_recalculation_reuses_model_weights(monkeypatch):
    teams = [team(i) for i in range(1, 9)]
    matches = []
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for index in range(220):
        a = teams[index % 8]
        b = teams[(index * 3 + 1) % 8]
        if a["id"] == b["id"]:
            b = teams[(index + 1) % 8]
        played = start + timedelta(days=index)
        matches.append({
            "id": f"history-{index:03d}",
            "team_a_id": a["id"], "team_b_id": b["id"],
            "winner_id": a["id"] if index % 3 else b["id"],
            "status": "completed", "best_of": 1 if index % 2 else 3,
            "played_at": played.isoformat(),
            "available_at": (played + timedelta(hours=2)).isoformat(),
        })
    snapshot = build_model_snapshot(teams, matches, "2026-10-01T00:00:00Z")
    assert snapshot["eligible_matches"] == 220
    assert snapshot["status"] == "trained_chronological_split"
    assert {"log_loss", "brier_score", "accuracy", "calibration_error_5_bins"}.issubset(snapshot["metrics"])

    trained_state = snapshot["state"]
    changed_matches = [dict(match) for match in matches]
    changed_matches[-1]["winner_id"] = changed_matches[-1]["team_b_id"] if changed_matches[-1]["winner_id"] == changed_matches[-1]["team_a_id"] else changed_matches[-1]["team_a_id"]
    original_import = builtins.__import__

    def no_sklearn_retraining(name, *args, **kwargs):
        if name.startswith("sklearn"):
            raise AssertionError("A result-only recalculation must reuse the trained model.")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sklearn_retraining)
    recalculated = build_model_snapshot(
        teams, changed_matches, "2026-10-01T00:00:00Z", model_state=trained_state, allow_training=False,
    )
    assert recalculated["status"] == "trained_chronological_split"
    assert recalculated["state"]["coefficients"] == trained_state["coefficients"]
    assert recalculated["state"]["intercept"] == trained_state["intercept"]
    assert recalculated["ratings"] != snapshot["ratings"]


def test_demo_data_and_hltv_provider_are_not_misrepresented_as_real():
    payload = json.loads((Path(__file__).parents[1] / "data" / "demo_seed.json").read_text())
    provider = HltvProvider()
    assert payload["source"] == "synthetic demonstration dataset"
    assert len(payload["teams"]) == 32
    assert not provider.enabled
    with pytest.raises(RuntimeError, match="Automated collection is disabled"):
        provider.refresh()


def test_inconsistent_stage_configuration_fails_with_actionable_error():
    with pytest.raises(ValueError, match="requires 16 entrants"):
        simulate_swiss([team(i) for i in range(1, 9)], swiss_rules(), "stage1", predictor, random.Random(1), True)
