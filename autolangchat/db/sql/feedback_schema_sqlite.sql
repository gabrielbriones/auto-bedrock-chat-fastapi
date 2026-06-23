-- ---------------------------------------------------------------------------
-- Feedback Storage Backend (SQLite variant)
--
-- Mirrors autolangchat/db/sql/feedback_schema.sql for the SQLite
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
                        CHECK (rating IN ('positive', 'negative')),
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
    conversation_history TEXT NOT NULL DEFAULT '[]',
    reviewer_comment    TEXT,
    reviewed_at         TEXT,

    -- SQLite does not enforce FK constraints by default; co-presence and
    -- approval invariants are enforced by CHECK constraints and the
    -- application layer (Pydantic model validators).
    integrated_into_kb_id   TEXT,
    integrated_at           TEXT,

    -- Set by the rollback endpoint when a synthesized article is removed.
    rolled_back_at          TEXT,
    rolled_back_by          TEXT,
    rollback_reason         TEXT,

    created_at          TEXT NOT NULL,

    -- A correction is a proposed fix to the AI's answer; only meaningful
    -- for negative feedback. If present, it must be non-empty.
    CHECK (correction_text IS NULL OR rating = 'negative'),
    CHECK (
        correction_text IS NULL
        OR length(trim(correction_text)) > 0
    ),
    CHECK (
        review_status = 'pending_review'
        OR (
            reviewer_id IS NOT NULL
            AND length(trim(reviewer_id)) > 0
            AND reviewed_at IS NOT NULL
        )
    ),
    -- Synthesis provenance invariants:
    -- Integration requires only that the entry has been approved.
    CHECK (
        integrated_into_kb_id IS NULL
        OR review_status = 'approved'
    ),
    CHECK ((integrated_into_kb_id IS NULL) = (integrated_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_feedback_status_created
    ON feedback (review_status, created_at);

CREATE INDEX IF NOT EXISTS idx_feedback_created_at
    ON feedback (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_feedback_user_created
    ON feedback (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_feedback_session
    ON feedback (session_id);
