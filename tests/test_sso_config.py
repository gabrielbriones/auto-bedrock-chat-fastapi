"""Unit tests for SSO configuration settings in ChatConfig"""

import os
from typing import Optional
from unittest.mock import patch

import pytest

from auto_bedrock_chat_fastapi.auth_handler import DEFAULT_SUPPORTED_AUTH_TYPES
from auto_bedrock_chat_fastapi.config import ChatConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Base env vars that satisfy the non-SSO required validators (ChatConfig
# validates model_id etc.) so we can focus on SSO fields.
_BASE_ENV = {
    "BEDROCK_MODEL_ID": "test-model",
}

# Minimal valid SSO env vars (using discovery URL)
_SSO_DISCOVERY_ENV = {
    **_BASE_ENV,
    "BEDROCK_SSO_ENABLED": "true",
    "BEDROCK_SSO_CLIENT_ID": "my-client-id",
    "BEDROCK_SSO_SESSION_SECRET": "super-secret-key-for-signing",
    "BEDROCK_SSO_DISCOVERY_URL": "https://idp.example.com/.well-known/openid-configuration",
}

# Minimal valid SSO env vars (using manual URLs)
_SSO_MANUAL_ENV = {
    **_BASE_ENV,
    "BEDROCK_SSO_ENABLED": "true",
    "BEDROCK_SSO_CLIENT_ID": "my-client-id",
    "BEDROCK_SSO_SESSION_SECRET": "super-secret-key-for-signing",
    "BEDROCK_SSO_AUTHORIZATION_URL": "https://idp.example.com/authorize",
    "BEDROCK_SSO_TOKEN_URL": "https://idp.example.com/token",
}


def _make_config(env_overrides: Optional[dict] = None) -> ChatConfig:
    """Create a ChatConfig with the given env overrides, no .env file."""
    env = {**_BASE_ENV, **(env_overrides or {})}
    with patch.dict(os.environ, env, clear=False):
        return ChatConfig(
            _env_file=None, **{k.lower(): v for k, v in env.items() if k.startswith("BEDROCK_") or k.startswith("AWS_")}
        )


def _make_sso_config(extra: Optional[dict] = None, use_discovery: bool = True) -> ChatConfig:
    """Shortcut to build a valid SSO-enabled config with optional overrides."""
    base = dict(_SSO_DISCOVERY_ENV) if use_discovery else dict(_SSO_MANUAL_ENV)
    if extra:
        base.update(extra)
    return _make_config(base)


# ---------------------------------------------------------------------------
# Tests: Default values
# ---------------------------------------------------------------------------


class TestSSOConfigDefaults:
    """SSO fields have correct defaults when SSO is disabled."""

    def test_sso_disabled_by_default(self):
        config = _make_config()
        assert config.sso_enabled is False

    def test_sso_provider_default_none(self):
        config = _make_config()
        assert config.sso_provider is None

    def test_sso_client_id_default_none(self):
        config = _make_config()
        assert config.sso_client_id is None

    def test_sso_client_secret_default_none(self):
        config = _make_config()
        assert config.sso_client_secret is None

    def test_sso_discovery_url_default_none(self):
        config = _make_config()
        assert config.sso_discovery_url is None

    def test_sso_authorization_url_default_none(self):
        config = _make_config()
        assert config.sso_authorization_url is None

    def test_sso_token_url_default_none(self):
        config = _make_config()
        assert config.sso_token_url is None

    def test_sso_userinfo_url_default_none(self):
        config = _make_config()
        assert config.sso_userinfo_url is None

    def test_sso_jwks_url_default_none(self):
        config = _make_config()
        assert config.sso_jwks_url is None

    def test_sso_scopes_default(self):
        config = _make_config()
        assert config.sso_scopes == "openid profile email"

    def test_sso_callback_path_default(self):
        config = _make_config()
        assert config.sso_callback_path == "/chat/auth/callback"

    def test_sso_session_secret_default_none(self):
        config = _make_config()
        assert config.sso_session_secret is None

    def test_sso_session_ttl_default(self):
        config = _make_config()
        assert config.sso_session_ttl == 3600


# ---------------------------------------------------------------------------
# Tests: Loading fields from env
# ---------------------------------------------------------------------------


class TestSSOConfigFromEnv:
    """SSO fields load correctly from environment variables."""

    def test_loads_all_sso_fields(self):
        env = {
            **_SSO_DISCOVERY_ENV,
            "BEDROCK_SSO_PROVIDER": "okta",
            "BEDROCK_SSO_CLIENT_SECRET": "client-secret-123",
            "BEDROCK_SSO_AUTHORIZATION_URL": "https://okta.example.com/authorize",
            "BEDROCK_SSO_TOKEN_URL": "https://okta.example.com/token",
            "BEDROCK_SSO_USERINFO_URL": "https://okta.example.com/userinfo",
            "BEDROCK_SSO_JWKS_URL": "https://okta.example.com/jwks",
            "BEDROCK_SSO_SCOPES": "openid profile email groups",
            "BEDROCK_SSO_CALLBACK_PATH": "/custom/callback",
            "BEDROCK_SSO_SESSION_TTL": "7200",
        }
        config = _make_config(env)

        assert config.sso_enabled is True
        assert config.sso_provider == "okta"
        assert config.sso_client_id == "my-client-id"
        assert config.sso_client_secret == "client-secret-123"
        assert config.sso_discovery_url == "https://idp.example.com/.well-known/openid-configuration"
        assert config.sso_authorization_url == "https://okta.example.com/authorize"
        assert config.sso_token_url == "https://okta.example.com/token"
        assert config.sso_userinfo_url == "https://okta.example.com/userinfo"
        assert config.sso_jwks_url == "https://okta.example.com/jwks"
        assert config.sso_scopes == "openid profile email groups"
        assert config.sso_callback_path == "/custom/callback"
        assert config.sso_session_ttl == 7200


# ---------------------------------------------------------------------------
# Tests: Validation — SSO enabled requires certain fields
# ---------------------------------------------------------------------------


class TestSSOConfigValidation:
    """Validation fires on invalid SSO configurations."""

    def test_error_when_sso_enabled_no_discovery_no_manual_urls(self):
        """Must provide discovery URL or manual auth + token URLs."""
        env = {
            **_BASE_ENV,
            "BEDROCK_SSO_ENABLED": "true",
            "BEDROCK_SSO_CLIENT_ID": "my-client-id",
            "BEDROCK_SSO_SESSION_SECRET": "secret",
        }
        with pytest.raises(ValueError, match="sso_discovery_url.*sso_authorization_url.*sso_token_url"):
            _make_config(env)

    def test_error_when_sso_enabled_only_authorization_url(self):
        """Manual mode requires BOTH authorization and token URLs."""
        env = {
            **_BASE_ENV,
            "BEDROCK_SSO_ENABLED": "true",
            "BEDROCK_SSO_CLIENT_ID": "my-client-id",
            "BEDROCK_SSO_SESSION_SECRET": "secret",
            "BEDROCK_SSO_AUTHORIZATION_URL": "https://idp.example.com/authorize",
        }
        with pytest.raises(ValueError, match="sso_token_url"):
            _make_config(env)

    def test_error_when_sso_enabled_only_token_url(self):
        """Manual mode requires BOTH authorization and token URLs."""
        env = {
            **_BASE_ENV,
            "BEDROCK_SSO_ENABLED": "true",
            "BEDROCK_SSO_CLIENT_ID": "my-client-id",
            "BEDROCK_SSO_SESSION_SECRET": "secret",
            "BEDROCK_SSO_TOKEN_URL": "https://idp.example.com/token",
        }
        with pytest.raises(ValueError, match="sso_authorization_url"):
            _make_config(env)

    def test_error_when_sso_enabled_no_client_id(self):
        """sso_client_id is required when SSO is enabled."""
        env = {
            **_BASE_ENV,
            "BEDROCK_SSO_ENABLED": "true",
            "BEDROCK_SSO_SESSION_SECRET": "secret",
            "BEDROCK_SSO_DISCOVERY_URL": "https://idp.example.com/.well-known/openid-configuration",
        }
        with pytest.raises(ValueError, match="sso_client_id"):
            _make_config(env)

    def test_error_when_sso_enabled_no_session_secret(self):
        """sso_session_secret is required when SSO is enabled."""
        env = {
            **_BASE_ENV,
            "BEDROCK_SSO_ENABLED": "true",
            "BEDROCK_SSO_CLIENT_ID": "my-client-id",
            "BEDROCK_SSO_DISCOVERY_URL": "https://idp.example.com/.well-known/openid-configuration",
        }
        with pytest.raises(ValueError, match="sso_session_secret"):
            _make_config(env)

    def test_valid_with_discovery_url_only(self):
        """SSO is valid with just discovery URL (+ client_id + session_secret)."""
        config = _make_sso_config(use_discovery=True)
        assert config.sso_enabled is True
        assert config.sso_discovery_url is not None

    def test_valid_with_manual_auth_and_token_urls(self):
        """SSO is valid with manual auth + token URLs (no discovery)."""
        config = _make_sso_config(use_discovery=False)
        assert config.sso_enabled is True
        assert config.sso_authorization_url is not None
        assert config.sso_token_url is not None

    def test_valid_with_discovery_and_manual_overrides(self):
        """Both discovery URL and manual overrides can coexist."""
        config = _make_sso_config(
            extra={
                "BEDROCK_SSO_AUTHORIZATION_URL": "https://idp.example.com/custom-authorize",
                "BEDROCK_SSO_TOKEN_URL": "https://idp.example.com/custom-token",
            },
            use_discovery=True,
        )
        assert config.sso_discovery_url is not None
        assert config.sso_authorization_url == "https://idp.example.com/custom-authorize"

    def test_no_validation_when_sso_disabled(self):
        """When sso_enabled=False, no SSO validation runs even if fields are missing."""
        config = _make_config()
        assert config.sso_enabled is False
        assert config.sso_client_id is None  # No error


# ---------------------------------------------------------------------------
# Tests: SSO provider validation
# ---------------------------------------------------------------------------


class TestSSOProviderValidation:
    """SSO provider field accepts only known values."""

    @pytest.mark.parametrize("provider", ["okta", "azure_ad", "auth0", "keycloak", "generic"])
    def test_valid_providers(self, provider):
        config = _make_sso_config(extra={"BEDROCK_SSO_PROVIDER": provider})
        assert config.sso_provider == provider

    def test_provider_normalized_to_lowercase(self):
        config = _make_sso_config(extra={"BEDROCK_SSO_PROVIDER": "Okta"})
        assert config.sso_provider == "okta"

    def test_invalid_provider_rejected(self):
        with pytest.raises(ValueError, match="sso_provider must be one of"):
            _make_sso_config(extra={"BEDROCK_SSO_PROVIDER": "unsupported_idp"})

    def test_provider_none_is_valid(self):
        config = _make_sso_config()
        assert config.sso_provider is None


# ---------------------------------------------------------------------------
# Tests: SSO in supported auth types
# ---------------------------------------------------------------------------


class TestSSOInSupportedAuthTypes:
    """SSO is included in the default supported auth types."""

    def test_sso_in_default_supported_auth_types(self):
        assert "sso" in DEFAULT_SUPPORTED_AUTH_TYPES

    def test_sso_in_config_supported_auth_types(self):
        config = _make_config()
        assert "sso" in config.supported_auth_types

    def test_all_original_auth_types_still_present(self):
        """Adding SSO doesn't remove existing auth types."""
        expected = {"bearer_token", "basic_auth", "api_key", "oauth2", "custom", "sso"}
        assert expected == set(DEFAULT_SUPPORTED_AUTH_TYPES)
