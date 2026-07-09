"""Phase 1 — AUTOCHAT_* environment variable tests.

Verifies that ChatConfig reads from AUTOCHAT_* env vars (not BEDROCK_*),
and that old BEDROCK_* vars are ignored.
"""

from __future__ import annotations

import os
from unittest.mock import patch


def _load_config(**env_overrides):
    """Import-safe helper: load ChatConfig with a clean env patch."""
    base_env = {
        # Minimal required var so pydantic-settings is happy
        "AUTOCHAT_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
    }
    base_env.update(env_overrides)
    with patch.dict(os.environ, base_env, clear=True):
        # Force re-import so pydantic-settings picks up the patched env
        from importlib import import_module, reload

        import autolangchat.config as cfg_mod

        reload(cfg_mod)
        return cfg_mod.ChatConfig()


class TestAutochatEnvVarPrefix:
    """AUTOCHAT_* vars are read; BEDROCK_* vars are ignored."""

    def test_model_id_from_autochat_var(self):
        config = _load_config(AUTOCHAT_MODEL_ID="us.anthropic.claude-3-haiku")
        assert config.model_id == "us.anthropic.claude-3-haiku"

    def test_bedrock_model_id_ignored(self):
        """BEDROCK_MODEL_ID must have no effect — config uses AUTOCHAT_MODEL_ID."""
        config = _load_config(
            AUTOCHAT_MODEL_ID="us.anthropic.claude-sonnet-4-6",
            BEDROCK_MODEL_ID="should-be-ignored",
        )
        assert config.model_id == "us.anthropic.claude-sonnet-4-6"

    def test_temperature_from_autochat_var(self):
        config = _load_config(AUTOCHAT_TEMPERATURE="0.3")
        assert abs(config.temperature - 0.3) < 1e-6

    def test_bedrock_temperature_ignored(self):
        config = _load_config(
            AUTOCHAT_TEMPERATURE="0.5",
            BEDROCK_TEMPERATURE="0.9",
        )
        assert abs(config.temperature - 0.5) < 1e-6

    def test_max_tokens_from_autochat_var(self):
        config = _load_config(AUTOCHAT_MAX_TOKENS="2048")
        assert config.max_tokens == 2048

    def test_log_level_from_autochat_var(self):
        config = _load_config(AUTOCHAT_LOG_LEVEL="DEBUG")
        assert config.log_level.upper() == "DEBUG"

    def test_system_prompt_from_autochat_var(self):
        config = _load_config(AUTOCHAT_SYSTEM_PROMPT="You are a helpful assistant.")
        assert config.system_prompt == "You are a helpful assistant."

    def test_feedback_enabled_from_autochat_var(self):
        config = _load_config(AUTOCHAT_FEEDBACK_ENABLED="true")
        assert config.feedback_enabled is True

    def test_token_usage_enabled_defaults_to_false(self):
        """token_usage_enabled is opt-in (default off) so existing deployments
        don't get surprise writes (XMGPLAT-10746)."""
        config = _load_config()
        assert config.token_usage_enabled is False

    def test_token_usage_enabled_from_autochat_var(self):
        config = _load_config(AUTOCHAT_TOKEN_USAGE_ENABLED="true")
        assert config.token_usage_enabled is True

    def test_token_usage_storage_type_defaults_to_sqlite(self):
        config = _load_config()
        assert config.token_usage_storage_type == "sqlite"

    def test_token_usage_database_path_from_autochat_var(self):
        config = _load_config(AUTOCHAT_TOKEN_USAGE_DATABASE_PATH="/tmp/token_usage.db")
        assert config.token_usage_database_path == "/tmp/token_usage.db"

    def test_token_usage_postgres_url_from_autochat_var(self):
        config = _load_config(AUTOCHAT_TOKEN_USAGE_POSTGRES_URL="postgresql://x/db")
        assert config.token_usage_postgres_url == "postgresql://x/db"

    def test_admin_enabled_from_autochat_var(self):
        config = _load_config(AUTOCHAT_ADMIN_ENABLED="true")
        assert config.admin_enabled is True

    def test_kb_storage_type_from_autochat_var(self):
        config = _load_config(AUTOCHAT_KB_STORAGE_TYPE="sqlite")
        assert config.kb_storage_type == "sqlite"

    def test_enable_rag_still_uses_unprefixed_name(self):
        """ENABLE_RAG keeps its name (no AUTOCHAT_ prefix) by design."""
        config = _load_config(ENABLE_RAG="true")
        assert config.enable_rag is True

    def test_llm_client_type_field_removed(self):
        """llm_client_type was removed; setting AUTOCHAT_LLM_CLIENT_TYPE has no effect."""
        config = _load_config(AUTOCHAT_LLM_CLIENT_TYPE="bedrock")
        assert not hasattr(config, "llm_client_type")

    def test_max_tool_calls_from_autochat_var(self):
        config = _load_config(AUTOCHAT_MAX_TOOL_CALLS="5")
        assert config.max_tool_calls == 5

    def test_session_timeout_from_autochat_var(self):
        config = _load_config(AUTOCHAT_SESSION_TIMEOUT="7200")
        assert config.session_timeout == 7200

    def test_aws_region_default(self):
        """AWS region has a sensible default when not set."""
        config = _load_config()
        assert config.aws_region  # non-empty string

    def test_fallback_model_from_autochat_var(self):
        config = _load_config(AUTOCHAT_FALLBACK_MODEL="us.anthropic.claude-3-haiku")
        assert config.fallback_model == "us.anthropic.claude-3-haiku"

    def test_bedrock_fallback_model_ignored(self):
        config = _load_config(
            AUTOCHAT_FALLBACK_MODEL="correct-fallback",
            BEDROCK_FALLBACK_MODEL="should-be-ignored",
        )
        assert config.fallback_model == "correct-fallback"
