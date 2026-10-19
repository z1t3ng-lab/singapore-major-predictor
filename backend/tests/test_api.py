from fastapi.testclient import TestClient

from app import database
from app import main as main_module
from app.main import app


def _path_matches(result):
    return [
        *[
            match
            for stage in result["projected_path"]["stages"].values()
            for round_data in stage["rounds"]
            for match in round_data["matches"]
        ],
        *[
            match
            for round_data in result["projected_path"]["playoffs"]["rounds"]
            for match in round_data["matches"]
        ],
    ]


def _simulate(client, simulations=8):
    response = client.post("/api/simulate", json={
        "simulations": simulations,
        "seed": 73,
        "cutoff": "2026-10-19T12:00:00Z",
    })
    assert response.status_code == 200, response.text
    return response.json()


def _confirm(client, match, winner_id, score=None, expected_version=0, invalidate_conflict_ids=None):
    return client.put(f"/api/matches/{match['id']}/result", json={
        "winner_id": winner_id,
        "score": score,
        "expected_result_version": expected_version,
        "invalidate_conflict_ids": invalidate_conflict_ids or [],
    })


def test_roster_history_is_cutoff_aware_and_demo_source_is_labeled(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.sqlite3")
    with TestClient(app) as client:
        assert client.post("/api/demo").status_code == 200
        overview = client.get("/api/overview").json()
        assert overview["data"]["synthetic_records_present"] is True
        assert "synthetic" in overview["data"]["active_source"]

        first_import = client.post("/api/import/json", json={
            "players": [{"id": "player-old", "name": "Old roster player"}],
            "roster_memberships": [{
                "team_id": "demo-01", "player_id": "player-old",
                "started_at": "2026-01-01", "ended_at": "2026-10-02", "role": "rifler",
                "available_at": "2026-01-01T00:00:00Z"
            }],
        })
        assert first_import.status_code == 200

        second_import = client.post("/api/import/json", json={
            "players": [
                *[{"id": f"player-new-{index}", "name": f"New player {index}"} for index in range(1, 6)],
                *[{"id": f"player-team2-{index}", "name": f"Team 2 player {index}"} for index in range(1, 6)],
            ],
            "roster_memberships": [
                *[{
                    "team_id": "demo-01", "player_id": f"player-new-{index}",
                    "started_at": "2026-10-03", "ended_at": None, "role": "player",
                    "available_at": "2026-10-03T00:00:00Z"
                } for index in range(1, 6)],
                *[{
                    "team_id": "demo-02", "player_id": f"player-team2-{index}",
                    "started_at": "2026-06-01", "ended_at": None, "role": "player",
                    "available_at": "2026-06-01T00:00:00Z"
                } for index in range(1, 6)],
            ],
        })
        assert second_import.status_code == 200

        detail_import = client.post("/api/import/json", json={
            "matches": [{
                "id": "history-demo-01-02", "team_a_id": "demo-01", "team_b_id": "demo-02",
                "best_of": 3, "status": "completed", "winner_id": "demo-01", "score": "2:1",
                "played_at": "2026-10-10T14:00:00Z", "available_at": "2026-10-10T18:00:00Z",
            }],
            "map_results": [{
                "match_id": "history-demo-01-02", "map_name": "de_inferno",
                "team_a_score": 13, "team_b_score": 10, "winner_id": "demo-01",
            }, {
                "match_id": "history-demo-01-02", "map_name": "de_mirage",
                "team_a_score": 11, "team_b_score": 13, "winner_id": "demo-02",
            }],
            "map_vetoes": [{
                "match_id": "history-demo-01-02", "sequence_no": 1, "action": "pick",
                "map_name": "de_inferno", "team_id": "demo-01", "available_at": "2026-10-10T13:00:00Z",
            }, {
                "match_id": "history-demo-01-02", "sequence_no": 2, "action": "ban",
                "map_name": "de_mirage", "team_id": "demo-02", "available_at": "2026-10-10T13:00:00Z",
            }],
            "player_stats": [
                *[{
                    "match_id": "history-demo-01-02", "player_id": f"player-new-{index}",
                    "measurement_period": "2026-10-10", "metric_version": "rating-2.0",
                    "stats": {"rating": 1.0 + index / 10, "adr": 72 + index, "kills": 20, "deaths": 15},
                    "available_at": "2026-10-11T08:00:00Z",
                } for index in range(1, 4)],
                *[{
                    "match_id": "history-demo-01-02", "player_id": f"player-team2-{index}",
                    "measurement_period": "2026-10-10", "metric_version": "rating-2.0",
                    "stats": {"rating": 1.0, "adr": 72, "kills": 18, "deaths": 17},
                    "available_at": "2026-10-11T08:00:00Z",
                } for index in range(1, 4)],
            ],
        })
        assert detail_import.status_code == 200

        current = client.post("/api/predict", json={
            "team_a_id": "demo-01", "team_b_id": "demo-02", "best_of": 3,
            "cutoff": "2026-10-19T12:00:00Z",
        }).json()
        assert current["roster_status"]["demo-01"]["active_members"] == 5
        assert not any("Only 1 active roster" in warning for warning in current["warnings"])
        assert current["map_pool_used"] is True
        assert current["map_veto_used"] is True
        assert current["player_metrics_used"] is True
        assert any(item["key"] == "player_adr" and item["used"] for item in current["context_features"])
        assert current["roster_continuity_used"] is True
        assert current["head_to_head_used"] is True
        assert current["head_to_head_count"] == 1
        assert current["head_to_head_results"][0]["winner_name"] == "Arcadium"
        assert current["map_results"][0]["map_name"] == "de_inferno"
        assert current["map_vetoes"][0]["action"] == "pick"
        assert any(item["stats"]["rating"] == 1.1 for item in current["player_stats"])
        assert current["team_a_probability"] + current["team_b_probability"] == 1.0

        before_transfer = client.post("/api/predict", json={
            "team_a_id": "demo-01", "team_b_id": "demo-02", "best_of": 3,
            "cutoff": "2026-10-01T12:00:00Z",
        }).json()
        assert before_transfer["roster_status"]["demo-01"]["active_members"] == 1
        assert before_transfer["head_to_head_count"] == 0
        assert before_transfer["map_results"] == []
        assert before_transfer["player_stats"] == []
        assert before_transfer["map_pool_used"] is False
        assert before_transfer["player_metrics_used"] is False
        assert before_transfer["head_to_head_used"] is False


def test_simulation_endpoint_persists_a_coherent_tournament_run(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "simulation.sqlite3")
    with TestClient(app) as client:
        assert client.post("/api/demo").status_code == 200
        response = client.post("/api/simulate", json={
            "simulations": 2,
            "seed": 19,
            "cutoff": "2026-10-19T12:00:00Z",
        })
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["run_id"]
        assert result["seed"] == 19
        assert len(result["projected_path"]["stages"]["stage1"]["advanced"]) == 8
        assert len(result["projected_path"]["stages"]["stage2"]["advanced"]) == 8
        assert len(result["projected_path"]["stages"]["stage3"]["advanced"]) == 8
        assert len(result["projected_path"]["playoffs"]["rounds"][0]["matches"]) == 4
        assert result["championship_probability_sum"] == 1.0
        saved = client.get("/api/exports/predictions")
        assert saved.status_code == 200
        assert saved.json()["id"] == result["run_id"]


def test_first_run_has_no_automatic_synthetic_teams_and_demo_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "empty-field.sqlite3")
    with TestClient(app) as client:
        overview = client.get("/api/overview").json()
        assert overview["data"]["team_records"] == 0
        assert overview["data"]["synthetic_records_present"] is False
        assert overview["data"]["active_source"] == "Awaiting official participant list"
        assert client.get("/api/teams").json()["teams"] == []
        assert client.post("/api/simulate", json={"simulations": 1}).status_code == 422

        demo = client.post("/api/demo")
        assert demo.status_code == 200
        assert demo.json()["teams_loaded"] == 32
        assert client.get("/api/overview").json()["data"]["synthetic_records_present"] is True


