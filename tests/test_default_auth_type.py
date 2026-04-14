"""Unit tests for default_auth_type configuration and UI behavior."""

import os
from unittest.mock import patch

import pytest

from auto_bedrock_chat_fastapi.config import ChatConfig

_BASE_ENV = {
    "BEDROCK_MODEL_ID": "test-model",
}


def _make_config(extra=None):
    env = {**_BASE_ENV, **(extra or {})}
    with patch.dict(os.environ, env, clear=False):
        return ChatConfig(
            _env_file=None,
            **{k.lower(): v for k, v in env.items() if k.startswith("BEDROCK_")},
        )


class TestDefaultAuthTypeConfig:
    """Tests for the default_auth_type configuration field."""

    def test_default_is_none(self):
        """default_auth_type is None when not set."""
        config = _make_config()
        assert config.default_auth_type is None

    def test_loads_from_env(self):
        """default_auth_type loads from BEDROCK_DEFAULT_AUTH_TYPE env var."""
        config = _make_config({"BEDROCK_DEFAULT_AUTH_TYPE": "bearer_token"})
        assert config.default_auth_type == "bearer_token"

    def test_valid_type_accepted(self):
        """Accepts a default_auth_type that is in supported_auth_types."""
        config = _make_config(
            {
                "BEDROCK_DEFAULT_AUTH_TYPE": "basic_auth",
                "BEDROCK_SUPPORTED_AUTH_TYPES": '["bearer_token", "basic_auth"]',
            }
        )
        assert config.default_auth_type == "basic_auth"

    def test_sso_as_default(self):
        """sso can be set as default_auth_type."""
        config = _make_config({"BEDROCK_DEFAULT_AUTH_TYPE": "sso"})
        assert config.default_auth_type == "sso"

    def test_invalid_type_rejected(self):
        """Raises ValueError when default_auth_type is not in supported_auth_types."""
        with pytest.raises(ValueError, match="not in supported_auth_types"):
            _make_config(
                {
                    "BEDROCK_DEFAULT_AUTH_TYPE": "not_a_real_type",
                    "BEDROCK_SUPPORTED_AUTH_TYPES": '["bearer_token", "basic_auth"]',
                }
            )

    def test_type_not_in_custom_supported_list(self):
        """Raises ValueError when default is a real type but not in the custom supported list."""
        with pytest.raises(ValueError, match="not in supported_auth_types"):
            _make_config(
                {
                    "BEDROCK_DEFAULT_AUTH_TYPE": "api_key",
                    "BEDROCK_SUPPORTED_AUTH_TYPES": '["bearer_token"]',
                }
            )

    def test_kwarg_override(self):
        """default_auth_type can be set as a kwarg (e.g. via add_bedrock_chat)."""
        with patch.dict(os.environ, _BASE_ENV, clear=False):
            config = ChatConfig(
                _env_file=None,
                BEDROCK_MODEL_ID="test-model",
                BEDROCK_DEFAULT_AUTH_TYPE="api_key",
            )
        assert config.default_auth_type == "api_key"
