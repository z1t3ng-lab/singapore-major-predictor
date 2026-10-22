from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .database import bump_state_version, connect, get_metadata, initialize_database, seed_demo, state_version, utc_now
from .engine import MatchDependencyConflict, predict_match, simulate_tournament
from .feature_engineering import enrich_teams_with_context
from .importers import csv_payload, import_records, record_import_error
from .model import available_matches, build_model_snapshot
from .providers import HltvProvider


EVENT_ID = "pgl-major-singapore-2026"
HLTV = HltvProvider()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Singapore Major Predictor API",
    version="1.0.0",
    description="Cutoff-aware predictions and Swiss/playoff simulations for PGL CS2 Major Singapore 2026.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def serialize_workspace(request: Request, call_next):
    from .workspace_lock import workspace_lock
    async with workspace_lock():
        return await call_next(request)


class SimulationRequest(BaseModel):
    simulations: int = Field(default=2500, ge=1, le=10000)
    seed: int = Field(default=73, ge=0, le=2**31 - 1)
    cutoff: str | None = None
    scenario_overrides: dict[str, str] = Field(default_factory=dict)


class MatchPredictionRequest(BaseModel):
    team_a_id: str
    team_b_id: str
    best_of: int = Field(default=3, description="1, 3, or 5")
    cutoff: str | None = None


class MatchResultRequest(BaseModel):
    winner_id: str | None = None
    score: str | None = None
    expected_result_version: int = Field(default=0, ge=0)
    invalidate_conflict_ids: list[str] = Field(default_factory=list)


def _team_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id,name,region,seed,elo_rating,rating_source,synthetic,updated_at FROM teams ORDER BY seed,id"
    ).fetchall()
    return [{**dict(row), "synthetic": bool(row["synthetic"])} for row in rows]


def _stage_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM stages WHERE event_id=? ORDER BY CASE slug "
        "WHEN 'stage1' THEN 1 WHEN 'stage2' THEN 2 WHEN 'stage3' THEN 3 ELSE 4 END", (EVENT_ID,)
    ).fetchall()
    return [{**dict(row), "rules": json.loads(row["rules_json"])} for row in rows]


def _all_match_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute("SELECT * FROM matches ORDER BY played_at,id").fetchall()]


def _projected_matches(result: dict[str, Any]) -> list[dict[str, Any]]:
    path = result.get("projected_path", {})
    matches: list[dict[str, Any]] = []
    for stage in path.get("stages", {}).values():
        for round_data in stage.get("rounds", []):
            matches.extend(round_data.get("matches", []))
    for round_data in path.get("playoffs", {}).get("rounds", []):
        matches.extend(round_data.get("matches", []))
    return matches


def _find_projected_match(result: dict[str, Any], match_id: str) -> dict[str, Any] | None:
    return next((
        match for match in _projected_matches(result)
        if match.get("id") == match_id or match.get("fixture_id") == match_id
    ), None)