def test_legacy_automatic_demo_is_removed_but_explicit_demo_is_persistent(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "legacy-demo.sqlite3")
    database.initialize_database()
    with database.connect() as connection:
        database.seed_demo(connection)
        connection.execute("DELETE FROM metadata WHERE key='demo_opt_in'")

    database.initialize_database()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0
        assert database.get_metadata(connection)["active_data_source"] == "Awaiting official participant list"

    with TestClient(app) as client:
        assert client.post("/api/demo").status_code == 200
    database.initialize_database()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 32
        assert database.get_metadata(connection)["demo_opt_in"] == "true"


def test_schema_migration_adds_roster_availability_to_existing_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "legacy-schema.sqlite3")
    with database.connect() as connection:
        connection.executescript((database.DATA_DIR / "migrations" / "001_initial_schema.sql").read_text())
    database.initialize_database()
    with database.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(roster_memberships)")}
        versions = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
        assert "available_at" in columns
        match_columns = {row["name"] for row in connection.execute("PRAGMA table_info(matches)")}
        run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(prediction_runs)")}
        assert {1, 2, 3, 4}.issubset(versions)
        assert {"fixture_type", "result_status", "confirmed_winner_id", "confirmed_score", "result_version", "original_prediction_json"}.issubset(match_columns)
        assert {"state_version", "result_json"}.issubset(run_columns)


