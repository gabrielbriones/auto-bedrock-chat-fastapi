-- ---------------------------------------------------------------------------
-- Token Usage Storage Backend (SQLite variant)
--
-- Mirrors autolangchat/db/sql/token_usage_schema.sql for the SQLite
-- backend used as the zero-config default. Differences vs. the Postgres DDL:
--
--   * No `gen_random_uuid()` / `now()` — `id` and `turn_ts` are populated
--     client-side by the caller (see `SQLiteTokenUsageStore.record_turn`).
--   * `turn_ts` is a TEXT column holding an ISO-8601 UTC timestamp string,
--     matching the `created_at` convention used by the `feedback` table.
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
