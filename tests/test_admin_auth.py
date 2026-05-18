"""Tests for the Expert Review Admin API authorization layer (T1)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auto_bedrock_chat_fastapi.admin_auth import (
    AdminIdentity,
    DenyAllAdminAuthorizer,
    RemoteAdminAuthorizer,
    SSOGroupAdminAuthorizer,
    build_admin_authorizer,
    resolve_admin_identity_from_auth_endpoint,
    resolve_admin_identity_from_sso_session,
)
from auto_bedrock_chat_fastapi.plugin import BedrockChatPlugin
from auto_bedrock_chat_fastapi.sso_session_store import SSOSessionStore

_SESSION_SECRET = "admin-test-session-secret-1234567890"


# ---------------------------------------------------------------------------
# Unit tests: built-in authorizers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deny_all_authorizer_rejects_everyone():
    authz = DenyAllAdminAuthorizer()
    identity = AdminIdentity(user_id="alice@example.com", groups=["everyone", "admins"])
    assert await authz.is_admin(identity) is False


@pytest.mark.asyncio
async def test_sso_group_authorizer_happy_path():
    authz = SSOGroupAdminAuthorizer(required_groups=["bedrock-admins", "platform-admins"])
    identity = AdminIdentity(user_id="alice@example.com", groups=["everyone", "bedrock-admins"])
    assert await authz.is_admin(identity) is True


@pytest.mark.asyncio
async def test_sso_group_authorizer_missing_group():
    authz = SSOGroupAdminAuthorizer(required_groups=["bedrock-admins"])
    identity = AdminIdentity(user_id="alice@example.com", groups=["everyone"])
    assert await authz.is_admin(identity) is False


@pytest.mark.asyncio
async def test_sso_group_authorizer_empty_required_denies():
    authz = SSOGroupAdminAuthorizer(required_groups=[])
    identity = AdminIdentity(user_id="alice@example.com", groups=["anything"])
    assert await authz.is_admin(identity) is False


def _make_mock_transport(responses: List[httpx.Response]) -> httpx.MockTransport:
    """Build a MockTransport that yields ``responses`` one per request."""
    counter = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = counter["i"]
        counter["i"] = i + 1
        if i >= len(responses):
            raise AssertionError(f"Unexpected extra HTTP call #{i + 1}")
        return responses[i]

    transport = httpx.MockTransport(handler)
    transport._counter = counter  # type: ignore[attr-defined]
    return transport


@pytest.mark.asyncio
async def test_remote_authorizer_grants_when_endpoint_returns_true():
    transport = _make_mock_transport([httpx.Response(200, json={"is_admin": True})])
    async with httpx.AsyncClient(transport=transport, base_url="https://host") as client:
        authz = RemoteAdminAuthorizer(
            endpoint_url="https://host/admin/check",
            http_client=client,
        )
        identity = AdminIdentity(user_id="alice@example.com")
        assert await authz.is_admin(identity) is True


@pytest.mark.asyncio
async def test_remote_authorizer_denies_on_non_2xx():
    """Endpoint returns non-2xx → not admin; no caching means a second call re-hits the endpoint."""
    transport = _make_mock_transport(
        [
            httpx.Response(403, json={"is_admin": False}),
            httpx.Response(403, json={"is_admin": False}),
        ]
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://host") as client:
        authz = RemoteAdminAuthorizer(
            endpoint_url="https://host/admin/check",
            http_client=client,
        )
        identity = AdminIdentity(user_id="bob@example.com")
        assert await authz.is_admin(identity) is False
        assert await authz.is_admin(identity) is False
        # No cache: both calls must reach the endpoint.
        assert transport._counter["i"] == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_remote_authorizer_reflects_revocation_on_next_call():
    """Without a cache, a flipped endpoint response is visible on the very next call."""
    transport = _make_mock_transport(
        [
            httpx.Response(200, json={"is_admin": True}),
            httpx.Response(200, json={"is_admin": False}),
        ]
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://host") as client:
        authz = RemoteAdminAuthorizer(
            endpoint_url="https://host/admin/check",
            http_client=client,
        )
        identity = AdminIdentity(user_id="carol@example.com")
        assert await authz.is_admin(identity) is True
        assert await authz.is_admin(identity) is False
        assert transport._counter["i"] == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_remote_authorizer_denies_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://host") as client:
        authz = RemoteAdminAuthorizer(
            endpoint_url="https://host/admin/check",
            http_client=client,
        )
        identity = AdminIdentity(user_id="dave@example.com")
        assert await authz.is_admin(identity) is False


@pytest.mark.asyncio
async def test_remote_authorizer_denies_on_non_dict_body():
    transport = _make_mock_transport([httpx.Response(200, json=["not", "a", "dict"])])
    async with httpx.AsyncClient(transport=transport, base_url="https://host") as client:
        authz = RemoteAdminAuthorizer(
            endpoint_url="https://host/admin/check",
            http_client=client,
        )
        identity = AdminIdentity(user_id="eve@example.com")
        assert await authz.is_admin(identity) is False


def test_remote_authorizer_validates_args():
    with pytest.raises(ValueError):
        RemoteAdminAuthorizer(endpoint_url="")


# ---------------------------------------------------------------------------
# Unit tests: identity resolver + factory
# ---------------------------------------------------------------------------


def test_resolve_identity_prefers_email_groups_from_user_info():
    session = {
        "user_info": {
            "email": "alice@example.com",
            "groups": ["everyone", "bedrock-admins"],
            "sub": "abc-123",
        },
        "id_token_claims": {"sub": "abc-123"},
    }
    identity = resolve_admin_identity_from_sso_session(session)
    assert identity is not None
    assert identity.user_id == "alice@example.com"
    assert identity.email == "alice@example.com"
    assert "bedrock-admins" in identity.groups


def test_resolve_identity_falls_back_to_id_token_groups():
    session = {
        "user_info": {},
        "id_token_claims": {
            "preferred_username": "alice",
            "cognito:groups": ["admins"],
        },
    }
    identity = resolve_admin_identity_from_sso_session(session)
    assert identity is not None
    assert identity.user_id == "alice"
    assert identity.groups == ["admins"]


def test_resolve_identity_returns_none_without_identifier():
    assert resolve_admin_identity_from_sso_session({}) is None
    assert resolve_admin_identity_from_sso_session({"user_info": {}, "id_token_claims": {}}) is None


def test_build_admin_authorizer_picks_remote_when_endpoint_set():
    config = MagicMock()
    config.admin_verification_endpoint = "https://host/admin/check"
    config.admin_required_groups = []
    authz = build_admin_authorizer(config)
    assert isinstance(authz, RemoteAdminAuthorizer)
    assert authz.endpoint_url == "https://host/admin/check"


def test_build_admin_authorizer_picks_group_when_only_groups_set():
    config = MagicMock()
    config.admin_verification_endpoint = None
    config.admin_required_groups = ["admins"]
    authz = build_admin_authorizer(config)
    assert isinstance(authz, SSOGroupAdminAuthorizer)


def test_build_admin_authorizer_resolves_relative_endpoint_against_base_url():
    config = MagicMock()
    config.admin_verification_endpoint = "/admin/check"
    config.admin_required_groups = []
    authz = build_admin_authorizer(config, app_base_url="https://host")
    assert isinstance(authz, RemoteAdminAuthorizer)
    assert authz.endpoint_url == "https://host/admin/check"


def test_build_admin_authorizer_falls_back_to_deny_all():
    config = MagicMock()
    config.admin_verification_endpoint = None
    config.admin_required_groups = []
    authz = build_admin_authorizer(config)
    assert isinstance(authz, DenyAllAdminAuthorizer)


# ---------------------------------------------------------------------------
# Integration tests: require_admin dependency
# ---------------------------------------------------------------------------


class _StubAuthorizer:
    """In-memory authorizer for integration tests."""

    def __init__(self, allowed_user_ids: Optional[set] = None) -> None:
        self.allowed = set(allowed_user_ids or [])
        self.calls = 0

    async def is_admin(self, identity: AdminIdentity) -> bool:
        self.calls += 1
        return identity.user_id in self.allowed


def _make_admin_config(
    *,
    admin_enabled: bool = True,
    sso_enabled: bool = True,
    auth_verification_endpoint: Optional[str] = None,
    supported_auth_types: Optional[list] = None,
) -> MagicMock:
    config = MagicMock()
    config.chat_endpoint = "/bedrock-chat"
    config.admin_enabled = admin_enabled
    config.sso_enabled = sso_enabled
    config.sso_session_secret = _SESSION_SECRET
    config.sso_session_ttl = 3600
    config.auth_verification_endpoint = auth_verification_endpoint
    # Default to the full set so existing tests advertise every scheme.
    config.supported_auth_types = (
        list(supported_auth_types)
        if supported_auth_types is not None
        else ["bearer_token", "basic_auth", "api_key", "oauth2", "oauth2_client_credentials", "sso", "custom"]
    )
    return config


def _build_admin_test_app(
    authorizer: _StubAuthorizer,
    *,
    sso_session_store: Optional[SSOSessionStore] = None,
    admin_enabled: bool = True,
    sso_enabled: bool = True,
    auth_verification_endpoint: Optional[str] = None,
    supported_auth_types: Optional[list] = None,
) -> tuple[FastAPI, "BedrockChatPlugin", SSOSessionStore]:
    """Construct a minimal plugin instance with only the admin dependency wired.

    Mounts a small ``GET`` and ``POST`` route under ``/bedrock-chat/admin/_probe``
    that depend on ``plugin._require_admin`` so the dependency contract can be
    exercised end-to-end without needing the real T3/T5 routes.
    """
    app = FastAPI()
    plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
    plugin.app = app
    plugin.config = _make_admin_config(
        admin_enabled=admin_enabled,
        sso_enabled=sso_enabled,
        auth_verification_endpoint=auth_verification_endpoint,
        supported_auth_types=supported_auth_types,
    )
    plugin.sso_session_store = sso_session_store or (SSOSessionStore(session_ttl=3600) if sso_enabled else None)
    plugin._admin_authorizer = authorizer
    plugin.app_base_url = "https://app.example.com"
    if admin_enabled:
        plugin._setup_admin_routes()

        @app.get(f"{plugin.config.chat_endpoint}/admin/_probe")
        async def _probe_get(identity: AdminIdentity = Depends(plugin._require_admin)):
            return {"ok": True, "user_id": identity.user_id, "method": "GET"}

        @app.post(f"{plugin.config.chat_endpoint}/admin/_probe")
        async def _probe_post(identity: AdminIdentity = Depends(plugin._require_admin)):
            return {"ok": True, "user_id": identity.user_id, "method": "POST"}

    return app, plugin, plugin.sso_session_store


def _issue_session_cookie(
    store: SSOSessionStore,
    *,
    email: str = "alice@example.com",
    user_info: Optional[Dict[str, Any]] = None,
) -> str:
    user_info = user_info if user_info is not None else {"email": email, "sub": "abc-123"}
    sid = store.create_session(
        tokens={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
        user_info=user_info,
        id_token_claims={"sub": "abc-123", "email": email},
    )
    return store.generate_session_token(sid, _SESSION_SECRET)


def test_admin_returns_401_when_unauthenticated():
    authz = _StubAuthorizer(allowed_user_ids={"alice@example.com"})
    app, plugin, _store = _build_admin_test_app(authz)
    client = TestClient(app)
    resp = client.post("/bedrock-chat/admin/_probe")
    assert resp.status_code == 401
    assert authz.calls == 0


def test_admin_returns_403_for_authenticated_non_admin():
    authz = _StubAuthorizer(allowed_user_ids={"alice@example.com"})
    app, plugin, store = _build_admin_test_app(authz)
    token = _issue_session_cookie(store, email="mallory@example.com")
    client = TestClient(app)
    resp = client.post(
        "/bedrock-chat/admin/_probe",
        cookies={"sso_session_token": token},
    )
    assert resp.status_code == 403
    assert authz.calls == 1


def test_admin_get_succeeds_for_admin():
    authz = _StubAuthorizer(allowed_user_ids={"alice@example.com"})
    app, plugin, store = _build_admin_test_app(authz)
    token = _issue_session_cookie(store, email="alice@example.com")
    client = TestClient(app)
    resp = client.get(
        "/bedrock-chat/admin/_probe",
        cookies={"sso_session_token": token},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "user_id": "alice@example.com", "method": "GET"}


def test_admin_post_succeeds_for_admin():
    authz = _StubAuthorizer(allowed_user_ids={"alice@example.com"})
    app, plugin, store = _build_admin_test_app(authz)
    token = _issue_session_cookie(store, email="alice@example.com")
    client = TestClient(app)
    resp = client.post(
        "/bedrock-chat/admin/_probe",
        cookies={"sso_session_token": token},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == "alice@example.com"


def test_admin_routes_not_registered_when_admin_disabled():
    authz = _StubAuthorizer(allowed_user_ids={"alice@example.com"})
    app, plugin, _store = _build_admin_test_app(authz, admin_enabled=False)
    client = TestClient(app)
    resp = client.post("/bedrock-chat/admin/_probe")
    assert resp.status_code == 404


def test_admin_requires_identity_source_when_sso_disabled_and_no_auth_endpoint():
    authz = _StubAuthorizer(allowed_user_ids={"alice@example.com"})
    app, plugin, _store = _build_admin_test_app(authz, sso_enabled=False)
    client = TestClient(app)
    resp = client.post("/bedrock-chat/admin/_probe")
    assert resp.status_code == 401
    assert resp.json()["code"] == "not_authenticated"


# ---------------------------------------------------------------------------
# Anonymous-admin escape hatch (require_tool_auth=False)
# ---------------------------------------------------------------------------


class TestAnonymousAdminEscapeHatch:
    """When ``require_tool_auth=False`` and no identity resolves, ``_enforce_admin``
    accepts the request as an anonymous admin (``user_id='anonymous'``).

    This is a dev/standalone convenience — see the security warning in
    ``plugin.py::_enforce_admin``.
    """

    def test_anonymous_admin_accepted_when_require_tool_auth_disabled(self, caplog):
        authz = _StubAuthorizer(allowed_user_ids=set())  # nobody is admin
        app, plugin, _store = _build_admin_test_app(authz)
        plugin.config.require_tool_auth = False  # MagicMock-friendly override

        client = TestClient(app)
        with caplog.at_level("WARNING"):
            resp = client.get("/bedrock-chat/admin/_probe")
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "anonymous"
        # Authorizer is bypassed entirely — anonymous never reaches is_admin().
        assert authz.calls == 0
        # The security-warning log must fire on every anonymous request.
        assert any(
            "admin request accepted as anonymous" in rec.message for rec in caplog.records
        ), "expected security-warning log to fire on anonymous admin request"

    def test_anonymous_admin_rejected_when_require_tool_auth_enabled(self):
        """The escape hatch must not engage when require_tool_auth=True."""
        authz = _StubAuthorizer(allowed_user_ids=set())
        app, plugin, _store = _build_admin_test_app(authz)
        plugin.config.require_tool_auth = True

        client = TestClient(app)
        resp = client.get("/bedrock-chat/admin/_probe")
        assert resp.status_code == 401
        assert resp.json()["code"] == "not_authenticated"

    def test_resolved_identity_still_passes_through_authorizer(self):
        """``require_tool_auth=False`` is a fallback only — a resolved identity
        is still subject to the authorizer's verdict (no auto-admin)."""
        authz = _StubAuthorizer(allowed_user_ids=set())  # nobody is admin
        sso_store = SSOSessionStore(session_ttl=3600)
        app, _plugin, _store = _build_admin_test_app(authz, sso_session_store=sso_store)
        _plugin.config.require_tool_auth = False

        token = _issue_session_cookie(sso_store, email="bob@example.com")
        client = TestClient(app)
        client.cookies.set("sso_session_token", token)
        resp = client.get("/bedrock-chat/admin/_probe")
        # Identity resolved → authorizer ran → bob is not admin → 403.
        assert resp.status_code == 403
        assert resp.json()["code"] == "not_admin"
        assert authz.calls == 1


