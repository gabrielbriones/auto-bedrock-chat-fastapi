from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ._autolangchat_imports import load_module

sso_handler_mod = load_module("autolangchat.sso.sso_handler", "sso/sso_handler.py")
SSOProvider = sso_handler_mod.SSOProvider
SSODiscoveryError = sso_handler_mod.SSODiscoveryError
SSOTokenError = sso_handler_mod.SSOTokenError
SSOValidationError = sso_handler_mod.SSOValidationError


def _make_config(**overrides):
    defaults = {
        "sso_enabled": True,
        "sso_client_id": "test-client-id",
        "sso_client_secret": "test-client-secret",
        "sso_session_secret": "test-session-secret",
        "sso_discovery_url": "https://idp.example.com/.well-known/openid-configuration",
        "sso_authorization_url": None,
        "sso_token_url": None,
        "sso_userinfo_url": None,
        "sso_jwks_url": None,
        "sso_scopes": "openid profile email",
        "sso_callback_path": "/chat/auth/callback",
        "sso_provider": None,
        "api_base_url": "https://app.example.com",
    }
    defaults.update(overrides)
    config = MagicMock()
    for key, value in defaults.items():
        setattr(config, key, value)
    return config


_DISCOVERY_DOC = {
    "authorization_endpoint": "https://idp.example.com/authorize",
    "token_endpoint": "https://idp.example.com/token",
    "userinfo_endpoint": "https://idp.example.com/userinfo",
    "jwks_uri": "https://idp.example.com/jwks",
    "issuer": "https://idp.example.com",
}


class TestDiscover:
    @pytest.mark.asyncio
    async def test_discover_resolves_endpoints(self):
        provider = SSOProvider(_make_config())

        mock_response = MagicMock()
        mock_response.json.return_value = _DISCOVERY_DOC
        mock_response.raise_for_status = MagicMock()

        with patch.object(sso_handler_mod.httpx, "AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await provider.discover()

        assert provider._authorization_endpoint == "https://idp.example.com/authorize"
        assert provider._token_endpoint == "https://idp.example.com/token"
        assert provider._userinfo_endpoint == "https://idp.example.com/userinfo"
        assert provider._jwks_uri == "https://idp.example.com/jwks"

    @pytest.mark.asyncio
    async def test_discover_raises_on_network_error(self):
        import httpx

        provider = SSOProvider(_make_config())

        with patch.object(sso_handler_mod.httpx, "AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client_cls.return_value = mock_client

            with pytest.raises(SSODiscoveryError, match="Failed to fetch"):
                await provider.discover()


class TestManualUrlOverrides:
    def test_manual_auth_url_overrides_discovered(self):
        provider = SSOProvider(_make_config(sso_authorization_url="https://manual.example.com/authorize"))
        provider._resolve_endpoints(discovered=_DISCOVERY_DOC)
        assert provider._authorization_endpoint == "https://manual.example.com/authorize"

    def test_manual_token_url_overrides_discovered(self):
        provider = SSOProvider(_make_config(sso_token_url="https://manual.example.com/token"))
        provider._resolve_endpoints(discovered=_DISCOVERY_DOC)
        assert provider._token_endpoint == "https://manual.example.com/token"


class TestPublicEndpointProperties:
    """Public read-only accessors for resolved endpoints.

    Added so the MCP discovery routes (``mcp/discovery.py``) can build RFC
    8414/9728 metadata without reaching into ``SSOProvider`` internals.
    """

    def test_properties_are_none_before_resolution(self):
        provider = SSOProvider(_make_config())
        assert provider.issuer is None
        assert provider.authorization_endpoint is None
        assert provider.token_endpoint is None
        assert provider.jwks_uri is None

    def test_properties_reflect_resolved_endpoints(self):
        provider = SSOProvider(_make_config())
        provider._resolve_endpoints(discovered=_DISCOVERY_DOC)

        assert provider.issuer == "https://idp.example.com"
        assert provider.authorization_endpoint == "https://idp.example.com/authorize"
        assert provider.token_endpoint == "https://idp.example.com/token"
        assert provider.jwks_uri == "https://idp.example.com/jwks"


class TestBuildAuthorizationUrl:
    def test_build_authorization_url_includes_pkce_parameters(self):
        provider = SSOProvider(_make_config())
        provider._resolve_endpoints(discovered=_DISCOVERY_DOC)

        url, verifier = provider.build_authorization_url(state="state-123", code_verifier="verifier-xyz")

        assert url.startswith("https://idp.example.com/authorize?")
        assert "response_type=code" in url
        assert "client_id=test-client-id" in url
        assert "state=state-123" in url
        assert verifier == "verifier-xyz"


class TestValidationErrors:
    def test_build_authorization_url_requires_endpoint(self):
        provider = SSOProvider(_make_config(sso_authorization_url=None, sso_discovery_url=None))

        with pytest.raises(SSODiscoveryError):
            provider.build_authorization_url(state="abc")