def _build_tournament_result(
    connection: sqlite3.Connection,
    simulations: int,
    seed: int,
    cutoff: str,
    scenario_overrides: dict[str, str],
    model_state: dict[str, Any] | None = None,
    allow_training: bool = True,
) -> dict[str, Any]:
    teams = _team_rows(connection)
    if len(teams) < 32:
        raise ValueError("Import 32 stage entrant team records before simulating the full tournament.")
    stages = _stage_rows(connection)
    stage_rules = {stage["slug"]: stage["rules"] for stage in stages if stage["slug"] in ("stage1", "stage2", "stage3")}
    entrants_by_stage = {slug: _direct_entrants(connection, slug) for slug in ("stage1", "stage2", "stage3")}
    matches = _all_match_rows(connection)
    training_matches = []
    for match in matches:
        training_match = dict(match)
        if match.get("result_status") == "confirmed" and match.get("confirmed_winner_id"):
            training_match["winner_id"] = match["confirmed_winner_id"]
            if match.get("confirmed_score") is not None:
                training_match["score"] = match["confirmed_score"]
        training_matches.append(training_match)
    prediction_teams = _prediction_team_rows(connection, teams, training_matches, cutoff)
    model = build_model_snapshot(prediction_teams, training_matches, cutoff, model_state, allow_training)
    tournament_matches = [
        match for match in matches
        if match.get("event_id") == EVENT_ID
        and match.get("stage_slug") in ("stage1", "stage2", "stage3", "playoffs")
    ]
    result = simulate_tournament(
        entrants_by_stage,
        stage_rules,
        model["predictor"],
        simulations,
        seed,
        scenario_overrides,
        tournament_matches,
    )
    result.update({
        "cutoff": cutoff,
        "model": {
            "name": model["name"],
            "status": model["status"],
            "status_message": model["status_message"],
            "eligible_matches": model["eligible_matches"],
            "metrics": model["metrics"],
            "metrics_scope": model["metrics_scope"],
            "trained_cutoff": model["trained_cutoff"],
        },
        "model_state": model["state"],
        "data_source": get_metadata(connection).get("active_data_source", "unknown"),
        "scenario": {"active": bool(scenario_overrides), "overrides": scenario_overrides},
        "sampling_uncertainty": "Approximate 95% interval = estimate ± 1.96 × standard error; estimates are Monte Carlo samples.",
        "assumptions": [
            "Each 16-team Swiss stage uses three wins to advance and three losses to be eliminated; this is provisional until organizer rules confirm it.",
            "Pairings group teams by record, order by configured seed, and avoid rematches when a complete no-rematch pairing exists.",
            "Stage 1 and Stage 2 opening matches are best-of-one; advancement/elimination matches are best-of-three. Stage 3 matches are best-of-three.",
            "A series uses one adjusted map probability for each map and assumes identical, independent map outcomes. Historical map vetoes only reweight the map pool; the current match veto is unknown unless represented by imported completed history.",
            "Map pool, player ratings, roster continuity, and head-to-head use fixed bounded heuristic priors, not learned coefficients. The UI exposes which signals were available and their sample sizes.",
            "Eight direct Stage 2 and eight direct Stage 3 entrants are populated from configured slots; exact seeds and playoff bracket order remain provisional.",
        ],
    })
    return result


