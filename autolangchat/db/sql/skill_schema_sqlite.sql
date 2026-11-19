-- ---------------------------------------------------------------------------
-- Skill Storage (SQLite variant)
--
-- Mirrors autolangchat/db/sql/skill_schema.sql for the SQLite backend.
-- Differences vs. the Postgres DDL:
--
--   * No JSONB — `parameters` is a TEXT column holding a JSON array,
--     serialized with `json.dumps`/`json.loads`.
--   * No BOOLEAN — `enabled` is an INTEGER constrained to 0/1.
--   * `created_at` / `updated_at` are TEXT (ISO-8601, UTC) with no DB-side
--     default; the application sets them explicitly on every write.
--
-- All statements are idempotent (`IF NOT EXISTS`) and safe to re-run.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS skills (
    name          TEXT PRIMARY KEY,

    display_name  TEXT    NOT NULL,
    description   TEXT    NOT NULL DEFAULT '',
    instructions  TEXT    NOT NULL,
    parameters    TEXT    NOT NULL DEFAULT '[]',
    enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),

    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    updated_by    TEXT
);
