-- ---------------------------------------------------------------------------
-- XMGPLAT-10417 — Feedback Storage Backend
--
-- Schema for the `feedback` table that backs FeedbackStore.
--
-- This file is the canonical DDL artifact:
--   * The database-provisioning task can apply it directly with `psql`.
--   * `FeedbackStore._init_schema()` reads and executes this file at startup
--     so dev/test environments self-bootstrap (mirrors the in-code DDL in
--     `auto_bedrock_chat_fastapi/pgvector_kb_store.py`).
--
-- All statements are idempotent (`IF NOT EXISTS` / `DO $$ ... $$`) and safe
-- to re-run.
-- ---------------------------------------------------------------------------

-- Required for `gen_random_uuid()` server-side default.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Enum types
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'feedback_rating') THEN
        CREATE TYPE feedback_rating AS ENUM ('positive', 'negative', 'correction');
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'feedback_review_status') THEN
        CREATE TYPE feedback_review_status AS ENUM ('pending_review', 'approved', 'rejected');
    END IF;
END$$;

-- ---------------------------------------------------------------------------
-- Feedback table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feedback (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Submission context
    session_id          TEXT        NOT NULL,
    user_id             TEXT        NOT NULL,

    -- Original AI response context
    query               TEXT        NOT NULL,
    ai_response         TEXT        NOT NULL,

    -- User input
    rating              feedback_rating NOT NULL,
    score               INTEGER     CHECK (score IS NULL OR (score BETWEEN 1 AND 5)),
    correction_text     TEXT,
    user_comment        TEXT,

    -- Provenance
    kb_sources_used     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    model_id            TEXT        NOT NULL,

    -- Review workflow
    review_status       feedback_review_status NOT NULL DEFAULT 'pending_review',
    reviewer_id         TEXT,
    reviewer_tags       TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    reviewer_comment    TEXT,
    reviewed_at         TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Mirror the Pydantic-side validation (see auto_bedrock_chat_fastapi/models.py):
    -- correction rating requires correction_text;
    -- positive rating must not carry a correction_text;
    -- decided review_status requires reviewer_id + reviewed_at.
    CONSTRAINT feedback_correction_text_required
        CHECK (rating <> 'correction' OR correction_text IS NOT NULL),
    CONSTRAINT feedback_positive_no_correction
        CHECK (rating <> 'positive' OR correction_text IS NULL),
    CONSTRAINT feedback_review_decision_complete
        CHECK (
            review_status = 'pending_review'
            OR (reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL)
        )
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Admin queue: list pending entries ordered by submission time.
CREATE INDEX IF NOT EXISTS idx_feedback_status_created
    ON feedback (review_status, created_at);

-- Date-range queries (admin API).
CREATE INDEX IF NOT EXISTS idx_feedback_created_at
    ON feedback (created_at DESC);

-- Tag overlap / containment queries (`reviewer_tags && ARRAY[...]`).
CREATE INDEX IF NOT EXISTS idx_feedback_reviewer_tags
    ON feedback USING gin (reviewer_tags);

-- Per-user history.
CREATE INDEX IF NOT EXISTS idx_feedback_user_created
    ON feedback (user_id, created_at DESC);

-- Per-session lookups.
CREATE INDEX IF NOT EXISTS idx_feedback_session
    ON feedback (session_id);
