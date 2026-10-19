export type Team = {
  id: string;
  name: string;
  region?: string | null;
  seed: number;
  elo_rating?: number | null;
  rating_source?: string | null;
  synthetic: boolean;
  direct_entries?: { stage: string; seed: number; type: string }[];
  wins?: number;
  losses?: number;
  status?: string;
};

export type Prediction = {
  team_a_id: string;
  team_b_id: string;
  team_a_probability: number;
  team_b_probability: number;
  predicted_winner_id: string;
  best_of: number;
  rating_a: number;
  rating_b: number;
  rating_difference: number;
  base_map_win_probability_a?: number;
  map_win_probability_a: number;
  sample_matches_a: number;
  sample_matches_b: number;
  reliability: string;
  explanation: string;
  warnings: string[];
  model: string;
  map_pool_used: boolean;
  map_veto_used?: boolean;
  player_metrics_used: boolean;
  roster_continuity_used?: boolean;
  head_to_head_used?: boolean;
  head_to_head_count?: number;
  context_log_odds?: number;
  context_method?: string;
  context_features?: {
    key: string;
    label: string;
    used: boolean;
    log_odds_adjustment: number;
    summary: string;
    sample_size: number;
    team_a_value?: number | null;
    metric_version?: string;
  }[];
};

export type PredictedMatch = {
  id: string;
  stage_slug: string;
  round_no: number;
  round_name?: string;
  team_a_id: string;
  team_b_id: string;
  winner_id: string;
  loser_id: string;
  best_of: number;
  status: "completed_result" | "predicted";
  fixture_confirmed: boolean;
  fixture_type?: "official" | "generated";
  pairing_status?: "official_fixture" | "provisional_pairing";
  outcome_source: string;
  forced_rematch?: boolean;
  prediction: Prediction;
  score?: string | null;
  confirmed_winner_id?: string | null;
  confirmed_score?: string | null;
  result_status?: "pending" | "confirmed" | "cleared" | "invalidated";
  result_source?: string | null;
  result_version?: number;
  result_created_at?: string | null;
  result_updated_at?: string | null;
  original_prediction?: Prediction | null;
  original_prediction_at?: string | null;
  prediction_version?: string | null;
};

export type StageRound = {
  round_no: number;
  name?: string;
  matches: PredictedMatch[];
  standings?: Team[];
};

export type ProjectedStage = {
  slug: string;
  rounds: StageRound[];
  advanced: Team[];
  eliminated: Team[];
};

export type ProbabilityEstimate = {
  probability: number;
  standard_error: number;
  count: number;
};

export type TeamProbabilities = {
  stage1_advance: ProbabilityEstimate;
  stage2_advance: ProbabilityEstimate;
  playoffs: ProbabilityEstimate;
  semifinals: ProbabilityEstimate;
  final: ProbabilityEstimate;
  champion: ProbabilityEstimate;
};

export type TournamentResult = {
  run_id: string;
  state_version?: number;
  cutoff: string;
  simulations: number;
  seed: number;
  data_source: string;
  scenario: { active: boolean; overrides: Record<string, string> };
  projected_path: {
    stages: Record<"stage1" | "stage2" | "stage3", ProjectedStage>;
    playoffs: { rounds: StageRound[]; champion_id: string };
  };
  probabilities: Record<string, TeamProbabilities>;
  championship_probability_sum: number;
  model: {
    name: string;
    status: string;
    status_message: string;
    eligible_matches: number;
    metrics: Record<string, number>;
    metrics_scope?: string | null;
  };
  sampling_uncertainty: string;
  assumptions: string[];
};

export type MatchResultConflict = {
  id: string;
  stage_slug: string;
  round_no: number;
  team_a_id: string;
  team_b_id: string;
  winner_id?: string | null;
  team_a_name: string;
  team_b_name: string;
  winner_name?: string | null;
  can_invalidate: boolean;
};

export type Overview = {
  event: {
    id: string;
    name: string;
    organizer: string;
    start_date: string;
    end_date: string;
    location: string;
    details_status: string;
  };
  official_source_urls: string[];
  stages: {
    slug: string;
    name: string;
    start_date: string;
    end_date: string;
    entrants: number;
    advances: number;
    rules: Record<string, unknown>;
  }[];
  notes: string[];
  data: {
    active_source: string;
    last_successful_refresh: string | null;
    match_records_available: number;
    completed_event_results: number;
    team_records: number;
    direct_stage_entrants: number;
    synthetic_records_present: boolean;
    latest_source_log: { provider: string; retrieved_at: string; status: string; records_read: number; error?: string | null } | null;
    hltv_automated_refresh: { enabled: boolean; reason: string };
  };
  model: { name: string; status: string; trained: boolean; metrics_scope?: string | null };
};