def _save_prediction_run(
    connection: sqlite3.Connection,
    result: dict[str, Any],
    cutoff: str,
    simulations: int,
    seed: int,
    scenario_overrides: dict[str, str],
    tournament_state_version: int,
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    created_at = utc_now()
    result.update({"run_id": run_id, "state_version": tournament_state_version})
    connection.execute(
        """INSERT INTO prediction_runs(id,data_cutoff,random_seed,simulations,model_name,created_at,
           scenario_json,state_version,result_json) VALUES(?,?,?,?,?,?,?,?,?)""",
        (run_id, cutoff, seed, simulations, result["model"]["name"], created_at,
         json.dumps(scenario_overrides), tournament_state_version, json.dumps(result)),
    )
    return result


def _validate_result_score(score: str | None, winner_id: str, match: dict[str, Any]) -> str | None:
    if score is None or not score.strip():
        return None
    best_of = int(match.get("best_of") or 0)
    if best_of not in (1, 3, 5):
        raise HTTPException(422, "This match has no supported series format for score validation.")
    normalized = score.strip().replace("-", ":")
    found = re.fullmatch(r"(\d+)\s*:\s*(\d+)", normalized)
    if not found:
        raise HTTPException(422, "Enter a series score as Team A maps:Team B maps, such as 2:1.")
    score_a, score_b = (int(value) for value in found.groups())
    needed = best_of // 2 + 1
    if max(score_a, score_b) != needed or min(score_a, score_b) >= needed or score_a == score_b:
        raise HTTPException(422, f"A BO{best_of} series score must end when one team reaches {needed} map wins.")
    score_winner = match["team_a_id"] if score_a > score_b else match["team_b_id"]
    if score_winner != winner_id:
        raise HTTPException(422, "The series score winner must match the selected actual winner.")
    return f"{score_a}:{score_b}"


def _dependency_conflict_detail(
    connection: sqlite3.Connection,
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    teams = {team["id"]: team["name"] for team in _team_rows(connection)}
    detailed = [{
        **item,
        "team_a_name": teams.get(item.get("team_a_id"), item.get("team_a_id")),
        "team_b_name": teams.get(item.get("team_b_id"), item.get("team_b_id")),
        "winner_name": teams.get(item.get("winner_id"), item.get("winner_id")),
    } for item in conflicts]
    return {
        "message": "This change makes later confirmed match results inconsistent with the regenerated tournament path.",
        "conflicts": detailed,
    }


def _direct_entrants(connection: sqlite3.Connection, stage_slug: str) -> list[dict[str, Any]]:
    return [
        {**dict(row), "synthetic": bool(row["synthetic"])}
        for row in connection.execute(
            """SELECT t.id,t.name,t.region,se.seed,t.elo_rating,t.rating_source,t.synthetic
               FROM stage_entrants se JOIN stages s ON s.id=se.stage_id
               JOIN teams t ON t.id=se.team_id
               WHERE s.event_id=? AND s.slug=? ORDER BY se.seed,t.id""",
            (EVENT_ID, stage_slug),
        ).fetchall()
    ]


def _prediction_team_rows(
    connection: sqlite3.Connection,
    teams: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    cutoff: str,
) -> list[dict[str, Any]]:
    """Build a cutoff-safe feature snapshot once per API prediction/simulation."""
    map_results = [dict(row) for row in connection.execute(
        """SELECT mr.match_id,mr.map_name,mr.winner_id FROM map_results mr"""
    ).fetchall()]
    map_vetoes = [dict(row) for row in connection.execute(
        """SELECT match_id,sequence_no,action,map_name,team_id,available_at FROM map_vetoes
           WHERE available_at IS NOT NULL AND julianday(available_at)<=julianday(?)""", (cutoff,)
    ).fetchall()]
    rosters = [dict(row) for row in connection.execute(
        """SELECT team_id,player_id,started_at,ended_at,role,available_at FROM roster_memberships
           WHERE available_at IS NOT NULL AND julianday(available_at)<=julianday(?)""", (cutoff,)
    ).fetchall()]
    player_stats = []
    for row in connection.execute(
        """SELECT id,match_id,player_id,measurement_period,metric_version,stats_json,available_at,source
           FROM player_stats WHERE available_at IS NOT NULL AND julianday(available_at)<=julianday(?)""", (cutoff,)
    ).fetchall():
        item = dict(row)
        try:
            item["stats"] = json.loads(item.pop("stats_json"))
        except (TypeError, ValueError):
            continue
        if not isinstance(item["stats"], dict):
            continue
        player_stats.append(item)
    return enrich_teams_with_context(teams, matches, map_results, map_vetoes, rosters, player_stats, cutoff)


def _cutoff(value: str | None) -> str:
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
        except ValueError as exc:
            raise HTTPException(422, "cutoff must be an ISO-8601 date or datetime") from exc
    return utc_now()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "singapore-major-predictor"}


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    with connect() as connection:
        metadata = get_metadata(connection)
        event = connection.execute("SELECT * FROM events WHERE id=?", (EVENT_ID,)).fetchone()
        if not event:
            raise HTTPException(500, "Event configuration is missing. Restart after restoring the seed data.")
        stages = _stage_rows(connection)
        teams = _team_rows(connection)
        matches = _all_match_rows(connection)
        model_snapshot = build_model_snapshot(teams, matches, utc_now())
        total_matches = connection.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]
        event_results = connection.execute(
            """SELECT COUNT(*) AS n FROM matches WHERE event_id=? AND result_status='confirmed'
               AND (confirmed_winner_id IS NOT NULL OR winner_id IS NOT NULL)""",
            (EVENT_ID,),
        ).fetchone()["n"]
        direct_count = connection.execute(
            "SELECT COUNT(*) AS n FROM stage_entrants se JOIN stages s ON s.id=se.stage_id WHERE s.event_id=?",
            (EVENT_ID,),
        ).fetchone()["n"]
        notes = json.loads(metadata.get("event_notes", "[]"))
        official_source_urls = json.loads(event["source_urls"] or "[]")
        latest_log = connection.execute(
            "SELECT provider,retrieved_at,status,records_read,error FROM source_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "event": dict(event),
            "official_source_urls": official_source_urls,
            "stages": stages,
            "notes": notes,
            "data": {
                "active_source": metadata.get("active_data_source", "unknown"),
                "last_successful_refresh": metadata.get("last_successful_refresh"),
                "match_records_available": total_matches,
                "completed_event_results": event_results,
                "team_records": len(teams),
                "direct_stage_entrants": direct_count,
                "synthetic_records_present": any(team["synthetic"] for team in teams),
                "latest_source_log": dict(latest_log) if latest_log else None,
                "hltv_automated_refresh": {"enabled": HLTV.enabled, "reason": HLTV.reason},
            },
            "model": {
                "name": model_snapshot["name"],
                "status": model_snapshot["status"],
                "trained": model_snapshot["status"] == "trained_chronological_split",
                "eligible_matches": model_snapshot["eligible_matches"],
                "status_message": model_snapshot["status_message"],
                "metrics": model_snapshot["metrics"],
                "metrics_scope": model_snapshot["metrics_scope"],
            },
        }


