"""Tests for the ``GET /admin/_capabilities`` probe's ``token_usage_enabled``
field (XMGPLAT-11085, Phase 1).

Uses the same bare-plugin construction pattern as
``test_admin_token_routes.py``'s plugin-guard tests (``_make_bare_plugin``),
so these tests exercise the real ``AutoLangChatPlugin._setup_admin_routes``
wiring rather than a hand-rolled ``FastAPI()`` app.
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeTokenUsageStore:
    """Minimal stand-in — the capability probe never calls store methods,
    it only checks ``is not None``."""


def _make_config():
    return SimpleNamespace(
        chat_endpoint="/chat",
        admin_enabled=True,
        admin_verification_endpoint=None,
        admin_authorized_users=[],
        admin_allow_anonymous=False,
        sso_enabled=False,
        sso_session_secret=None,
        auth_verification_endpoint=None,
        supported_auth_types=[],
        require_tool_auth=False,
        enable_ui=False,
        kb_embedding_model="m",
        feedback_synthesis_system_prompt=None,
        timeout=30.0,
    )


def _make_bare_plugin(token_usage_store):
    from autolangchat.admin.admin_auth import build_admin_authorizer
    from autolangchat.plugin import AutoLangChatPlugin

    plugin = object.__new__(AutoLangChatPlugin)
    plugin.app = FastAPI()
    plugin.app_base_url = "http://localhost:8000"
    plugin.config = _make_config()
    plugin.sso_session_store = None
    plugin.embedding_client = None
    plugin._feedback_store = None
    plugin._kb_store = None
    plugin._admin_authorizer_override = None
    plugin._token_usage_store = token_usage_store
    plugin._admin_authorizer = build_admin_authorizer(plugin.config, app_base_url=plugin.app_base_url)
    plugin._setup_admin_routes()
    return plugin


def test_capabilities_token_usage_enabled_false_when_store_none():
    plugin = _make_bare_plugin(None)
    client = TestClient(plugin.app)
    resp = client.get("/chat/admin/_capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_usage_enabled"] is False
    # Unrelated fields keep their existing dev-mode-anonymous behaviour
    # (require_tool_auth=False in _make_config).
    assert body["is_admin"] is True
    assert body["anonymous"] is True


def test_capabilities_token_usage_enabled_true_when_store_configured():
    plugin = _make_bare_plugin(_FakeTokenUsageStore())
    client = TestClient(plugin.app)
    resp = client.get("/chat/admin/_capabilities")
    assert resp.status_code == 200
    assert resp.json()["token_usage_enabled"] is True


def test_capabilities_token_usage_enabled_reported_even_when_not_admin():
    # require_tool_auth=True with no identity source configured (sso_enabled=False,
    # auth_verification_endpoint=None in _make_config) means _resolve_identity()
    # returns None, so is_admin/anonymous are both False — but token_usage_enabled
    # must still be reported since it's independent of the admin/auth outcome.
    plugin = _make_bare_plugin(_FakeTokenUsageStore())
    plugin.config.require_tool_auth = True
    client = TestClient(plugin.app)
    resp = client.get("/chat/admin/_capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_admin"] is False
    assert body["anonymous"] is False
    assert body["token_usage_enabled"] is True
