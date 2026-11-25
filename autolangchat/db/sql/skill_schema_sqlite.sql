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
--   * No foreign key from `skill_revisions` to `skills` (SQLite enforces
--     them only per connection); the store deletes the history itself.
--   * Databases created before revisions existed lack `skills.revision`;
--     `SQLiteSkillStore` adds it and the baseline history after this script.
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
    revision      INTEGER NOT NULL DEFAULT 1,

    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    updated_by    TEXT
);

-- The content of every revision of every skill (the current one included).
CREATE TABLE IF NOT EXISTS skill_revisions (
    name          TEXT    NOT NULL,
    revision      INTEGER NOT NULL,

    display_name  TEXT    NOT NULL,
    description   TEXT    NOT NULL DEFAULT '',
    instructions  TEXT    NOT NULL,
    parameters    TEXT    NOT NULL DEFAULT '[]',

    updated_at    TEXT    NOT NULL,
    updated_by    TEXT,

    PRIMARY KEY (name, revision)
);
