"""Unit tests for the feedback storage backend (XMGPLAT-10417).

These tests do **not** require a real database. They cover:

* Pydantic ``FeedbackEntry`` validators
* Status-transition table semantics
* ``WebSocketChatHandler._handle_feedback_message`` dispatch (happy path,
  unauthorized, validation errors, store-disabled, unknown message)
* ``AuthenticatedUserAuthorizer`` default behavior

Database CRUD is covered by ``tests/test_feedback_store_integration.py``
which talks to a live Postgres when ``TEST_FEEDBACK_PG_URL`` is set.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from auto_bedrock_chat_fastapi.feedback_store import AuthenticatedUserAuthorizer
from auto_bedrock_chat_fastapi.models import (
    ALLOWED_REVIEW_TRANSITIONS,
    FeedbackEntry,
    FeedbackStats,
    Rating,
    ReviewStatus,
)
from auto_bedrock_chat_fastapi.session_manager import ChatMessage, ChatSession
from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

# ---------------------------------------------------------------------------
# FeedbackEntry validators
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


def test_defaults_pending_review_and_uuid_id():
    e = _entry()
    assert e.review_status == ReviewStatus.PENDING_REVIEW
    assert e.created_at.tzinfo is not None
    assert str(e.id)


def test_correction_requires_correction_text():
    with pytest.raises(ValidationError, match="correction_text"):
        _entry(rating=Rating.CORRECTION)


def test_positive_forbids_correction_text():
    with pytest.raises(ValidationError, match="correction_text"):
        _entry(correction_text="nope")


@pytest.mark.parametrize("score", [0, 6, -1, 100])
def test_score_must_be_1_to_5(score):
    with pytest.raises(ValidationError):
        _entry(rating=Rating.NEGATIVE, score=score)


def test_decided_status_requires_reviewer_fields():
    with pytest.raises(ValidationError, match="reviewer_id"):
        _entry(review_status=ReviewStatus.APPROVED)


def test_optional_text_fields_stripped_to_none():
    e = _entry(rating=Rating.NEGATIVE, user_comment="   ", correction_text="  fix me  ")
    assert e.user_comment is None
    assert e.correction_text == "fix me"


# ---------------------------------------------------------------------------
# Status-transition table
# ---------------------------------------------------------------------------


def test_pending_can_go_to_approved_or_rejected():
    allowed = ALLOWED_REVIEW_TRANSITIONS[ReviewStatus.PENDING_REVIEW]
    assert ReviewStatus.APPROVED in allowed
    assert ReviewStatus.REJECTED in allowed
    assert ReviewStatus.PENDING_REVIEW not in allowed


def test_decided_cannot_go_back_to_pending():
    assert ReviewStatus.PENDING_REVIEW not in ALLOWED_REVIEW_TRANSITIONS[ReviewStatus.APPROVED]
    assert ReviewStatus.PENDING_REVIEW not in ALLOWED_REVIEW_TRANSITIONS[ReviewStatus.REJECTED]


def test_decisions_can_flip():
    # Reviewer can flip approved -> rejected and vice versa.
    assert ReviewStatus.REJECTED in ALLOWED_REVIEW_TRANSITIONS[ReviewStatus.APPROVED]
    assert ReviewStatus.APPROVED in ALLOWED_REVIEW_TRANSITIONS[ReviewStatus.REJECTED]


# ---------------------------------------------------------------------------
# Default authorizer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user_id,allowed",
    [("alice", True), ("u-123", True), (None, False), ("", False)],
)
def test_default_authorizer(user_id, allowed):
    assert AuthenticatedUserAuthorizer().can_submit(user_id) is allowed


# ---------------------------------------------------------------------------
# WebSocket handler dispatch
# ---------------------------------------------------------------------------


@pytest.fixture
def handler_factory():
    """Build a barely-initialized ``WebSocketChatHandler`` for dispatch tests.

    Avoids the heavy real ``__init__`` (Bedrock client, tool manager, etc.).
    """

    def _make(*, with_store: bool = True, authorizer=None):
        h = WebSocketChatHandler.__new__(WebSocketChatHandler)
        h._total_errors = 0
        h.config = MagicMock(model_id="anthropic.claude-test")
        h.feedback_authorizer = authorizer or AuthenticatedUserAuthorizer()

        if with_store:
            store = MagicMock()
            store.create = AsyncMock()
            h.feedback_store = store
        else:
            h.feedback_store = None

        # session manager stub
        h.session_manager = MagicMock()
        h.session_manager.get_session = AsyncMock()
        h.session_manager.get_conversation_history = AsyncMock(return_value=[])

        # capture sent messages
        h._sent = []

        async def send(_ws, payload):
            h._sent.append(payload)

        h._send_message = send  # type: ignore[assignment]
        h._send_error = AsyncMock()
        return h

    return _make


def _seed_session(handler, *, user_id="u1", ai_metadata=None):
    sess = ChatSession(session_id="s1", websocket=MagicMock(), user_id=user_id)
    ai = ChatMessage(
        role="assistant",
        content="answer text",
        metadata=ai_metadata
        or {
            "query": "what is IPC?",
            "kb_sources": [{"title": "doc", "source": "docs"}],
            "model_id": "anthropic.claude-test",
        },
    )
    sess.conversation_history = [ai]
    handler.session_manager.get_session.return_value = sess
    handler.session_manager.get_conversation_history.return_value = [ai]
    return sess, ai


@pytest.mark.asyncio
async def test_feedback_happy_path(handler_factory):
    h = handler_factory()
    _, ai = _seed_session(h)
    persisted = _entry(id=uuid4())
    h.feedback_store.create.return_value = persisted

    await h._handle_feedback_message(
        MagicMock(),
        {"message_id": ai.message_id, "rating": "positive"},
    )

    assert h.feedback_store.create.await_count == 1
    sent = h._sent[0]
    assert sent["type"] == "feedback_ack"
    assert sent["status"] == "pending_review"
    assert sent["feedback_id"] == str(persisted.id)


@pytest.mark.asyncio
async def test_feedback_unauthorized(handler_factory):
    deny = MagicMock()
    deny.can_submit = MagicMock(return_value=False)
    h = handler_factory(authorizer=deny)
    _seed_session(h)

    await h._handle_feedback_message(MagicMock(), {"message_id": "x", "rating": "positive"})

    assert h._sent[0] == {
        "type": "error",
        "code": "unauthorized_feedback",
        "detail": "You are not authorized to submit feedback",
        "timestamp": h._sent[0]["timestamp"],
    }
    assert h.feedback_store.create.await_count == 0


@pytest.mark.asyncio
async def test_feedback_store_disabled(handler_factory):
    h = handler_factory(with_store=False)
    _seed_session(h)
    await h._handle_feedback_message(MagicMock(), {"message_id": "x", "rating": "positive"})
    assert h._sent[0]["code"] == "feedback_unavailable"


@pytest.mark.asyncio
async def test_feedback_unknown_rating(handler_factory):
    h = handler_factory()
    _, ai = _seed_session(h)
    await h._handle_feedback_message(MagicMock(), {"message_id": ai.message_id, "rating": "meh"})
    assert h._sent[0]["code"] == "invalid_feedback"
    assert h.feedback_store.create.await_count == 0


@pytest.mark.asyncio
async def test_feedback_unknown_message_id(handler_factory):
    h = handler_factory()
    _seed_session(h)
    await h._handle_feedback_message(MagicMock(), {"message_id": "nope", "rating": "positive"})
    assert h._sent[0]["code"] == "invalid_feedback"


@pytest.mark.asyncio
async def test_feedback_correction_requires_text(handler_factory):
    h = handler_factory()
    _, ai = _seed_session(h)
    await h._handle_feedback_message(MagicMock(), {"message_id": ai.message_id, "rating": "correction"})
    assert h._sent[0]["code"] == "invalid_feedback"
    assert "correction_text" in h._sent[0]["detail"]


@pytest.mark.asyncio
async def test_feedback_missing_required_fields(handler_factory):
    h = handler_factory()
    _seed_session(h)

    await h._handle_feedback_message(MagicMock(), {"rating": "positive"})
    assert h._sent[-1]["code"] == "invalid_feedback"

    await h._handle_feedback_message(MagicMock(), {"message_id": "x"})
    assert h._sent[-1]["code"] == "invalid_feedback"


@pytest.mark.asyncio
async def test_feedback_no_session_returns_error(handler_factory):
    h = handler_factory()
    h.session_manager.get_session.return_value = None
    await h._handle_feedback_message(MagicMock(), {"message_id": "x", "rating": "positive"})
    # C2: missing-session uses the feedback envelope, not the legacy error one.
    h._send_error.assert_not_awaited()
    assert h._sent[-1]["type"] == "error"
    assert h._sent[-1]["code"] == "feedback_unavailable"
    assert h._sent[-1]["detail"] == "Session not found"


@pytest.mark.asyncio
async def test_feedback_unexpected_exception_does_not_leak_detail(handler_factory):
    """C3: catch-all path must not echo str(exc) (could leak SQL/driver internals)."""
    h = handler_factory()
    _, ai = _seed_session(h)
    h.feedback_store.create.side_effect = RuntimeError('duplicate key value violates unique constraint "feedback_pkey"')
    await h._handle_feedback_message(
        MagicMock(),
        {"message_id": ai.message_id, "rating": "positive"},
    )
    payload = h._sent[-1]
    assert payload["code"] == "feedback_error"
    assert payload["detail"] == "Internal error while processing feedback"
    assert "feedback_pkey" not in payload["detail"]
    assert h._total_errors == 1


# ---------------------------------------------------------------------------
# ChatMessage.message_id round-trip
# ---------------------------------------------------------------------------


def test_chat_message_has_message_id_and_round_trips():
    m = ChatMessage(role="assistant", content="hi")
    assert m.message_id
    m2 = ChatMessage.from_dict(m.to_dict())
    assert m2.message_id == m.message_id


# ---------------------------------------------------------------------------
# FeedbackStats
# ---------------------------------------------------------------------------


def test_feedback_stats_defaults():
    s = FeedbackStats()
    assert s.total == 0
    assert s.by_status == {}
    assert s.by_rating == {}


def test_feedback_stats_round_trip():
    s = FeedbackStats(
        total=3,
        by_status={ReviewStatus.PENDING_REVIEW: 2, ReviewStatus.APPROVED: 1},
        by_rating={Rating.POSITIVE: 2, Rating.CORRECTION: 1},
    )
    dumped = s.model_dump()
    assert dumped["total"] == 3
    assert dumped["by_status"][ReviewStatus.PENDING_REVIEW] == 2


# ---------------------------------------------------------------------------
# Plugin startup: partial-open failure must close the pool (C1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_open_feedback_store_closes_pool_on_failure():
    """If FeedbackStore.open() raises (e.g. schema bootstrap fails after the
    pool was opened), the plugin must explicitly close the partially-opened
    pool — otherwise psycopg's background task and acquired connections leak.
    """
    from auto_bedrock_chat_fastapi.plugin import BedrockChatPlugin

    plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
    store = MagicMock()
    store.open = AsyncMock(side_effect=RuntimeError("schema bootstrap failed"))
    store.close = AsyncMock()
    plugin._feedback_store = store
    plugin.websocket_handler = MagicMock()
    plugin.websocket_handler.feedback_store = store

    await plugin._startup_open_feedback_store()

    store.open.assert_awaited_once()
    store.close.assert_awaited_once()
    assert plugin._feedback_store is None
    assert plugin.websocket_handler.feedback_store is None


@pytest.mark.asyncio
async def test_startup_open_feedback_store_swallows_close_failure():
    """A secondary failure in close() must not mask the original disable path."""
    from auto_bedrock_chat_fastapi.plugin import BedrockChatPlugin

    plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
    store = MagicMock()
    store.open = AsyncMock(side_effect=RuntimeError("primary failure"))
    store.close = AsyncMock(side_effect=RuntimeError("secondary failure"))
    plugin._feedback_store = store
    plugin.websocket_handler = MagicMock()
    plugin.websocket_handler.feedback_store = store

    await plugin._startup_open_feedback_store()  # must not raise

    store.close.assert_awaited_once()
    assert plugin._feedback_store is None
    assert plugin.websocket_handler.feedback_store is None


@pytest.mark.asyncio
async def test_startup_open_feedback_store_happy_path():
    from auto_bedrock_chat_fastapi.plugin import BedrockChatPlugin

    plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
    store = MagicMock()
    store.open = AsyncMock()
    store.close = AsyncMock()
    plugin._feedback_store = store
    plugin.websocket_handler = MagicMock()
    plugin.websocket_handler.feedback_store = store

    await plugin._startup_open_feedback_store()

    store.open.assert_awaited_once()
    store.close.assert_not_awaited()
    assert plugin._feedback_store is store
    assert plugin.websocket_handler.feedback_store is store
