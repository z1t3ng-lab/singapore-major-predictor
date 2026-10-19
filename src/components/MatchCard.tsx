import React from "react";
import { percentage } from "@/lib/helpers";
import type { PredictedMatch, Team } from "@/lib/types";

function TeamMark({ name }: { name: string }) {
  return <span className="team-mark">{name.slice(0, 2).toUpperCase()}</span>;
}

export function MatchCard(props: {
  match: PredictedMatch;
  teamMap: Map<string, Team>;
  draftOverrides: Record<string, string>;
  onScenarioChange: (match: PredictedMatch, winnerId: string) => void;
  onOpen: () => void;
}) {
  const { match, teamMap, draftOverrides, onScenarioChange, onOpen } = props;
  const a = teamMap.get(match.team_a_id);
  const b = teamMap.get(match.team_b_id);
  const prediction = match.prediction;
  const selectedWinner = draftOverrides[match.id];
  const winner = teamMap.get(selectedWinner ?? match.winner_id)?.name ?? "TBD";
  const actual = match.outcome_source === "completed_result";
  return <article className="match-card">
    <div className="match-card-top"><span className={actual ? "match-status status-complete" : match.fixture_confirmed ? "match-status status-confirmed" : "match-status status-projected"}><i />{actual ? "COMPLETED RESULT" : match.fixture_confirmed ? "CONFIRMED FIXTURE" : "PROJECTED PAIRING"}</span><span className="match-format">BO{match.best_of}</span></div>
    <button className="match-sides" onClick={onOpen} aria-label={`View details for ${a?.name ?? match.team_a_id} versus ${b?.name ?? match.team_b_id}`}>
      <span className={`match-side${match.winner_id === match.team_a_id ? " match-side-winner" : ""}`}><TeamMark name={a?.name ?? match.team_a_id} /><span><b>{a?.name ?? match.team_a_id}</b><small>SEED #{a?.seed ?? "—"}</small></span><strong>{actual && match.score ? match.score.split(":")[0] : percentage(prediction.team_a_probability)}</strong></span>
      <span className="match-side-divider"><i>VS</i></span>
      <span className={`match-side${match.winner_id === match.team_b_id ? " match-side-winner" : ""}`}><TeamMark name={b?.name ?? match.team_b_id} /><span><b>{b?.name ?? match.team_b_id}</b><small>SEED #{b?.seed ?? "—"}</small></span><strong>{actual && match.score ? match.score.split(":")[1] : percentage(prediction.team_b_probability)}</strong></span>
    </button>
    <div className="probability-track"><i style={{ width: `${prediction.team_a_probability * 100}%` }} /></div>
    <div className="match-pick-row"><span>MODEL PICK <b>{winner}</b></span>{match.forced_rematch && <em>rematch unavoidable</em>}<button onClick={onOpen}>DETAILS ↗</button></div>
    {!actual && <label className="scenario-select"><span>TRY A DIFFERENT WINNER</span><select value={selectedWinner ?? ""} onChange={(event) => onScenarioChange(match, event.currentTarget.value)}>
      <option value="">Use model pick · {teamMap.get(match.winner_id)?.name ?? "winner"}</option>
      <option value={match.team_a_id}>{a?.name ?? match.team_a_id} wins</option>
      <option value={match.team_b_id}>{b?.name ?? match.team_b_id} wins</option>
    </select></label>}
  </article>;
}
