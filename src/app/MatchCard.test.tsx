import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MatchCard } from "@/components/MatchCard";
import type { PredictedMatch, Team } from "@/lib/types";

const first: Team = { id: "a", name: "Alpha", seed: 1, synthetic: false };
const second: Team = { id: "b", name: "Bravo", seed: 2, synthetic: false };
const match: PredictedMatch = {
  id: "stage1:r1:a::b",
  stage_slug: "stage1",
  round_no: 1,
  team_a_id: "a",
  team_b_id: "b",
  winner_id: "a",
  loser_id: "b",
  best_of: 1,
  status: "predicted",
  fixture_confirmed: false,
  outcome_source: "model_projection",
  prediction: {
    team_a_id: "a", team_b_id: "b", team_a_probability: 0.64, team_b_probability: 0.36,
    predicted_winner_id: "a", best_of: 1, rating_a: 1600, rating_b: 1500,
    rating_difference: 100, map_win_probability_a: 0.64, sample_matches_a: 0,
    sample_matches_b: 0, reliability: "low", explanation: "Elo baseline.", warnings: [],
    model: "Interpretable Elo baseline", map_pool_used: false, player_metrics_used: false,
  },
};

afterEach(cleanup);

describe("MatchCard scenario control", () => {
  it("lets a user choose another projected winner", () => {
    const onScenarioChange = vi.fn();
    render(<MatchCard match={match} teamMap={new Map([[first.id, first], [second.id, second]])} draftOverrides={{}} onScenarioChange={onScenarioChange} onOpen={vi.fn()} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "b" } });
    expect(onScenarioChange).toHaveBeenCalledOnce();
    expect(onScenarioChange.mock.calls[0][0].id).toBe(match.id);
    expect(onScenarioChange.mock.calls[0][1]).toBe("b");
  });

  it("shows the conditional match win probabilities", () => {
    render(<MatchCard match={match} teamMap={new Map([[first.id, first], [second.id, second]])} draftOverrides={{}} onScenarioChange={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText("64%")).toBeTruthy();
    expect(screen.getByText("36%")).toBeTruthy();
  });

  it("keeps the model pick separate from the confirmed winner and exposes edit and clear actions", () => {
    const onManageResult = vi.fn();
    const actual: PredictedMatch = {
      ...match,
      winner_id: "b",
      loser_id: "a",
      status: "completed_result",
      outcome_source: "completed_result",
      result_status: "confirmed",
      confirmed_winner_id: "b",
      confirmed_score: "0:1",
      original_prediction: match.prediction,
    };
    render(<MatchCard match={actual} teamMap={new Map([[first.id, first], [second.id, second]])} draftOverrides={{}} onScenarioChange={vi.fn()} onOpen={vi.fn()} onManageResult={onManageResult} />);
    expect(screen.getByText(/MODEL PICK/).textContent).toContain("Alpha");
    expect(screen.getByText(/ACTUAL WINNER/).textContent).toContain("Bravo");
    expect(screen.getByText("PREDICTION MISSED")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit Result" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear Result" }));
    expect(onManageResult.mock.calls.map((call) => call[0])).toEqual([actual, actual]);
    expect(onManageResult.mock.calls.map((call) => call[1])).toEqual(["edit", "clear"]);
  });

  it("does not claim a prediction was correct when an imported result has no saved forecast", () => {
    const imported: PredictedMatch = {
      ...match,
      winner_id: "b", loser_id: "a", status: "completed_result",
      outcome_source: "completed_result", result_status: "confirmed", confirmed_winner_id: "b",
    };
    render(<MatchCard match={imported} teamMap={new Map([[first.id, first], [second.id, second]])} draftOverrides={{}} onScenarioChange={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText(/NO SAVED PRE-MATCH PICK/).textContent).toContain("Unavailable");
    expect(screen.queryByText(/PREDICTION (CORRECT|MISSED)/)).toBeNull();
  });
});
