"""Import parsing, validation, normalization and persistence for CSV/JSON exports."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Any

from fastapi import HTTPException

from .database import connect, get_metadata, utc_now


ALLOWED_RECORDS = ("teams", "matches", "players", "roster_memberships", "map_results", "map_vetoes", "player_stats")


def record_import_error(source_url: str | None, message: str) -> None:
    with connect() as connection:
        connection.execute(
            "INSERT INTO source_log(provider,source_url,retrieved_at,status,records_read,error) VALUES(?,?,?,?,0,?)",
            ("user-supplied import", source_url, utc_now(), "error", message),
        )


def csv_payload(text: str, kind: str) -> dict[str, list[dict[str, Any]]]:
    if kind not in ("teams", "matches"):
        raise ValueError("CSV kind must be teams or matches")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not reader.fieldnames:
        raise ValueError("CSV needs a header row")
    for row in rows:
        for key in ("seed", "elo_rating", "best_of", "round_no"):
            if row.get(key) in ("", None):
                row[key] = None
            elif row.get(key) is not None:
                row[key] = int(row[key]) if key in ("seed", "best_of", "round_no") else float(row[key])
        if kind == "matches":
            for key in ("stage_slug", "winner_id", "score", "played_at", "available_at", "source_url"):
                if row.get(key) == "":
                    row[key] = None
            row["status"] = row.get("status") or ("completed" if row.get("winner_id") else "scheduled")
    return {kind: rows}


def import_records(
    payload: dict[str, Any],
    source_url: str | None,
    event_id: str,
    replace_demo: bool = False,
) -> dict[str, int]:
    if not isinstance(payload, dict):
        raise HTTPException(422, "JSON import must be an object with arrays such as teams and matches.")
    for key in ALLOWED_RECORDS:
        if key in payload and not isinstance(payload[key], list):
            raise HTTPException(422, f"{key} must be an array")
    now = utc_now()
    inserted = {key: 0 for key in ALLOWED_RECORDS}
    try:
        with connect() as connection:
            valid_stage_slugs = {row["slug"] for row in connection.execute("SELECT slug FROM stages WHERE event_id=?", (event_id,))}
            if replace_demo:
                if not payload.get("teams"):
                    raise ValueError("Replacing the demo roster requires a complete teams array.")
                stage_counts = {slug: 0 for slug in ("stage1", "stage2", "stage3")}
                for item in payload["teams"]:
                    if item.get("stage_entry") in stage_counts:
                        stage_counts[item["stage_entry"]] += 1
                expected = {"stage1": 16, "stage2": 8, "stage3": 8}
                if stage_counts != expected or len(payload["teams"]) != 32:
                    raise ValueError(f"A replacement roster needs exactly 16 Stage 1, 8 Stage 2, and 8 Stage 3 direct entrants; received {stage_counts}.")
                synthetic_ids = [row["id"] for row in connection.execute("SELECT id FROM teams WHERE synthetic=1")]
                for team_id in synthetic_ids:
                    connection.execute("DELETE FROM stage_entrants WHERE team_id=?", (team_id,))
                    connection.execute("DELETE FROM teams WHERE id=?", (team_id,))
            for item in payload.get("teams", []):
                if not item.get("id") or not item.get("name"):
                    raise ValueError("Each team needs a stable id and name.")
                stage_entry = item.get("stage_entry")
                if stage_entry and stage_entry not in ("stage1", "stage2", "stage3"):
                    raise ValueError(f"Unknown stage_entry {stage_entry!r} for team {item['id']}.")
                connection.execute(
                    """INSERT INTO teams(id,name,region,seed,elo_rating,rating_source,synthetic,updated_at)
                       VALUES(?,?,?,?,?,?,0,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                       region=excluded.region,seed=excluded.seed,elo_rating=excluded.elo_rating,
                       rating_source=excluded.rating_source,synthetic=0,updated_at=excluded.updated_at""",
                    (str(item["id"]), str(item["name"]), item.get("region"), item.get("seed"),
                     item.get("elo_rating"), item.get("rating_source", "user-supplied import"), now),
                )
                if stage_entry:
                    stage_id_row = connection.execute(
                        "SELECT id FROM stages WHERE event_id=? AND slug=?", (event_id, stage_entry)
                    ).fetchone()
                    if not stage_id_row:
                        raise ValueError(f"Stage {stage_entry!r} is not configured for this event.")
                    connection.execute(
                        "DELETE FROM stage_entrants WHERE team_id=? AND stage_id IN (SELECT id FROM stages WHERE event_id=?)",
                        (str(item["id"]), event_id),
                    )
                    connection.execute(
                        "INSERT INTO stage_entrants(stage_id,team_id,seed,entry_type) VALUES(?,?,?,'direct') "
                        "ON CONFLICT(stage_id,team_id) DO UPDATE SET seed=excluded.seed,entry_type='direct'",
                        (stage_id_row["id"], str(item["id"]), int(item.get("seed") or 999)),
                    )
                inserted["teams"] += 1
            for item in payload.get("players", []):
                if not item.get("id") or not item.get("name"):
                    raise ValueError("Each player needs a stable id and name.")
                connection.execute(
                    "INSERT INTO players(id,name,country,source) VALUES(?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name,country=excluded.country,source=excluded.source",
                    (item["id"], item["name"], item.get("country"), item.get("source", "user-supplied import")),
                )
                inserted["players"] += 1
            for item in payload.get("matches", []):
                required = ("id", "team_a_id", "team_b_id")
                if any(not item.get(key) for key in required):
                    raise ValueError("Each match needs id, team_a_id, and team_b_id.")
                status = item.get("status", "completed" if item.get("winner_id") else "scheduled")
                if status not in ("scheduled", "completed"):
                    raise ValueError(f"Match {item['id']} status must be scheduled or completed.")
                if status == "completed" and item.get("winner_id") not in (item["team_a_id"], item["team_b_id"]):
                    raise ValueError(f"Completed match {item['id']} needs winner_id equal to a participant.")
                if item["team_a_id"] == item["team_b_id"]:
                    raise ValueError(f"Match {item['id']} cannot have the same team twice.")
                stage_slug = item.get("stage_slug")
                if stage_slug and stage_slug not in valid_stage_slugs and stage_slug != "playoffs":
                    raise ValueError(f"Unknown tournament stage {stage_slug!r} in match {item['id']}.")
                connection.execute(
                    """INSERT INTO matches(id,event_id,stage_slug,round_no,team_a_id,team_b_id,best_of,status,
                       winner_id,score,played_at,available_at,source,source_url,retrieved_at,parsing_error)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET
                       event_id=excluded.event_id,stage_slug=excluded.stage_slug,round_no=excluded.round_no,
                       team_a_id=excluded.team_a_id,team_b_id=excluded.team_b_id,best_of=excluded.best_of,
                       status=excluded.status,winner_id=excluded.winner_id,score=excluded.score,played_at=excluded.played_at,
                       available_at=excluded.available_at,source=excluded.source,source_url=excluded.source_url,
                       retrieved_at=excluded.retrieved_at,parsing_error=NULL""",
                    (str(item["id"]), event_id if stage_slug else None, stage_slug, item.get("round_no"),
                     item["team_a_id"], item["team_b_id"], item.get("best_of"), status, item.get("winner_id"),
                     item.get("score"), item.get("played_at"), item.get("available_at") or now,
                     item.get("source", "user-supplied import"), item.get("source_url") or source_url, now),
                )
                inserted["matches"] += 1
            for item in payload.get("roster_memberships", []):
                connection.execute(
                    "INSERT INTO roster_memberships(team_id,player_id,started_at,ended_at,role,source,available_at) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(team_id,player_id,started_at) DO UPDATE SET ended_at=excluded.ended_at,role=excluded.role,source=excluded.source,available_at=excluded.available_at",
                    (item["team_id"], item["player_id"], item.get("started_at"), item.get("ended_at"), item.get("role"),
                     item.get("source", "user-supplied import"), item.get("available_at") or now),
                )
                inserted["roster_memberships"] += 1
            for item in payload.get("map_results", []):
                connection.execute(
                    "INSERT INTO map_results(match_id,map_name,team_a_score,team_b_score,winner_id) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(match_id,map_name) DO UPDATE SET team_a_score=excluded.team_a_score,team_b_score=excluded.team_b_score,winner_id=excluded.winner_id",
                    (item["match_id"], item["map_name"], item.get("team_a_score"), item.get("team_b_score"), item.get("winner_id")),
                )
                inserted["map_results"] += 1
            for item in payload.get("map_vetoes", []):
                required = ("match_id", "sequence_no", "action", "map_name", "team_id")
                if any(item.get(key) in (None, "") for key in required):
                    raise ValueError("Each map veto needs match_id, sequence_no, action, map_name, and team_id.")
                if item["action"] not in ("pick", "ban"):
                    raise ValueError(f"Map veto action for match {item['match_id']} must be pick or ban.")
                match = connection.execute(
                    "SELECT team_a_id,team_b_id FROM matches WHERE id=?", (item["match_id"],)
                ).fetchone()
                if not match:
                    raise ValueError(f"Map veto {item['match_id']} references an unknown match.")
                if item["team_id"] not in (match["team_a_id"], match["team_b_id"]):
                    raise ValueError(f"Map veto team for match {item['match_id']} must be one of its participants.")
                connection.execute(
                    "INSERT INTO map_vetoes(match_id,sequence_no,action,map_name,team_id,available_at,source) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(match_id,sequence_no) DO UPDATE SET "
                    "action=excluded.action,map_name=excluded.map_name,team_id=excluded.team_id,"
                    "available_at=excluded.available_at,source=excluded.source",
                    (item["match_id"], int(item["sequence_no"]), item["action"], item["map_name"],
                     item["team_id"], item.get("available_at") or now, item.get("source", "user-supplied import")),
                )
                inserted["map_vetoes"] += 1
            for item in payload.get("player_stats", []):
                if not isinstance(item.get("stats", {}), dict):
                    raise ValueError("Player-stat stats must be a JSON object of named metrics.")
                connection.execute(
                    "INSERT INTO player_stats(match_id,player_id,measurement_period,metric_version,stats_json,available_at,source) VALUES(?,?,?,?,?,?,?)",
                    (item.get("match_id"), item["player_id"], item["measurement_period"], item.get("metric_version"),
                     json.dumps(item.get("stats", {})), item.get("available_at") or now, item.get("source", "user-supplied import")),
                )
                inserted["player_stats"] += 1
            has_synthetic = connection.execute("SELECT EXISTS(SELECT 1 FROM teams WHERE synthetic=1)").fetchone()[0]
            source_name = "user-supplied import + synthetic demo records" if has_synthetic else "user-supplied import"
            connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('active_data_source',?)", (source_name,))
            connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('last_successful_refresh',?)", (now,))
            connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('is_synthetic',?)", ("true" if has_synthetic else "false",))
            connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('demo_opt_in',?)", ("true" if has_synthetic else "false",))
            connection.execute(
                "INSERT INTO source_log(provider,source_url,retrieved_at,status,records_read,error) VALUES(?,?,?,?,?,NULL)",
                ("user-supplied import", source_url, now, "loaded", sum(inserted.values())),
            )
    except HTTPException:
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        record_import_error(source_url, str(exc))
        raise HTTPException(422, f"Import rejected: {exc}") from exc
    return inserted
