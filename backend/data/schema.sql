PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT,
    seed INTEGER,
    elo_rating REAL,
    rating_source TEXT,
    synthetic INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT,
    source TEXT
);

CREATE TABLE IF NOT EXISTS roster_memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL REFERENCES teams(id),
    player_id TEXT NOT NULL REFERENCES players(id),
    started_at TEXT,
    ended_at TEXT,
    role TEXT,
    source TEXT,
    available_at TEXT,
    UNIQUE(team_id, player_id, started_at)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    organizer TEXT,
    start_date TEXT,
    end_date TEXT,
    location TEXT,
    source_urls TEXT,
    details_status TEXT NOT NULL DEFAULT 'partially_confirmed'
);

CREATE TABLE IF NOT EXISTS stages (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    entrants INTEGER NOT NULL,
    advances INTEGER NOT NULL,
    rules_json TEXT NOT NULL,
    UNIQUE(event_id, slug)
);

CREATE TABLE IF NOT EXISTS stage_entrants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id TEXT NOT NULL REFERENCES stages(id),
    team_id TEXT NOT NULL REFERENCES teams(id),
    seed INTEGER NOT NULL,
    entry_type TEXT NOT NULL DEFAULT 'direct',
    UNIQUE(stage_id, team_id)
);

CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    event_id TEXT REFERENCES events(id),
    stage_slug TEXT,
    round_no INTEGER,
    team_a_id TEXT NOT NULL REFERENCES teams(id),
    team_b_id TEXT NOT NULL REFERENCES teams(id),
    best_of INTEGER,
    status TEXT NOT NULL DEFAULT 'completed',
    winner_id TEXT REFERENCES teams(id),
    score TEXT,
    played_at TEXT,
    available_at TEXT,
    source TEXT NOT NULL,
    source_url TEXT,
    retrieved_at TEXT,
    parsing_error TEXT,
    CHECK(team_a_id <> team_b_id),
    CHECK(winner_id IS NULL OR winner_id = team_a_id OR winner_id = team_b_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_event_stage_round
    ON matches(event_id, stage_slug, round_no);
CREATE INDEX IF NOT EXISTS idx_matches_played_at
    ON matches(played_at, available_at);

CREATE TABLE IF NOT EXISTS map_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL REFERENCES matches(id),
    map_name TEXT NOT NULL,
    team_a_score INTEGER,
    team_b_score INTEGER,
    winner_id TEXT REFERENCES teams(id),
    UNIQUE(match_id, map_name)
);

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

CREATE TABLE IF NOT EXISTS player_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT REFERENCES matches(id),
    player_id TEXT NOT NULL REFERENCES players(id),
    measurement_period TEXT NOT NULL,
    metric_version TEXT,
    stats_json TEXT NOT NULL,
    available_at TEXT,
    source TEXT
);

CREATE TABLE IF NOT EXISTS prediction_runs (
    id TEXT PRIMARY KEY,
    data_cutoff TEXT NOT NULL,
    random_seed INTEGER NOT NULL,
    simulations INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    scenario_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    source_url TEXT,
    retrieved_at TEXT NOT NULL,
    status TEXT NOT NULL,
    records_read INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
