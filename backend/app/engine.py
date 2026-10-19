"""Explainable match probabilities and tournament simulation primitives."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from math import comb, exp, log, sqrt
from typing import Any, Callable, Iterable

from .feature_engineering import compare_team_signals


def map_win_probability(rating_a: float, rating_b: float) -> float:
    """Elo expected score for one map. Ratings are inputs, not learned effects."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def series_win_probability(map_probability: float, best_of: int) -> float:
    """Probability of winning a best-of series, assuming identical independent maps."""
    if best_of not in (1, 3, 5):
        raise ValueError("best_of must be 1, 3, or 5")
    wins_needed = best_of // 2 + 1
    return sum(
        comb(best_of, wins) * map_probability**wins * (1.0 - map_probability) ** (best_of - wins)
        for wins in range(wins_needed, best_of + 1)
    )


def _logit(probability: float) -> float:
    probability = min(1.0 - 1e-12, max(1e-12, probability))
    return log(probability / (1.0 - probability))


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + exp(-value))
    exp_value = exp(value)
    return exp_value / (1.0 + exp_value)


def apply_context_adjustment(probability: float, context_log_odds: float) -> float:
    """Apply the disclosed context prior to a match probability."""
    return _sigmoid(_logit(probability) + context_log_odds)


def series_probability_with_map_context(base_series_probability: float, best_of: int, context_log_odds: float) -> float:
    """Adjust a trained series probability by applying context to its equivalent map probability."""
    low, high = 0.0, 1.0
    for _ in range(60):
        middle = (low + high) / 2.0
        if series_win_probability(middle, best_of) < base_series_probability:
            low = middle
        else:
            high = middle
    base_map_probability = (low + high) / 2.0
    adjusted_map_probability = apply_context_adjustment(base_map_probability, context_log_odds)
    return series_win_probability(adjusted_map_probability, best_of)


