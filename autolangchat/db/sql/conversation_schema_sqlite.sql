-- ---------------------------------------------------------------------------
-- Conversation Metadata Storage (SQLite variant)
--
-- Mirrors autolangchat/db/sql/conversation_schema.sql for the SQLite
-- backend used as the zero-config default. Differences vs. the Postgres DDL:
--
--   * `id` is plain TEXT (not UUID) with no server-side default: the value
--     is always supplied by the application, since it must equal the
--     LangGraph checkpoint `thread_id` for the conversation.
--   * No JSONB — `metadata` is a TEXT column holding a JSON object. The
--     application serializes/deserializes it with `json.dumps`/`json.loads`.
--   * `is_archived` is stored as an INTEGER 0/1 flag (SQLite has no native
--     BOOLEAN type) with a CHECK constraint restricting it to 0 or 1.
--   * `created_at` / `updated_at` are TEXT (ISO-8601, UTC) with no DB-side
--     default — the application sets both explicitly on every write so
--     lexical (TEXT) ordering matches chronological ordering, mirroring
--     `feedback_schema_sqlite.sql`.
--
-- LangGraph checkpoint rows (not this table) remain the source of truth for
-- message history; this table only tracks per-conversation metadata used to
-- build the per-user conversation list/sidebar.
--
-- All statements are idempotent (`IF NOT EXISTS`) and safe to re-run.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,

    user_id         TEXT NOT NULL,
    title           TEXT,

    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    message_count   INTEGER NOT NULL DEFAULT 0,
    metadata        TEXT NOT NULL DEFAULT '{}',
    is_archived     INTEGER NOT NULL DEFAULT 0
                    CHECK (is_archived IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_conv_user
    ON conversations (user_id);

CREATE INDEX IF NOT EXISTS idx_conv_updated
    ON conversations (updated_at DESC);
