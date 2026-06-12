-- ---------------------------------------------------------------------------
-- Feedback Storage Backend
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
        CREATE TYPE feedback_rating AS ENUM ('positive', 'negative');
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

    -- Set by the synthesizer when this entry is incorporated into a KB article.
    -- Plain TEXT (no FK) so this table can be deployed independently of the
    -- KB store (e.g. feedback-only deployments or separate Postgres DBs).
    integrated_into_kb_id   TEXT,
    integrated_at           TIMESTAMPTZ,

    -- Set by the rollback endpoint when a synthesized article is removed.
    rolled_back_at          TIMESTAMPTZ,
    rolled_back_by          TEXT,
    rollback_reason         TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Mirror the Pydantic-side validation (see auto_bedrock_chat_fastapi/models.py):
    -- correction_text is a proposed fix and only makes sense for negative
    -- feedback; when present it must also be non-empty.
    -- A decided review_status requires non-empty reviewer_id + reviewed_at.
    CONSTRAINT feedback_correction_text_negative_only
        CHECK (correction_text IS NULL OR rating = 'negative'),
    CONSTRAINT feedback_correction_text_non_empty
        CHECK (
            correction_text IS NULL
            OR length(btrim(correction_text)) > 0
        ),
    CONSTRAINT feedback_review_decision_complete
        CHECK (
            review_status = 'pending_review'
            OR (
                reviewer_id IS NOT NULL
                AND length(btrim(reviewer_id)) > 0
                AND reviewed_at IS NOT NULL
            )
        ),
    -- Synthesis provenance invariants:
    -- Integration requires only that the entry has been approved.
    -- correction_text is not required (synthesis can use reviewer_comment alone).
    CONSTRAINT feedback_integrated_requires_approved_correction
        CHECK (
            integrated_into_kb_id IS NULL
            OR review_status = 'approved'
        ),
    -- Both integration fields must be set together or both null.
    CONSTRAINT feedback_integrated_copresence
        CHECK (
            (integrated_into_kb_id IS NULL) = (integrated_at IS NULL)
        )
);

-- Migration: add conversation_history column (idempotent)
ALTER TABLE feedback
ADD COLUMN IF NOT EXISTS conversation_history JSONB NOT NULL DEFAULT '[]'::jsonb;

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Add synthesis provenance columns
-- to existing deployments that predate the DDL.  Both statements are
-- no-ops on fresh installs where the columns are already present in the
-- CREATE TABLE above.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'feedback' AND column_name = 'integrated_into_kb_id'
    ) THEN
        ALTER TABLE feedback
            ADD COLUMN integrated_into_kb_id TEXT,
            ADD COLUMN integrated_at TIMESTAMPTZ,
            ADD CONSTRAINT feedback_integrated_requires_approved_correction
                CHECK (
                    integrated_into_kb_id IS NULL
                    OR review_status = 'approved'
                ),
            ADD CONSTRAINT feedback_integrated_copresence
                CHECK ((integrated_into_kb_id IS NULL) = (integrated_at IS NULL));
    END IF;
END$$;

-- Constraint relaxation migration: drop the old strict constraint that required
-- correction_text IS NOT NULL and replace it with one that only requires
-- review_status = 'approved'.  Idempotent — safe to run on every startup.
DO $$
DECLARE
    v_def TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid)
    INTO v_def
    FROM pg_constraint
    WHERE conrelid = 'feedback'::regclass
      AND conname = 'feedback_integrated_requires_approved_correction';

    IF FOUND AND v_def LIKE '%correction_text%' THEN
        ALTER TABLE feedback
            DROP CONSTRAINT feedback_integrated_requires_approved_correction,
            ADD CONSTRAINT feedback_integrated_requires_approved_correction
                CHECK (
                    integrated_into_kb_id IS NULL
                    OR review_status = 'approved'
                );
    END IF;
END$$;

-- Add rollback audit columns to existing deployments.  Idempotent.
-- Each column is checked independently to handle partially-migrated deployments.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'feedback' AND column_name = 'rolled_back_at'
    ) THEN
        ALTER TABLE feedback ADD COLUMN rolled_back_at TIMESTAMPTZ;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'feedback' AND column_name = 'rolled_back_by'
    ) THEN
        ALTER TABLE feedback ADD COLUMN rolled_back_by TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'feedback' AND column_name = 'rollback_reason'
    ) THEN
        ALTER TABLE feedback ADD COLUMN rollback_reason TEXT;
    END IF;
END$$;

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
