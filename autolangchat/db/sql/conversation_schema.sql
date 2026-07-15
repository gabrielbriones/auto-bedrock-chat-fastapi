-- ---------------------------------------------------------------------------
-- Conversation Metadata Storage
--
-- Schema for the `conversations` table that backs ConversationStore.
--
-- This is a lightweight metadata index only (id, user_id, title,
-- timestamps) mapping user identities to their LangGraph conversation
-- threads. LangGraph checkpoint rows remain the single source of truth for
-- message history — there is deliberately no `messages` table here.
--
-- This file is the canonical DDL artifact:
--   * The database-provisioning task can apply it directly with `psql`.
--   * `ConversationStore._init_schema()` reads and executes this file at
--     startup so dev/test environments self-bootstrap (mirrors
--     `feedback_schema.sql` / `token_usage_schema.sql`).
--
-- All statements are idempotent (`IF NOT EXISTS`) and safe to re-run.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS conversations (
    -- No server-side default: the value is always supplied by the
    -- application, since it must equal the LangGraph checkpoint
    -- `thread_id` for the conversation.
    id              UUID PRIMARY KEY,

    user_id         TEXT        NOT NULL,
    title           TEXT,

    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,

    message_count   INTEGER     NOT NULL DEFAULT 0,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    is_archived     BOOLEAN     NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_conv_user
    ON conversations (user_id);

CREATE INDEX IF NOT EXISTS idx_conv_updated
    ON conversations (updated_at DESC);
