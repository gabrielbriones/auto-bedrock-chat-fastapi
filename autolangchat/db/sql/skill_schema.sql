-- ---------------------------------------------------------------------------
-- Skill Storage
--
-- Schema for the `skills` table that backs SkillStore, and its
-- `skill_revisions` history.
--
-- One row per admin-managed skill, keyed by its stable `name` slug
-- (`^[a-z0-9][a-z0-9_-]{0,63}$`, enforced by the application). `revision`
-- starts at 1 and is incremented on every content change (enable/disable
-- keeps it). Every revision's content, the current one included, is kept
-- in `skill_revisions`; a rollback writes the old content as a new
-- revision, so history is never rewritten. Deleting a skill cascades to
-- its history.
--
-- `parameters` is an ordered JSON array of presentation hints for the
-- composer UI; runtime values are always strings and the database performs
-- no validation of it. Size limits (instructions, description, parameter
-- count) are configurable and enforced by the application.
--
-- This file is the canonical DDL artifact:
--   * The database-provisioning task can apply it directly with `psql`.
--   * `PostgresSkillStore._apply_schema()` reads and executes this file at
--     startup so dev/test environments self-bootstrap.
--
-- All statements are idempotent and safe to re-run. They also migrate a
-- database created before revisions existed: its skills get revision 1 and
-- a matching baseline history entry.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS skills (
    name          TEXT PRIMARY KEY,

    display_name  TEXT        NOT NULL,
    description   TEXT        NOT NULL DEFAULT '',
    instructions  TEXT        NOT NULL,
    parameters    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    enabled       BOOLEAN     NOT NULL DEFAULT TRUE,
    revision      INTEGER     NOT NULL DEFAULT 1,

    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    -- The admin identity (`AdminIdentity.user_id`) of the last writer.
    updated_by    TEXT
);

-- Databases created before revisions existed.
ALTER TABLE skills ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS skill_revisions (
    name          TEXT        NOT NULL REFERENCES skills (name) ON DELETE CASCADE,
    revision      INTEGER     NOT NULL,

    display_name  TEXT        NOT NULL,
    description   TEXT        NOT NULL DEFAULT '',
    instructions  TEXT        NOT NULL,
    parameters    JSONB       NOT NULL DEFAULT '[]'::jsonb,

    updated_at    TIMESTAMPTZ NOT NULL,
    updated_by    TEXT,

    PRIMARY KEY (name, revision)
);

-- Baseline history for skills whose current revision has none (pre-revision databases).
INSERT INTO skill_revisions (name, revision, display_name, description, instructions, parameters, updated_at, updated_by)
     SELECT name, revision, display_name, description, instructions, parameters, updated_at, updated_by
       FROM skills
ON CONFLICT (name, revision) DO NOTHING;