@app.get("/api/teams")
def list_teams() -> dict[str, Any]:
    with connect() as connection:
        teams = _team_rows(connection)
        direct_entries = connection.execute(
            """SELECT se.team_id,s.slug AS stage_slug,se.seed,se.entry_type
               FROM stage_entrants se JOIN stages s ON s.id=se.stage_id WHERE s.event_id=?""", (EVENT_ID,)
        ).fetchall()
        entries: dict[str, list[dict[str, Any]]] = {}
        for row in direct_entries:
            entries.setdefault(row["team_id"], []).append({"stage": row["stage_slug"], "seed": row["seed"], "type": row["entry_type"]})
        for team in teams:
            team["direct_entries"] = entries.get(team["id"], [])
        return {"teams": teams}


@app.post("/api/demo")
def load_demo_dataset() -> dict[str, Any]:
    """Load synthetic teams only after an explicit user action on an empty dataset."""
    with connect() as connection:
        existing = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("teams", "matches", "players", "roster_memberships", "player_stats", "map_results", "map_vetoes")
        }
        if any(existing.values()):
            raise HTTPException(409, "The demo can only be loaded into an empty dataset. Export your data before replacing it.")
        seed_demo(connection)
        bump_state_version(connection)
        return {
            "ok": True,
            "data_source": get_metadata(connection).get("active_data_source"),
            "teams_loaded": connection.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
        }


@app.get("/api/stages/{stage_slug}")
def stage_detail(stage_slug: str) -> dict[str, Any]:
    with connect() as connection:
        stages = _stage_rows(connection)
        stage = next((item for item in stages if item["slug"] == stage_slug), None)
        if not stage:
            raise HTTPException(404, "Unknown stage")
        entrants = _direct_entrants(connection, stage_slug)
        imported_fixtures = [
            dict(row) for row in connection.execute(
                "SELECT * FROM matches WHERE event_id=? AND stage_slug=? ORDER BY round_no,id",
                (EVENT_ID, stage_slug),
            ).fetchall()
        ]
        return {"stage": stage, "direct_entrants": entrants, "confirmed_matches": imported_fixtures}


