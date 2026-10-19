import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResultDialog } from "@/components/ResultDialog";
import { saveMatchResult } from "@/lib/api";
import type { MatchResultResponse } from "@/lib/api";
import type { PredictedMatch, Team } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  clearMatchResult: vi.fn(),
  resultConflictsFromError: vi.fn(() => null),
  saveMatchResult: vi.fn(),
}));

const first: Team = { id: "a", name: "Alpha", seed: 1, synthetic: false };
const second: Team = { id: "b", name: "Bravo", seed: 2, synthetic: false };
const match: PredictedMatch = {
  id: "stage1:r1:a::b", stage_slug: "stage1", round_no: 1,
  team_a_id: "a", team_b_id: "b", winner_id: "a", loser_id: "b", best_of: 1,
  status: "predicted", fixture_confirmed: false, outcome_source: "model_projection",
  result_status: "pending", result_version: 0,
  prediction: {
    team_a_id: "a", team_b_id: "b", team_a_probability: 0.64, team_b_probability: 0.36,
    predicted_winner_id: "a", best_of: 1, rating_a: 1600, rating_b: 1500, rating_difference: 100,
    map_win_probability_a: 0.64, sample_matches_a: 0, sample_matches_b: 0, reliability: "low",
    explanation: "Elo baseline.", warnings: [], model: "Interpretable Elo baseline",
    map_pool_used: false, player_metrics_used: false,
  },
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Set match result dialog", () => {
  it("validates the score, asks for confirmation, and submits the selected participant", async () => {
    const response = { message: "Result confirmed. Predictions updated.", result: {}, invalidated_result_ids: [] } as unknown as MatchResultResponse;
    vi.mocked(saveMatchResult).mockResolvedValue(response);
    const onSaved = vi.fn();
    render(<ResultDialog match={match} teamMap={new Map([[first.id, first], [second.id, second]])} action="set" onClose={vi.fn()} onBusyChange={vi.fn()} onSaved={onSaved} />);

    fireEvent.change(screen.getByLabelText("ACTUAL WINNER"), { target: { value: "b" } });
    fireEvent.change(screen.getByLabelText("Alpha map wins"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Bravo map wins"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Review result" }));
    expect(screen.getByRole("alert").textContent).toContain("score winner must match");
    expect(saveMatchResult).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Alpha map wins"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Bravo map wins"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Review result" }));
    expect(screen.getByText(/Confirm Bravo as the actual winner/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm result" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(response));
    expect(saveMatchResult).toHaveBeenCalledWith("stage1:r1:a::b", expect.objectContaining({
      winner_id: "b", score: "0:1", expected_result_version: 0,
    }));
  });
});