def test_manual_result_upset_edit_clear_recalculation_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "manual-result.sqlite3")
    with TestClient(app) as client:
        assert client.post("/api/demo").status_code == 200
        before = _simulate(client)
        original = before["projected_path"]["stages"]["stage1"]["rounds"][0]["matches"][0]
        original_prediction = original["prediction"]
        upset_winner = original["team_b_id"] if original_prediction["predicted_winner_id"] == original["team_a_id"] else original["team_a_id"]
        score = "1:0" if upset_winner == original["team_a_id"] else "0:1"

        saved = _confirm(client, original, upset_winner, score)
        assert saved.status_code == 200, saved.text
        response = saved.json()
        updated = next(match for match in _path_matches(response["result"]) if match["id"] == original["id"])
        assert updated["result_status"] == "confirmed"
        assert updated["confirmed_winner_id"] == upset_winner
        assert updated["winner_id"] == upset_winner
        assert updated["score"] == score
        assert updated["original_prediction"]["predicted_winner_id"] == original_prediction["predicted_winner_id"]
        assert updated["original_prediction"]["team_a_probability"] == original_prediction["team_a_probability"]
        assert updated["prediction"]["team_a_probability"] == original_prediction["team_a_probability"]
        assert updated["prediction"]["team_a_probability"] != 1.0
        standings = {team["id"]: team for team in response["result"]["projected_path"]["stages"]["stage1"]["rounds"][0]["standings"]}
        loser = original["team_b_id"] if upset_winner == original["team_a_id"] else original["team_a_id"]
        assert (standings[upset_winner]["wins"], standings[upset_winner]["losses"]) == (1, 0)
        assert (standings[loser]["wins"], standings[loser]["losses"]) == (0, 1)

        repeated = _simulate(client)
        repeated_match = next(match for match in _path_matches(repeated) if match["id"] == original["id"])
        assert repeated_match["winner_id"] == upset_winner
        repeated_standings = {team["id"]: team for team in repeated["projected_path"]["stages"]["stage1"]["rounds"][0]["standings"]}
        assert (repeated_standings[upset_winner]["wins"], repeated_standings[upset_winner]["losses"]) == (1, 0)
        assert (repeated_standings[loser]["wins"], repeated_standings[loser]["losses"]) == (0, 1)
        stale = _confirm(client, original, original["winner_id"], expected_version=0)
        assert stale.status_code == 409

        edited = _confirm(client, original, original["winner_id"], "1:0" if original["winner_id"] == original["team_a_id"] else "0:1", expected_version=1)
        assert edited.status_code == 200, edited.text
        edited_match = next(match for match in _path_matches(edited.json()["result"]) if match["id"] == original["id"])
        assert edited_match["winner_id"] == original["winner_id"]
        assert edited_match["result_version"] == 2
        assert edited_match["original_prediction"]["predicted_winner_id"] == original_prediction["predicted_winner_id"]

        cleared = client.request("DELETE", f"/api/matches/{original['id']}/result", json={"expected_result_version": 2})
        assert cleared.status_code == 200, cleared.text
        cleared_match = next(match for match in _path_matches(cleared.json()["result"]) if match["id"] == original["id"])
        assert cleared_match["result_status"] == "cleared"
        assert cleared_match["confirmed_winner_id"] is None
        assert cleared_match["original_prediction"]["predicted_winner_id"] == original_prediction["predicted_winner_id"]

    # A new app lifespan reads the durable result fields from SQLite.
    with TestClient(app) as restarted:
        persisted = _simulate(restarted)
        cleared_match = next(match for match in _path_matches(persisted) if match["id"] == original["id"])
        assert cleared_match["result_status"] == "cleared"
        assert cleared_match["confirmed_winner_id"] is None


