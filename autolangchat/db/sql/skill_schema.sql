-- ---------------------------------------------------------------------------
-- Skill Storage
--
-- Schema for the `skills` table that backs SkillStore.
--
-- One row per admin-managed skill, keyed by its stable `name` slug
-- (`^[a-z0-9][a-z0-9_-]{0,63}$`, enforced by the application). An update
-- replaces the whole row's content (last write wins); there are no
-- revisions yet.
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
-- All statements are idempotent (`IF NOT EXISTS`) and safe to re-run.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS skills (
    name          TEXT PRIMARY KEY,

    display_name  TEXT        NOT NULL,
    description   TEXT        NOT NULL DEFAULT '',
    instructions  TEXT        NOT NULL,
    parameters    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    enabled       BOOLEAN     NOT NULL DEFAULT TRUE,

    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    -- The admin identity (`AdminIdentity.user_id`) of the last writer.
    updated_by    TEXT
);
