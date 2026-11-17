-- ---------------------------------------------------------------------------
-- Token Usage Storage Backend (SQLite variant)
--
-- Mirrors autolangchat/db/sql/token_usage_schema.sql for the SQLite
-- backend used as the zero-config default. Differences vs. the Postgres DDL:
--
--   * No `now()` default for `turn_ts` — `id` and `turn_ts` are populated
--     client-side by the caller (see `SQLiteTokenUsageStore.record_turn`).
--   * `turn_ts` is a TEXT column holding an ISO-8601 UTC timestamp string,
--     matching the `created_at` convention used by the `feedback` table.
--   * SQLite lacks `ADD COLUMN IF NOT EXISTS`, so `cache_read_tokens`,
--     `cache_write_tokens`, `estimated_cost_micros`, `currency`,
--     `pricing_revision` and `final` are defined only in
--     `token_usage_sqlite._ADDED_COLUMNS` and added (to fresh and
--     pre-existing tables alike) by `SQLiteTokenUsageStore._migrate_columns_locked`.
--
-- All statements are idempotent (`IF NOT EXISTS`) and safe to re-run.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS token_usage (
    id                  TEXT PRIMARY KEY,

    session_id          TEXT NOT NULL,
    user_id             TEXT,

    model_id            TEXT NOT NULL,

    input_tokens        INTEGER NOT NULL
                        CHECK (input_tokens >= 0),
    output_tokens       INTEGER NOT NULL
                        CHECK (output_tokens >= 0),

    turn_ts             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_token_usage_turn_ts
    ON token_usage (turn_ts DESC);

CREATE INDEX IF NOT EXISTS idx_token_usage_session
    ON token_usage (session_id);

CREATE INDEX IF NOT EXISTS idx_token_usage_user_turn_ts
    ON token_usage (user_id, turn_ts DESC);

-- One row per AI invocation attributed to a turn (chat, retry,
-- summarization, title, embedding). `id` is the client-generated event id,
-- so replayed writes are ignored.
CREATE TABLE IF NOT EXISTS token_usage_events (
    id                  TEXT PRIMARY KEY,
    turn_id             TEXT NOT NULL,
    purpose             TEXT NOT NULL,
    provider            TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL CHECK (input_tokens >= 0),
    output_tokens       INTEGER NOT NULL CHECK (output_tokens >= 0),
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0 CHECK (cache_read_tokens >= 0),
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0 CHECK (cache_write_tokens >= 0),
    -- Subset of cache_write_tokens written with the one-hour TTL.
    cache_write_1h_tokens INTEGER NOT NULL DEFAULT 0 CHECK (cache_write_1h_tokens >= 0),
    event_ts            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_token_usage_events_turn
    ON token_usage_events (turn_id);
