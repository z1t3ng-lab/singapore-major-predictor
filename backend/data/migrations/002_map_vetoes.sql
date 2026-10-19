CREATE TABLE IF NOT EXISTS map_vetoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL REFERENCES matches(id),
    sequence_no INTEGER NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('pick', 'ban')),
    map_name TEXT NOT NULL,
    team_id TEXT NOT NULL REFERENCES teams(id),
    available_at TEXT NOT NULL,
    source TEXT,
    UNIQUE(match_id, sequence_no)
);

CREATE INDEX IF NOT EXISTS idx_map_vetoes_match_available
    ON map_vetoes(match_id, available_at);
