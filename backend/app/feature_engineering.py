"""Cutoff-safe, inspectable context features for CS2 match predictions.

These deliberately conservative adjustments are transparent heuristic priors, not
learned coefficients. They are only populated from records available by the chosen
prediction cutoff; missing or incomparable inputs are omitted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import exp, isfinite
from typing import Any

def _time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        text = str(value)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        # Month and year precision are common for measurement windows.
        try:
            text = str(value)
            if len(text) == 7:
                return datetime.strptime(text, "%Y-%m").replace(tzinfo=timezone.utc)
            if len(text) == 4:
                return datetime.strptime(text, "%Y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _eligible_matches(matches: list[dict[str, Any]], cutoff: datetime) -> list[dict[str, Any]]:
    eligible = []
    for match in matches:
        played = _time(match.get("played_at"))
        available = _time(match.get("available_at"))
        if (match.get("status") == "completed" and match.get("winner_id")
                and played and available and played <= cutoff and available <= cutoff):
            eligible.append(match)
    return sorted(eligible, key=lambda item: (_time(item.get("played_at")), str(item.get("id", ""))))


def enrich_teams_with_context(
    teams: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    map_results: list[dict[str, Any]],
    map_vetoes: list[dict[str, Any]],
    roster_memberships: list[dict[str, Any]],
    player_stats: list[dict[str, Any]],
    cutoff: str,
) -> list[dict[str, Any]]:
    """Attach only cutoff-eligible team signals consumed by ``predict_match``."""
    cutoff_time = _time(cutoff)
    if cutoff_time is None:
        raise ValueError("cutoff must be an ISO-8601 date or datetime")
    eligible_matches = _eligible_matches(matches, cutoff_time)
    eligible_by_id = {str(row["id"]): row for row in eligible_matches}
    team_ids = {str(team["id"]) for team in teams}
    signals: dict[str, dict[str, Any]] = {
        team_id: {
            "maps": {},
            "vetoes": {},
            "roster": {"active_members": 0, "continuity": None, "known_tenure_members": 0},
            "player_metrics": {},
            "head_to_head": {},
        }
        for team_id in team_ids
    }

    # Map-level records must point to a completed match that was available by the cutoff.
    for row in map_results:
        match = eligible_by_id.get(str(row.get("match_id")))
        winner_id = str(row.get("winner_id") or "")
        map_name = str(row.get("map_name") or "").strip()
        if not match or winner_id not in (match["team_a_id"], match["team_b_id"]) or not map_name:
            continue
        for team_id in (str(match["team_a_id"]), str(match["team_b_id"])):
            if team_id not in signals:
                continue
            item = signals[team_id]["maps"].setdefault(map_name, {"wins": 0, "matches": 0})
            item["matches"] += 1
            item["wins"] += int(winner_id == team_id)

    # Veto actions are useful as a map-selection tendency, but never treated as map wins.
    for row in map_vetoes:
        match = eligible_by_id.get(str(row.get("match_id")))
        team_id = str(row.get("team_id") or "")
        map_name = str(row.get("map_name") or "").strip()
        action = str(row.get("action") or "").lower()
        veto_available = _time(row.get("available_at"))
        if (not match or not veto_available or veto_available > cutoff_time or team_id not in signals
                or team_id not in (match["team_a_id"], match["team_b_id"]) or not map_name):
            continue
        if action not in ("pick", "ban"):
            continue
        item = signals[team_id]["vetoes"].setdefault(map_name, {"pick": 0, "ban": 0})
        item[action] += 1

    # Current roster snapshot and time on roster; unknown start dates are not called continuity.
    active_rosters: dict[str, set[str]] = {team_id: set() for team_id in team_ids}
    active_known_rosters: dict[str, set[str]] = {team_id: set() for team_id in team_ids}
    active_memberships: dict[str, list[dict[str, Any]]] = {team_id: [] for team_id in team_ids}
    for row in roster_memberships:
        team_id = str(row.get("team_id") or "")
        player_id = str(row.get("player_id") or "")
        membership_available = _time(row.get("available_at"))
        started = _time(row.get("started_at"))
        ended = _time(row.get("ended_at"))
        if (team_id not in signals or not player_id or not membership_available or membership_available > cutoff_time
                or (started and started > cutoff_time) or (ended and ended <= cutoff_time)):
            continue
        active_rosters[team_id].add(player_id)
        if started:
            active_known_rosters[team_id].add(player_id)
        active_memberships[team_id].append(row)
    for team_id, members in active_memberships.items():
        known = [row for row in members if _time(row.get("started_at"))]
        tenured = [row for row in known if (cutoff_time - _time(row["started_at"])).days >= 90]
        continuity = len(tenured) / len(members) if members and len(known) >= 3 else None
        signals[team_id]["roster"] = {
            "active_members": len(members),
            "known_tenure_members": len(known),
            "continuity": continuity,
        }

    # A player statistic is current only if its player is on exactly one active roster,
    # the measurement window is not in the future, and its linked match was available.
    player_teams: dict[str, set[str]] = {}
    for team_id, player_ids in active_rosters.items():
        for player_id in player_ids:
            player_teams.setdefault(player_id, set()).add(team_id)
    latest_player_metrics: dict[tuple[str, str], dict[str, Any]] = {}
    for row in player_stats:
        player_id = str(row.get("player_id") or "")
        available = _time(row.get("available_at"))
        measured = _time(row.get("measurement_period"))
        if not player_id or not available or available > cutoff_time or not measured or measured > cutoff_time:
            continue
        if len(player_teams.get(player_id, set())) != 1:
            continue
        match_id = row.get("match_id")
        if match_id and str(match_id) not in eligible_by_id:
            continue
        if match_id is None and not row.get("measurement_period"):
            continue
        try:
            stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
            if isinstance(row.get("stats_json"), str):
                import json
                stats = json.loads(row["stats_json"])
            metric_values = {
                "rating": _finite_number(stats.get("rating")),
                "adr": _finite_number(stats.get("adr", stats.get("damage_per_round"))),
            }
        except (TypeError, ValueError):
            continue
        version = str(row.get("metric_version") or "").strip()
        if not version:
            continue
        for metric, value in metric_values.items():
            if value is None:
                continue
            key = (player_id, metric)
            current = latest_player_metrics.get(key)
            current_order = (_time(current.get("measurement_period")), _time(current.get("available_at"))) if current else None
            row_order = (measured, available)
            if current_order is None or row_order > current_order:
                latest_player_metrics[key] = {**row, "metric_value": value, "metric_version": version}
    for (player_id, metric), row in latest_player_metrics.items():
        team_id = next(iter(player_teams[player_id]))
        by_version = signals[team_id]["player_metrics"].setdefault(row["metric_version"], {}).setdefault(metric, [])
        by_version.append(row["metric_value"])
    for team_id in team_ids:
        signals[team_id]["player_metrics"] = {
            version: {
                metric: {"players": len(values), "mean": sum(values) / len(values)}
                for metric, values in by_metric.items() if values
            }
            for version, by_metric in signals[team_id]["player_metrics"].items()
        }

    # Recency-decayed H2H, with relevance reduced when both historical and current rosters are known.
    def roster_at(team_id: str, when: datetime) -> set[str] | None:
        rows = [
            row for row in roster_memberships
            if str(row.get("team_id")) == team_id
            and (not _time(row.get("started_at")) or _time(row.get("started_at")) <= when)
            and (not _time(row.get("ended_at")) or _time(row.get("ended_at")) > when)
        ]
        result = {
            str(row["player_id"]) for row in rows
            if row.get("player_id") and _time(row.get("started_at"))
        }
        return result if len(result) >= 3 else None

    for match in eligible_matches:
        a_id, b_id = str(match["team_a_id"]), str(match["team_b_id"])
        if a_id not in signals or b_id not in signals:
            continue
        played = _time(match.get("played_at"))
        if not played:
            continue
        age_days = max(0.0, (cutoff_time - played).total_seconds() / 86400)
        recency = 0.5 ** (age_days / 180.0)
        current_a, current_b = active_known_rosters[a_id], active_known_rosters[b_id]
        past_a, past_b = roster_at(a_id, played), roster_at(b_id, played)
        if current_a and current_b and past_a and past_b:
            def overlap(old: set[str], new: set[str]) -> float:
                return len(old & new) / max(len(old | new), 1)
            roster_relevance = (overlap(past_a, current_a) + overlap(past_b, current_b)) / 2
        else:
            roster_relevance = 0.5
        weight = recency * roster_relevance
        winner = str(match["winner_id"])
        for own, opponent in ((a_id, b_id), (b_id, a_id)):
            item = signals[own]["head_to_head"].setdefault(opponent, {"wins": 0, "losses": 0, "weighted_wins": 0.0, "weighted_losses": 0.0})
            won = winner == own
            item["wins" if won else "losses"] += 1
            item["weighted_wins" if won else "weighted_losses"] += weight

    enriched: list[dict[str, Any]] = []
    for team in teams:
        team_id = str(team["id"])
        team_copy = dict(team)
        team_copy["_prediction_signals"] = signals[team_id]
        enriched.append(team_copy)
    return enriched


def compare_team_signals(team_a: dict[str, Any], team_b: dict[str, Any]) -> dict[str, Any]:
    """Return conservative pairwise context adjustments in log-odds units."""
    a = team_a.get("_prediction_signals") or {}
    b = team_b.get("_prediction_signals") or {}
    effects: list[dict[str, Any]] = []

    # Map results are shrunk toward 50% with a symmetric Beta(4,4) prior.
    maps_a, maps_b = a.get("maps", {}), b.get("maps", {})
    map_names = sorted(set(maps_a) | set(maps_b))
    map_scores: list[tuple[str, float, float]] = []
    map_delta = 0.0
    veto_delta = 0.0
    for name in map_names:
        record_a, record_b = maps_a.get(name, {"wins": 0, "matches": 0}), maps_b.get(name, {"wins": 0, "matches": 0})
        if int(record_a.get("matches", 0)) + int(record_b.get("matches", 0)) == 0:
            continue
        rate_a = (float(record_a.get("wins", 0)) + 4.0) / (float(record_a.get("matches", 0)) + 8.0)
        rate_b = (float(record_b.get("wins", 0)) + 4.0) / (float(record_b.get("matches", 0)) + 8.0)
        map_scores.append((name, rate_a, rate_b))
    veto_a, veto_b = a.get("vetoes", {}), b.get("vetoes", {})
    has_veto = bool(veto_a or veto_b)
    if map_scores:
        weights = []
        for name, _, _ in map_scores:
            va, vb = veto_a.get(name, {}), veto_b.get(name, {})
            pref_a = (float(va.get("pick", 0)) - float(va.get("ban", 0))) / (float(va.get("pick", 0)) + float(va.get("ban", 0)) + 4.0)
            pref_b = (float(vb.get("pick", 0)) - float(vb.get("ban", 0))) / (float(vb.get("pick", 0)) + float(vb.get("ban", 0)) + 4.0)
            weights.append(max(0.5, min(2.0, exp(0.7 * (pref_a + pref_b)))))
        total_weight = sum(weights)
        map_difference = sum(rate_a - rate_b for _, rate_a, rate_b in map_scores) / len(map_scores)
        veto_weighted_difference = sum(weight * (rate_a - rate_b) for weight, (_, rate_a, rate_b) in zip(weights, map_scores)) / total_weight
        map_delta = max(-0.24, min(0.24, map_difference * 0.70))
        veto_delta = max(-0.24, min(0.24, veto_weighted_difference * 0.70)) - map_delta
        map_samples_a = sum(int(row.get("matches", 0)) for row in maps_a.values())
        map_samples_b = sum(int(row.get("matches", 0)) for row in maps_b.values())
        effects.append({
            "key": "map_pool", "label": "Map pool", "used": True,
            "log_odds_adjustment": map_delta,
            "summary": f"{len(map_scores)} map(s) with imported results; {map_samples_a} / {map_samples_b} map samples.",
            "sample_size": map_samples_a + map_samples_b,
            "team_a_value": round(map_difference, 4),
        })
    else:
        map_samples_a = map_samples_b = 0
        effects.append({"key": "map_pool", "label": "Map pool", "used": False, "log_odds_adjustment": 0.0, "summary": "No cutoff-eligible map results for these teams.", "sample_size": 0})
        map_delta = 0.0

    map_weights = []
    for name, _, _ in map_scores:
        va, vb = veto_a.get(name, {}), veto_b.get(name, {})
        pref_a = (float(va.get("pick", 0)) - float(va.get("ban", 0))) / (float(va.get("pick", 0)) + float(va.get("ban", 0)) + 4.0)
        pref_b = (float(vb.get("pick", 0)) - float(vb.get("ban", 0))) / (float(vb.get("pick", 0)) + float(vb.get("ban", 0)) + 4.0)
        map_weights.append(max(0.5, min(2.0, exp(0.7 * (pref_a + pref_b)))))
    veto_changes_pool = len(map_weights) > 1 and max(map_weights) - min(map_weights) > 1e-9
    if map_scores and has_veto and veto_changes_pool:
        # Veto history changes the weight of each map in the unknown-veto pool; it is not a result.
        effects.append({"key": "map_veto", "label": "Map veto history", "used": True, "log_odds_adjustment": veto_delta,
                        "summary": f"Historical picks and bans shift the map adjustment by {veto_delta:+.3f} log-odds; the current veto is unknown.",
                        "sample_size": sum(sum(int(v) for v in item.values()) for profile in (veto_a, veto_b) for item in profile.values())})
    else:
        effects.append({"key": "map_veto", "label": "Map veto history", "used": False, "log_odds_adjustment": veto_delta,
                        "summary": ("Veto records are present, but the available map pool is too small to change its weights." if has_veto and map_scores else "No usable cutoff-eligible veto history; this matchup's veto is not known."), "sample_size": 0})

    # Compare rating and ADR only within one shared metric version and at least 3 players per team.
    player_a, player_b = a.get("player_metrics", {}), b.get("player_metrics", {})
    metric_used = False
    for metric, key, label, coefficient, cap in (
        ("rating", "player_rating", "Active player rating", 0.60, 0.20),
        ("adr", "player_adr", "Active player ADR", 0.002, 0.10),
    ):
        common_versions = [
            version for version in set(player_a) & set(player_b)
            if int((player_a[version].get(metric) or {}).get("players", 0)) >= 3
            and int((player_b[version].get(metric) or {}).get("players", 0)) >= 3
        ]
        if common_versions:
            version = max(common_versions, key=lambda value: min(
                player_a[value][metric]["players"], player_b[value][metric]["players"]
            ))
            difference = float(player_a[version][metric]["mean"]) - float(player_b[version][metric]["mean"])
            delta = max(-cap, min(cap, difference * coefficient))
            metric_used = True
            metric_name = "rating" if metric == "rating" else "ADR"
            effects.append({"key": key, "label": label, "used": True,
                            "log_odds_adjustment": delta,
                            "summary": f"Mean {metric_name} difference {difference:+.3f} ({version}); {player_a[version][metric]['players']} / {player_b[version][metric]['players']} active players.",
                            "sample_size": player_a[version][metric]["players"] + player_b[version][metric]["players"],
                            "team_a_value": round(difference, 4), "metric_version": version})
        else:
            any_metrics = bool(player_a or player_b)
            reason = "Fewer than three comparable active players per team." if any_metrics else "No cutoff-eligible active-roster player metrics."
            if player_a and player_b and not (set(player_a) & set(player_b)):
                reason = "Metric versions differ between the teams; player values are not compared."
            effects.append({"key": key, "label": label, "used": False,
                            "log_odds_adjustment": 0.0, "summary": reason, "sample_size": 0})

    roster_a, roster_b = a.get("roster", {}), b.get("roster", {})
    continuity_a, continuity_b = roster_a.get("continuity"), roster_b.get("continuity")
    if continuity_a is not None and continuity_b is not None:
        continuity_difference = float(continuity_a) - float(continuity_b)
        roster_delta = max(-0.10, min(0.10, continuity_difference * 0.10))
        effects.append({"key": "roster_continuity", "label": "Roster continuity", "used": True,
                        "log_odds_adjustment": roster_delta,
                        "summary": f"Share with at least 90 days together: {continuity_a:.0%} / {continuity_b:.0%}.",
                        "sample_size": int(roster_a.get("active_members", 0)) + int(roster_b.get("active_members", 0)),
                        "team_a_value": round(continuity_difference, 4)})
    else:
        roster_delta = 0.0
        effects.append({"key": "roster_continuity", "label": "Roster continuity", "used": False,
                        "log_odds_adjustment": 0.0, "summary": "At least three dated active roster memberships per team are required.",
                        "sample_size": int(roster_a.get("active_members", 0)) + int(roster_b.get("active_members", 0))})

    h2h_a = (a.get("head_to_head") or {}).get(str(team_b["id"]), {})
    h2h_b = (b.get("head_to_head") or {}).get(str(team_a["id"]), {})
    h2h_total = int(h2h_a.get("wins", 0)) + int(h2h_a.get("losses", 0))
    weighted_edge = float(h2h_a.get("weighted_wins", 0.0)) - float(h2h_a.get("weighted_losses", 0.0))
    h2h_signal = weighted_edge / (6.0 + float(h2h_a.get("weighted_wins", 0.0)) + float(h2h_a.get("weighted_losses", 0.0)))
    h2h_delta = max(-0.15, min(0.15, h2h_signal * 0.15)) if h2h_total else 0.0
    effects.append({"key": "head_to_head", "label": "Head-to-head", "used": h2h_total > 0,
                    "log_odds_adjustment": h2h_delta,
                    "summary": (f"{h2h_a.get('wins', 0)}–{h2h_a.get('losses', 0)} overall; recent results are shrunk toward even and discounted when rosters differ." if h2h_total else "No eligible prior meetings."),
                    "sample_size": h2h_total, "team_a_value": round(h2h_signal, 4) if h2h_total else None})

    total_delta = max(-0.45, min(0.45, sum(float(item["log_odds_adjustment"]) for item in effects)))
    return {
        "context_log_odds": total_delta,
        "effects": effects,
        "map_pool_used": any(item["key"] == "map_pool" and item["used"] for item in effects),
        "map_veto_used": any(item["key"] == "map_veto" and item["used"] for item in effects),
        "player_metrics_used": metric_used,
        "roster_continuity_used": any(item["key"] == "roster_continuity" and item["used"] for item in effects),
        "head_to_head_used": h2h_total > 0,
        "map_samples_a": map_samples_a,
        "map_samples_b": map_samples_b,
        "head_to_head_count": h2h_total,
    }
