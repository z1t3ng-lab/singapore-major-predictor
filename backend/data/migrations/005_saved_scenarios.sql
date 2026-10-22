CREATE TABLE IF NOT EXISTS saved_scenarios (
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES prediction_runs(id), created_at TEXT NOT NULL
);