@app.post("/api/predict")
def predict(request: MatchPredictionRequest) -> dict[str, Any]:
    if request.best_of not in (1, 3, 5):
        raise HTTPException(422, "best_of must be 1, 3, or 5")
    cutoff = _cutoff(request.cutoff)
    with connect() as connection:
        teams = {team["id"]: team for team in _team_rows(connection)}
        if request.team_a_id not in teams or request.team_b_id not in teams:
            raise HTTPException(404, "Unknown team ID")
        if request.team_a_id == request.team_b_id:
            raise HTTPException(422, "A team cannot play itself")
        rows = _all_match_rows(connection)
        prediction_teams = _prediction_team_rows(connection, list(teams.values()), rows, cutoff)
        prediction_teams_by_id = {team["id"]: team for team in prediction_teams}
        snapshot = build_model_snapshot(prediction_teams, rows, cutoff)
        result = snapshot["predictor"](prediction_teams_by_id[request.team_a_id], prediction_teams_by_id[request.team_b_id], request.best_of)
        eligible = available_matches(rows, cutoff)
        pair_history = [m for m in eligible if {m["team_a_id"], m["team_b_id"]} == {request.team_a_id, request.team_b_id}]
        head_to_head_results = [
            {
                "match_id": match["id"],
                "played_at": match.get("played_at"),
                "winner_id": match["winner_id"],
                "winner_name": teams[match["winner_id"]]["name"],
                "score": match.get("score"),
                "best_of": match.get("best_of"),
                "source": match.get("source"),
            }
            for match in pair_history[-5:]
        ]
        map_rows = connection.execute(
            """SELECT m.id AS match_id,m.played_at,m.team_a_id,m.team_b_id,m.best_of,m.source,
                      mr.map_name,mr.team_a_score,mr.team_b_score,mr.winner_id
               FROM map_results mr JOIN matches m ON m.id=mr.match_id
               WHERE (m.team_a_id IN (?,?) OR m.team_b_id IN (?,?))
                 AND m.status='completed' AND m.winner_id IS NOT NULL
                 AND m.played_at IS NOT NULL AND m.available_at IS NOT NULL
                 AND julianday(m.played_at)<=julianday(?) AND julianday(m.available_at)<=julianday(?)
               ORDER BY julianday(m.played_at) DESC,m.id,mr.map_name LIMIT 20""",
            (request.team_a_id, request.team_b_id, request.team_a_id, request.team_b_id, cutoff, cutoff),
        ).fetchall()
        map_results = [
            {
                **dict(row),
                "team_a_name": teams[row["team_a_id"]]["name"],
                "team_b_name": teams[row["team_b_id"]]["name"],
                "winner_name": teams[row["winner_id"]]["name"] if row["winner_id"] in teams else row["winner_id"],
            }
            for row in map_rows
        ]
        veto_rows = connection.execute(
            """SELECT mv.match_id,mv.sequence_no,mv.action,mv.map_name,mv.team_id,mv.available_at,
                      m.played_at,m.team_a_id,m.team_b_id
               FROM map_vetoes mv JOIN matches m ON m.id=mv.match_id
               WHERE m.team_a_id IN (?,?) AND m.team_b_id IN (?,?)
                 AND m.status='completed' AND m.winner_id IS NOT NULL
                 AND m.played_at IS NOT NULL AND m.available_at IS NOT NULL AND mv.available_at IS NOT NULL
                 AND julianday(m.played_at)<=julianday(?) AND julianday(m.available_at)<=julianday(?)
                 AND julianday(mv.available_at)<=julianday(?)
               ORDER BY julianday(m.played_at) DESC,mv.sequence_no LIMIT 20""",
            (request.team_a_id, request.team_b_id, request.team_a_id, request.team_b_id, cutoff, cutoff, cutoff),
        ).fetchall()
        map_vetoes = [
            {**dict(row), "team_name": teams[row["team_id"]]["name"]}
            for row in veto_rows
        ]
        player_rows = connection.execute(
            """SELECT ps.match_id,ps.player_id,p.name,rm.team_id,ps.measurement_period,ps.metric_version,
                      ps.stats_json,ps.available_at,ps.source
               FROM player_stats ps JOIN players p ON p.id=ps.player_id
               JOIN roster_memberships rm ON rm.player_id=ps.player_id
               LEFT JOIN matches m ON m.id=ps.match_id
               WHERE rm.team_id IN (?,?) AND ps.available_at IS NOT NULL
                 AND julianday(ps.available_at)<=julianday(?) AND julianday(ps.measurement_period)<=julianday(?)
                 AND rm.available_at IS NOT NULL AND julianday(rm.available_at)<=julianday(?)
                 AND (rm.started_at IS NULL OR julianday(rm.started_at)<=julianday(?))
                 AND (rm.ended_at IS NULL OR julianday(rm.ended_at)>julianday(?))
                 AND (ps.match_id IS NULL OR (m.id IS NOT NULL AND m.status='completed'
                      AND m.winner_id IS NOT NULL AND m.played_at IS NOT NULL AND m.available_at IS NOT NULL
                      AND julianday(m.played_at)<=julianday(?) AND julianday(m.available_at)<=julianday(?)))
               ORDER BY julianday(ps.available_at) DESC,ps.id DESC LIMIT 20""",
            (request.team_a_id, request.team_b_id, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff),
        ).fetchall()
        player_stats = [
            {
                **dict(row),
                "stats": json.loads(row["stats_json"]),
                "team_name": teams[row["team_id"]]["name"],
            }
            for row in player_rows
        ]
        recent = {
            team_id: [
                {"match_id": item["id"], "played_at": item["played_at"], "opponent_id": item["team_b_id"] if item["team_a_id"] == team_id else item["team_a_id"],
                 "result": "W" if item["winner_id"] == team_id else "L", "source": item["source"]}
                for item in eligible if team_id in (item["team_a_id"], item["team_b_id"])
            ][-5:]
            for team_id in (request.team_a_id, request.team_b_id)
        }
        result.update({
            "team_a_name": teams[request.team_a_id]["name"],
            "team_b_name": teams[request.team_b_id]["name"],
            "cutoff": cutoff,
            "model_status": {
                "name": snapshot["name"], "status": snapshot["status"],
                "status_message": snapshot["status_message"],
                "eligible_matches": snapshot["eligible_matches"], "metrics": snapshot["metrics"],
                "metrics_scope": snapshot["metrics_scope"],
            },
            "recent_results": recent,
            "head_to_head_count": len(pair_history),
            "head_to_head_results": head_to_head_results,
            "head_to_head_used": result["head_to_head_used"],
            "map_results": map_results,
            "map_results_available": len(map_results),
            "map_vetoes": map_vetoes,
            "map_vetoes_available": len(map_vetoes),
            "player_stats": player_stats,
            "player_metrics_available": len(player_stats),
            "data_warnings": [item["summary"] for item in result["context_features"] if not item["used"]],
        })
        result["roster_status"] = {}
        cutoff_date = cutoff[:10]
        for team_id in (request.team_a_id, request.team_b_id):
            roster = connection.execute(
                """SELECT p.id,p.name,rm.started_at,rm.ended_at FROM roster_memberships rm
                   JOIN players p ON p.id=rm.player_id WHERE rm.team_id=?
                   AND rm.available_at IS NOT NULL AND julianday(rm.available_at)<=julianday(?)
                   AND (rm.started_at IS NULL OR date(rm.started_at)<=date(?))
                   AND (rm.ended_at IS NULL OR date(rm.ended_at)>date(?))""",
                (team_id, cutoff, cutoff_date, cutoff_date),
            ).fetchall()
            result["roster_status"][team_id] = {"active_members": len(roster), "available": bool(roster)}
            if not roster:
                result["warnings"].append(f"No roster snapshot is available for {teams[team_id]['name']} at the selected cutoff.")
            elif len(roster) < 5:
                result["warnings"].append(f"Only {len(roster)} active roster member(s) are recorded for {teams[team_id]['name']}.")
        result["warnings"] = list(dict.fromkeys(result["warnings"]))
        return result


