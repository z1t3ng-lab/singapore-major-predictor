import React, { useState } from "react";
import { clearMatchResult, resultConflictsFromError, saveMatchResult } from "@/lib/api";
import type { MatchResultResponse } from "@/lib/api";
import type { MatchResultConflict, PredictedMatch, Team } from "@/lib/types";

export type ResultAction = "set" | "edit" | "clear";

export function ResultDialog(props: {
  match: PredictedMatch;
  teamMap: Map<string, Team>;
  action: ResultAction;
  onClose: () => void;
  onBusyChange: (busy: boolean) => void;
  onSaved: (response: MatchResultResponse) => void;
}) {
  const { match, teamMap, action, onClose, onSaved, onBusyChange } = props;
  const a = teamMap.get(match.team_a_id);
  const b = teamMap.get(match.team_b_id);
  const [step, setStep] = useState<"form" | "confirm" | "conflicts">(action === "clear" ? "confirm" : "form");
  const [winnerId, setWinnerId] = useState(action === "edit" ? match.confirmed_winner_id ?? match.winner_id : "");
  const existingScore = match.confirmed_score ?? (match.result_status === "confirmed" ? match.score : null) ?? "";
  const [scoreA, setScoreA] = useState(action === "edit" ? existingScore.split(":")[0] ?? "" : "");
  const [scoreB, setScoreB] = useState(action === "edit" ? existingScore.split(":")[1] ?? "" : "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [conflicts, setConflicts] = useState<MatchResultConflict[]>([]);

  const winnerName = teamMap.get(winnerId)?.name ?? "the selected team";
  const score = scoreA !== "" && scoreB !== "" ? `${scoreA}:${scoreB}` : null;
  const invalidationAllowed = conflicts.length > 0 && conflicts.every((item) => item.can_invalidate);

  function validateForm() {
    if (!winnerId) return "Select the actual winner.";
    if (winnerId !== match.team_a_id && winnerId !== match.team_b_id) return "Winner must be one of the teams in this match.";
    if ((scoreA === "") !== (scoreB === "")) return "Enter both map counts or leave the score empty.";
    if (scoreA !== "" && scoreB !== "") {
      const first = Number(scoreA);
      const second = Number(scoreB);
      const needed = Math.floor(match.best_of / 2) + 1;
      if (!Number.isInteger(first) || !Number.isInteger(second) || first < 0 || second < 0 || Math.max(first, second) !== needed || Math.min(first, second) >= needed || first === second) {
        return `A BO${match.best_of} series must end when one team reaches ${needed} map wins.`;
      }
      const scoreWinner = first > second ? match.team_a_id : match.team_b_id;
      if (scoreWinner !== winnerId) return "The score winner must match the selected actual winner.";
    }
    return "";
  }

  async function submit(invalidateIds: string[] = []) {
    setBusy(true);
    onBusyChange(true);
    setError("");
    try {
      const payload = {
        winner_id: action === "clear" ? null : winnerId,
        score: action === "clear" ? null : score,
        expected_result_version: match.result_version ?? 0,
        invalidate_conflict_ids: invalidateIds,
      };
      const response = action === "clear"
        ? await clearMatchResult(match.id, payload)
        : await saveMatchResult(match.id, payload);
      onSaved(response);
    } catch (cause) {
      const foundConflicts = resultConflictsFromError(cause);
      if (foundConflicts) {
        setConflicts(foundConflicts);
        setStep("conflicts");
      } else {
        setError(cause instanceof Error ? cause.message : "The result could not be saved.");
      }
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }

  function continueToConfirmation() {
    const validationError = validateForm();
    if (validationError) {
      setError(validationError);
      return;
    }
    setError("");
    setStep("confirm");
  }

  const title = action === "set" ? "Set match result" : action === "edit" ? "Edit confirmed result" : "Clear confirmed result";
  return <div className="result-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="result-dialog" role="dialog" aria-modal="true" aria-labelledby="result-dialog-title">
      <div className="result-dialog-top"><span className="section-overline">{match.stage_slug === "playoffs" ? "PLAYOFFS" : match.stage_slug.toUpperCase()} · ROUND {match.round_no} · BO{match.best_of}</span><button className="detail-close" onClick={onClose} disabled={busy} aria-label="Close result editor">×</button></div>
      <h2 id="result-dialog-title">{title}</h2>
      <p className="result-dialog-match">{a?.name ?? match.team_a_id}<i>vs</i>{b?.name ?? match.team_b_id}</p>

      {step === "form" && <>
        <label className="result-field"><span>ACTUAL WINNER</span><select value={winnerId} onChange={(event) => setWinnerId(event.currentTarget.value)}>
          <option value="">Choose the winner</option>
          <option value={match.team_a_id}>{a?.name ?? match.team_a_id}</option>
          <option value={match.team_b_id}>{b?.name ?? match.team_b_id}</option>
        </select></label>
        <div className="result-field"><span>FINAL SERIES SCORE <small>OPTIONAL</small></span>
          <div className="series-score-fields"><label><span>{a?.name ?? "Team A"}</span><input aria-label={`${a?.name ?? "Team A"} map wins`} inputMode="numeric" type="number" min={0} max={Math.floor(match.best_of / 2) + 1} value={scoreA} onChange={(event) => setScoreA(event.currentTarget.value)} /></label><i>:</i><label><span>{b?.name ?? "Team B"}</span><input aria-label={`${b?.name ?? "Team B"} map wins`} inputMode="numeric" type="number" min={0} max={Math.floor(match.best_of / 2) + 1} value={scoreB} onChange={(event) => setScoreB(event.currentTarget.value)} /></label></div>
        </div>
        <p className="result-dialog-note">The saved score is the series map count in Team A:Team B order. No map scores or player statistics are inferred.</p>
        {error && <div className="result-dialog-error" role="alert">{error}</div>}
        <div className="result-dialog-actions"><button className="result-secondary" onClick={onClose}>Cancel</button><button className="result-primary" onClick={continueToConfirmation}>Review result</button></div>
      </>}

      {step === "confirm" && <>
        {action === "clear" ? <div className="result-confirm-copy"><b>Clear this confirmed outcome?</b><span>Its match will return to unresolved/predicted state. Downstream predictions will be recalculated. Any later confirmed result affected by this change will be shown for explicit review.</span></div> : <div className="result-confirm-copy"><b>Confirm {winnerName} as the actual winner?</b><span>{score ? `Series score: ${score.replace(":", "–")}. ` : "No series score provided. "}The original model probabilities will remain available; dependent predictions will be recalculated with this outcome locked in.</span></div>}
        {error && <div className="result-dialog-error" role="alert">{error}</div>}
        <div className="result-dialog-actions"><button className="result-secondary" disabled={busy} onClick={() => action === "clear" ? onClose() : setStep("form")}>Back</button><button className="result-primary" disabled={busy} onClick={() => void submit()}>{busy ? "Recalculating…" : action === "clear" ? "Confirm clear" : "Confirm result"}</button></div>
      </>}

      {step === "conflicts" && <>
        <div className="result-conflict-intro"><b>This change affects later confirmed results.</b><span>Review these matches. The tournament state is unchanged until you explicitly confirm the invalidation.</span></div>
        <ul className="result-conflict-list">{conflicts.map((item) => <li key={item.id}><b>{item.stage_slug === "playoffs" ? "Playoffs" : item.stage_slug.replace("stage", "Stage ")} · Round {item.round_no}</b><span>{item.team_a_name} vs {item.team_b_name}{item.winner_name ? ` · ${item.winner_name} won` : ""}</span><code>{item.id}</code></li>)}</ul>
        {!invalidationAllowed && <p className="result-dialog-error">An affected official fixture cannot be removed automatically. Resolve that fixture conflict before changing this result.</p>}
        {error && <div className="result-dialog-error" role="alert">{error}</div>}
        <p className="result-dialog-note">Confirming this will clear the listed confirmed outcomes while retaining their fixtures and audit records, then rebuild the affected tournament path.</p>
        <div className="result-dialog-actions"><button className="result-secondary" disabled={busy} onClick={() => { setStep(action === "clear" ? "confirm" : "form"); setError(""); }}>Back</button><button className="result-danger" disabled={busy || !invalidationAllowed} onClick={() => void submit(conflicts.map((item) => item.id))}>{busy ? "Recalculating…" : "Confirm and clear affected results"}</button></div>
      </>}
    </section>
  </div>;
}
