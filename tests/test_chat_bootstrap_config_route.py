"""Tests for ``GET {chat_endpoint}/config`` (BC-001, CONTRACT-001 §6).

Uses the same bare-plugin construction pattern as
``test_admin_capabilities.py``'s ``_make_bare_plugin`` (``object.__new__``),
so these tests exercise the real ``AutoLangChatPlugin._setup_routes``/
``_build_chat_bootstrap_context`` wiring rather than a hand-rolled app.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from autolangchat.config import ChatConfig
from autolangchat.plugin import AutoLangChatPlugin, _build_bootstrap_payload


class _FakeSessionStore:
    """Minimal test double for ``SSOSessionStore`` — see
    ``test_sso_silent_external_idp_cookie.py`` for the same convention: a
    token is only "valid" here if it follows ``"valid:<session_id>"``.
    """

    def __init__(self, sessions=None):
        self._sessions = sessions or {}

    @staticmethod
    def validate_session_token(token, secret):
        if isinstance(token, str) and token.startswith("valid:"):
            return token.split(":", 1)[1]
        return None

    def get_session(self, session_id):
        return self._sessions.get(session_id)


def _make_config(**overrides):
    return ChatConfig().model_copy(update=overrides)


def _make_bare_plugin(config, sso_session_store=None):
    plugin = object.__new__(AutoLangChatPlugin)
    plugin.app = FastAPI()
    plugin.config = config
    plugin.sso_session_store = sso_session_store
    plugin._preset_prompts = []
    plugin._preset_variables = []
    plugin._feedback_store = None
    plugin._feedback_authorizer = None
    plugin._conversation_store = None
    plugin._kb_store = None
    plugin.embedding_client = None
    plugin.websocket_handler = None
    plugin.chat_graph = None
    plugin._setup_templates()
    plugin._setup_routes()
    return plugin


def test_config_route_unauthenticated_returns_200_with_sso_false():
    plugin = _make_bare_plugin(_make_config(sso_enabled=False))
    client = TestClient(plugin.app)

    resp = client.get("/chat/config")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ssoAuthenticated"] is False
    assert body["ssoUserDisplay"] == ""


def test_config_route_sets_cache_control_no_store():
    plugin = _make_bare_plugin(_make_config(sso_enabled=False))
    client = TestClient(plugin.app)

    resp = client.get("/chat/config")

    assert resp.headers["cache-control"] == "no-store"


def test_config_route_authenticated_reflects_sso_session():
    session_store = _FakeSessionStore({"sess-1": {"user_info": {"email": "user@example.com"}, "id_token_claims": {}}})
    plugin = _make_bare_plugin(
        _make_config(sso_enabled=True, sso_session_secret="s3cr3t"),
        sso_session_store=session_store,
    )
    client = TestClient(plugin.app)
    client.cookies.set("sso_session_token", "valid:sess-1")

    resp = client.get("/chat/config")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ssoAuthenticated"] is True
    assert body["ssoUserDisplay"] == "user@example.com"


def test_config_route_matches_ui_endpoint_builder_context():
    # Both routes are built from the exact same _build_chat_bootstrap_context
    # dict (BC-001 acceptance criterion), so every JSON field must match its
    # snake_case counterpart in the chat.html context.
    plugin = _make_bare_plugin(_make_config(sso_enabled=False))
    client = TestClient(plugin.app)

    config_resp = client.get("/chat/config").json()
    ui_html = client.get("/chat/ui").text

    assert config_resp["modelId"] in ui_html
    assert config_resp["uiTitle"] in ui_html
    assert config_resp["websocketUrl"] == "/chat/ws"


def test_config_route_never_leaks_credentials_or_secrets():
    plugin = _make_bare_plugin(
        _make_config(
            sso_enabled=True,
            sso_session_secret="s3cr3t",
            aws_access_key_id="AKIAFAKE",
            aws_secret_access_key="fake-secret-key",
        )
    )
    client = TestClient(plugin.app)

    body = client.get("/chat/config").json()

    forbidden_substrings = ("secret", "password", "credential", "connection_string", "aws_")
    serialized_keys = " ".join(body.keys()).lower()
    for forbidden in forbidden_substrings:
        assert forbidden not in serialized_keys
    assert "s3cr3t" not in str(body).lower()
    assert "fake-secret-key" not in str(body)


def test_build_bootstrap_payload_maps_all_contract_001_keys():
    context = {
        "websocket_url": "/chat/ws",
        "auth_enabled": True,
        "require_tool_auth": False,
        "supported_auth_types": ["bearer_token"],
        "default_auth_type": "",
        "ui_title": "AI Assistant",
        "model_id": "us.anthropic.claude-sonnet-5",
        "model_display_name": "Claude Sonnet 5 (US)",
        "ui_welcome_message": "Welcome",
        "app_title": "Test App",
        "preset_prompts": [],
        "preset_variables": [],
        "sso_enabled": False,
        "sso_login_url": "/chat/auth/sso/login",
        "sso_logout_url": "/chat/auth/sso/logout",
        "sso_authenticated": False,
        "sso_user_display": "",
        "feedback_enabled": False,
        "lock_input_while_responding": True,
        "admin_enabled": False,
        "admin_prefix": "",
        "dashboard_url": "",
        "conversation_persistence_enabled": False,
        "enable_config_sidebar": False,
        "allowed_dynamic_overrides": None,
        "available_models": [{"id": "m1", "name": "Model 1"}],
        "available_model_groups": None,
        "override_defaults": {},
    }

    payload = _build_bootstrap_payload(context)

    assert payload["websocketUrl"] == "/chat/ws"
    assert payload["modelId"] == "us.anthropic.claude-sonnet-5"
    assert payload["uiTitle"] == "AI Assistant"
    assert payload["appTitle"] == "Test App"
    assert payload["modelDisplayName"] == "Claude Sonnet 5 (US)"
    assert payload["uiWelcomeMessage"] == "Welcome"
    # Published rather than derived by the client from ssoLoginUrl (CONTRACT-002 BC-009).
    assert payload["ssoLogoutUrl"] == "/chat/auth/sso/logout"
    # available_model_groups=None (matches chat.html's `default([], true)` filter)
    assert payload["availableModelGroups"] == []