def predict_match(
    team_a: dict[str, Any],
    team_b: dict[str, Any],
    best_of: int,
    ratings: dict[str, float] | None = None,
    sample_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    ratings = ratings or {}
    sample_counts = sample_counts or {}
    rating_a = float(ratings.get(team_a["id"], team_a.get("elo_rating") or 1500.0))
    rating_b = float(ratings.get(team_b["id"], team_b.get("elo_rating") or 1500.0))
    base_map_p = map_win_probability(rating_a, rating_b)
    context = compare_team_signals(team_a, team_b)
    map_p = _sigmoid(_logit(base_map_p) + context["context_log_odds"])
    a_p = series_win_probability(map_p, best_of)
    count_a = int(sample_counts.get(team_a["id"], 0))
    count_b = int(sample_counts.get(team_b["id"], 0))
    warnings: list[str] = []
    if min(count_a, count_b) < 10:
        warnings.append("Limited historical match sample; treat this as a low-confidence baseline.")
    if team_a.get("synthetic") or team_b.get("synthetic"):
        warnings.append("Synthetic demonstration ratings are not real team data.")
    for feature in context["effects"]:
        if not feature["used"]:
            if feature["key"] == "player_rating":
                warnings.append(feature["summary"])
            elif feature["key"] == "roster_continuity":
                warnings.append(feature["summary"])
            elif feature["key"] == "map_pool" and min(context["map_samples_a"], context["map_samples_b"]) == 0:
                warnings.append("Map-specific performance is missing or available for only one team.")
    used_labels = [item["label"].lower() for item in context["effects"] if item["used"]]
    explanation = (
        f"The Elo baseline starts from {rating_a:.0f} vs {rating_b:.0f} and converts the difference "
        f"to a {base_map_p:.0%} single-map estimate. Cutoff-safe context adjusts it to {map_p:.0%}"
        f" using {', '.join(used_labels) if used_labels else 'no usable imported context records'}. "
        f"The BO{best_of} probability then uses that map estimate under the stated identical-map assumption. "
        "Context coefficients are fixed, bounded heuristics, not learned effects."
    )
    return {
        "team_a_id": team_a["id"],
        "team_b_id": team_b["id"],
        "team_a_probability": a_p,
        "team_b_probability": 1.0 - a_p,
        "predicted_winner_id": team_a["id"] if a_p >= 0.5 else team_b["id"],
        "best_of": best_of,
        "rating_a": rating_a,
        "rating_b": rating_b,
        "rating_difference": rating_a - rating_b,
        "base_map_win_probability_a": base_map_p,
        "map_win_probability_a": map_p,
        "sample_matches_a": count_a,
        "sample_matches_b": count_b,
        "reliability": "low" if warnings else "moderate",
        "explanation": explanation,
        "warnings": warnings,
        "model": "Interpretable Elo + context priors",
        "map_pool_used": context["map_pool_used"],
        "map_veto_used": context["map_veto_used"],
        "player_metrics_used": context["player_metrics_used"],
        "roster_continuity_used": context["roster_continuity_used"],
        "head_to_head_used": context["head_to_head_used"],
        "head_to_head_count": context["head_to_head_count"],
        "context_log_odds": context["context_log_odds"],
        "context_features": context["effects"],
        "context_method": "Fixed bounded heuristic priors; not trained coefficients.",
    }


def match_key(stage_slug: str, round_no: int, team_a_id: str, team_b_id: str) -> str:
    left, right = sorted((team_a_id, team_b_id))
    return f"{stage_slug}:r{round_no}:{left}::{right}"


def _pair_without_rematches(
    team_ids: list[str], teams: dict[str, dict[str, Any]], played: set[frozenset[str]]
) -> tuple[list[tuple[str, str, bool]], bool]:
    ordered = sorted(team_ids, key=lambda team_id: (teams[team_id].get("seed", 999), team_id))

    def search(remaining: list[str]) -> list[tuple[str, str, bool]] | None:
        if not remaining:
            return []
        first = remaining[0]
        choices = list(reversed(remaining[1:]))
        for opponent in choices:
            pair = frozenset((first, opponent))
            if pair in played:
                continue
            rest = [item for item in remaining[1:] if item != opponent]
            tail = search(rest)
            if tail is not None:
                return [(first, opponent, False), *tail]
        return None

    result = search(ordered)
    if result is not None:
        return result, False

    # If a rematch-free perfect pairing is impossible, choose the least bad
    # seeded pairing and label it. This keeps the Swiss round complete.
    if len(ordered) % 2:
        raise ValueError("Cannot pair an odd-sized record group; check the Swiss entrant count and fixtures.")
    forced: list[tuple[str, str, bool]] = []
    for index in range(len(ordered) // 2):
        a, b = ordered[index], ordered[-(index + 1)]
        forced.append((a, b, frozenset((a, b)) in played))
    return forced, True


def _round_pairs(
    active: list[str],
    states: dict[str, dict[str, Any]],
    teams: dict[str, dict[str, Any]],
    played: set[frozenset[str]],
    round_no: int,
    fixed_fixtures: list[dict[str, Any]],
) -> list[tuple[str, str, bool, dict[str, Any] | None]]:
    by_record: dict[tuple[int, int], list[str]] = defaultdict(list)
    for team_id in active:
        state = states[team_id]
        by_record[(state["wins"], state["losses"])].append(team_id)

    normalized_fixed: list[dict[str, Any]] = []
    used: set[str] = set()
    for fixture in fixed_fixtures:
        a, b = fixture["team_a_id"], fixture["team_b_id"]
        if a not in active or b not in active:
            raise ValueError(f"Confirmed fixture in round {round_no} contains a team that is not active.")
        if a in used or b in used:
            raise ValueError(f"A team appears in more than one confirmed fixture in round {round_no}.")
        if (states[a]["wins"], states[a]["losses"]) != (states[b]["wins"], states[b]["losses"]):
            raise ValueError(f"Confirmed fixture {a} vs {b} conflicts with current Swiss records.")
        used.update((a, b))
        normalized_fixed.append(fixture)

    result: list[tuple[str, str, bool, dict[str, Any] | None]] = []
    group_order = sorted(by_record, key=lambda record: (-record[0], record[1]))
    if round_no == 1:
        # Opening round is high seed against low seed unless confirmed fixtures
        # have already fixed some of the teams.
        group_order = group_order[:1]

    for record in group_order:
        group = by_record[record]
        for fixture in normalized_fixed:
            if fixture["team_a_id"] in group and fixture["team_a_id"] not in {x for pair in result for x in pair[:2]}:
                result.append((fixture["team_a_id"], fixture["team_b_id"], False, fixture))
        paired = {team_id for a, b, _, _ in result for team_id in (a, b)}
        remaining = [team_id for team_id in group if team_id not in paired]

        if round_no == 1:
            ordered = sorted(remaining, key=lambda team_id: (teams[team_id].get("seed", 999), team_id))
            if len(ordered) % 2:
                raise ValueError("Stage 1 opening pairing has an odd number of teams; check entrant configuration.")
            pairs = [(ordered[i], ordered[-1 - i], False) for i in range(len(ordered) // 2)]
            result.extend((a, b, rematch, None) for a, b, rematch in pairs)
        elif remaining:
            pairs, _ = _pair_without_rematches(remaining, teams, played)
            result.extend((a, b, rematch, None) for a, b, rematch in pairs)

    if len(result) * 2 != len(active):
        raise ValueError(
            "Swiss pairing did not cover all active teams. Check round fixtures, entrant count, and record groups."
        )
    return result


def simulate_swiss(
    entrants: Iterable[dict[str, Any]],
    rules: dict[str, Any],
    stage_slug: str,
    predictor: Callable[[dict[str, Any], dict[str, Any], int], dict[str, Any]],
    rng: Any,
    projection: bool = False,
    scenario_overrides: dict[str, str] | None = None,
    actual_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    teams_list = [deepcopy(team) for team in entrants]
    advance_count = int(rules["advances"])
    wins_to_advance = int(rules["wins_to_advance"])
    losses_to_eliminate = int(rules["losses_to_eliminate"])
    if len(teams_list) != int(rules["entrants"]):
        raise ValueError(f"{stage_slug} requires {rules['entrants']} entrants; received {len(teams_list)}.")
    if advance_count <= 0 or advance_count >= len(teams_list):
        raise ValueError(f"{stage_slug} advancement count must be between 1 and entrants - 1.")

    by_id = {team["id"]: team for team in teams_list}
    states = {team["id"]: {"wins": 0, "losses": 0, "status": "active"} for team in teams_list}
    actual_matches = actual_matches or []
    scenario_overrides = scenario_overrides or {}
    fixtures_by_round: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for match in actual_matches:
        if match.get("stage_slug") == stage_slug and match.get("round_no"):
            fixtures_by_round[int(match["round_no"])].append(match)
    played: set[frozenset[str]] = set()
    rounds: list[dict[str, Any]] = []
    max_rounds = int(rules.get("max_rounds", len(teams_list)))
    round_no = 1
    while any(state["status"] == "active" for state in states.values()):
        if round_no > max_rounds:
            raise ValueError(f"{stage_slug} exceeded {max_rounds} rounds before all teams advanced or were eliminated.")
        active = [team_id for team_id, state in states.items() if state["status"] == "active"]
        pairings = _round_pairs(active, states, by_id, played, round_no, fixtures_by_round.get(round_no, []))
        round_matches: list[dict[str, Any]] = []
        for team_a_id, team_b_id, forced_rematch, fixture in pairings:
            a, b = by_id[team_a_id], by_id[team_b_id]
            if rules.get("all_matches_best_of_three"):
                best_of = 3
            else:
                is_decider = (
                    states[team_a_id]["wins"] >= wins_to_advance - 1
                    or states[team_a_id]["losses"] >= losses_to_eliminate - 1
                    or states[team_b_id]["wins"] >= wins_to_advance - 1
                    or states[team_b_id]["losses"] >= losses_to_eliminate - 1
                )
                best_of = int(rules["decider_match_best_of"] if is_decider else rules["opening_match_best_of"])
            if fixture and fixture.get("best_of"):
                best_of = int(fixture["best_of"])
            prediction = predictor(a, b, best_of)
            key = match_key(stage_slug, round_no, team_a_id, team_b_id)
            actual_winner = fixture.get("winner_id") if fixture else None
            override_winner = scenario_overrides.get(key)
            if actual_winner:
                winner_id = actual_winner
                outcome_source = "completed_result"
            elif override_winner:
                if override_winner not in (team_a_id, team_b_id):
                    raise ValueError(f"Scenario winner for {key} is not one of the teams in the match.")
                winner_id = override_winner
                outcome_source = "scenario_override"
            elif projection:
                winner_id = prediction["predicted_winner_id"]
                outcome_source = "model_projection"
            else:
                winner_id = team_a_id if rng.random() < prediction["team_a_probability"] else team_b_id
                outcome_source = "monte_carlo"
            loser_id = team_b_id if winner_id == team_a_id else team_a_id
            states[winner_id]["wins"] += 1
            states[loser_id]["losses"] += 1
            if states[winner_id]["wins"] >= wins_to_advance:
                states[winner_id]["status"] = "advanced"
            if states[loser_id]["losses"] >= losses_to_eliminate:
                states[loser_id]["status"] = "eliminated"
            played.add(frozenset((team_a_id, team_b_id)))
            round_matches.append({
                "id": key,
                "stage_slug": stage_slug,
                "round_no": round_no,
                "team_a_id": team_a_id,
                "team_b_id": team_b_id,
                "best_of": best_of,
                "winner_id": winner_id,
                "loser_id": loser_id,
                "status": "completed_result" if actual_winner else "predicted",
                "fixture_confirmed": bool(fixture),
                "outcome_source": outcome_source,
                "forced_rematch": forced_rematch,
                "prediction": prediction,
                "score": fixture.get("score") if fixture else None,
            })
        standings = sorted(
            [
                {**by_id[team_id], **states[team_id]}
                for team_id in states
            ],
            key=lambda team: (-team["wins"], team["losses"], team.get("seed", 999)),
        )
        rounds.append({"round_no": round_no, "matches": round_matches, "standings": standings})
        round_no += 1

    advanced = [
        {**by_id[team_id], **states[team_id]}
        for team_id in states if states[team_id]["status"] == "advanced"
    ]
    eliminated = [
        {**by_id[team_id], **states[team_id]}
        for team_id in states if states[team_id]["status"] == "eliminated"
    ]
    if len(advanced) != advance_count or len(eliminated) != len(teams_list) - advance_count:
        raise ValueError(
            f"{stage_slug} finished with {len(advanced)} advancing teams and {len(eliminated)} eliminated; "
            f"expected {advance_count} and {len(teams_list) - advance_count}."
        )
    advanced.sort(key=lambda team: (team.get("seed", 999), team["id"]))
    return {"slug": stage_slug, "rounds": rounds, "advanced": advanced, "eliminated": eliminated}


def simulate_playoffs(
    qualifiers: list[dict[str, Any]],
    predictor: Callable[[dict[str, Any], dict[str, Any], int], dict[str, Any]],
    rng: Any,
    projection: bool = False,
    scenario_overrides: dict[str, str] | None = None,
    actual_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if len(qualifiers) != 8:
        raise ValueError(f"Playoffs require exactly 8 teams; received {len(qualifiers)}.")
    teams = {team["id"]: deepcopy(team) for team in qualifiers}
    seed_order = sorted(qualifiers, key=lambda team: (team.get("seed", 999), team["id"]))
    qf_pairs = [
        (seed_order[0], seed_order[7]),
        (seed_order[3], seed_order[4]),
        (seed_order[1], seed_order[6]),
        (seed_order[2], seed_order[5]),
    ]
    scenario_overrides = scenario_overrides or {}
    actual_matches = actual_matches or []
    actual_by_round: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for match in actual_matches:
        if match.get("stage_slug") == "playoffs" and match.get("round_no"):
            actual_by_round[int(match["round_no"])].append(match)
    rounds: list[dict[str, Any]] = []
    previous_winners: list[str] = []
    champion: str | None = None
    for round_no, best_of in ((1, 3), (2, 3), (3, 5)):
        pairs: list[tuple[dict[str, Any], dict[str, Any]]]
        if round_no == 1:
            pairs = qf_pairs
        else:
            pairs = [(teams[previous_winners[i]], teams[previous_winners[i + 1]]) for i in range(0, len(previous_winners), 2)]
        # If confirmed fixtures exist, their pairings take precedence. Require
        # them to form a complete round to avoid silently changing the bracket.
        fixtures = actual_by_round.get(round_no, [])
        if fixtures:
            expected = {frozenset((a["id"], b["id"])) for a, b in pairs}
            supplied = {frozenset((m["team_a_id"], m["team_b_id"])) for m in fixtures}
            if not supplied.issubset(expected):
                raise ValueError(f"Confirmed playoff fixture in round {round_no} conflicts with the configured bracket.")
            fixture_lookup = {frozenset((m["team_a_id"], m["team_b_id"])): m for m in fixtures}
        else:
            fixture_lookup = {}
        matches: list[dict[str, Any]] = []
        winners: list[str] = []
        for a, b in pairs:
            fixture = fixture_lookup.get(frozenset((a["id"], b["id"])))
            if fixture and fixture.get("best_of"):
                match_best_of = int(fixture["best_of"])
            else:
                match_best_of = best_of
            prediction = predictor(a, b, match_best_of)
            key = match_key("playoffs", round_no, a["id"], b["id"])
            actual_winner = fixture.get("winner_id") if fixture else None
            override = scenario_overrides.get(key)
            if actual_winner:
                winner_id, outcome_source = actual_winner, "completed_result"
            elif override:
                if override not in (a["id"], b["id"]):
                    raise ValueError(f"Scenario winner for {key} is not one of the teams in the match.")
                winner_id, outcome_source = override, "scenario_override"
            elif projection:
                winner_id, outcome_source = prediction["predicted_winner_id"], "model_projection"
            else:
                winner_id = a["id"] if rng.random() < prediction["team_a_probability"] else b["id"]
                outcome_source = "monte_carlo"
            loser_id = b["id"] if winner_id == a["id"] else a["id"]
            matches.append({
                "id": key,
                "stage_slug": "playoffs",
                "round_no": round_no,
                "round_name": ("Quarterfinals", "Semifinals", "Grand Final")[round_no - 1],
                "team_a_id": a["id"],
                "team_b_id": b["id"],
                "best_of": match_best_of,
                "winner_id": winner_id,
                "loser_id": loser_id,
                "status": "completed_result" if actual_winner else "predicted",
                "fixture_confirmed": bool(fixture),
                "outcome_source": outcome_source,
                "prediction": prediction,
                "score": fixture.get("score") if fixture else None,
            })
            winners.append(winner_id)
        rounds.append({"round_no": round_no, "name": ("Quarterfinals", "Semifinals", "Grand Final")[round_no - 1], "matches": matches})
        previous_winners = winners
    champion = previous_winners[0]
    return {"rounds": rounds, "champion_id": champion}


def simulate_tournament(
    entrants_by_stage: dict[str, list[dict[str, Any]]],
    stage_rules: dict[str, dict[str, Any]],
    predictor: Callable[[dict[str, Any], dict[str, Any], int], dict[str, Any]],
    simulations: int,
    seed: int,
    scenario_overrides: dict[str, str] | None = None,
    actual_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a coherent projected path plus full-tournament Monte Carlo trials."""
    if simulations < 1 or simulations > 10000:
        raise ValueError("simulations must be between 1 and 10,000")
    scenario_overrides = scenario_overrides or {}
    actual_matches = actual_matches or []

    def stage_entrants(stage_slug: str, previous: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        direct = entrants_by_stage.get(stage_slug, [])
        merged = {team["id"]: deepcopy(team) for team in direct}
        for team in previous or []:
            merged[team["id"]] = deepcopy(team)
        return sorted(merged.values(), key=lambda team: (team.get("seed", 999), team["id"]))

    path_rng = __import__("random").Random(seed)
    path: dict[str, Any] = {"stages": {}}
    s1 = simulate_swiss(
        stage_entrants("stage1"), stage_rules["stage1"], "stage1", predictor,
        path_rng, True, scenario_overrides, actual_matches,
    )
    path["stages"]["stage1"] = s1
    s2 = simulate_swiss(
        stage_entrants("stage2", s1["advanced"]), stage_rules["stage2"], "stage2", predictor,
        path_rng, True, scenario_overrides, actual_matches,
    )
    path["stages"]["stage2"] = s2
    s3 = simulate_swiss(
        stage_entrants("stage3", s2["advanced"]), stage_rules["stage3"], "stage3", predictor,
        path_rng, True, scenario_overrides, actual_matches,
    )
    path["stages"]["stage3"] = s3
    playoff_path = simulate_playoffs(s3["advanced"], predictor, path_rng, True, scenario_overrides, actual_matches)
    path["playoffs"] = playoff_path

    # Count how often each team reaches each point in the actual tournament path.
    all_team_ids = sorted({team["id"] for stage in entrants_by_stage.values() for team in stage})
    counts = {
        team_id: {"stage1_advance": 0, "stage2_advance": 0, "playoffs": 0, "semifinals": 0, "final": 0, "champion": 0}
        for team_id in all_team_ids
    }
    mc_rng = __import__("random").Random(seed)
    for _ in range(simulations):
        run1 = simulate_swiss(stage_entrants("stage1"), stage_rules["stage1"], "stage1", predictor, mc_rng, False, scenario_overrides, actual_matches)
        run2 = simulate_swiss(stage_entrants("stage2", run1["advanced"]), stage_rules["stage2"], "stage2", predictor, mc_rng, False, scenario_overrides, actual_matches)
        run3 = simulate_swiss(stage_entrants("stage3", run2["advanced"]), stage_rules["stage3"], "stage3", predictor, mc_rng, False, scenario_overrides, actual_matches)
        playoffs = simulate_playoffs(run3["advanced"], predictor, mc_rng, False, scenario_overrides, actual_matches)
        for team in run1["advanced"]:
            counts[team["id"]]["stage1_advance"] += 1
        for team in run2["advanced"]:
            counts[team["id"]]["stage2_advance"] += 1
        for team in run3["advanced"]:
            counts[team["id"]]["playoffs"] += 1
        for match in playoffs["rounds"][0]["matches"]:
            counts[match["winner_id"]]["semifinals"] += 1
        for match in playoffs["rounds"][1]["matches"]:
            counts[match["winner_id"]]["final"] += 1
        counts[playoffs["champion_id"]]["champion"] += 1

    probabilities: dict[str, dict[str, Any]] = {}
    for team_id, values in counts.items():
        probabilities[team_id] = {}
        for key, count in values.items():
            probability = count / simulations
            probabilities[team_id][key] = {
                "probability": probability,
                "standard_error": sqrt(probability * (1.0 - probability) / simulations),
                "count": count,
            }
    return {
        "projected_path": path,
        "probabilities": probabilities,
        "simulations": simulations,
        "seed": seed,
        "championship_probability_sum": sum(x["champion"]["probability"] for x in probabilities.values()),
    }
