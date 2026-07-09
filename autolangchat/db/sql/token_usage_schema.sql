-- ---------------------------------------------------------------------------
-- Token Usage Storage Backend
--
-- Schema for the `token_usage` table that backs TokenUsageStore.
--
-- This file is the canonical DDL artifact:
--   * The database-provisioning task can apply it directly with `psql`.
--   * `PostgresTokenUsageStore._apply_schema()` reads and executes this file
--     at startup so dev/test environments self-bootstrap (mirrors
--     `feedback_schema.sql` / `PostgresFeedbackStore._apply_schema()`).
--
-- All statements are idempotent (`IF NOT EXISTS`) and safe to re-run.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- Token usage table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS token_usage (
    -- `id` is always supplied by the caller (the stable message_id, see
    -- PostgresTokenUsageStore.record_turn) as a plain string, matching the
    -- SQLite schema's `id TEXT PRIMARY KEY`. No server-side UUID default or
    -- pgcrypto extension is required.
    id                  TEXT PRIMARY KEY,

    -- Turn context
    session_id          TEXT        NOT NULL,
    user_id             TEXT,

    -- Model that produced this turn's response
    model_id            TEXT        NOT NULL,

    -- Per-turn token counts (accumulated across tool-call rounds upstream
    -- in llm_call_node before being recorded here)
    input_tokens        INTEGER     NOT NULL CHECK (input_tokens >= 0),
    output_tokens       INTEGER     NOT NULL CHECK (output_tokens >= 0),

    turn_ts             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_token_usage_turn_ts
    ON token_usage (turn_ts DESC);

CREATE INDEX IF NOT EXISTS idx_token_usage_session
    ON token_usage (session_id);

CREATE INDEX IF NOT EXISTS idx_token_usage_user_turn_ts
    ON token_usage (user_id, turn_ts DESC);
