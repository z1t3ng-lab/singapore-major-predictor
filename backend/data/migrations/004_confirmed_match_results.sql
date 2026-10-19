ALTER TABLE matches ADD COLUMN fixture_type TEXT NOT NULL DEFAULT 'official';
ALTER TABLE matches ADD COLUMN result_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE matches ADD COLUMN confirmed_winner_id TEXT REFERENCES teams(id);
ALTER TABLE matches ADD COLUMN confirmed_score TEXT;
ALTER TABLE matches ADD COLUMN result_source TEXT;
ALTER TABLE matches ADD COLUMN result_created_at TEXT;
ALTER TABLE matches ADD COLUMN result_updated_at TEXT;
ALTER TABLE matches ADD COLUMN result_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE matches ADD COLUMN original_prediction_json TEXT;
ALTER TABLE matches ADD COLUMN original_prediction_at TEXT;
ALTER TABLE matches ADD COLUMN prediction_version TEXT;
ALTER TABLE prediction_runs ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE prediction_runs ADD COLUMN result_json TEXT;

UPDATE matches
SET result_status = CASE WHEN status = 'completed' AND winner_id IS NOT NULL THEN 'confirmed' ELSE 'pending' END,
    result_source = CASE WHEN status = 'completed' AND winner_id IS NOT NULL THEN source ELSE NULL END,
    result_created_at = CASE WHEN status = 'completed' AND winner_id IS NOT NULL THEN COALESCE(available_at, retrieved_at) ELSE NULL END,
    result_updated_at = CASE WHEN status = 'completed' AND winner_id IS NOT NULL THEN COALESCE(retrieved_at, available_at) ELSE NULL END;
