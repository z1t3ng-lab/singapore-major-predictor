import React from "react";
import { percentage } from "@/lib/helpers";
import type { PredictedMatch, Team } from "@/lib/types";
import type { ResultAction } from "./ResultDialog";

function TeamMark({ name }: { name: string }) {
  return <span className="team-mark">{name.slice(0, 2).toUpperCase()}</span>;
}

export function MatchCard(props: {
  match: PredictedMatch;
  teamMap: Map<string, Team>;
  draftOverrides: Record<string, string>;
  onScenarioChange: (match: PredictedMatch, winnerId: string) => void;
  onOpen: () => void;
  onManageResult?: (match: PredictedMatch, action: ResultAction) => void;
}) {
  const { match, teamMap, draftOverrides, onScenarioChange, onOpen, onManageResult } = props;
  const a = teamMap.get(match.team_a_id);
  const b = teamMap.get(match.team_b_id);
  const prediction = match.prediction;
  const selectedWinner = draftOverrides[match.id];
  const actual = match.result_status === "confirmed" || match.outcome_source === "completed_result";
  const actualWinnerId = match.confirmed_winner_id ?? (actual ? match.winner_id : null);
  const actualWinner = actualWinnerId ? teamMap.get(actualWinnerId)?.name ?? actualWinnerId : null;
  const modelPrediction = match.original_prediction ?? prediction;
  const displayedProbabilities = actual && match.original_prediction ? match.original_prediction : prediction;
  const modelWinner = teamMap.get(modelPrediction.predicted_winner_id)?.name ?? "TBD";
  const score = match.confirmed_score ?? match.score;
  const scoreParts = actual && score ? score.split(":") : null;
  const predictedOutcome = teamMap.get(selectedWinner ?? match.winner_id)?.name ?? "TBD";
  const predictionMatched = actualWinnerId === modelPrediction.predicted_winner_id;
  return <article className="match-card">
    <div className="match-card-top"><span className={actual ? "match-status status-complete" : match.fixture_confirmed ? "match-status status-confirmed" : "match-status status-projected"}><i />{actual ? "CONFIRMED RESULT" : match.fixture_confirmed ? "OFFICIAL FIXTURE" : match.result_status === "cleared" ? "RESULT CLEARED · PREDICTED" : "PROVISIONAL PAIRING"}</span><span className="match-format">BO{match.best_of}</span></div>
    <button className="match-sides" onClick={onOpen} aria-label={`View details for ${a?.name ?? match.team_a_id} versus ${b?.name ?? match.team_b_id}`}>
      <span className={`match-side${(actualWinnerId ?? match.winner_id) === match.team_a_id ? " match-side-winner" : ""}`}><TeamMark name={a?.name ?? match.team_a_id} /><span><b>{a?.name ?? match.team_a_id}</b><small>SEED #{a?.seed ?? "—"}</small></span><strong>{scoreParts ? scoreParts[0] : percentage(displayedProbabilities.team_a_probability)}</strong></span>
      <span className="match-side-divider"><i>VS</i></span>
      <span className={`match-side${(actualWinnerId ?? match.winner_id) === match.team_b_id ? " match-side-winner" : ""}`}><TeamMark name={b?.name ?? match.team_b_id} /><span><b>{b?.name ?? match.team_b_id}</b><small>SEED #{b?.seed ?? "—"}</small></span><strong>{scoreParts ? scoreParts[1] : percentage(displayedProbabilities.team_b_probability)}</strong></span>
    </button>
    <div className="probability-track"><i style={{ width: `${displayedProbabilities.team_a_probability * 100}%` }} /></div>
    <div className="match-pick-row"><span>{match.original_prediction ? "ORIGINAL MODEL PICK" : actual ? "NO SAVED PRE-MATCH PICK" : "MODEL PICK"} <b>{match.original_prediction || !actual ? modelWinner : "Unavailable"}</b></span>{match.forced_rematch && <em>rematch unavoidable</em>}<button onClick={onOpen}>DETAILS ↗</button></div>
    {actual && actualWinner && <div className="match-actual-row">ACTUAL WINNER <b>{actualWinner}</b>{match.original_prediction && <em className={`prediction-verdict${predictionMatched ? "" : " missed"}`}>PREDICTION {predictionMatched ? "CORRECT" : "MISSED"}</em>}</div>}
    {!actual && match.outcome_source === "scenario_override" && <div className="pairing-note">Current scenario pick: <b>{predictedOutcome}</b></div>}
    {match.pairing_status && <div className={`pairing-note${match.pairing_status === "official_fixture" ? " official-pairing" : ""}`}>{match.pairing_status === "official_fixture" ? "Official fixture · pairing fixed" : "Provisional pairing · depends on earlier results"}</div>}
    {onManageResult && <div className="match-result-actions">
      <button onClick={() => onManageResult(match, actual ? "edit" : "set")}>{actual ? "Edit Result" : "Set Result"}</button>
      {actual && <button className="result-clear-button" onClick={() => onManageResult(match, "clear")}>Clear Result</button>}
    </div>}
    {!actual && <label className="scenario-select"><span>TRY A DIFFERENT WINNER</span><select value={selectedWinner ?? ""} onChange={(event) => onScenarioChange(match, event.currentTarget.value)}>
      <option value="">Use model pick · {teamMap.get(match.winner_id)?.name ?? "winner"}</option>
      <option value={match.team_a_id}>{a?.name ?? match.team_a_id} wins</option>
      <option value={match.team_b_id}>{b?.name ?? match.team_b_id} wins</option>
    </select></label>}
  </article>;
}
