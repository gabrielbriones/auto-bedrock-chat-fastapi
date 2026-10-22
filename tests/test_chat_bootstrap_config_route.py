"""Tests for ``GET {chat_endpoint}/config`` (BC-001, CONTRACT-001 §6).

Uses the same bare-plugin construction pattern as
``test_admin_capabilities.py``'s ``_make_bare_plugin`` (``object.__new__``),
so these tests exercise the real ``AutoLangChatPlugin._setup_routes``/
``_build_chat_bootstrap_context`` wiring rather than a hand-rolled app.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import autolangchat.artifacts as artifacts_module
from autolangchat.artifacts import ArtifactError
from autolangchat.config import ChatConfig
from autolangchat.plugin import AutoLangChatPlugin, _build_bootstrap_payload
from autolangchat.session_manager import ChatSessionManager
from autolangchat.websocket_handler import WebSocketChatHandler


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
    from autolangchat.admin.admin_auth import build_admin_authorizer

    plugin = object.__new__(AutoLangChatPlugin)
    plugin.app = FastAPI()
    plugin.app_base_url = "http://localhost:8000"
    plugin.config = config
    plugin.sso_session_store = sso_session_store
    plugin._preset_prompts = []
    plugin._preset_variables = []
    plugin._feedback_store = None
    plugin._feedback_authorizer = None
    plugin._conversation_store = None
    plugin._kb_store = None
    plugin._token_usage_store = None
    plugin.embedding_client = None
    plugin.websocket_handler = None
    plugin.chat_graph = None
    plugin._admin_authorizer_override = None
    plugin._admin_authorizer = build_admin_authorizer(config, app_base_url=plugin.app_base_url)
    plugin._setup_routes()
    return plugin


@pytest.mark.parametrize("chat_endpoint", ["/chat", "/custom-chat"])
async def test_stats_route_surfaces_aggregate_artifacts_without_private_data(chat_endpoint, monkeypatch, caplog):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    config = _make_config(
        chat_endpoint=chat_endpoint,
        artifact_max_age_seconds=10,
        artifact_max_items_per_session=1,
        admin_enabled=True,
        require_tool_auth=False,
    )
    plugin = _make_bare_plugin(config)
    manager = ChatSessionManager(config)
    handler = WebSocketChatHandler(manager, config, chat_graph=None)
    plugin.websocket_handler = handler
    store = manager.artifact_store
    store.register_connection("private-connection", "private-user")
    scope = store.activate("private-connection", "private-thread", "private-user")
    private_values = ["private-connection", "private-user", "private-thread", "private-payload", "private-metadata"]
    try:
        with TestClient(plugin.app) as client, caplog.at_level(logging.DEBUG, logger="autolangchat.artifacts"):
            empty = client.get(f"{chat_endpoint}/stats")
            assert empty.status_code == 200
            assert empty.json()["sessions"]["artifacts"] == {
                "active_artifacts": 0,
                "bytes": 0,
                "puts": 0,
                "expiries": 0,
                "quota_rejections": 0,
                "failed_reads": 0,
            }
            handle = store.put(scope, "private-payload", {"source": "private-metadata"})
            private_values.append(handle)
            assert store.put(scope, "private-payload", {"source": "private-metadata"}) == handle
            with pytest.raises(ArtifactError, match="quota"):
                store.put(scope, "rejected", {})
            assert not store.contains(scope, "unknown")
            with pytest.raises(ArtifactError, match="unavailable"):
                store.read_region(scope, "unknown")
            expected = {
                "active_artifacts": 1,
                "bytes": 15,
                "puts": 1,
                "expiries": 0,
                "quota_rejections": 1,
                "failed_reads": 1,
            }
            assert (await handler.get_statistics())["sessions"]["artifacts"] == expected
            response = client.get(f"{chat_endpoint}/stats")
            assert response.status_code == 200
            assert response.json()["sessions"]["artifacts"] == expected
            assert response.json()["websocket"] == {"total_messages_handled": 0, "total_errors": 0}
            assert response.json()["tools"] == {}
            for private in private_values:
                assert private not in response.text
                assert private not in caplog.text

            clock[0] = 10
            expired = client.get(f"{chat_endpoint}/stats").json()["sessions"]["artifacts"]
            assert expired == {**expected, "active_artifacts": 0, "bytes": 0, "expiries": 1}
    finally:
        await handler.http_client.aclose()
        store.close()


@pytest.mark.parametrize(
    "overrides,status,body_key",
    [
        ({"admin_enabled": False, "require_tool_auth": False}, 404, "detail"),
        ({"admin_enabled": True, "require_tool_auth": True}, 401, "code"),
    ],
)
def test_stats_route_requires_admin_and_never_calls_handler_when_denied(overrides, status, body_key):
    plugin = _make_bare_plugin(_make_config(**overrides))
    plugin.websocket_handler = MagicMock()
    plugin.websocket_handler.get_statistics = AsyncMock(return_value={"sessions": {"artifacts": {}}})

    response = TestClient(plugin.app).get("/chat/stats")

    assert response.status_code == status
    assert body_key in response.json()
    assert "artifacts" not in response.text
    plugin.websocket_handler.get_statistics.assert_not_awaited()


def test_stats_route_advertises_configured_security_schemes_in_openapi():
    plugin = _make_bare_plugin(
        _make_config(
            admin_enabled=True,
            require_tool_auth=True,
            auth_verification_endpoint="http://auth.example/verify",
            supported_auth_types=["bearer_token", "api_key"],
        )
    )

    schema = TestClient(plugin.app).get("/openapi.json").json()

    security = schema["paths"]["/chat/stats"]["get"]["security"]
    advertised = {name for requirement in security for name in requirement}
    assert advertised == {"HTTPBearer", "APIKeyHeader"}
    assert {"HTTPBearer", "APIKeyHeader"} <= set(schema["components"]["securitySchemes"])


@pytest.mark.parametrize("llm_healthy", [True, False])
@pytest.mark.parametrize(
    "overrides",
    [
        {"admin_enabled": False, "require_tool_auth": False},
        {"admin_enabled": True, "require_tool_auth": True},
    ],
)
def test_health_never_exposes_statistics_to_unauthenticated_callers(overrides, llm_healthy):
    plugin = _make_bare_plugin(_make_config(**overrides))
    plugin.websocket_handler = MagicMock()
    plugin.websocket_handler.get_statistics = AsyncMock(return_value={"sessions": {"artifacts": {"bytes": 1}}})
    llm = MagicMock()
    llm.ainvoke = AsyncMock() if llm_healthy else AsyncMock(side_effect=RuntimeError("model unavailable"))

    with (
        patch("autolangchat.plugin.build_bedrock_kwargs", return_value={}),
        patch("langchain_aws.ChatBedrockConverse", return_value=llm),
    ):
        response = TestClient(plugin.app).get("/chat/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == ("healthy" if llm_healthy else "degraded")
    assert "statistics" not in body
    assert "artifacts" not in response.text
    plugin.websocket_handler.get_statistics.assert_not_awaited()


def test_config_route_unauthenticated_returns_200_with_sso_false():
    plugin = _make_bare_plugin(_make_config(sso_enabled=False))
    client = TestClient(plugin.app)

    resp = client.get("/chat/config")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ssoAuthenticated"] is False
    assert body["ssoUserDisplay"] == ""
    assert body["uiEndpoint"] == "/chat/ui"


def test_config_route_reports_custom_ui_endpoint():
    plugin = _make_bare_plugin(_make_config(ui_endpoint="/portal/ui"))

    assert TestClient(plugin.app).get("/chat/config").json()["uiEndpoint"] == "/portal/ui"


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


def test_setup_routes_leaves_ui_endpoint_for_the_spa_mount():
    # The Jinja page at ui_endpoint is gone (BC-002); _setup_routes() itself no
    # longer registers anything there, so mount_spa (called separately by
    # __init__, after _setup_routes) is free to serve the SPA from that exact
    # path without colliding with a route registered here.
    plugin = _make_bare_plugin(_make_config(sso_enabled=False))
    client = TestClient(plugin.app)

    resp = client.get("/chat/ui")

    assert resp.status_code == 404
    assert client.get("/chat/config").json()["websocketUrl"] == "/chat/ws"


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
        "ui_endpoint": "/chat/ui",
        "auth_enabled": True,
        "require_tool_auth": False,
        "supported_auth_types": ["bearer_token"],
        "default_auth_type": "",
        "ui_title": "AI Assistant",
        "model_id": "us.anthropic.claude-sonnet-5",
        "default_region": "global",
        "model_display_name": "Claude Sonnet 5 (US)",
        "ui_welcome_message": "Welcome",
        "app_title": "Test App",
        "preset_prompts": [],
        "preset_variables": [],
        "sso_enabled": False,
        "sso_login_url": "/chat/auth/sso/login",
        "sso_logout_url": "/chat/auth/sso/logout",
        "sso_refresh_url": "/chat/auth/sso/refresh",
        "sso_authenticated": False,
        "sso_user_display": "",
        "auth_expiration_behaviour": "proactive",
        "sso_session_renewal_interval": 900,
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
    assert payload["uiEndpoint"] == "/chat/ui"
    assert payload["modelId"] == "us.anthropic.claude-sonnet-5"
    assert payload["defaultRegion"] == "global"
    assert payload["uiTitle"] == "AI Assistant"
    assert payload["appTitle"] == "Test App"
    assert payload["modelDisplayName"] == "Claude Sonnet 5 (US)"
    assert payload["uiWelcomeMessage"] == "Welcome"
    # Published rather than derived by the client from ssoLoginUrl (CONTRACT-002 BC-009).
    assert payload["ssoLogoutUrl"] == "/chat/auth/sso/logout"
    assert payload["ssoRefreshUrl"] == "/chat/auth/sso/refresh"
    assert payload["authExpirationBehaviour"] == "proactive"
    assert payload["ssoSessionRenewalIntervalSeconds"] == 900
    # available_model_groups=None (matches chat.html's `default([], true)` filter)
    assert payload["availableModelGroups"] == []
