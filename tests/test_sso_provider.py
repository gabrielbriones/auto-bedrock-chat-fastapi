import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "autolangchat"


def _install_package_stubs():
    package = types.ModuleType("autolangchat")
    package.__path__ = [str(PACKAGE_ROOT)]
    sso_pkg = types.ModuleType("autolangchat.sso")
    sso_pkg.__path__ = [str(PACKAGE_ROOT / "sso")]
    return {"autolangchat": package, "autolangchat.sso": sso_pkg}


def _load_sso_handler_module():
    module_name = "autolangchat.sso.sso_handler"
    module_path = PACKAGE_ROOT / "sso" / "sso_handler.py"
    installed = _install_package_stubs()
    original = {name: sys.modules.get(name) for name in installed}
    try:
        sys.modules.update(installed)
        spec = spec_from_file_location(module_name, module_path)
        module = module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in original.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


sso_handler_mod = _load_sso_handler_module()
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
