"""Unit tests for the conversation REST API (XMGPLAT-10380).

Covers ``register_conversation_routes`` end-to-end through a real FastAPI
``TestClient``:

  (a) response shape for list/get/create/patch/messages;
  (b) pagination (``limit``/``offset`` for list, ``limit``/``before`` cursor
      for messages);
  (c) auth isolation — 401 with no identity, 403 when the ``user_id`` query
      param doesn't match the caller, 404 (not a distinguishing 403) for
      path-addressed conversations owned by someone else;
  (d) ``409 conversation_history_unavailable`` only when the conversation has
      recorded turns (``message_count > 0``) but ``aget_state`` yields no
      checkpoint values — a zero-message (just-created) conversation returns
      ``200`` with an empty message list instead, vs. a normal ``200`` once a
      checkpoint exists.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from starlette.testclient import TestClient

from autolangchat.conversation_routes import register_conversation_routes
from autolangchat.db import SQLiteConversationStore


class _FakeState:
    def __init__(self, values):
        self.values = values


class _FakeChatGraph:
    """Minimal aget_state-only fake — the messages endpoint never invokes
    the graph, only reads its checkpoint."""

    def __init__(self):
        self.checkpoints = {}

    async def aget_state(self, cfg):
        thread_id = cfg["configurable"]["thread_id"]
        if thread_id not in self.checkpoints:
            return _FakeState(None)
        return _FakeState({"messages": self.checkpoints[thread_id]})


@pytest.fixture
def store():
    s = SQLiteConversationStore(db_path=":memory:")
    asyncio.run(s.open())
    yield s
    asyncio.run(s.close())


@pytest.fixture
def chat_graph():
    return _FakeChatGraph()


@pytest.fixture
def client(store, chat_graph):
    app = FastAPI()

    async def require_conversation_user(request: Request):
        user = request.headers.get("X-User")
        if not user:
            raise HTTPException(status_code=401, detail={"code": "not_authenticated", "message": "Not authenticated"})
        return SimpleNamespace(user_id=user)

    register_conversation_routes(
        app,
        prefix="/chat",
        conversation_store=store,
        chat_graph=chat_graph,
        require_conversation_user=require_conversation_user,
    )
    return TestClient(app)


def _auth(user):
    return {"X-User": user}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_no_identity_returns_401(client):
    r = client.get("/chat/conversations", params={"user_id": "alice"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "not_authenticated"


def test_list_with_mismatched_user_id_returns_403(client):
    r = client.get("/chat/conversations", params={"user_id": "bob"}, headers=_auth("alice"))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "forbidden"


def test_delete_all_with_mismatched_user_id_returns_403(client):
    r = client.delete("/chat/conversations", params={"user_id": "bob"}, headers=_auth("alice"))
    assert r.status_code == 403


def test_create_on_behalf_of_another_user_returns_403(client):
    r = client.post("/chat/conversations", json={"user_id": "bob"}, headers=_auth("alice"))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# CRUD response shape
# ---------------------------------------------------------------------------


def test_create_conversation_response_shape(client):
    r = client.post("/chat/conversations", json={"user_id": "alice", "title": "First"}, headers=_auth("alice"))
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {
        "id",
        "user_id",
        "title",
        "created_at",
        "updated_at",
        "message_count",
        "metadata",
        "is_archived",
    }
    assert body["user_id"] == "alice"
    assert body["title"] == "First"
    assert body["message_count"] == 0
    assert body["is_archived"] is False


def test_create_without_title_defaults_to_null(client):
    r = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice"))
    assert r.status_code == 201
    assert r.json()["title"] is None


def test_get_conversation_metadata(client):
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    r = client.get(f"/chat/conversations/{created['id']}", headers=_auth("alice"))
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_missing_conversation_returns_404(client):
    r = client.get("/chat/conversations/nonexistent", headers=_auth("alice"))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "conversation_not_found"


def test_get_other_users_conversation_returns_404_not_403(client):
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    r = client.get(f"/chat/conversations/{created['id']}", headers=_auth("bob"))
    # Ownership mismatch must be indistinguishable from "doesn't exist".
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "conversation_not_found"


def test_patch_updates_title(client):
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    r = client.patch(f"/chat/conversations/{created['id']}", json={"title": "Renamed"}, headers=_auth("alice"))
    assert r.status_code == 200
    assert r.json()["title"] == "Renamed"


def test_patch_requires_at_least_one_field(client):
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    r = client.patch(f"/chat/conversations/{created['id']}", json={}, headers=_auth("alice"))
    assert r.status_code == 422


def test_patch_other_users_conversation_returns_404(client):
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    r = client.patch(f"/chat/conversations/{created['id']}", json={"title": "Hijacked"}, headers=_auth("bob"))
    assert r.status_code == 404


def test_delete_conversation(client):
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    r = client.delete(f"/chat/conversations/{created['id']}", headers=_auth("alice"))
    assert r.status_code == 204
    assert client.get(f"/chat/conversations/{created['id']}", headers=_auth("alice")).status_code == 404


def test_delete_other_users_conversation_returns_404_and_does_not_delete(client):
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    r = client.delete(f"/chat/conversations/{created['id']}", headers=_auth("bob"))
    assert r.status_code == 404
    assert client.get(f"/chat/conversations/{created['id']}", headers=_auth("alice")).status_code == 200


def test_delete_all_conversations(client):
    client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice"))
    client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice"))
    r = client.delete("/chat/conversations", params={"user_id": "alice"}, headers=_auth("alice"))
    assert r.status_code == 200
    assert r.json() == {"deleted_count": 2}


# ---------------------------------------------------------------------------
# List pagination + user isolation
# ---------------------------------------------------------------------------


def test_list_pagination_response_shape(client):
    for _ in range(3):
        client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice"))

    r = client.get("/chat/conversations", params={"user_id": "alice", "limit": 2, "offset": 0}, headers=_auth("alice"))
    body = r.json()
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["items"]) == 2

    r2 = client.get("/chat/conversations", params={"user_id": "alice", "limit": 2, "offset": 2}, headers=_auth("alice"))
    assert len(r2.json()["items"]) == 1


def test_list_only_returns_own_conversations(client):
    client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice"))
    client.post("/chat/conversations", json={"user_id": "bob"}, headers=_auth("bob"))

    r = client.get("/chat/conversations", params={"user_id": "alice"}, headers=_auth("alice"))
    body = r.json()
    assert body["total"] == 1
    assert all(item["user_id"] == "alice" for item in body["items"])


# ---------------------------------------------------------------------------
# Messages endpoint
# ---------------------------------------------------------------------------


def test_messages_returns_empty_list_for_zero_message_conversation(client):
    """A brand-new conversation with no recorded turns (just created via
    POST, never chatted in) has no checkpoint yet — that must return 200
    with an empty message list, not conversation_history_unavailable."""
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    r = client.get(f"/chat/conversations/{created['id']}/messages", headers=_auth("alice"))
    assert r.status_code == 200
    assert r.json()["messages"] == []


def test_messages_returns_409_when_turns_recorded_but_checkpoint_missing(client, store):
    """A conversation with recorded turns (message_count > 0) whose
    checkpoint is missing really has lost history (e.g. MemorySaver after a
    restart) — that must return conversation_history_unavailable."""
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    asyncio.run(store.record_turn(created["id"]))  # message_count becomes 1, no matching checkpoint exists

    r = client.get(f"/chat/conversations/{created['id']}/messages", headers=_auth("alice"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "conversation_history_unavailable"


def test_messages_returns_history_once_checkpoint_exists(client, chat_graph):
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    chat_graph.checkpoints[created["id"]] = [
        {"role": "user", "content": "hi", "metadata": {"message_id": "m1"}},
        {"role": "assistant", "content": "hello", "metadata": {"message_id": "m2"}},
    ]

    r = client.get(f"/chat/conversations/{created['id']}/messages", headers=_auth("alice"))
    assert r.status_code == 200
    body = r.json()
    assert body["conversation_id"] == created["id"]
    assert [m["message_id"] for m in body["messages"]] == ["m1", "m2"]


def test_messages_before_cursor_pagination(client, chat_graph):
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    chat_graph.checkpoints[created["id"]] = [
        {"role": "user", "content": "1", "metadata": {"message_id": "m1"}},
        {"role": "assistant", "content": "2", "metadata": {"message_id": "m2"}},
        {"role": "user", "content": "3", "metadata": {"message_id": "m3"}},
        {"role": "assistant", "content": "4", "metadata": {"message_id": "m4"}},
    ]

    r = client.get(
        f"/chat/conversations/{created['id']}/messages",
        params={"before": "m3", "limit": 50},
        headers=_auth("alice"),
    )
    assert [m["message_id"] for m in r.json()["messages"]] == ["m1", "m2"]


def test_messages_of_other_users_conversation_returns_404(client, chat_graph):
    created = client.post("/chat/conversations", json={"user_id": "alice"}, headers=_auth("alice")).json()
    chat_graph.checkpoints[created["id"]] = [{"role": "user", "content": "hi", "metadata": {"message_id": "m1"}}]

    r = client.get(f"/chat/conversations/{created['id']}/messages", headers=_auth("bob"))
    assert r.status_code == 404
