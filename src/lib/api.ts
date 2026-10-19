import type { MatchResultConflict, Overview, Team, TournamentResult } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : detail?.message ?? `API request failed (${response.status})`;
    throw new ApiError(message, response.status, detail);
  }
  return response.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly detail?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

export const getOverview = () => request<Overview>("/api/overview");
export const getTeams = async () => (await request<{ teams: Team[] }>("/api/teams")).teams;
export const loadDemoDataset = () => request<{ ok: boolean; data_source: string; teams_loaded: number }>("/api/demo", { method: "POST" });

export function runSimulation(input: {
  simulations: number;
  seed: number;
  cutoff: string;
  scenario_overrides: Record<string, string>;
}) {
  return request<TournamentResult>("/api/simulate", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export type MatchResultInput = {
  winner_id?: string | null;
  score?: string | null;
  expected_result_version: number;
  invalidate_conflict_ids?: string[];
};

export type MatchResultResponse = {
  message: string;
  result: TournamentResult;
  invalidated_result_ids: string[];
};

export async function saveMatchResult(matchId: string, input: MatchResultInput) {
  return request<MatchResultResponse>(`/api/matches/${encodeURIComponent(matchId)}/result`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export async function clearMatchResult(matchId: string, input: MatchResultInput) {
  return request<MatchResultResponse>(`/api/matches/${encodeURIComponent(matchId)}/result`, {
    method: "DELETE",
    body: JSON.stringify(input),
  });
}

export function resultConflictsFromError(error: unknown): MatchResultConflict[] | null {
  if (!(error instanceof ApiError) || error.status !== 409 || !error.detail || typeof error.detail !== "object") return null;
  const conflicts = (error.detail as { conflicts?: unknown }).conflicts;
  return Array.isArray(conflicts) ? conflicts as MatchResultConflict[] : null;
}

export function getMatchDetail(input: {
  team_a_id: string;
  team_b_id: string;
  best_of: number;
  cutoff: string;
}) {
  return request<Record<string, unknown>>("/api/predict", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function importFile(file: File, kind: "json" | "teams" | "matches", replaceDemo: boolean) {
  const query = new URLSearchParams();
  if (replaceDemo) query.set("replace_demo", "true");
  if (kind !== "json") query.set("kind", kind);
  const endpoint = kind === "json" ? "/api/import/json" : "/api/import/csv";
  const response = await fetch(`${API_URL}${endpoint}?${query}`, {
    method: "POST",
    headers: { "Content-Type": kind === "json" ? "application/json" : "text/csv" },
    body: await file.text(),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail ?? `Import failed (${response.status})`);
  return result;
}