@app.post("/api/simulate")
def simulate(request: SimulationRequest) -> dict[str, Any]:
    cutoff = _cutoff(request.cutoff)
    with connect() as connection:
        snapshot_version = state_version(connection)
        try:
            result = _build_tournament_result(
                connection, request.simulations, request.seed, cutoff, request.scenario_overrides
            )
        except MatchDependencyConflict as exc:
            raise HTTPException(409, _dependency_conflict_detail(connection, exc.conflicts)) from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, f"Tournament configuration error: {exc}") from exc
        connection.execute("BEGIN IMMEDIATE")
        if state_version(connection) != snapshot_version:
            raise HTTPException(409, "Tournament data changed while this prediction was running. Run it again to get a current result.")
        return _save_prediction_run(
            connection, result, cutoff, request.simulations, request.seed,
            request.scenario_overrides, snapshot_version,
        )


def _mutate_match_result(match_id: str, request: MatchResultRequest, clear: bool) -> dict[str, Any]:
    from .workspace import backup
    backup("before match result change")
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        latest_run = connection.execute(
            "SELECT * FROM prediction_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if not latest_run or not latest_run["result_json"]:
            raise HTTPException(409, "Run a tournament prediction before setting a match result.")
        current_version = state_version(connection)
        if int(latest_run["state_version"] or 0) != current_version:
            raise HTTPException(409, "The current prediction is out of date. Recompute the tournament, then try again.")
        try:
            stored_result = json.loads(latest_run["result_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(500, "The latest prediction snapshot could not be read.") from exc
        projected_match = _find_projected_match(stored_result, match_id)
        if not projected_match:
            raise HTTPException(409, "This match is no longer part of the current projected tournament path. Refresh the page and select the current matchup.")

        record_id = projected_match.get("fixture_id") or projected_match["id"]
        existing_row = connection.execute("SELECT * FROM matches WHERE id=?", (record_id,)).fetchone()
        if existing_row:
            row = dict(existing_row)
            if (row["team_a_id"], row["team_b_id"]) not in (
                (projected_match["team_a_id"], projected_match["team_b_id"]),
                (projected_match["team_b_id"], projected_match["team_a_id"]),
            ):
                raise HTTPException(409, "This match identifier now refers to different participants. Refresh and select the current matchup.")
        else:
            if projected_match.get("fixture_type") == "official":
                raise HTTPException(409, "The official fixture record is missing from storage. Re-import the current fixtures before setting its result.")
            now = utc_now()
            connection.execute(
                """INSERT INTO matches(id,event_id,stage_slug,round_no,team_a_id,team_b_id,best_of,status,
                   source,available_at,retrieved_at,fixture_type,result_status)
                   VALUES(?,?,?,?,?,?,?,'scheduled','manual result entry',?,?,?,'pending')""",
                (record_id, EVENT_ID, projected_match["stage_slug"], projected_match["round_no"],
                 projected_match["team_a_id"], projected_match["team_b_id"], projected_match["best_of"], now, now, "generated"),
            )
            row = dict(connection.execute("SELECT * FROM matches WHERE id=?", (record_id,)).fetchone())

        if int(row.get("result_version") or 0) != request.expected_result_version:
            raise HTTPException(409, "This result changed in another update. Refresh the tournament and try again.")
        if clear:
            if projected_match.get("result_status") != "confirmed" and not projected_match.get("confirmed_winner_id"):
                raise HTTPException(409, "This match does not have a confirmed result to clear.")
            connection.execute(
                """UPDATE matches SET result_status='cleared',confirmed_winner_id=NULL,confirmed_score=NULL,
                   result_source='manual',result_updated_at=?,result_version=result_version+1,status='scheduled'
                   WHERE id=?""",
                (utc_now(), record_id),
            )
        else:
            winner_id = request.winner_id
            if not winner_id:
                raise HTTPException(422, "Select one of the participating teams as the winner.")
            if winner_id not in (projected_match["team_a_id"], projected_match["team_b_id"]):
                raise HTTPException(422, "The selected winner must be one of this match's participants.")
            score = _validate_result_score(request.score, winner_id, projected_match)
            original_prediction_json = row.get("original_prediction_json")
            original_prediction_at = row.get("original_prediction_at")
            prediction_version = row.get("prediction_version")
            if not original_prediction_json and row.get("result_status") != "confirmed" and row.get("status") != "completed":
                original_prediction_json = json.dumps(projected_match["prediction"])
                original_prediction_at = latest_run["created_at"]
                prediction_version = latest_run["id"]
            now = utc_now()
            connection.execute(
                """UPDATE matches SET status='completed',confirmed_winner_id=?,confirmed_score=?,result_status='confirmed',
                   result_source='manual',result_created_at=COALESCE(result_created_at,?),result_updated_at=?,
                   result_version=result_version+1,original_prediction_json=?,original_prediction_at=?,prediction_version=?,
                   available_at=?,retrieved_at=?
                   WHERE id=?""",
                (winner_id, score, now, now, original_prediction_json, original_prediction_at, prediction_version,
                 now, now, record_id),
            )

        simulations = int(latest_run["simulations"])
        seed = int(latest_run["random_seed"])
        cutoff = latest_run["data_cutoff"]
        scenario_overrides = json.loads(latest_run["scenario_json"] or "{}")
        model_state = stored_result.get("model_state")
        invalidated_ids: list[str] = []
        try:
            result = _build_tournament_result(
                connection, simulations, seed, cutoff, scenario_overrides,
                model_state=model_state, allow_training=False,
            )
            if request.invalidate_conflict_ids:
                raise HTTPException(409, "The downstream conflicts changed. Reopen the result and review the current conflict list.")
        except MatchDependencyConflict as exc:
            conflicts = exc.conflicts
            conflict_ids = {str(item["id"]) for item in conflicts}
            supplied_ids = set(request.invalidate_conflict_ids)
            if supplied_ids != conflict_ids:
                raise HTTPException(409, _dependency_conflict_detail(connection, conflicts)) from exc
            if any(not item.get("can_invalidate") for item in conflicts):
                raise HTTPException(409, {
                    "message": "At least one conflicting result belongs to an official fixture and cannot be invalidated as a generated prediction.",
                    "conflicts": conflicts,
                }) from exc
            for conflict in conflicts:
                current = connection.execute(
                    "SELECT fixture_type,result_status FROM matches WHERE id=?", (conflict["id"],)
                ).fetchone()
                if not current or current["result_status"] != "confirmed":
                    raise HTTPException(409, "A downstream result changed while conflicts were being reviewed. Refresh and try again.") from exc
                connection.execute(
                    """UPDATE matches SET result_status='cleared',confirmed_winner_id=NULL,confirmed_score=NULL,
                       result_source='manual',result_updated_at=?,result_version=result_version+1,status='scheduled'
                       WHERE id=?""",
                    (utc_now(), conflict["id"]),
                )
                invalidated_ids.append(str(conflict["id"]))
            try:
                result = _build_tournament_result(
                    connection, simulations, seed, cutoff, scenario_overrides,
                    model_state=model_state, allow_training=False,
                )
            except (MatchDependencyConflict, ValueError, KeyError) as second_exc:
                raise HTTPException(422, f"The tournament could not be recalculated after the confirmed conflicts were cleared: {second_exc}") from second_exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, f"Tournament recalculation failed: {exc}") from exc

        new_version = bump_state_version(connection)
        result = _save_prediction_run(
            connection, result, cutoff, simulations, seed, scenario_overrides, new_version,
        )
        return {
            "message": "Result cleared. Predictions updated." if clear else "Result confirmed. Predictions updated.",
            "result": result,
            "invalidated_result_ids": invalidated_ids,
        }


@app.put("/api/matches/{match_id}/result")
def set_match_result(match_id: str, request: MatchResultRequest) -> dict[str, Any]:
    return _mutate_match_result(match_id, request, clear=False)


@app.delete("/api/matches/{match_id}/result")
def clear_match_result(match_id: str, request: MatchResultRequest) -> dict[str, Any]:
    return _mutate_match_result(match_id, request, clear=True)


@app.post("/api/import/json")
async def import_json(request: Request, replace_demo: bool = Query(default=False), expected_state_version: int | None = None) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        record_import_error(request.headers.get("x-source-url"), "Request body is not valid JSON")
        raise HTTPException(400, "Request body is not valid JSON") from exc
    with connect() as connection:
        if expected_state_version is not None and state_version(connection) != expected_state_version:
            raise HTTPException(409, "Data changed since preview. Preview the import again.")
    counts = import_records(payload, request.headers.get("x-source-url"), EVENT_ID, replace_demo)
    return {"ok": True, "records_imported": counts, "data_source": "user-supplied import"}


@app.post("/api/import/csv")
async def import_csv(request: Request, kind: str = Query(..., pattern="^(teams|matches)$"), replace_demo: bool = Query(default=False), expected_state_version: int | None = None) -> dict[str, Any]:
    try:
        text = (await request.body()).decode("utf-8-sig")
        payload = csv_payload(text, kind)
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        record_import_error(request.headers.get("x-source-url"), str(exc))
        raise HTTPException(422, f"CSV import rejected: {exc}") from exc
    with connect() as connection:
        if expected_state_version is not None and state_version(connection) != expected_state_version:
            raise HTTPException(409, "Data changed since preview. Preview the import again.")
    counts = import_records(payload, request.headers.get("x-source-url"), EVENT_ID, replace_demo)
    return {"ok": True, "records_imported": counts, "data_source": "user-supplied import"}


@app.get("/api/exports/predictions")
def latest_prediction() -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute("SELECT * FROM prediction_runs ORDER BY rowid DESC LIMIT 1").fetchone()
        if not row:
            raise HTTPException(404, "No prediction run is stored yet")
        return dict(row)


from .workspace import router as workspace_router
app.include_router(workspace_router)