def test_result_rejects_unrelated_winners_and_inconsistent_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "invalid-result.sqlite3")
    with TestClient(app) as client:
        assert client.post("/api/demo").status_code == 200
        result = _simulate(client)
        match = result["projected_path"]["stages"]["stage1"]["rounds"][0]["matches"][0]
        unrelated = _confirm(client, match, "team-not-in-this-match")
        assert unrelated.status_code == 422
        winner = match["team_a_id"]
        mismatched = _confirm(client, match, winner, "0:1")
        assert mismatched.status_code == 422
        valid = _confirm(client, match, winner, "1:0")
        assert valid.status_code == 200, valid.text


def test_changed_earlier_result_requires_explicit_downstream_invalidation(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "result-conflict.sqlite3")
    with TestClient(app) as client:
        assert client.post("/api/demo").status_code == 200
        first_result = _simulate(client)
        stage1 = first_result["projected_path"]["stages"]["stage1"]
        first = stage1["rounds"][0]["matches"][0]
        unchanged = stage1["rounds"][0]["matches"][1]
        first_saved = _confirm(client, first, first["winner_id"])
        assert first_saved.status_code == 200, first_saved.text
        current = first_saved.json()["result"]
        second_round = current["projected_path"]["stages"]["stage1"]["rounds"][1]
        first_winner = first["winner_id"]
        dependent = next(match for match in second_round["matches"] if first_winner in (match["team_a_id"], match["team_b_id"]))
        dependent_saved = _confirm(client, dependent, dependent["winner_id"])
        assert dependent_saved.status_code == 200, dependent_saved.text
        unrelated_saved = _confirm(client, unchanged, unchanged["winner_id"])
        assert unrelated_saved.status_code == 200, unrelated_saved.text

        upset_winner = first["team_b_id"] if first["winner_id"] == first["team_a_id"] else first["team_a_id"]
        conflict = _confirm(client, first, upset_winner, expected_version=1)
        assert conflict.status_code == 409
        conflict_ids = [item["id"] for item in conflict.json()["detail"]["conflicts"]]
        assert dependent["id"] in conflict_ids

        resolved = _confirm(client, first, upset_winner, expected_version=1, invalidate_conflict_ids=conflict_ids)
        assert resolved.status_code == 200, resolved.text
        assert dependent["id"] in resolved.json()["invalidated_result_ids"]
        final_matches = _path_matches(resolved.json()["result"])
        current_first = next(match for match in final_matches if match["id"] == first["id"])
        current_unaffected = next(match for match in final_matches if match["id"] == unchanged["id"])
        assert current_first["winner_id"] == upset_winner
        assert current_unaffected["result_status"] == "confirmed"
        with database.connect() as connection:
            invalidated = connection.execute("SELECT result_status,confirmed_winner_id FROM matches WHERE id=?", (dependent["id"],)).fetchone()
            assert invalidated["result_status"] == "cleared"
            assert invalidated["confirmed_winner_id"] is None


