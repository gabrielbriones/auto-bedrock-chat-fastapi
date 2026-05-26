"""Tests for the GET /admin/_capabilities endpoint (dashboard capability probe).

Covers the server-state × require_tool_auth combinations from the spec,
as well as admin-disabled and always-200 semantics:

| admin_enabled | require_tool_auth | identity resolves | authorizer decision | Expected                       |
|---------------|-------------------|-------------------|---------------------|--------------------------------|
| False         | any               | —                 | —                   | 404 (route not mounted)        |
| True          | False             | any               | — (bypassed)        | 200 {is_admin: T, anonymous: T}|
| True          | True              | Yes               | True                | 200 {is_admin: T, anonymous: F}|
| True          | True              | Yes               | False               | 200 {is_admin: F, anonymous: F}|
| True          | True              | No                | —                   | 200 {is_admin: F, anonymous: F}|
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_bedrock_chat_fastapi.admin_auth import AdminIdentity
from auto_bedrock_chat_fastapi.plugin import BedrockChatPlugin
from auto_bedrock_chat_fastapi.sso_session_store import SSOSessionStore

_SESSION_SECRET = "cap-test-session-secret-987654321"


# ---------------------------------------------------------------------------
# Shared test fixtures / helpers (mirrors the pattern in test_admin_auth.py)
# ---------------------------------------------------------------------------


class _StubAuthorizer:
    """In-memory authorizer: grants only user_ids listed in ``allowed``."""

    def __init__(self, allowed: set | None = None) -> None:
        self.allowed = set(allowed or [])

    async def is_admin(self, identity: AdminIdentity) -> bool:
        return identity.user_id in self.allowed


def _make_config(
    *,
    admin_enabled: bool = True,
    sso_enabled: bool = True,
    require_tool_auth: bool = True,
    auth_verification_endpoint: Optional[str] = None,
) -> MagicMock:
    cfg = MagicMock()
    cfg.chat_endpoint = "/bedrock-chat"
    cfg.admin_enabled = admin_enabled
    cfg.sso_enabled = sso_enabled
    cfg.sso_session_secret = _SESSION_SECRET
    cfg.sso_session_ttl = 3600
    cfg.require_tool_auth = require_tool_auth
    cfg.auth_verification_endpoint = auth_verification_endpoint
    cfg.supported_auth_types = ["bearer_token", "basic_auth", "api_key"]
    cfg.enable_ui = False  # keep templates out of scope for unit tests
    return cfg


def _build_app(
    authorizer: _StubAuthorizer,
    *,
    admin_enabled: bool = True,
    sso_enabled: bool = True,
    require_tool_auth: bool = True,
    auth_verification_endpoint: Optional[str] = None,
) -> tuple[FastAPI, SSOSessionStore]:
    """Build a minimal plugin app with only the admin routes wired."""
    app = FastAPI()
    plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
    plugin.app = app
    plugin.config = _make_config(
        admin_enabled=admin_enabled,
        sso_enabled=sso_enabled,
        require_tool_auth=require_tool_auth,
        auth_verification_endpoint=auth_verification_endpoint,
    )
    store = SSOSessionStore(session_ttl=3600) if sso_enabled else None
    plugin.sso_session_store = store
    plugin._admin_authorizer = authorizer
    plugin.app_base_url = "https://app.test"
    plugin.templates = None  # UI disabled in unit tests
    plugin._feedback_store = None
    plugin._kb_store = None

    if admin_enabled:
        plugin._setup_admin_routes()

    return app, store


def _make_session(store: SSOSessionStore, email: str) -> str:
    """Create an SSO session and return the signed cookie value."""
    sid = store.create_session(
        tokens={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
        user_info={"email": email, "sub": "sub-" + email},
        id_token_claims={"sub": "sub-" + email, "email": email},
    )
    return store.generate_session_token(sid, _SESSION_SECRET)


# ---------------------------------------------------------------------------
# admin_enabled=False: route not mounted → 404
# ---------------------------------------------------------------------------


def test_capabilities_404_when_admin_disabled():
    """When admin_enabled=False the route is never registered → 404."""
    app, _ = _build_app(_StubAuthorizer(), admin_enabled=False)
    client = TestClient(app)
    resp = client.get("/bedrock-chat/admin/_capabilities")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# admin_enabled=True, authenticated admin
# ---------------------------------------------------------------------------


def test_capabilities_admin_user_returns_is_admin_true():
    """Authenticated user that the authorizer grants → {is_admin: true, anonymous: false}."""
    authz = _StubAuthorizer(allowed={"alice@example.com"})
    app, store = _build_app(authz)
    token = _make_session(store, "alice@example.com")

    client = TestClient(app)
    resp = client.get(
        "/bedrock-chat/admin/_capabilities",
        cookies={"sso_session_token": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"is_admin": True, "anonymous": False}


# ---------------------------------------------------------------------------
# admin_enabled=True, authenticated non-admin — always 200
# ---------------------------------------------------------------------------


def test_capabilities_non_admin_user_returns_is_admin_false():
    """Authenticated user rejected by authorizer → {is_admin: false, anonymous: false}, NOT 403."""
    authz = _StubAuthorizer(allowed={"alice@example.com"})
    app, store = _build_app(authz)
    token = _make_session(store, "mallory@example.com")

    client = TestClient(app)
    resp = client.get(
        "/bedrock-chat/admin/_capabilities",
        cookies={"sso_session_token": token},
    )
    # The endpoint must never 403 — the button just stays hidden.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"is_admin": False, "anonymous": False}


# ---------------------------------------------------------------------------
# admin_enabled=True, no identity, require_tool_auth=True
# ---------------------------------------------------------------------------


def test_capabilities_unauthenticated_with_require_auth_returns_false():
    """No credentials + require_tool_auth=True → {is_admin: false, anonymous: false}, never 401."""
    authz = _StubAuthorizer()
    app, _ = _build_app(authz, require_tool_auth=True)

    client = TestClient(app)
    resp = client.get("/bedrock-chat/admin/_capabilities")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"is_admin": False, "anonymous": False}


# ---------------------------------------------------------------------------
# admin_enabled=True, require_tool_auth=False (anon admin — unconditional)
# ---------------------------------------------------------------------------


def test_capabilities_anonymous_admin_returns_is_admin_true_anonymous_true():
    """Anonymous-admin escape hatch active — no credentials → {is_admin: true, anonymous: true}."""
    authz = _StubAuthorizer()  # empty — authorizer never called
    app, _ = _build_app(authz, require_tool_auth=False)

    client = TestClient(app)
    resp = client.get("/bedrock-chat/admin/_capabilities")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"is_admin": True, "anonymous": True}


def test_capabilities_anonymous_admin_ignores_valid_sso_cookie():
    """require_tool_auth=False overrides any presented credentials.

    Even when the caller has a valid SSO session belonging to a user the
    authorizer would grant, the anonymous-admin escape hatch fires
    unconditionally: the response is {is_admin: true, anonymous: true},
    not {is_admin: true, anonymous: false}.
    """
    authz = _StubAuthorizer(allowed={"alice@example.com"})
    app, store = _build_app(authz, sso_enabled=True, require_tool_auth=False)
    token = _make_session(store, "alice@example.com")

    client = TestClient(app)
    resp = client.get(
        "/bedrock-chat/admin/_capabilities",
        cookies={"sso_session_token": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Must be anonymous=True, not the SSO identity
    assert body == {"is_admin": True, "anonymous": True}


# ---------------------------------------------------------------------------
# No identity source configured at all
# ---------------------------------------------------------------------------


def test_capabilities_no_identity_source_returns_false():
    """No SSO and no auth_verification_endpoint → identity=None → {is_admin: false}."""
    authz = _StubAuthorizer(allowed={"nobody"})
    # Disable SSO and no auth endpoint — identity resolution always returns None.
    app, _ = _build_app(authz, sso_enabled=False, require_tool_auth=True)

    client = TestClient(app)
    resp = client.get("/bedrock-chat/admin/_capabilities")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_admin"] is False


# ---------------------------------------------------------------------------
# Always 200: even when the caller has a bad/expired cookie
# ---------------------------------------------------------------------------


def test_capabilities_always_200_with_invalid_cookie():
    """An invalid cookie → identity=None → {is_admin: false}, not 401 or 403."""
    authz = _StubAuthorizer(allowed={"alice@example.com"})
    app, _ = _build_app(authz)

    client = TestClient(app)
    resp = client.get(
        "/bedrock-chat/admin/_capabilities",
        cookies={"sso_session_token": "garbage.token.value"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_admin"] is False


# ---------------------------------------------------------------------------
# Response schema consistency
# ---------------------------------------------------------------------------


def test_capabilities_response_has_required_keys():
    """Response always contains both 'is_admin' and 'anonymous' keys."""
    authz = _StubAuthorizer()
    app, _ = _build_app(authz)

    client = TestClient(app)
    resp = client.get("/bedrock-chat/admin/_capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert "is_admin" in body
    assert "anonymous" in body
    assert isinstance(body["is_admin"], bool)
    assert isinstance(body["anonymous"], bool)
