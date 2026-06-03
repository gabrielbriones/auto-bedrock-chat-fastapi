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
    -- ON DELETE SET NULL so that deleting the article returns the row to the
    -- "approved but not yet integrated" queue automatically.
    integrated_into_kb_id   TEXT        REFERENCES documents(id) ON DELETE SET NULL,
    integrated_at           TIMESTAMPTZ,

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
            ADD COLUMN integrated_into_kb_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
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

-- ---------------------------------------------------------------------------
-- Trigger: keep integrated_at in sync with integrated_into_kb_id
--
-- The FK column has ON DELETE SET NULL, which only nulls integrated_into_kb_id
-- when the referenced KB document is deleted.  integrated_at would then remain
-- set, violating the feedback_integrated_copresence constraint.  This trigger
-- fires BEFORE UPDATE and clears integrated_at whenever integrated_into_kb_id
-- transitions to NULL.  CREATE OR REPLACE makes it idempotent on every restart.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION feedback_sync_integrated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.integrated_into_kb_id IS NULL AND OLD.integrated_into_kb_id IS NOT NULL THEN
        NEW.integrated_at := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_feedback_sync_integrated_at ON feedback;
CREATE TRIGGER trg_feedback_sync_integrated_at
    BEFORE UPDATE ON feedback
    FOR EACH ROW EXECUTE FUNCTION feedback_sync_integrated_at();
