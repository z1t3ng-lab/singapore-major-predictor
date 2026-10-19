import type { PredictedMatch, TournamentResult } from "./types";

export function setScenarioWinner(
  current: Record<string, string>,
  matchId: string,
  winnerId: string | null,
): Record<string, string> {
  const next = { ...current };
  if (!winnerId) delete next[matchId];
  else next[matchId] = winnerId;
  return next;
}

export function scenarioChanged(
  draft: Record<string, string>,
  applied: Record<string, string>,
): boolean {
  const keys = new Set([...Object.keys(draft), ...Object.keys(applied)]);
  return [...keys].some((key) => draft[key] !== applied[key]);
}

export function exportRows(result: TournamentResult): Record<string, string | number>[] {
  const rows: Record<string, string | number>[] = [];
  for (const stage of Object.values(result.projected_path.stages)) {
    for (const round of stage.rounds) {
      for (const match of round.matches) rows.push(matchExportRow(match));
    }
  }
  for (const round of result.projected_path.playoffs.rounds) {
    for (const match of round.matches) rows.push(matchExportRow(match));
  }
  return rows;
}

function matchExportRow(match: PredictedMatch): Record<string, string | number> {
  return {
    match_id: match.id,
    stage: match.stage_slug,
    round: match.round_name ?? `Round ${match.round_no}`,
    team_a_id: match.team_a_id,
    team_b_id: match.team_b_id,
    best_of: match.best_of,
    team_a_win_probability: match.prediction.team_a_probability,
    team_b_win_probability: match.prediction.team_b_probability,
    model_predicted_winner_id: match.prediction.predicted_winner_id,
    original_model_winner_id: match.original_prediction?.predicted_winner_id ?? "",
    original_team_a_probability: match.original_prediction?.team_a_probability ?? "",
    original_team_b_probability: match.original_prediction?.team_b_probability ?? "",
    path_winner_id: match.winner_id,
    confirmed_winner_id: match.result_status === "confirmed" || match.outcome_source === "completed_result" ? match.confirmed_winner_id ?? match.winner_id : "",
    result_status: match.result_status ?? "pending",
    result_source: match.result_source ?? "",
    outcome_source: match.outcome_source,
    confirmed_fixture: match.fixture_confirmed ? "yes" : "no",
    score: match.score ?? "",
  };
}

export function rowsToCsv(rows: Record<string, string | number>[]): string {
  if (rows.length === 0) return "";
  const headers = Object.keys(rows[0]);
  const escape = (value: string | number) => `"${String(value).replaceAll('"', '""')}"`;
  return [headers.map(escape).join(","), ...rows.map((row) => headers.map((key) => escape(row[key] ?? "")).join(","))].join("\n");
}

export function percentage(value: number | undefined): string {
  return `${Math.round((value ?? 0) * 100)}%`;
}
