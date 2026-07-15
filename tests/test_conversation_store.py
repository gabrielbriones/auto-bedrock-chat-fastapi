"""Unit tests for the conversation metadata store (XMGPLAT-10380).

Covers ``SQLiteConversationStore``:

  (a) CRUD — create/get/update/delete/list/count round-trip correctly;
  (b) pagination — ``limit``/``offset`` validation and newest-updated-first
      ordering;
  (c) user isolation — one user's ``list_conversations``/``get_conversation_count``
      never surfaces another user's rows;
  (d) ``record_turn`` increments ``message_count`` and bumps ``updated_at``;
  (e) ``max_conversations_per_user`` pruning evicts the least-recently-updated
      conversation(s) on overflow;
  (f) not-found errors from ``update_conversation``/``record_turn``;
  (g) idempotent ``delete_conversation`` on a missing id.

And the ``create_conversation_store`` factory (``autolangchat/db/__init__.py``):

  (h) returns ``None`` when ``conversation_persistence_enabled=False``;
  (i) returns ``None`` (with a warning, not a crash) when enabled but no
      usable SQLite path can be resolved.
"""

import pytest

from autolangchat.config import ChatConfig
from autolangchat.db import SQLiteConversationStore, create_conversation_store
from autolangchat.exceptions import ConversationNotFoundError


async def _open_store(**kwargs):
    store = SQLiteConversationStore(db_path=":memory:", **kwargs)
    await store.open()
    return store


async def test_create_and_get_conversation_round_trip():
    store = await _open_store()
    try:
        await store.create_conversation("conv-1", "alice", title="Hello", metadata={"foo": "bar"})
        row = await store.get_conversation("conv-1")
        assert row["id"] == "conv-1"
        assert row["user_id"] == "alice"
        assert row["title"] == "Hello"
        assert row["metadata"] == {"foo": "bar"}
        assert row["message_count"] == 0
        assert row["is_archived"] is False
        assert row["created_at"] == row["updated_at"]
    finally:
        await store.close()


async def test_get_conversation_returns_none_when_missing():
    store = await _open_store()
    try:
        assert await store.get_conversation("nonexistent") is None
    finally:
        await store.close()


async def test_update_conversation_title_and_metadata():
    store = await _open_store()
    try:
        await store.create_conversation("conv-1", "alice")
        await store.update_conversation("conv-1", title="New title")
        row = await store.get_conversation("conv-1")
        assert row["title"] == "New title"
        assert row["metadata"] == {}

        await store.update_conversation("conv-1", metadata={"k": "v"})
        row = await store.get_conversation("conv-1")
        # Title from the previous update must survive a metadata-only patch.
        assert row["title"] == "New title"
        assert row["metadata"] == {"k": "v"}
    finally:
        await store.close()


async def test_update_conversation_requires_a_field():
    store = await _open_store()
    try:
        await store.create_conversation("conv-1", "alice")
        with pytest.raises(ValueError):
            await store.update_conversation("conv-1")
    finally:
        await store.close()


async def test_update_conversation_raises_not_found():
    store = await _open_store()
    try:
        with pytest.raises(ConversationNotFoundError):
            await store.update_conversation("nonexistent", title="x")
    finally:
        await store.close()


async def test_record_turn_increments_count_and_bumps_updated_at():
    store = await _open_store()
    try:
        await store.create_conversation("conv-1", "alice")
        before = await store.get_conversation("conv-1")

        await store.record_turn("conv-1")
        await store.record_turn("conv-1")
        after = await store.get_conversation("conv-1")

        assert after["message_count"] == 2
        assert after["updated_at"] >= before["updated_at"]
    finally:
        await store.close()


async def test_record_turn_raises_not_found():
    store = await _open_store()
    try:
        with pytest.raises(ConversationNotFoundError):
            await store.record_turn("nonexistent")
    finally:
        await store.close()


async def test_delete_conversation_removes_row_and_is_idempotent():
    store = await _open_store()
    try:
        await store.create_conversation("conv-1", "alice")
        await store.delete_conversation("conv-1")
        assert await store.get_conversation("conv-1") is None
        # Deleting again (or a never-existed id) must not raise.
        await store.delete_conversation("conv-1")
        await store.delete_conversation("never-existed")
    finally:
        await store.close()


async def test_delete_all_conversations_returns_deleted_count():
    store = await _open_store()
    try:
        await store.create_conversation("c1", "alice")
        await store.create_conversation("c2", "alice")
        await store.create_conversation("c3", "bob")

        deleted = await store.delete_all_conversations("alice")
        assert deleted == 2
        assert await store.get_conversation_count("alice") == 0
        # Bob's conversation is untouched.
        assert await store.get_conversation_count("bob") == 1
    finally:
        await store.close()


