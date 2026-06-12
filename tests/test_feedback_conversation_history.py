"""Tests for XMGPLAT-10683 — conversation history context window on feedback.

Covers T8 acceptance criteria:
  (a) history slice is trimmed to N
  (b) system/tool messages are excluded
  (c) N=0 stores empty list
  (d) SQLite round-trip preserves list structure
  (e) Postgres round-trip preserves list structure (mocked)
  (f) config validation rejects negative N
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from auto_bedrock_chat_fastapi.db import AuthenticatedUserAuthorizer
from auto_bedrock_chat_fastapi.db.feedback_sqlite import SQLiteFeedbackStore
from auto_bedrock_chat_fastapi.models import FeedbackEntry, Rating
from auto_bedrock_chat_fastapi.session_manager import ChatMessage, ChatSession
from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(**overrides) -> FeedbackEntry:
    base = dict(
        session_id="s1",
        user_id="u1",
        query="q",
        ai_response="a",
        rating=Rating.POSITIVE,
        model_id="m",
    )
    base.update(overrides)
    return FeedbackEntry(**base)


@pytest.fixture
def handler():
    """Minimal WebSocketChatHandler for dispatch tests."""
    h = WebSocketChatHandler.__new__(WebSocketChatHandler)
    h._total_errors = 0
    h.config = MagicMock(
        model_id="anthropic.claude-test",
        feedback_max_history_context=5,
    )
    h.feedback_authorizer = AuthenticatedUserAuthorizer()

    store = MagicMock()
    store.create = AsyncMock()
    h.feedback_store = store

    h.session_manager = MagicMock()
    h.session_manager.get_session = AsyncMock()
    h.session_manager.get_conversation_history = AsyncMock(return_value=[])

    h._sent = []

    async def send(_ws, payload):
        h._sent.append(payload)

    h._send_message = send
    h._send_error = AsyncMock()
    return h


def _build_history(messages_spec):
    """Build a list of ChatMessages from (role, content) tuples."""
    return [ChatMessage(role=role, content=content) for role, content in messages_spec]


# ---------------------------------------------------------------------------
# (a) History slice is trimmed to N
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_trimmed_to_max_context(handler):
    """When more than N user/assistant messages precede the rated message,
    only the last N are captured."""
    handler.config.feedback_max_history_context = 3

    # Build 6 user/assistant messages + the rated assistant message
    history_msgs = _build_history(
        [
            ("user", "msg1"),
            ("assistant", "reply1"),
            ("user", "msg2"),
            ("assistant", "reply2"),
            ("user", "msg3"),
            ("assistant", "reply3"),
        ]
    )
    # The rated message is the last assistant
    rated_ai = ChatMessage(
        role="assistant",
        content="rated answer",
        metadata={"query": "msg4", "model_id": "anthropic.claude-test"},
    )
    user_before_rated = ChatMessage(role="user", content="msg4")
    full_history = history_msgs + [user_before_rated, rated_ai]

    sess = ChatSession(session_id="s1", websocket=MagicMock(), user_id="u1")
    sess.conversation_history = full_history
    handler.session_manager.get_session.return_value = sess
    handler.session_manager.get_conversation_history.return_value = full_history

    persisted = _entry(id=uuid4())
    handler.feedback_store.create.return_value = persisted

    await handler._handle_feedback_message(
        MagicMock(),
        {"message_id": rated_ai.message_id, "rating": "positive"},
    )

    # Extract the conversation_history passed to create
    call_args = handler.feedback_store.create.call_args[0][0]
    assert len(call_args.conversation_history) == 3
    # Should be the last 3: reply2, msg3, reply3... wait — last 3 from filtered
    # The 7 user/assistant messages before rated_ai: msg1, reply1, msg2, reply2, msg3, reply3, msg4
    # Last 3: reply3, msg4... no, let me re-check. history[:ai_idx] filtered to user/assistant, last 3
    # history[:ai_idx] = history_msgs + [user_before_rated] = 7 messages, all user/assistant
    # last 3 = assistant:reply3, user:msg4... no: reply2(idx3), msg3(idx4), reply3(idx5), msg4(idx6)
    # last 3 = [reply3, msg4... no] = [msg3, reply3, msg4]? Let me count:
    # filtered = [msg1, reply1, msg2, reply2, msg3, reply3, msg4] (7 items)
    # last 3 = [reply3, msg4]... no, [-3:] = [reply3, msg4]... wait 7 items [-3:] = items at idx 4,5,6
    # idx4=msg3, idx5=reply3, idx6=msg4
    assert call_args.conversation_history[-1]["content"] == "msg4"
    assert call_args.conversation_history[-1]["role"] == "user"


# ---------------------------------------------------------------------------
# (b) System/tool messages are excluded from the slice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_and_tool_messages_excluded(handler):
    """system and tool role messages must not appear in conversation_history."""
    handler.config.feedback_max_history_context = 10

    system_msg = ChatMessage(role="system", content="You are helpful")
    user_msg = ChatMessage(role="user", content="hello")
    tool_msg = ChatMessage(role="tool", content="tool result data")
    assistant_msg = ChatMessage(role="assistant", content="hi there")
    rated_ai = ChatMessage(
        role="assistant",
        content="final answer",
        metadata={"query": "follow up", "model_id": "anthropic.claude-test"},
    )
    user_followup = ChatMessage(role="user", content="follow up")

    full_history = [system_msg, user_msg, tool_msg, assistant_msg, user_followup, rated_ai]

    sess = ChatSession(session_id="s1", websocket=MagicMock(), user_id="u1")
    sess.conversation_history = full_history
    handler.session_manager.get_session.return_value = sess
    handler.session_manager.get_conversation_history.return_value = full_history

    persisted = _entry(id=uuid4())
    handler.feedback_store.create.return_value = persisted

    await handler._handle_feedback_message(
        MagicMock(),
        {"message_id": rated_ai.message_id, "rating": "negative"},
    )

    call_args = handler.feedback_store.create.call_args[0][0]
    roles = [m["role"] for m in call_args.conversation_history]
    assert "system" not in roles
    assert "tool" not in roles
    # Should contain: user:hello, assistant:hi there, user:follow up
    assert roles == ["user", "assistant", "user"]


# ---------------------------------------------------------------------------
# (c) N=0 stores empty list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n_zero_stores_empty_list(handler):
    """When feedback_max_history_context == 0, conversation_history is []."""
    handler.config.feedback_max_history_context = 0

    user_msg = ChatMessage(role="user", content="question")
    rated_ai = ChatMessage(
        role="assistant",
        content="answer",
        metadata={"query": "question", "model_id": "anthropic.claude-test"},
    )
    full_history = [user_msg, rated_ai]

    sess = ChatSession(session_id="s1", websocket=MagicMock(), user_id="u1")
    sess.conversation_history = full_history
    handler.session_manager.get_session.return_value = sess
    handler.session_manager.get_conversation_history.return_value = full_history

    persisted = _entry(id=uuid4())
    handler.feedback_store.create.return_value = persisted

    await handler._handle_feedback_message(
        MagicMock(),
        {"message_id": rated_ai.message_id, "rating": "positive"},
    )

    call_args = handler.feedback_store.create.call_args[0][0]
    assert call_args.conversation_history == []


# ---------------------------------------------------------------------------
# (d) SQLite round-trip preserves list structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_roundtrip_preserves_conversation_history():
    """conversation_history survives INSERT → SELECT in SQLite store."""
    store = SQLiteFeedbackStore(db_path=":memory:", init_schema=True)
    await store.open()
    try:
        history = [
            {"role": "user", "content": "what is X?"},
            {"role": "assistant", "content": "X is a thing"},
            {"role": "user", "content": "tell me more"},
        ]
        entry = _entry(
            rating=Rating.NEGATIVE,
            conversation_history=history,
        )
        persisted = await store.create(entry)
        assert persisted.conversation_history == history

        fetched = await store.get(entry.id)
        assert fetched is not None
        assert fetched.conversation_history == history
        assert isinstance(fetched.conversation_history, list)
        assert all(isinstance(m, dict) for m in fetched.conversation_history)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_roundtrip_empty_conversation_history():
    """Empty conversation_history round-trips correctly."""
    store = SQLiteFeedbackStore(db_path=":memory:", init_schema=True)
    await store.open()
    try:
        entry = _entry(conversation_history=[])
        persisted = await store.create(entry)
        assert persisted.conversation_history == []

        fetched = await store.get(entry.id)
        assert fetched is not None
        assert fetched.conversation_history == []
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# (e) Postgres round-trip preserves list structure (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_row_to_entry_preserves_conversation_history():
    """_row_to_entry correctly maps conversation_history from a Postgres row."""
    from datetime import datetime, timezone

    from auto_bedrock_chat_fastapi.db.feedback_postgres import _FEEDBACK_COLUMNS, PostgresFeedbackStore

    store = PostgresFeedbackStore.__new__(PostgresFeedbackStore)

    history = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]

    # Build a fake row matching _FEEDBACK_COLUMNS order
    row_data = {
        "id": uuid4(),
        "session_id": "s1",
        "user_id": "u1",
        "query": "q",
        "ai_response": "a",
        "rating": "positive",
        "score": None,
        "correction_text": None,
        "user_comment": None,
        "kb_sources_used": [],
        "model_id": "m",
        "review_status": "pending_review",
        "reviewer_id": None,
        "reviewer_tags": [],
        "conversation_history": history,
        "reviewer_comment": None,
        "reviewed_at": None,
        "integrated_into_kb_id": None,
        "integrated_at": None,
        "created_at": datetime.now(timezone.utc),
    }
    row = tuple(row_data[col] for col in _FEEDBACK_COLUMNS)

    entry = store._row_to_entry(row)
    assert entry.conversation_history == history


@pytest.mark.asyncio
async def test_postgres_row_to_entry_null_conversation_history():
    """NULL conversation_history from Postgres is normalized to []."""
    from datetime import datetime, timezone

    from auto_bedrock_chat_fastapi.db.feedback_postgres import _FEEDBACK_COLUMNS, PostgresFeedbackStore

    store = PostgresFeedbackStore.__new__(PostgresFeedbackStore)

    row_data = {
        "id": uuid4(),
        "session_id": "s1",
        "user_id": "u1",
        "query": "q",
        "ai_response": "a",
        "rating": "positive",
        "score": None,
        "correction_text": None,
        "user_comment": None,
        "kb_sources_used": [],
        "model_id": "m",
        "review_status": "pending_review",
        "reviewer_id": None,
        "reviewer_tags": [],
        "conversation_history": None,  # NULL from DB
        "reviewer_comment": None,
        "reviewed_at": None,
        "integrated_into_kb_id": None,
        "integrated_at": None,
        "created_at": datetime.now(timezone.utc),
    }
    row = tuple(row_data[col] for col in _FEEDBACK_COLUMNS)

    entry = store._row_to_entry(row)
    assert entry.conversation_history == []


# ---------------------------------------------------------------------------
# (f) Config validation rejects negative N
# ---------------------------------------------------------------------------


def test_config_rejects_negative_feedback_max_history_context():
    """feedback_max_history_context must be >= 0."""
    from auto_bedrock_chat_fastapi.config import ChatConfig

    with pytest.raises(ValidationError):
        ChatConfig(
            BEDROCK_FEEDBACK_MAX_HISTORY_CONTEXT=-1,
            _env_file=None,
        )


def test_config_accepts_zero_feedback_max_history_context():
    """feedback_max_history_context=0 is valid (disables capture)."""
    from auto_bedrock_chat_fastapi.config import ChatConfig

    cfg = ChatConfig(
        BEDROCK_FEEDBACK_MAX_HISTORY_CONTEXT=0,
        _env_file=None,
    )
    assert cfg.feedback_max_history_context == 0


def test_config_default_feedback_max_history_context():
    """Default value is 5."""
    from auto_bedrock_chat_fastapi.config import ChatConfig

    cfg = ChatConfig(_env_file=None)
    assert cfg.feedback_max_history_context == 5


# ---------------------------------------------------------------------------
# Existing callers not broken — FeedbackEntry defaults conversation_history=[]
# ---------------------------------------------------------------------------


def test_feedback_entry_defaults_conversation_history_empty():
    """Existing code that constructs FeedbackEntry without conversation_history
    still works (backward compatibility)."""
    entry = _entry()
    assert entry.conversation_history == []


def test_feedback_entry_accepts_conversation_history():
    """FeedbackEntry correctly stores a provided conversation_history."""
    history = [{"role": "user", "content": "hi"}]
    entry = _entry(conversation_history=history)
    assert entry.conversation_history == history