def test_playoff_upset_recalculates_semifinal_slots_and_title_probabilities(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "playoff-result.sqlite3")
    with TestClient(app) as client:
        assert client.post("/api/demo").status_code == 200
        result = _simulate(client)
        qf = result["projected_path"]["playoffs"]["rounds"][0]["matches"][0]
        winner = qf["team_b_id"] if qf["winner_id"] == qf["team_a_id"] else qf["team_a_id"]
        response = _confirm(client, qf, winner)
        assert response.status_code == 200, response.text
        updated = response.json()["result"]
        current_qf = next(match for match in updated["projected_path"]["playoffs"]["rounds"][0]["matches"] if match["id"] == qf["id"])
        assert current_qf["winner_id"] == winner
        semifinals = updated["projected_path"]["playoffs"]["rounds"][1]["matches"]
        assert any(winner in (match["team_a_id"], match["team_b_id"]) for match in semifinals)
        assert updated["championship_probability_sum"] == 1.0


def test_manual_result_availability_respects_prediction_cutoff(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "result-cutoff.sqlite3")
    with TestClient(app) as client:
        assert client.post("/api/demo").status_code == 200
        initial = _simulate(client)
        opening = initial["projected_path"]["stages"]["stage1"]["rounds"][0]["matches"][0]
        imported = client.post("/api/import/json", json={
            "matches": [{
                "id": "cutoff-fixture",
                "stage_slug": "stage1",
                "round_no": 1,
                "team_a_id": opening["team_a_id"],
                "team_b_id": opening["team_b_id"],
                "best_of": 1,
                "status": "scheduled",
                "played_at": "2026-10-18T12:00:00Z",
                "available_at": "2026-10-18T13:00:00Z",
            }],
        })
        assert imported.status_code == 200, imported.text
        current = client.post("/api/simulate", json={
            "simulations": 4, "seed": 73, "cutoff": "2026-10-19T00:00:00Z",
        }).json()
        fixture_match = next(
            match for match in current["projected_path"]["stages"]["stage1"]["rounds"][0]["matches"]
            if match["fixture_id"] == "cutoff-fixture"
        )
        saved = _confirm(client, fixture_match, fixture_match["team_a_id"])
        assert saved.status_code == 200, saved.text
        assert saved.json()["result"]["model"]["eligible_matches"] == 0
        with database.connect() as connection:
            row = connection.execute("SELECT available_at,result_source FROM matches WHERE id='cutoff-fixture'").fetchone()
            assert row["result_source"] == "manual"
            assert row["available_at"] > "2026-10-19T00:00:00Z"


def test_prediction_started_on_old_state_cannot_persist_after_result_state_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "stale-prediction.sqlite3")
    with TestClient(app) as client:
        assert client.post("/api/demo").status_code == 200
        original_builder = main_module._build_tournament_result

        def simulate_then_change_state(connection, *args, **kwargs):
            result = original_builder(connection, *args, **kwargs)
            with database.connect() as concurrent_connection:
                database.bump_state_version(concurrent_connection)
            return result

        monkeypatch.setattr(main_module, "_build_tournament_result", simulate_then_change_state)
        stale = client.post("/api/simulate", json={"simulations": 2, "seed": 73})
        assert stale.status_code == 409
        assert "changed while this prediction was running" in stale.json()["detail"]
        with database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM prediction_runs").fetchone()[0] == 0