async def test_list_conversations_pagination_and_ordering():
    store = await _open_store()
    try:
        await store.create_conversation("c1", "alice", title="First")
        await store.record_turn("c1")  # bumps c1's updated_at forward
        await store.create_conversation("c2", "alice", title="Second")

        # c2 was created after c1's record_turn touch, so c2 should sort
        # first (newest-updated-first).
        page1 = await store.list_conversations("alice", limit=1, offset=0)
        assert [c["id"] for c in page1] == ["c2"]

        page2 = await store.list_conversations("alice", limit=1, offset=1)
        assert [c["id"] for c in page2] == ["c1"]

        full = await store.list_conversations("alice", limit=50, offset=0)
        assert [c["id"] for c in full] == ["c2", "c1"]
    finally:
        await store.close()


async def test_list_conversations_validates_limit_and_offset():
    store = await _open_store()
    try:
        with pytest.raises(ValueError):
            await store.list_conversations("alice", limit=0)
        with pytest.raises(ValueError):
            await store.list_conversations("alice", offset=-1)
    finally:
        await store.close()


async def test_user_isolation_list_and_count():
    store = await _open_store()
    try:
        await store.create_conversation("a1", "alice")
        await store.create_conversation("a2", "alice")
        await store.create_conversation("b1", "bob")

        alice_list = await store.list_conversations("alice")
        assert {c["id"] for c in alice_list} == {"a1", "a2"}
        assert await store.get_conversation_count("alice") == 2

        bob_list = await store.list_conversations("bob")
        assert {c["id"] for c in bob_list} == {"b1"}
        assert await store.get_conversation_count("bob") == 1
    finally:
        await store.close()


async def test_max_conversations_per_user_prunes_oldest():
    store = await _open_store(max_conversations_per_user=2)
    try:
        await store.create_conversation("c1", "alice")
        await store.create_conversation("c2", "alice")
        await store.create_conversation("c3", "alice")  # triggers pruning

        assert await store.get_conversation_count("alice") == 2
        remaining = {c["id"] for c in await store.list_conversations("alice")}
        # c1 is the least-recently-updated and should have been evicted.
        assert remaining == {"c2", "c3"}
    finally:
        await store.close()


async def test_max_conversations_per_user_zero_disables_pruning():
    store = await _open_store(max_conversations_per_user=0)
    try:
        for i in range(5):
            await store.create_conversation(f"c{i}", "alice")
        assert await store.get_conversation_count("alice") == 5
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# create_conversation_store factory
# ---------------------------------------------------------------------------


def test_create_conversation_store_returns_none_when_disabled():
    config = ChatConfig(AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED=False)
    assert create_conversation_store(config) is None


def test_create_conversation_store_returns_none_without_a_resolvable_path(monkeypatch):
    config = ChatConfig(
        AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED=True,
        AUTOCHAT_CONVERSATION_STORAGE_TYPE="sqlite",
        # kb_database_path has a non-empty default and its env alias is
        # "KB_DATABASE_PATH" (no AUTOCHAT_ prefix) — must be cleared
        # explicitly too for the sqlite fallback chain to resolve to nothing.
        KB_DATABASE_PATH="",
    )
    assert config.conversation_db_path is None
    assert config.feedback_database_path is None
    assert config.kb_database_path == ""
    assert create_conversation_store(config) is None


def test_create_conversation_store_builds_sqlite_store():
    config = ChatConfig(
        AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED=True,
        AUTOCHAT_CONVERSATION_DB_PATH=":memory:",
        AUTOCHAT_MAX_CONVERSATIONS_PER_USER=7,
    )
    store = create_conversation_store(config)
    assert isinstance(store, SQLiteConversationStore)
    assert store._max_conversations_per_user == 7


def test_create_conversation_store_falls_back_to_feedback_database_path():
    config = ChatConfig(
        AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED=True,
        AUTOCHAT_FEEDBACK_DATABASE_PATH="/tmp/shared.db",
    )
    store = create_conversation_store(config)
    assert isinstance(store, SQLiteConversationStore)
    assert store._db_path == "/tmp/shared.db"


def test_create_conversation_store_unknown_storage_type_returns_none():
    config = ChatConfig(
        AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED=True,
        AUTOCHAT_CONVERSATION_STORAGE_TYPE="mongodb",
        AUTOCHAT_CONVERSATION_DB_PATH=":memory:",
    )
    assert create_conversation_store(config) is None
