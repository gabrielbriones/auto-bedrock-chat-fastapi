-- ---------------------------------------------------------------------------
-- XMGPLAT-10417 — Feedback Storage Backend (SQLite variant)
--
-- Mirrors auto_bedrock_chat_fastapi/sql/feedback_schema.sql for the SQLite
-- backend used as the zero-config default. Differences vs. the Postgres DDL:
--
--   * No native enum types — `rating` and `review_status` are TEXT with
--     CHECK constraints listing the allowed values.
--   * No JSONB / TEXT[] — `kb_sources_used` and `reviewer_tags` are TEXT
--     columns holding JSON arrays. The application serializes/deserializes
--     them with `json.dumps`/`json.loads`.
--   * No `gen_random_uuid()` / `now()` — defaults are populated client-side
--     by the FeedbackEntry Pydantic model.
--   * No GIN index for `reviewer_tags` — list-by-tag queries scan via
--     `json_each` which is acceptable for the expected volume.
--
-- All statements are idempotent (`IF NOT EXISTS`) and safe to re-run.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feedback (
    id                  TEXT PRIMARY KEY,

    session_id          TEXT NOT NULL,
    user_id             TEXT NOT NULL,

    query               TEXT NOT NULL,
    ai_response         TEXT NOT NULL,

    rating              TEXT NOT NULL
                        CHECK (rating IN ('positive', 'negative', 'correction')),
    score               INTEGER
                        CHECK (score IS NULL OR (score BETWEEN 1 AND 5)),
    correction_text     TEXT,
    user_comment        TEXT,

    kb_sources_used     TEXT NOT NULL DEFAULT '[]',
    model_id            TEXT NOT NULL,

    review_status       TEXT NOT NULL DEFAULT 'pending_review'
                        CHECK (review_status IN ('pending_review', 'approved', 'rejected')),
    reviewer_id         TEXT,
    reviewer_tags       TEXT NOT NULL DEFAULT '[]',
    reviewer_comment    TEXT,
    reviewed_at         TEXT,

    created_at          TEXT NOT NULL,

    CHECK (
        rating <> 'correction'
        OR (correction_text IS NOT NULL AND length(trim(correction_text)) > 0)
    ),
    CHECK (rating <> 'positive' OR correction_text IS NULL),
    CHECK (
        review_status = 'pending_review'
        OR (
            reviewer_id IS NOT NULL
            AND length(trim(reviewer_id)) > 0
            AND reviewed_at IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_feedback_status_created
    ON feedback (review_status, created_at);

CREATE INDEX IF NOT EXISTS idx_feedback_created_at
    ON feedback (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_feedback_user_created
    ON feedback (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_feedback_session
    ON feedback (session_id);
