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

from auto_bedrock_chat_fastapi.db import AuthenticatedUserAuthorizer
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


def test_reviewer_id_whitespace_rejected_for_decided_status():
    """C7: reviewer_id is stripped; whitespace-only on a decided status fails."""
    from datetime import datetime, timezone

    with pytest.raises(ValidationError, match="reviewer_id"):
        _entry(
            review_status=ReviewStatus.APPROVED,
            reviewer_id="   ",
            reviewed_at=datetime.now(timezone.utc),
        )


def test_reviewer_id_stripped_to_value():
    """C7: surrounding whitespace is trimmed."""
    from datetime import datetime, timezone

    e = _entry(
        review_status=ReviewStatus.APPROVED,
        reviewer_id="  alice  ",
        reviewed_at=datetime.now(timezone.utc),
    )
    assert e.reviewer_id == "alice"


def test_reviewer_tags_stripped_and_blanks_dropped():
    """C7: reviewer_tags entries are stripped and empty/whitespace tags removed."""
    e = _entry(reviewer_tags=["  bug ", "", "   ", "regression"])
    assert e.reviewer_tags == ["bug", "regression"]


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


def test_decided_can_update_in_same_status():
    # Admins can re-submit with corrected tags/comment without changing decision.
    assert ReviewStatus.APPROVED in ALLOWED_REVIEW_TRANSITIONS[ReviewStatus.APPROVED]
    assert ReviewStatus.REJECTED in ALLOWED_REVIEW_TRANSITIONS[ReviewStatus.REJECTED]


# ---------------------------------------------------------------------------
# Default authorizer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user_id,allowed",
    [
        ("alice", True),
        ("u-123", True),
        (None, False),
        ("", False),
        ("   ", False),
        ("\t\n", False),
    ],
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
        "type": "feedback_error",
        "code": "unauthorized_feedback",
        "message": "You are not authorized to submit feedback",
        "message_id": "x",
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
async def test_feedback_positive_forbids_correction_text(handler_factory):
    """correction_text is only valid for negative ratings (positive + text → 422)."""
    h = handler_factory()
    _, ai = _seed_session(h)
    await h._handle_feedback_message(
        MagicMock(),
        {"message_id": ai.message_id, "rating": "positive", "correction_text": "nope"},
    )
    assert h._sent[0]["code"] == "invalid_feedback"
    assert "correction_text" in h._sent[0]["message"]


@pytest.mark.asyncio
async def test_feedback_missing_required_fields(handler_factory):
    h = handler_factory()
    _seed_session(h)

    await h._handle_feedback_message(MagicMock(), {"rating": "positive"})
    assert h._sent[-1]["code"] == "invalid_feedback"

    await h._handle_feedback_message(MagicMock(), {"message_id": "x"})
    assert h._sent[-1]["code"] == "invalid_feedback"


@pytest.mark.asyncio
async def test_feedback_score_out_of_range_caught_as_invalid(handler_factory):
    """C13: pydantic.ValidationError must be mapped to invalid_feedback."""
    h = handler_factory()
    _, ai = _seed_session(h)
    await h._handle_feedback_message(
        MagicMock(),
        {"message_id": ai.message_id, "rating": "positive", "score": 99},
    )
    assert h._sent[-1]["code"] == "invalid_feedback"
    assert "score" in h._sent[-1]["message"]


@pytest.mark.asyncio
async def test_feedback_no_session_returns_error(handler_factory):
    h = handler_factory()
    h.session_manager.get_session.return_value = None
    await h._handle_feedback_message(MagicMock(), {"message_id": "x", "rating": "positive"})
    # C2: missing-session uses the feedback envelope, not the legacy error one.
    h._send_error.assert_not_awaited()
    assert h._sent[-1]["type"] == "feedback_error"
    assert h._sent[-1]["code"] == "feedback_unavailable"
    assert h._sent[-1]["message"] == "Session not found"


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
    assert payload["type"] == "feedback_error"
    assert payload["code"] == "feedback_error"
    assert payload["message"] == "Internal error while processing feedback"
    assert "feedback_pkey" not in payload["message"]
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
        by_rating={Rating.POSITIVE: 2, Rating.NEGATIVE: 1},
        with_correction=1,
    )
    dumped = s.model_dump()
    assert dumped["total"] == 3
    assert dumped["by_status"][ReviewStatus.PENDING_REVIEW] == 2
    assert dumped["with_correction"] == 1


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


