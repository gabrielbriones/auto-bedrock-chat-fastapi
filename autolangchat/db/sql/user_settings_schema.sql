-- ---------------------------------------------------------------------------
-- Per-User Settings Storage
--
-- Schema for the `user_settings` table that backs UserSettingsStore.
--
-- One row per user, keyed by the canonical identity the plugin already
-- resolves (`extract_user_id_from_sso_session` for SSO, `verified_user_info`
-- for tool-auth — i.e. `session.user_id`). The row holds that user's chat
-- configuration overrides (model_id, temperature, max_tokens, ...) so the
-- Settings sidebar survives refresh, reconnect, new sessions and redeploys.
--
-- `settings` is deliberately an opaque JSONB document rather than a typed,
-- relational schema: the meaningful parameter set varies per model (some
-- models reject `temperature`, output-token caps differ, future models may
-- expose new knobs). Validation happens at write time through
-- `ChatConfig.validate_overrides()`, and unknown/stale keys read back from
-- the document are ignored rather than fatal.
--
-- This file is the canonical DDL artifact:
--   * The database-provisioning task can apply it directly with `psql`.
--   * `PostgresUserSettingsStore._apply_schema()` reads and executes this
--     file at startup so dev/test environments self-bootstrap (mirrors
--     `conversation_schema.sql` / `token_usage_schema.sql`).
--
-- All statements are idempotent (`IF NOT EXISTS`) and safe to re-run.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_settings (
    -- The canonical user identity (`session.user_id`). Unique primary key:
    -- get-or-create relies on `ON CONFLICT (user_id) DO NOTHING` being
    -- race safe when the same user opens several tabs at once.
    user_id     TEXT PRIMARY KEY,

    settings    JSONB       NOT NULL DEFAULT '{}'::jsonb,

    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);
