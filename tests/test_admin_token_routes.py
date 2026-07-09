"""Tests for the Admin Token Usage Analytics HTTP routes (XMGPLAT-10748).

Covers:

  (a) each of the four routes (``/summary``, ``/by-user``, ``/by-day``,
      ``/top-users``) returns the expected response envelope shape;
  (b) ``/by-day`` with an invalid range (``end <= start``) returns 400 with
      the ``invalid_date_range`` error code;
  (c) an unauthenticated request returns 401, and a non-admin request
      returns 403 (via ``require_admin`` raising ``AdminAPIError``, mirroring
      ``test_admin_openapi.py``'s pattern);
  (d) ``AutoLangChatPlugin._setup_admin_routes()`` does not register any
      ``/tokens/*`` route when ``_token_usage_store`` is ``None``, and does
      register all four when it is configured.

(a)-(c) use the lightweight ``load_module``-based import pattern (mirroring
``tests/test_admin_feedback_routes.py``) so these tests don't require the
heavy ``autolangchat`` package to be importable. (d) needs the real
``autolangchat.plugin`` module (and its ``langchain``/``langchain-aws``
dependencies), matching ``tests/test_plugin_lifecycle.py``'s approach.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._autolangchat_imports import load_module

exceptions_mod = load_module("autolangchat.exceptions", "exceptions.py")
models_mod = load_module("autolangchat.models", "models.py")
admin_errors_mod = load_module(
    "autolangchat.admin.admin_errors",
    "admin/admin_errors.py",
    extra_modules={"autolangchat.exceptions": exceptions_mod, "autolangchat.models": models_mod},
)
token_routes_mod = load_module(
    "autolangchat.admin.admin_token_routes",
    "admin/admin_token_routes.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.admin.admin_errors": admin_errors_mod,
    },
)

AdminAPIError = exceptions_mod.AdminAPIError
register_admin_error_handlers = admin_errors_mod.register_admin_error_handlers
register_admin_token_routes = token_routes_mod.register_admin_token_routes


class _Identity(SimpleNamespace):
    user_id: str = "admin"


class _FakeTokenUsageStore:
    """In-memory stand-in for ``BaseTokenUsageStore`` — only the four
    read-only query methods the routes call are implemented."""

    def __init__(self):
        self.summary = [{"model_id": "m1", "input_tokens": 10, "output_tokens": 20, "turn_count": 2}]
        self.by_user = [
            {
                "session_id": "sess-1",
                "model_id": "m1",
                "input_tokens": 5,
                "output_tokens": 5,
                "turn_ts": "2026-01-15T00:00:00+00:00",
            }
        ]
        self.by_day = [{"date": "2026-01-15", "input_tokens": 5, "output_tokens": 5, "turn_count": 1}]
        self.top_users = [{"user_id": "alice", "input_tokens": 100, "output_tokens": 200}]
        self.list_by_user_calls = []
        self.aggregate_by_day_calls = []
        self.aggregate_by_user_calls = []

    async def aggregate_by_model(self):
        return self.summary

    async def list_by_user(self, user_id, limit=50, offset=0):
        self.list_by_user_calls.append((user_id, limit, offset))
        return self.by_user

    async def aggregate_by_day(self, start, end):
        self.aggregate_by_day_calls.append((start, end))
        if end <= start:
            raise ValueError("end must be after start")
        return self.by_day

    async def aggregate_by_user(self, limit=10):
        self.aggregate_by_user_calls.append(limit)
        return self.top_users[:limit]


def _build_app(store=None, *, require_admin=None):
    app = FastAPI()
    register_admin_error_handlers(app)

    if require_admin is None:

        async def require_admin():
            return _Identity(user_id="admin")

    register_admin_token_routes(
        app,
        prefix="/bedrock-chat/admin",
        token_usage_store=store or _FakeTokenUsageStore(),
        require_admin=require_admin,
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Route-shape tests
# ---------------------------------------------------------------------------


def test_tokens_summary_returns_expected_shape():
    client = _build_app()
    resp = client.get("/bedrock-chat/admin/tokens/summary")
    assert resp.status_code == 200
    assert resp.json() == {"items": [{"model_id": "m1", "input_tokens": 10, "output_tokens": 20, "turn_count": 2}]}


def test_tokens_by_user_returns_expected_shape():
    store = _FakeTokenUsageStore()
    client = _build_app(store)
    resp = client.get("/bedrock-chat/admin/tokens/by-user", params={"user_id": "alice"})
    assert resp.status_code == 200
    assert resp.json() == {
        "user_id": "alice",
        "items": [
            {
                "session_id": "sess-1",
                "model_id": "m1",
                "input_tokens": 5,
                "output_tokens": 5,
                "turn_ts": "2026-01-15T00:00:00+00:00",
            }
        ],
    }
    assert store.list_by_user_calls == [("alice", 50, 0)]


def test_tokens_by_user_requires_user_id_query_param():
    client = _build_app()
    resp = client.get("/bedrock-chat/admin/tokens/by-user")
    assert resp.status_code == 422


def test_tokens_by_user_forwards_limit_and_offset():
    store = _FakeTokenUsageStore()
    client = _build_app(store)
    resp = client.get(
        "/bedrock-chat/admin/tokens/by-user",
        params={"user_id": "alice", "limit": 10, "offset": 5},
    )
    assert resp.status_code == 200
    assert store.list_by_user_calls == [("alice", 10, 5)]


def test_tokens_by_day_returns_expected_shape():
    client = _build_app()
    resp = client.get(
        "/bedrock-chat/admin/tokens/by-day",
        params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-31T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"items": [{"date": "2026-01-15", "input_tokens": 5, "output_tokens": 5, "turn_count": 1}]}


def test_tokens_by_day_invalid_range_returns_400():
    client = _build_app()
    resp = client.get(
        "/bedrock-chat/admin/tokens/by-day",
        params={"start": "2026-01-31T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "invalid_date_range"


def test_tokens_by_day_requires_start_and_end():
    client = _build_app()
    resp = client.get("/bedrock-chat/admin/tokens/by-day")
    assert resp.status_code == 422


def test_tokens_top_users_returns_expected_shape():
    store = _FakeTokenUsageStore()
    client = _build_app(store)
    resp = client.get("/bedrock-chat/admin/tokens/top-users", params={"limit": 5})
    assert resp.status_code == 200
    assert resp.json() == {"items": [{"user_id": "alice", "input_tokens": 100, "output_tokens": 200}]}
    assert store.aggregate_by_user_calls == [5]


def test_tokens_top_users_uses_default_limit_when_omitted():
    store = _FakeTokenUsageStore()
    client = _build_app(store)
    resp = client.get("/bedrock-chat/admin/tokens/top-users")
    assert resp.status_code == 200
    assert store.aggregate_by_user_calls == [10]


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


def test_unauthenticated_request_returns_401():
    async def require_admin():
        raise AdminAPIError(status_code=401, code="not_authenticated", detail="not authenticated")

    client = _build_app(require_admin=require_admin)
    resp = client.get("/bedrock-chat/admin/tokens/summary")
    assert resp.status_code == 401
    assert resp.json()["code"] == "not_authenticated"


def test_non_admin_request_returns_403():
    async def require_admin():
        raise AdminAPIError(status_code=403, code="not_admin", detail="not admin")

    client = _build_app(require_admin=require_admin)
    resp = client.get("/bedrock-chat/admin/tokens/summary")
    assert resp.status_code == 403
    assert resp.json()["code"] == "not_admin"


# ---------------------------------------------------------------------------
# Plugin-level registration guard (requires the real ``autolangchat.plugin``
# module — mirrors ``tests/test_plugin_lifecycle.py``'s bare-plugin pattern).
# ---------------------------------------------------------------------------


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


def test_routes_not_registered_when_store_is_none():
    plugin = _make_bare_plugin(None)
    paths = [r.path for r in plugin.app.routes]
    assert not any("/tokens/" in p for p in paths)


def test_routes_registered_when_store_is_configured():
    plugin = _make_bare_plugin(_FakeTokenUsageStore())
    paths = sorted(r.path for r in plugin.app.routes if "/tokens/" in r.path)
    assert paths == [
        "/chat/admin/tokens/by-day",
        "/chat/admin/tokens/by-user",
        "/chat/admin/tokens/summary",
        "/chat/admin/tokens/top-users",
    ]
