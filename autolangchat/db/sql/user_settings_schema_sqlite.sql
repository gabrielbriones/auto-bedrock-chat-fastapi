-- ---------------------------------------------------------------------------
-- Per-User Settings Storage (SQLite variant)
--
-- Mirrors autolangchat/db/sql/user_settings_schema.sql for the SQLite
-- backend used as the zero-config default. Differences vs. the Postgres DDL:
--
--   * No JSONB — `settings` is a TEXT column holding a JSON object. The
--     application serializes/deserializes it with `json.dumps`/`json.loads`.
--   * `created_at` / `updated_at` are TEXT (ISO-8601, UTC) with no DB-side
--     default — the application sets both explicitly on every write so
--     lexical (TEXT) ordering matches chronological ordering, mirroring
--     `conversation_schema_sqlite.sql`.
--
-- `user_id` remains the unique primary key so `INSERT OR IGNORE` gives the
-- same race-safe get-or-create semantics as the Postgres
-- `ON CONFLICT (user_id) DO NOTHING`.
--
-- All statements are idempotent (`IF NOT EXISTS`) and safe to re-run.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_settings (
    user_id     TEXT PRIMARY KEY,

    settings    TEXT NOT NULL DEFAULT '{}',

    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
