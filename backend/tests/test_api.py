from fastapi.testclient import TestClient

from app import database
from app.main import app


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
        assert {1, 2, 3}.issubset(versions)