# ---------------------------------------------------------------------------
# C6: feedback for tool-call assistant messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_succeeds_for_tool_call_message(handler_factory):
    """C6: tool-call assistant turns must be feedback-able via message_id."""
    h = handler_factory()
    sess = ChatSession(session_id="s1", websocket=MagicMock(), user_id="u1")
    ai = ChatMessage(
        role="assistant",
        content="",  # tool-call turns may have empty content
        tool_calls=[{"id": "t1", "name": "lookup", "input": {}}],
        tool_results=[{"tool_use_id": "t1", "content": "ok"}],
        metadata={
            "query": "search docs",
            "kb_sources": [],
            "model_id": "anthropic.claude-test",
        },
    )
    sess.conversation_history = [ai]
    h.session_manager.get_session.return_value = sess
    h.session_manager.get_conversation_history.return_value = [ai]

    persisted = _entry(id=uuid4())
    h.feedback_store.create.return_value = persisted

    await h._handle_feedback_message(
        MagicMock(),
        {"message_id": ai.message_id, "rating": "positive"},
    )

    assert h.feedback_store.create.await_count == 1
    assert h._sent[0]["type"] == "feedback_ack"


# ---------------------------------------------------------------------------
# C9: update_review rejects whitespace-only reviewer_id without DB round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_review_rejects_whitespace_reviewer_id():
    from auto_bedrock_chat_fastapi.db.feedback_postgres import PostgresFeedbackStore as FeedbackStore

    store = FeedbackStore.__new__(FeedbackStore)
    store._pool = MagicMock()  # must not be touched
    with pytest.raises(ValueError, match="reviewer_id is required"):
        await store.update_review(
            uuid4(),
            ReviewStatus.APPROVED,
            reviewer_id="   ",
            tags=[],
            comment=None,
        )
    store._pool.connection.assert_not_called()


@pytest.mark.asyncio
async def test_update_review_rejects_empty_reviewer_id():
    from auto_bedrock_chat_fastapi.db.feedback_postgres import PostgresFeedbackStore as FeedbackStore

    store = FeedbackStore.__new__(FeedbackStore)
    store._pool = MagicMock()
    with pytest.raises(ValueError, match="reviewer_id is required"):
        await store.update_review(
            uuid4(),
            ReviewStatus.REJECTED,
            reviewer_id="",
            tags=[],
            comment=None,
        )
    store._pool.connection.assert_not_called()


# ---------------------------------------------------------------------------
# C12: list_by_tags normalizes caller-provided tags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_by_tags_normalizes_and_skips_empty():
    from auto_bedrock_chat_fastapi.db.feedback_postgres import PostgresFeedbackStore as FeedbackStore

    store = FeedbackStore.__new__(FeedbackStore)
    store._fetch_all = AsyncMock(return_value=[])

    result = await store.list_by_tags([" perf ", "", "  ", "bug"])

    assert result == []
    assert store._fetch_all.await_count == 1
    args, _ = store._fetch_all.call_args
    # params tuple is positional arg[1]
    assert args[1] == (["perf", "bug"],)


@pytest.mark.asyncio
async def test_list_by_tags_all_empty_short_circuits():
    from auto_bedrock_chat_fastapi.db.feedback_postgres import PostgresFeedbackStore as FeedbackStore

    store = FeedbackStore.__new__(FeedbackStore)
    store._fetch_all = AsyncMock(return_value=[])

    result = await store.list_by_tags(["", "   ", "\t"])

    assert result == []
    store._fetch_all.assert_not_called()


# ---------------------------------------------------------------------------
# C11: update_review normalizes reviewer tags before persisting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_review_normalizes_tags():
    from auto_bedrock_chat_fastapi.db.feedback_postgres import PostgresFeedbackStore as FeedbackStore

    store = FeedbackStore.__new__(FeedbackStore)

    # Capture the UPDATE call's params
    executed: list = []

    fid = uuid4()
    row = (
        fid,
        "sess-1",
        "alice",
        "q",
        "a",
        "positive",
        None,
        None,
        None,
        "approved",
        "reviewer-1",
        ["bug", "regression"],
        None,
        None,
        None,
        None,
    )

    cur = MagicMock()
    cur.__aenter__ = AsyncMock(return_value=cur)
    cur.__aexit__ = AsyncMock(return_value=False)

    async def _execute(sql, params):
        executed.append((sql, params))

    async def _fetchone():
        # First call returns current status; second returns RETURNING row
        return ("pending_review",) if len(executed) == 1 else row

    cur.execute = _execute
    cur.fetchone = _fetchone

    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.cursor = MagicMock(return_value=cur)
    conn.commit = AsyncMock()

    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn)
    store._pool = pool

    # Stub the row mapper to avoid full model construction
    store._row_to_entry = MagicMock(return_value="ENTRY")

    result = await store.update_review(
        fid,
        ReviewStatus.APPROVED,
        reviewer_id="reviewer-1",
        tags=[" bug ", "", "regression", "bug", "  "],
        comment=None,
    )

    assert result == "ENTRY"
    # Second execute is the UPDATE; params[2] is the normalized tags list
    update_params = executed[1][1]
    assert update_params[2] == ["bug", "regression"]
