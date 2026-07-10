"""Tests for the pluggable feedback metadata enrichment feature (XMGPLAT-10687, T8).

Covers AC14 (a) through (h):

  (a) the enrichment HTTP call is made with the correct AC3 payload when a URL
      is configured (seven session fields only — no ``duration_seconds`` and no
      credentials — plus the already-filtered ``conversation_history``, and the
      per-request timeout override);
  (b) the returned dict is stored verbatim in ``FeedbackEntry.entry_metadata``;
  (c) no HTTP call is made when the URL is not configured;
  (d) a timeout is treated as an error and the submission proceeds with an empty
      dict when ``fail_on_error=False``;
  (e) a timeout causes rejection (``FeedbackError``) when ``fail_on_error=True``;
  (f) a non-200 response is handled per the ``fail_on_error`` flag;
  (g) ``entry_metadata`` round-trips through the SQLite store;
  (h) ``entry_metadata`` round-trips through the Postgres store.

The enrichment-helper and handler cases drive the real
``WebSocketChatHandler`` code with light stand-ins for its heavy collaborators
(session manager, chat graph, feedback store, authorizer). The SQLite round-trip
runs against a real in-memory database; the Postgres round-trip fakes the async
connection layer (no live server in CI), mirroring the existing
``test_feedback_store_delete`` approach.
"""

import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from ._autolangchat_imports import install_package_stubs, load_module

# Keep the package stubs installed for the lifetime of this module so
# ``importlib.resources`` can resolve ``autolangchat.db.sql`` when the SQLite
# store loads its schema DDL at runtime.
sys.modules.update(install_package_stubs())

exceptions_mod = load_module("autolangchat.exceptions", "exceptions.py")
models_mod = load_module(
    "autolangchat.models",
    "models.py",
    extra_modules={"autolangchat.exceptions": exceptions_mod},
)


def _stub_module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


# ``websocket_handler`` pulls in fastapi, langgraph tooling, the SSO layer, etc.
# at import time. Only ``exceptions`` and ``models`` are exercised by the code
# paths under test, so the remaining top-level imports are satisfied with light
# stand-ins (the names just have to exist for the module to import).
_handler_stubs = {
    "autolangchat.exceptions": exceptions_mod,
    "autolangchat.models": models_mod,
    "autolangchat.auth_handler": _stub_module(
        "autolangchat.auth_handler",
        AuthenticationHandler=object,
        AuthType=object,
        Credentials=object,
    ),
    "autolangchat.config": _stub_module("autolangchat.config", ChatConfig=object),
    "autolangchat.db": _stub_module(
        "autolangchat.db",
        AuthenticatedUserAuthorizer=object,
        BaseFeedbackStore=object,
        BaseKBStore=object,
        BaseTokenUsageStore=object,
        FeedbackAuthorizer=object,
    ),
    "autolangchat.graph": _stub_module("autolangchat.graph"),
    "autolangchat.graph.tools": _stub_module("autolangchat.graph.tools"),
    "autolangchat.graph.tools.manager": _stub_module("autolangchat.graph.tools.manager", AuthInfo=object),
    "autolangchat.session_manager": _stub_module(
        "autolangchat.session_manager", ChatSession=object, ChatSessionManager=object
    ),
}

websocket_handler_mod = load_module(
    "autolangchat.websocket_handler",
    "websocket_handler.py",
    extra_modules=_handler_stubs,
)

# Stores for the round-trip cases (real code, loaded like test_feedback_store_delete).
feedback_base_mod = load_module(
    "autolangchat.db.feedback_base",
    "db/feedback_base.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
    },
)
feedback_sqlite_mod = load_module(
    "autolangchat.db.feedback_sqlite",
    "db/feedback_sqlite.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.db.feedback_base": feedback_base_mod,
    },
)
feedback_postgres_mod = load_module(
    "autolangchat.db.feedback_postgres",
    "db/feedback_postgres.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.db.feedback_base": feedback_base_mod,
    },
)

