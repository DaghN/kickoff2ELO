-- Kool Elo — core relational schema (imports + ratings)
-- SQLite: enable FK enforcement per connection (see import script).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS players (
    player_id    TEXT    PRIMARY KEY,
    display_name TEXT    NOT NULL,
    rating       REAL    NOT NULL DEFAULT 1600
);

CREATE TABLE IF NOT EXISTS games (
    game_id       TEXT    PRIMARY KEY,
    start_time    TEXT    NOT NULL,  -- ISO-like 'YYYY-MM-DD HH:MM:SS' for lexicographic sort
    player_a_id   TEXT    NOT NULL,
    player_b_id   TEXT    NOT NULL,
    score_a       INTEGER NOT NULL CHECK (score_a >= 0),
    score_b       INTEGER NOT NULL CHECK (score_b >= 0),
    duration_secs INTEGER NOT NULL CHECK (duration_secs >= 0),
    elo_a_before  REAL,
    elo_b_before  REAL,
    elo_a_after   REAL,
    elo_b_after   REAL,
    FOREIGN KEY (player_a_id) REFERENCES players (player_id),
    FOREIGN KEY (player_b_id) REFERENCES players (player_id)
);

CREATE INDEX IF NOT EXISTS idx_games_start_time ON games (start_time);
CREATE INDEX IF NOT EXISTS idx_games_player_a ON games (player_a_id);
CREATE INDEX IF NOT EXISTS idx_games_player_b ON games (player_b_id);
