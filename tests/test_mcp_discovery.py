"""Tests for MCP OAuth discovery metadata endpoints.

Verifies ``autolangchat/mcp/discovery.py`` builds the RFC 9728
``/.well-known/oauth-protected-resource`` and RFC 8414
``/.well-known/oauth-authorization-server`` routes correctly, including the
fallback issuer resolution when the IdP's discovery document doesn't
include an explicit ``issuer`` claim.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

mcp = pytest.importorskip("mcp", reason="mcp SDK not installed (optional [mcp] extra)")

from starlette.applications import Starlette  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from autolangchat.mcp.discovery import (  # noqa: E402
    build_authorization_server_metadata_route,
    build_protected_resource_routes,
)


class _FakeSSOProvider:
    def __init__(self, *, issuer=None, authorization_endpoint=None, token_endpoint=None, discover_error=None):
        self.issuer = issuer
        self.authorization_endpoint = authorization_endpoint
        self.token_endpoint = token_endpoint
        self._discover_error = discover_error
        self.discover = AsyncMock(side_effect=discover_error) if discover_error else AsyncMock(return_value=None)


def _make_config(**overrides):
    defaults = {
        "sso_discovery_url": None,
        "sso_authorization_url": None,
        "sso_token_url": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestBuildProtectedResourceRoutes:
    def test_well_known_path_derived_from_resource_url(self):
        provider = _FakeSSOProvider(issuer="https://idp.example.com")
        routes = build_protected_resource_routes("https://app.example.com/chat/mcp", provider, _make_config())

        assert len(routes) == 1
        assert routes[0].path == "/.well-known/oauth-protected-resource/chat/mcp"

    def test_returns_resource_and_authorization_servers(self):
        provider = _FakeSSOProvider(issuer="https://idp.example.com")
        routes = build_protected_resource_routes("https://app.example.com/chat/mcp", provider, _make_config())
        app = Starlette(routes=routes)
        client = TestClient(app)

        response = client.get("/.well-known/oauth-protected-resource/chat/mcp")

        assert response.status_code == 200
        body = response.json()
        assert body["resource"] == "https://app.example.com/chat/mcp"
        assert body["authorization_servers"] == ["https://idp.example.com/"]
        provider.discover.assert_awaited_once()

    def test_falls_back_to_manual_url_origin_when_issuer_missing(self):
        provider = _FakeSSOProvider(issuer=None)
        config = _make_config(sso_authorization_url="https://idp.example.com/oauth2/authorize")
        routes = build_protected_resource_routes("https://app.example.com/chat/mcp", provider, config)
        app = Starlette(routes=routes)
        client = TestClient(app)

        response = client.get("/.well-known/oauth-protected-resource/chat/mcp")

        assert response.status_code == 200
        assert response.json()["authorization_servers"] == ["https://idp.example.com/"]

    def test_returns_503_when_no_issuer_resolvable(self):
        provider = _FakeSSOProvider(issuer=None)
        routes = build_protected_resource_routes("https://app.example.com/chat/mcp", provider, _make_config())
        app = Starlette(routes=routes)
        client = TestClient(app)

        response = client.get("/.well-known/oauth-protected-resource/chat/mcp")

        assert response.status_code == 503

    def test_returns_503_when_discovery_fails(self):
        provider = _FakeSSOProvider(discover_error=RuntimeError("network down"))
        routes = build_protected_resource_routes("https://app.example.com/chat/mcp", provider, _make_config())
        app = Starlette(routes=routes)
        client = TestClient(app)

        response = client.get("/.well-known/oauth-protected-resource/chat/mcp")

        assert response.status_code == 503


class TestBuildAuthorizationServerMetadataRoute:
    def test_returns_rfc8414_shaped_metadata(self):
        provider = _FakeSSOProvider(
            issuer="https://idp.example.com",
            authorization_endpoint="https://idp.example.com/authorize",
            token_endpoint="https://idp.example.com/token",
        )
        route = build_authorization_server_metadata_route(provider, _make_config())
        app = Starlette(routes=[route])
        client = TestClient(app)

        response = client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        body = response.json()
        assert body["issuer"] == "https://idp.example.com/"
        assert body["authorization_endpoint"] == "https://idp.example.com/authorize"
        assert body["token_endpoint"] == "https://idp.example.com/token"
        assert body["code_challenge_methods_supported"] == ["S256"]
        provider.discover.assert_awaited_once()

    def test_returns_503_when_endpoints_not_resolved(self):
        provider = _FakeSSOProvider(issuer=None, authorization_endpoint=None, token_endpoint=None)
        route = build_authorization_server_metadata_route(provider, _make_config())
        app = Starlette(routes=[route])
        client = TestClient(app)

        response = client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 503

    def test_returns_503_when_discovery_fails(self):
        provider = _FakeSSOProvider(discover_error=RuntimeError("network down"))
        route = build_authorization_server_metadata_route(provider, _make_config())
        app = Starlette(routes=[route])
        client = TestClient(app)

        response = client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 503