WebSocketChatHandler = websocket_handler_mod.WebSocketChatHandler
FeedbackError = exceptions_mod.FeedbackError
FeedbackEntry = models_mod.FeedbackEntry
Rating = models_mod.Rating
SQLiteFeedbackStore = feedback_sqlite_mod.SQLiteFeedbackStore
PostgresFeedbackStore = feedback_postgres_mod.PostgresFeedbackStore


# ---------------------------------------------------------------------------
# Fakes / builders
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, *, json_data=None, json_error=False):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("response body is not valid JSON")
        return self._json_data


class _FakeHTTPClient:
    """Records ``post`` calls and returns a canned response (or raises)."""

    def __init__(self, *, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.calls = []

    async def post(self, url, *, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self._raises is not None:
            raise self._raises
        return self._response


def _make_config(**overrides):
    cfg = dict(
        feedback_metadata_enrichment_url="https://enrich.example/api",
        feedback_metadata_enrichment_timeout=2.0,
        feedback_metadata_enrichment_fail_on_error=False,
        feedback_max_history_context=10,
        model_id="anthropic.claude-default",
    )
    cfg.update(overrides)
    return SimpleNamespace(**cfg)


def _make_session(**overrides):
    now = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
    fields = dict(
        session_id="sess-1",
        user_id="alice",
        user_agent="pytest-agent/1.0",
        ip_address="203.0.113.7",
        metadata={},
        created_at=now,
        last_activity=now,
        # Sensitive fields that must NEVER appear in the enrichment payload.
        credentials="SECRET-TOKEN",
        auth_handler=object(),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _make_handler(config, http_client):
    handler = WebSocketChatHandler.__new__(WebSocketChatHandler)
    handler.config = config
    handler.http_client = http_client
    return handler


# ---------------------------------------------------------------------------
# (a) / (AC3, AC6, AC8) enrichment HTTP call payload + per-request timeout
# ---------------------------------------------------------------------------


async def test_enrichment_call_uses_ac3_payload_and_request_timeout():
    expected_metadata = {"tenant_id": "acme", "roles": ["admin"]}
    client = _FakeHTTPClient(response=_FakeResponse(json_data=expected_metadata))
    config = _make_config(feedback_metadata_enrichment_timeout=1.5)
    handler = _make_handler(config, client)
    session = _make_session()
    conversation_history = [
        {"role": "user", "content": "what is the answer?"},
        {"role": "assistant", "content": "42"},
    ]

    result = await handler._fetch_feedback_metadata(session, conversation_history)

    assert result == expected_metadata
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "https://enrich.example/api"
    # Per-request timeout override (AC6) — not the global client timeout.
    assert call["timeout"] == 1.5

    body = call["json"]
    # conversation_history is forwarded verbatim (already filtered upstream).
    assert body["conversation_history"] == conversation_history

    # Session payload carries exactly the seven AC3 fields and nothing else —
    # in particular no duration_seconds and no credentials/auth_handler (AC8).
    assert set(body["session"].keys()) == {
        "session_id",
        "user_id",
        "user_agent",
        "ip_address",
        "metadata",
        "created_at",
        "last_activity",
    }
    assert body["session"]["session_id"] == "sess-1"
    assert body["session"]["created_at"] == session.created_at.isoformat()
    assert "credentials" not in body["session"]
    assert "auth_handler" not in body["session"]
    assert "duration_seconds" not in body["session"]


async def test_enrichment_non_object_body_returns_empty_when_lenient():
    client = _FakeHTTPClient(response=_FakeResponse(json_data=[1, 2, 3]))
    handler = _make_handler(_make_config(), client)

    result = await handler._fetch_feedback_metadata(_make_session(), [])

    assert result == {}


# ---------------------------------------------------------------------------
# (d) / (e) timeout behaviour per fail_on_error
# ---------------------------------------------------------------------------


async def test_timeout_proceeds_with_empty_dict_when_lenient():
    client = _FakeHTTPClient(raises=httpx.TimeoutException("timed out"))
    handler = _make_handler(_make_config(feedback_metadata_enrichment_fail_on_error=False), client)

    result = await handler._fetch_feedback_metadata(_make_session(), [])

    assert result == {}
    assert len(client.calls) == 1


async def test_timeout_rejects_when_strict():
    client = _FakeHTTPClient(raises=httpx.TimeoutException("timed out"))
    handler = _make_handler(_make_config(feedback_metadata_enrichment_fail_on_error=True), client)

    with pytest.raises(FeedbackError):
        await handler._fetch_feedback_metadata(_make_session(), [])


# ---------------------------------------------------------------------------
# (f) non-200 response per fail_on_error
# ---------------------------------------------------------------------------


async def test_non_200_returns_empty_dict_when_lenient():
    client = _FakeHTTPClient(response=_FakeResponse(status_code=503))
    handler = _make_handler(_make_config(feedback_metadata_enrichment_fail_on_error=False), client)

    result = await handler._fetch_feedback_metadata(_make_session(), [])

    assert result == {}


async def test_non_200_rejects_when_strict():
    client = _FakeHTTPClient(response=_FakeResponse(status_code=503))
    handler = _make_handler(_make_config(feedback_metadata_enrichment_fail_on_error=True), client)

    with pytest.raises(FeedbackError):
        await handler._fetch_feedback_metadata(_make_session(), [])


# ---------------------------------------------------------------------------
# Handler integration: (b) metadata stored, (c) no call when URL unset
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


class _FakeSessionManager:
    def __init__(self, session):
        self._session = session

    async def get_session(self, websocket):
        return self._session


class _FakeChatGraph:
    def __init__(self, messages):
        self._messages = messages

    async def aget_state(self, cfg):
        return SimpleNamespace(values={"messages": self._messages})


class _FakeAuthorizer:
    def can_submit(self, user_id):
        return True


class _CapturingFeedbackStore:
    def __init__(self):
        self.created = None

    async def create(self, entry):
        self.created = entry
        return entry


def _make_integration_handler(config, http_client, session, messages, store):
    handler = WebSocketChatHandler.__new__(WebSocketChatHandler)
    handler.config = config
    handler.http_client = http_client
    handler.feedback_store = store
    handler.session_manager = _FakeSessionManager(session)
    handler.chat_graph = _FakeChatGraph(messages)
    handler.feedback_authorizer = _FakeAuthorizer()
    handler._total_errors = 0
    return handler


def _conversation_messages():
    return [
        {"role": "user", "content": "what is the answer?", "metadata": {"message_id": "u1"}},
        {
            "role": "assistant",
            "content": "42",
            "metadata": {"message_id": "m1", "model_id": "anthropic.claude-test"},
        },
    ]


async def test_handler_stores_enrichment_metadata_on_entry():
    expected_metadata = {"tenant_id": "acme", "roles": ["admin"]}
    client = _FakeHTTPClient(response=_FakeResponse(json_data=expected_metadata))
    store = _CapturingFeedbackStore()
    handler = _make_integration_handler(
        _make_config(),
        client,
        _make_session(),
        _conversation_messages(),
        store,
    )
    websocket = _FakeWebSocket()

    await handler._handle_feedback_message(websocket, {"message_id": "m1", "rating": Rating.NEGATIVE.value})

    # The enrichment call happened and its dict landed on the persisted entry.
    assert len(client.calls) == 1
    assert store.created is not None
    assert store.created.entry_metadata == expected_metadata
    # A feedback_ack (not an error) was returned to the client.
    assert websocket.sent and websocket.sent[-1]["type"] == "feedback_ack"


async def test_handler_makes_no_call_and_empty_metadata_when_url_unset():
    client = _FakeHTTPClient(response=_FakeResponse(json_data={"should": "not be used"}))
    store = _CapturingFeedbackStore()
    handler = _make_integration_handler(
        _make_config(feedback_metadata_enrichment_url=None),
        client,
        _make_session(),
        _conversation_messages(),
        store,
    )
    websocket = _FakeWebSocket()

    await handler._handle_feedback_message(websocket, {"message_id": "m1", "rating": Rating.NEGATIVE.value})

    # No HTTP request was made and entry_metadata defaulted to {}.
    assert client.calls == []
    assert store.created is not None
    assert store.created.entry_metadata == {}
    assert websocket.sent and websocket.sent[-1]["type"] == "feedback_ack"


# ---------------------------------------------------------------------------
# (g) SQLite round-trip
# ---------------------------------------------------------------------------


def _make_entry(**kwargs):
    defaults = dict(
        session_id="sess-1",
        user_id="alice",
        query="what is the answer?",
        ai_response="42",
        rating=Rating.NEGATIVE,
        model_id="anthropic.claude-test",
    )
    defaults.update(kwargs)
    return FeedbackEntry(**defaults)


async def test_sqlite_entry_metadata_round_trip():
    metadata = {"tenant_id": "acme", "roles": ["admin", "reviewer"], "nested": {"flag": True}}
    store = SQLiteFeedbackStore(":memory:")
    await store.open()
    try:
        created = await store.create(_make_entry(entry_metadata=metadata))
        assert created.entry_metadata == metadata

        fetched = await store.get(created.id)
        assert fetched is not None
        assert fetched.entry_metadata == metadata
    finally:
        await store.close()


async def test_sqlite_entry_metadata_defaults_to_empty_dict():
    store = SQLiteFeedbackStore(":memory:")
    await store.open()
    try:
        created = await store.create(_make_entry())
        fetched = await store.get(created.id)
        assert fetched is not None
        assert fetched.entry_metadata == {}
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# (h) Postgres round-trip (faked async connection layer)
# ---------------------------------------------------------------------------


class _RTCursor:
    """Async cursor mapping INSERT ... RETURNING and SELECT against a dict.

    The store builds INSERT params in the exact ``_FEEDBACK_COLUMNS`` order and
    its ``RETURNING`` projection is ``_SELECT_COLS`` (the same order), so the
    inserted params tuple doubles as the returned row.
    """

    def __init__(self, rows):
        self._rows = rows
        self._row = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params):
        if "INSERT INTO feedback" in sql:
            self._rows[str(params[0])] = tuple(params)
            self._row = tuple(params)
        else:  # SELECT ... WHERE id = %s
            self._row = self._rows.get(str(params[0]))

    async def fetchone(self):
        return self._row


class _RTConnection:
    def __init__(self, rows):
        self._rows = rows
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _RTCursor(self._rows)

    async def commit(self):
        self.commits += 1


class _RTPool:
    def __init__(self):
        self.rows = {}

    def connection(self):
        return _RTConnection(self.rows)


def _make_postgres_store():
    store = PostgresFeedbackStore.__new__(PostgresFeedbackStore)
    store._pool = _RTPool()
    # Identity wrapper stands in for psycopg's Jsonb adapter: native JSONB is
    # returned as a Python object, so the value round-trips as a plain dict.
    store._Jsonb = lambda value: value
    return store


async def test_postgres_entry_metadata_round_trip():
    metadata = {"tenant_id": "acme", "roles": ["admin"], "nested": {"flag": True}}
    store = _make_postgres_store()

    created = await store.create(_make_entry(entry_metadata=metadata))
    assert created.entry_metadata == metadata

    fetched = await store.get(created.id)
    assert fetched is not None
    assert fetched.entry_metadata == metadata


async def test_postgres_entry_metadata_null_normalizes_to_empty_dict():
    store = _make_postgres_store()

    created = await store.create(_make_entry())
    # Simulate a NULL JSONB column coming back from the driver.
    row = list(store._pool.rows[str(created.id)])
    cols = feedback_postgres_mod._FEEDBACK_COLUMNS
    row[cols.index("entry_metadata")] = None
    store._pool.rows[str(created.id)] = tuple(row)

    fetched = await store.get(created.id)
    assert fetched is not None
    assert fetched.entry_metadata == {}
