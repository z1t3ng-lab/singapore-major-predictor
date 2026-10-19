import type { Overview, Team, TournamentResult } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `API request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
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
