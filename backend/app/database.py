from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
SCHEMA_PATH = DATA_DIR / "schema.sql"
DEMO_PATH = DATA_DIR / "demo_seed.json"
DB_PATH = Path(os.getenv("MAJOR_DB_PATH", str(DATA_DIR / "major.sqlite3"))).expanduser()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    with connect() as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)", (utc_now(),)
        )
        for version, filename in (
            (2, "002_map_vetoes.sql"),
            (3, "003_roster_availability.sql"),
            (4, "004_confirmed_match_results.sql"),
        ):
            migration_path = DATA_DIR / "migrations" / filename
            if version == 3:
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(roster_memberships)")}
                if "available_at" not in columns:
                    connection.executescript(migration_path.read_text(encoding="utf-8"))
            elif version == 4:
                match_columns = {row["name"] for row in connection.execute("PRAGMA table_info(matches)")}
                run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(prediction_runs)")}
                if "fixture_type" not in match_columns or "result_json" not in run_columns:
                    connection.executescript(migration_path.read_text(encoding="utf-8"))
            else:
                connection.executescript(migration_path.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                (version, utc_now()),
            )
        payload = json.loads(DEMO_PATH.read_text(encoding="utf-8"))
        seed_event_configuration(connection, payload)
        metadata = get_metadata(connection)
        if (
            metadata.get("active_data_source") == "synthetic demonstration dataset"
            and metadata.get("demo_opt_in") != "true"
        ):
            _remove_legacy_automatic_demo(connection)
            metadata = get_metadata(connection)
        if "active_data_source" not in metadata:
            _set_metadata(connection, {
                "active_data_source": "Awaiting official participant list",
                "is_synthetic": "false",
                "demo_opt_in": "false",
                "tournament_state_version": "0",
                "event_notes": json.dumps([
                    "The official PGL Major Singapore 2026 participant list has not been published as of October 19, 2026.",
                    "The invite tracker lists a November 2 Valve Regional Standings cutoff; team identities remain unconfirmed until invitations are announced.",
                    "Team-specific predictions are unavailable until real participant records are imported. Synthetic data can be loaded explicitly for demonstration.",
                    "PGL confirms the event window and 32-team, three-stage structure; HLTV lists Stage 1 one day before PGL's published event start.",
                ]),
            })


def seed_event_configuration(connection: sqlite3.Connection, payload: dict[str, Any] | None = None) -> None:
    payload = payload or json.loads(DEMO_PATH.read_text(encoding="utf-8"))
    event = payload["event"]
    connection.execute(
        """INSERT OR IGNORE INTO events(id,name,organizer,start_date,end_date,location,source_urls,details_status)
           VALUES(?,?,?,?,?,?,?,?)""",
        (event["id"], event["name"], event["organizer"], event["start_date"], event["end_date"],
         event["location"], json.dumps(event["source_urls"]), event["details_status"]),
    )
    for stage in payload["stages"]:
        rules = dict(stage["rules"])
        rules["entrants"] = stage["entrants"]
        rules["advances"] = stage["advances"]
        connection.execute(
            """INSERT OR IGNORE INTO stages(id,event_id,slug,name,start_date,end_date,entrants,advances,rules_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (f"{event['id']}:{stage['slug']}", event["id"], stage["slug"], stage["name"],
             stage.get("start_date"), stage.get("end_date"), stage["entrants"], stage["advances"],
             json.dumps(rules)),
        )


def seed_demo(connection: sqlite3.Connection) -> None:
    payload = json.loads(DEMO_PATH.read_text(encoding="utf-8"))
    event = payload["event"]
    now = utc_now()
    seed_event_configuration(connection, payload)
    for team in payload["teams"]:
        connection.execute(
            """INSERT OR IGNORE INTO teams(id,name,region,seed,elo_rating,rating_source,synthetic,updated_at)
               VALUES(?,?,?,?,?,?,1,?)""",
            (team["id"], team["name"], team.get("region"), team["seed"], team["elo_rating"],
             "synthetic seed in demo data", now),
        )
        stage_slug = team.get("stage_entry")
        if stage_slug:
            stage_id = f"{event['id']}:{stage_slug}"
            connection.execute(
                "INSERT OR IGNORE INTO stage_entrants(stage_id,team_id,seed,entry_type) VALUES(?,?,?,'direct')",
                (stage_id, team["id"], team["seed"]),
            )
    _set_metadata(connection, {
        "active_data_source": payload["source"],
        "last_successful_refresh": now,
        "is_synthetic": "true",
        "demo_opt_in": "true",
        "event_notes": json.dumps(payload["notes"]),
    })
    connection.execute(
        "INSERT INTO source_log(provider,source_url,retrieved_at,status,records_read,error) VALUES(?,?,?,?,?,NULL)",
        (payload["source"], None, now, "loaded", len(payload["teams"])),
    )


def _set_metadata(connection: sqlite3.Connection, values: dict[str, str]) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)", values.items()
    )


def _remove_legacy_automatic_demo(connection: sqlite3.Connection) -> None:
    """Remove the old first-run demo only when it is the entire stored dataset."""
    tables = ("matches", "players", "roster_memberships", "player_stats", "map_results", "map_vetoes")
    if any(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables):
        return
    teams = connection.execute("SELECT id,synthetic FROM teams").fetchall()
    if not teams or any(not row["synthetic"] for row in teams):
        return
    team_ids = [row["id"] for row in teams]
    connection.executemany("DELETE FROM stage_entrants WHERE team_id=?", ((team_id,) for team_id in team_ids))
    connection.executemany("DELETE FROM teams WHERE id=?", ((team_id,) for team_id in team_ids))
    connection.execute("DELETE FROM metadata WHERE key='last_successful_refresh'")
    _set_metadata(connection, {
        "active_data_source": "Awaiting official participant list",
        "is_synthetic": "false",
        "demo_opt_in": "false",
        "event_notes": json.dumps([
            "The official PGL Major Singapore 2026 participant list has not been published as of October 19, 2026.",
            "The invite tracker lists a November 2 Valve Regional Standings cutoff; team identities remain unconfirmed until invitations are announced.",
            "Team-specific predictions are unavailable until real participant records are imported. Synthetic data can be loaded explicitly for demonstration.",
            "PGL confirms the event window and 32-team, three-stage structure; HLTV lists Stage 1 one day before PGL's published event start.",
        ]),
    })
    now = utc_now()
    connection.execute(
        "INSERT INTO source_log(provider,source_url,retrieved_at,status,records_read,error) VALUES(?,?,?,?,0,NULL)",
        ("legacy automatic demo cleanup", None, now, "unloaded"),
    )


def get_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in connection.execute("SELECT key,value FROM metadata")}


def state_version(connection: sqlite3.Connection) -> int:
    return int(get_metadata(connection).get("tournament_state_version", "0"))


def bump_state_version(connection: sqlite3.Connection) -> int:
    next_version = state_version(connection) + 1
    _set_metadata(connection, {"tournament_state_version": str(next_version)})
    return next_version


def as_dicts(rows: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]
