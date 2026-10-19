"""Cutoff-aware Elo snapshots, optional chronological model training and metrics."""

from __future__ import annotations

from datetime import datetime, timezone
from math import exp
from typing import Any

import numpy as np
import pandas as pd

from .engine import map_win_probability, predict_match, series_probability_with_map_context


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def available_matches(matches: list[dict[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    cutoff_time = _parse_time(cutoff)
    if cutoff_time is None:
        raise ValueError("cutoff must be an ISO-8601 date or datetime")
    frame = pd.DataFrame(matches)
    if frame.empty or not {"winner_id", "status", "played_at", "available_at"}.issubset(frame.columns):
        return []
    frame["_played"] = pd.to_datetime(frame["played_at"], utc=True, errors="coerce")
    frame["_available"] = pd.to_datetime(frame["available_at"], utc=True, errors="coerce")
    cutoff_timestamp = pd.Timestamp(cutoff_time)
    frame = frame[
        frame["winner_id"].notna()
        & (frame["status"] == "completed")
        & frame["_played"].notna()
        & frame["_available"].notna()
        & (frame["_played"] <= cutoff_timestamp)
        & (frame["_available"] <= cutoff_timestamp)
    ].sort_values(["_played", "id"], kind="stable")
    return frame.drop(columns=["_played", "_available"]).to_dict("records")


def _chronological_rows(
    teams: dict[str, dict[str, Any]], matches: list[dict[str, Any]], cutoff: str
) -> tuple[list[list[float]], list[int], dict[str, float], dict[str, int]]:
    eligible = available_matches(matches, cutoff)
    use_external_seed_rating = len(eligible) == 0
    cutoff_time = _parse_time(cutoff)
    ratings = {
        team_id: (
            float(team.get("elo_rating") or 1500.0)
            if use_external_seed_rating and (
                _parse_time(team.get("updated_at")) is None
                or (cutoff_time is not None and _parse_time(team.get("updated_at")) <= cutoff_time)
            )
            else 1500.0
        )
        for team_id, team in teams.items()
    }
    counts = {team_id: 0 for team_id in teams}
    features: list[list[float]] = []
    outcomes: list[int] = []
    for match in eligible:
        a, b = match["team_a_id"], match["team_b_id"]
        if a not in ratings or b not in ratings or match["winner_id"] not in (a, b):
            continue
        p = map_win_probability(ratings[a], ratings[b])
        best_of = int(match.get("best_of") or 1)
        features.append([ratings[a] - ratings[b], 1.0 if best_of >= 3 else 0.0])
        outcome = int(match["winner_id"] == a)
        outcomes.append(outcome)
        k_factor = 24.0
        ratings[a] += k_factor * (outcome - p)
        ratings[b] += k_factor * ((1 - outcome) - (1 - p))
        counts[a] += 1
        counts[b] += 1
    return features, outcomes, ratings, counts


def _metrics(actual: list[int], probabilities: list[float]) -> dict[str, float]:
    if not actual:
        return {}
    y = np.asarray(actual, dtype=float)
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-15, 1 - 1e-15)
    losses = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    log_loss = float(np.mean(losses))
    brier = float(np.mean((p - y) ** 2))
    accuracy = float(np.mean((p >= 0.5) == (y == 1)))
    calibration_error = 0.0
    for lower in (0.0, 0.2, 0.4, 0.6, 0.8):
        mask = (p >= lower) & (p < lower + 0.2)
        if np.any(mask):
            calibration_error += float(np.mean(mask)) * abs(float(np.mean(y[mask])) - float(np.mean(p[mask])))
    return {
        "log_loss": log_loss,
        "brier_score": brier,
        "accuracy": accuracy,
        "calibration_error_5_bins": calibration_error,
    }


def build_model_snapshot(
    team_list: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    cutoff: str,
    model_state: dict[str, Any] | None = None,
    allow_training: bool = True,
) -> dict[str, Any]:
    teams = {team["id"]: team for team in team_list}
    features, outcomes, ratings, counts = _chronological_rows(teams, matches, cutoff)
    coefficients: list[float] | None = None
    intercept: float | None = None
    metrics: dict[str, float] = {}
    status = "baseline_only"
    model_name = "Interpretable Elo + context priors"
    trained_cutoff = cutoff
    if model_state and model_state.get("status") == "trained_chronological_split":
        try:
            candidate = [float(value) for value in model_state["coefficients"]]
            candidate_intercept = float(model_state["intercept"])
            if len(candidate) != 2:
                raise ValueError("Expected two model coefficients.")
            coefficients = candidate
            intercept = candidate_intercept
            metrics = dict(model_state.get("metrics") or {})
            status = "trained_chronological_split"
            model_name = str(model_state.get("name") or "Logistic regression (Elo + format) with context priors")
            trained_cutoff = str(model_state.get("trained_cutoff") or cutoff)
        except (KeyError, TypeError, ValueError):
            coefficients = None
            intercept = None
    elif allow_training and len(outcomes) >= 200:
        try:
            from sklearn.linear_model import LogisticRegression

            split = int(len(outcomes) * 0.8)
            if split >= 100 and len(outcomes) - split >= 40:
                model = LogisticRegression(max_iter=1000, random_state=0, solver="liblinear")
                model.fit(features[:split], outcomes[:split])
                validation_probabilities = [float(x[1]) for x in model.predict_proba(features[split:])]
                metrics = _metrics(outcomes[split:], validation_probabilities)
                status = "trained_chronological_split"
                model_name = "Logistic regression (Elo + format) with context priors"
                coefficients = [float(value) for value in model.coef_[0]]
                intercept = float(model.intercept_[0])
        except (ImportError, ValueError):
            coefficients = None
            intercept = None

    def predictor(team_a: dict[str, Any], team_b: dict[str, Any], best_of: int) -> dict[str, Any]:
        # Swiss entrant records carry stage-specific seeds; merge in the shared
        # cutoff snapshot so every simulated pairing receives the same features.
        team_a = {**teams.get(team_a["id"], {}), **team_a}
        team_b = {**teams.get(team_b["id"], {}), **team_b}
        prediction = predict_match(team_a, team_b, best_of, ratings, counts)
        if coefficients is not None and intercept is not None:
            linear_predictor = intercept + coefficients[0] * prediction["rating_difference"] + coefficients[1] * (1.0 if best_of >= 3 else 0.0)
            base_p = 1.0 / (1.0 + exp(-max(-700.0, min(700.0, linear_predictor))))
            p = series_probability_with_map_context(base_p, best_of, prediction["context_log_odds"])
            prediction.update({
                "team_a_probability": p,
                "team_b_probability": 1.0 - p,
                "predicted_winner_id": team_a["id"] if p >= 0.5 else team_b["id"],
                "model": model_name,
                "base_series_probability_a": base_p,
                "explanation": (
                    "A chronological logistic regression uses pre-match Elo difference and series format; "
                    "the cutoff-safe context inputs shown below adjust its equivalent map probability using "
                    "fixed bounded heuristic priors. Holdout metrics evaluate the Elo-and-format regression "
                    "before those context adjustments."
                ),
            })
        return prediction

    if len(outcomes) < 200:
        status_message = f"Only {len(outcomes)} eligible completed matches are available; 200 are required before training."
    elif not allow_training:
        status_message = "Model fitting was skipped during this result update; current Elo inputs and the existing baseline were used."
    elif coefficients is None:
        status_message = "The optional scikit-learn dependency or a valid chronological split is unavailable; using Elo."
    else:
        status_message = "A chronological held-out evaluation was completed; the trained model is active."
    return {
        "predictor": predictor,
        "ratings": ratings,
        "sample_counts": counts,
        "name": model_name,
        "status": status,
        "status_message": status_message,
        "eligible_matches": len(outcomes),
        "metrics": metrics,
        "metrics_scope": "Elo + series format model before context priors" if metrics else None,
        "trained_cutoff": trained_cutoff if status == "trained_chronological_split" else None,
        "state": {
            "status": status,
            "name": model_name,
            "coefficients": coefficients,
            "intercept": intercept,
            "metrics": metrics,
            "trained_cutoff": trained_cutoff if status == "trained_chronological_split" else None,
        },
    }