# ---------------------------------------------------------------------------
# Integration tests: auth_verification_endpoint identity path
# ---------------------------------------------------------------------------


def _patch_auth_endpoint_resolver(monkeypatch, handler):
    """Replace ``resolve_admin_identity_from_auth_endpoint`` with a stub.

    The stub receives ``(request, endpoint_url)`` and returns either an
    :class:`AdminIdentity` or ``None``.
    """
    import auto_bedrock_chat_fastapi.plugin as plugin_module

    async def _stub(request, endpoint_url, **kwargs):
        return handler(request, endpoint_url)

    # Patch the symbol imported inside ``_setup_admin_routes``. The
    # function is bound at import time inside the closure, so we have to
    # patch on the module path that the closure resolves through.
    monkeypatch.setattr(plugin_module, "_admin_auth_resolver_imported", True, raising=False)
    # The dependency imports lazily; the symbol is re-imported on each
    # registration call, so patching the source module is sufficient.
    import auto_bedrock_chat_fastapi.admin_auth as admin_auth_module

    monkeypatch.setattr(
        admin_auth_module,
        "resolve_admin_identity_from_auth_endpoint",
        _stub,
    )


def test_admin_resolves_identity_from_auth_verification_endpoint(monkeypatch):
    """Non-SSO caller authenticates via Authorization header forwarded to the verification endpoint."""

    def resolver(request, endpoint_url):
        # Verify we forwarded the Authorization header.
        assert request.headers.get("authorization") == "Bearer caller-token"
        assert endpoint_url == "https://auth.example.com/whoami"
        return AdminIdentity(user_id="alice@example.com", email="alice@example.com")

    _patch_auth_endpoint_resolver(monkeypatch, resolver)

    authz = _StubAuthorizer(allowed_user_ids={"alice@example.com"})
    app, _plugin, _store = _build_admin_test_app(
        authz,
        sso_enabled=False,
        auth_verification_endpoint="https://auth.example.com/whoami",
    )
    client = TestClient(app)
    resp = client.get(
        "/bedrock-chat/admin/_probe",
        headers={"Authorization": "Bearer caller-token"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == "alice@example.com"


def test_admin_returns_401_when_auth_endpoint_rejects_caller(monkeypatch):
    """Verification endpoint returns no identity \u2192 401, authorizer is not consulted."""

    _patch_auth_endpoint_resolver(monkeypatch, lambda request, url: None)

    authz = _StubAuthorizer(allowed_user_ids={"alice@example.com"})
    app, _plugin, _store = _build_admin_test_app(
        authz,
        sso_enabled=False,
        auth_verification_endpoint="https://auth.example.com/whoami",
    )
    client = TestClient(app)
    resp = client.get(
        "/bedrock-chat/admin/_probe",
        headers={"Authorization": "Bearer bogus"},
    )
    assert resp.status_code == 401
    assert authz.calls == 0


def test_admin_resolves_relative_auth_endpoint_against_app_base_url(monkeypatch):
    """Relative ``auth_verification_endpoint`` is resolved against ``app_base_url``."""

    captured: dict = {}

    def resolver(request, endpoint_url):
        captured["url"] = endpoint_url
        return AdminIdentity(user_id="alice@example.com")

    _patch_auth_endpoint_resolver(monkeypatch, resolver)

    authz = _StubAuthorizer(allowed_user_ids={"alice@example.com"})
    app, _plugin, _store = _build_admin_test_app(
        authz,
        sso_enabled=False,
        auth_verification_endpoint="/internal/whoami",
    )
    client = TestClient(app)
    resp = client.get(
        "/bedrock-chat/admin/_probe",
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 200
    assert captured["url"] == "https://app.example.com/internal/whoami"


def test_admin_prefers_sso_cookie_over_auth_endpoint(monkeypatch):
    """When both an SSO cookie and Authorization header are present, SSO wins (no auth-endpoint call)."""

    def resolver(request, endpoint_url):  # pragma: no cover - should not be called
        raise AssertionError("auth-endpoint resolver should not be invoked when SSO cookie resolves")

    _patch_auth_endpoint_resolver(monkeypatch, resolver)

    authz = _StubAuthorizer(allowed_user_ids={"alice@example.com"})
    app, _plugin, store = _build_admin_test_app(
        authz,
        auth_verification_endpoint="https://auth.example.com/whoami",
    )
    token = _issue_session_cookie(store, email="alice@example.com")
    client = TestClient(app)
    resp = client.get(
        "/bedrock-chat/admin/_probe",
        cookies={"sso_session_token": token},
        headers={"Authorization": "Bearer ignored"},
    )
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "alice@example.com"


# ---------------------------------------------------------------------------
# OpenAPI security advertisement
# ---------------------------------------------------------------------------


def _admin_security_schemes(app: FastAPI) -> set:
    """Collect the union of security scheme names referenced by admin operations."""
    spec = app.openapi()
    found = set()
    for path, ops in spec.get("paths", {}).items():
        if "/admin/" not in path:
            continue
        for op in ops.values():
            if not isinstance(op, dict):
                continue
            for entry in op.get("security", []) or []:
                found.update(entry.keys())
    return found


def test_admin_openapi_advertises_only_configured_schemes_for_oauth2_and_sso():
    """``supported_auth_types=[oauth2_client_credentials, sso]`` -> Bearer + SSO cookie only."""
    authz = _StubAuthorizer()
    app, _plugin, _store = _build_admin_test_app(
        authz,
        auth_verification_endpoint="https://auth.example.com/whoami",
        supported_auth_types=["oauth2_client_credentials", "sso"],
    )
    schemes = _admin_security_schemes(app)
    assert "SSOSessionCookie" in schemes
    assert "HTTPBearer" in schemes
    assert "HTTPBasic" not in schemes
    assert "APIKeyHeader" not in schemes


def test_admin_openapi_advertises_basic_when_supported():
    """``basic_auth`` in ``supported_auth_types`` -> HTTPBasic scheme is advertised."""
    authz = _StubAuthorizer()
    app, _plugin, _store = _build_admin_test_app(
        authz,
        sso_enabled=False,
        auth_verification_endpoint="https://auth.example.com/whoami",
        supported_auth_types=["basic_auth"],
    )
    schemes = _admin_security_schemes(app)
    assert "HTTPBasic" in schemes
    assert "HTTPBearer" not in schemes
    assert "APIKeyHeader" not in schemes
    assert "SSOSessionCookie" not in schemes


def test_admin_openapi_omits_api_key_when_not_supported():
    """``api_key`` absent from ``supported_auth_types`` -> APIKeyHeader is not advertised."""
    authz = _StubAuthorizer()
    app, _plugin, _store = _build_admin_test_app(
        authz,
        sso_enabled=False,
        auth_verification_endpoint="https://auth.example.com/whoami",
        supported_auth_types=["bearer_token"],
    )
    schemes = _admin_security_schemes(app)
    assert schemes == {"HTTPBearer"}


def test_admin_openapi_advertises_api_key_when_supported():
    """``api_key`` in ``supported_auth_types`` -> APIKeyHeader scheme is advertised."""
    authz = _StubAuthorizer()
    app, _plugin, _store = _build_admin_test_app(
        authz,
        sso_enabled=False,
        auth_verification_endpoint="https://auth.example.com/whoami",
        supported_auth_types=["api_key"],
    )
    schemes = _admin_security_schemes(app)
    assert schemes == {"APIKeyHeader"}


@pytest.mark.asyncio
async def test_auth_endpoint_resolver_returns_identity_on_2xx_dict():
    """Endpoint returns ``{user_id, email, groups}`` \u2192 AdminIdentity built from it."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer caller-token"
        return httpx.Response(
            200,
            json={
                "user_id": "alice@example.com",
                "email": "alice@example.com",
                "groups": ["bedrock-admins"],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://auth") as client:
        request = MagicMock()
        request.headers = {"authorization": "Bearer caller-token"}
        identity = await resolve_admin_identity_from_auth_endpoint(request, "https://auth/whoami", http_client=client)
    assert identity is not None
    assert identity.user_id == "alice@example.com"
    assert identity.email == "alice@example.com"
    assert identity.groups == ["bedrock-admins"]


@pytest.mark.asyncio
async def test_auth_endpoint_resolver_returns_none_without_auth_headers():
    """No Authorization / X-API-Key header \u2192 no call, return None."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("endpoint must not be called without auth headers")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://auth") as client:
        request = MagicMock()
        request.headers = {}
        identity = await resolve_admin_identity_from_auth_endpoint(request, "https://auth/whoami", http_client=client)
    assert identity is None


@pytest.mark.asyncio
async def test_auth_endpoint_resolver_returns_none_on_non_2xx():
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "nope"}))
    async with httpx.AsyncClient(transport=transport, base_url="https://auth") as client:
        request = MagicMock()
        request.headers = {"authorization": "Bearer x"}
        identity = await resolve_admin_identity_from_auth_endpoint(request, "https://auth/whoami", http_client=client)
    assert identity is None


@pytest.mark.asyncio
async def test_auth_endpoint_resolver_forwards_x_api_key():
    """``X-API-Key`` header is forwarded just like Authorization."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-api-key") == "secret-key"
        assert "authorization" not in {k.lower() for k in request.headers}
        return httpx.Response(200, json={"user_id": "svc-bot"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://auth") as client:
        request = MagicMock()
        request.headers = {"x-api-key": "secret-key"}
        identity = await resolve_admin_identity_from_auth_endpoint(request, "https://auth/whoami", http_client=client)
    assert identity is not None
    assert identity.user_id == "svc-bot"


@pytest.mark.asyncio
async def test_auth_endpoint_resolver_returns_none_without_identifier():
    """Endpoint returns a 2xx body with no ``user_id`` / ``email`` / ``sub`` / ``username`` \u2192 None."""

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"groups": ["everyone"]}))
    async with httpx.AsyncClient(transport=transport, base_url="https://auth") as client:
        request = MagicMock()
        request.headers = {"authorization": "Bearer x"}
        identity = await resolve_admin_identity_from_auth_endpoint(request, "https://auth/whoami", http_client=client)
    assert identity is None
