"""Tests for ``AutoLangChatPlugin._setup_mcp_routes`` wiring.

Uses the same bare-plugin construction pattern as
``test_admin_capabilities.py`` (``object.__new__(AutoLangChatPlugin)`` +
only the attributes ``_setup_mcp_routes`` touches), so these tests exercise
the real route-mounting code in ``plugin.py`` rather than a hand-rolled app.
Requires the optional ``mcp`` extra — skipped automatically if not installed.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed (optional [mcp] extra)")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class _FakeSessionManager:
    async def handle_request(self, scope, receive, send):  # pragma: no cover - not exercised here
        raise NotImplementedError


class _FakeSSOProvider:
    def __init__(self):
        self.issuer = "https://idp.example.com"
        self.authorization_endpoint = "https://idp.example.com/authorize"
        self.token_endpoint = "https://idp.example.com/token"
        self.discover = AsyncMock(return_value=None)


def _make_config(*, mcp_enabled=True, sso_enabled=False, base_urls_configured=True):
    return SimpleNamespace(
        mcp_endpoint="/chat/mcp",
        mcp_enabled=mcp_enabled,
        sso_enabled=sso_enabled,
        sso_public_base_url="https://app.example.com" if base_urls_configured else None,
        api_base_url="https://app.example.com" if base_urls_configured else None,
        sso_authorization_url=None,
        sso_discovery_url=None,
        sso_token_url=None,
    )


def _make_bare_plugin(*, sso_enabled, base_urls_configured=True):
    from autolangchat.plugin import AutoLangChatPlugin, _load_mcp_imports

    _load_mcp_imports()

    plugin = object.__new__(AutoLangChatPlugin)
    plugin.app = FastAPI()
    plugin.config = _make_config(sso_enabled=sso_enabled, base_urls_configured=base_urls_configured)
    plugin._mcp_session_manager = _FakeSessionManager()
    plugin.sso_provider = _FakeSSOProvider() if sso_enabled else None
    plugin._setup_mcp_routes()
    return plugin


def test_mcp_endpoint_mounted():
    plugin = _make_bare_plugin(sso_enabled=False)

    mounts = [r for r in plugin.app.router.routes if getattr(r, "path", None) == "/chat/mcp"]
    assert len(mounts) == 1


def test_well_known_routes_not_mounted_when_sso_disabled():
    plugin = _make_bare_plugin(sso_enabled=False)

    paths = {getattr(r, "path", None) for r in plugin.app.router.routes}
    assert not any(p and "well-known" in p for p in paths)


def test_well_known_routes_mounted_when_sso_enabled():
    plugin = _make_bare_plugin(sso_enabled=True)

    paths = {getattr(r, "path", None) for r in plugin.app.router.routes}
    assert "/.well-known/oauth-protected-resource/chat/mcp" in paths
    assert "/.well-known/oauth-authorization-server" in paths


def test_protected_resource_metadata_served_correctly():
    plugin = _make_bare_plugin(sso_enabled=True)
    client = TestClient(plugin.app)

    response = client.get("/.well-known/oauth-protected-resource/chat/mcp")

    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == "https://app.example.com/chat/mcp"
    assert body["authorization_servers"] == ["https://idp.example.com/"]


def test_authorization_server_metadata_served_correctly():
    plugin = _make_bare_plugin(sso_enabled=True)
    client = TestClient(plugin.app)

    response = client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    body = response.json()
    assert body["issuer"] == "https://idp.example.com/"
    assert body["authorization_endpoint"] == "https://idp.example.com/authorize"


def test_no_base_url_configured_skips_protected_resource_route_without_crashing():
    """Regression test: previously ``AnyHttpUrl('')  + mcp_endpoint`` raised a
    pydantic ValidationError inside ``_setup_mcp_routes`` (called synchronously
    from ``__init__``), crashing plugin construction whenever neither
    ``sso_public_base_url`` nor ``api_base_url`` was configured. Must now skip
    the protected-resource route gracefully; the AS-metadata route (which
    doesn't depend on this app's own URL) still mounts.
    """
    plugin = _make_bare_plugin(sso_enabled=True, base_urls_configured=False)

    paths = {getattr(r, "path", None) for r in plugin.app.router.routes}
    assert not any(p and "oauth-protected-resource" in p for p in paths)
    assert "/.well-known/oauth-authorization-server" in paths
