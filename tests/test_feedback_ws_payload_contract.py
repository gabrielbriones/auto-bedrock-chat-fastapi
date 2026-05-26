"""Regression tests pinning the WebSocket *payload contract* between the
chat-client JS and ``WebSocketChatHandler._handle_feedback_message``.

These tests intentionally hand-build the exact JSON shapes emitted by
:mod:`auto_bedrock_chat_fastapi.static.chat-client.js` for the T3/T4
flows. If a future refactor renames a field or changes a rating value
on either side, one of these will fail loudly.

The handshake mechanics (auth, store-disabled, validation errors) are
already covered exhaustively by ``tests/test_feedback_store.py``; this
file is narrowly scoped to "the bytes the browser actually sends".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from auto_bedrock_chat_fastapi.db import AuthenticatedUserAuthorizer
from auto_bedrock_chat_fastapi.models import FeedbackEntry, Rating
from auto_bedrock_chat_fastapi.session_manager import ChatMessage, ChatSession
from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler


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
    h = WebSocketChatHandler.__new__(WebSocketChatHandler)
    h._total_errors = 0
    h.config = MagicMock(model_id="anthropic.claude-test")
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

    h._send_message = send  # type: ignore[assignment]
    h._send_error = AsyncMock()
    return h


def _seed(h):
    sess = ChatSession(session_id="s1", websocket=MagicMock(), user_id="u1")
    ai = ChatMessage(
        role="assistant",
        content="answer text",
        metadata={"query": "what is IPC?", "model_id": "anthropic.claude-test"},
    )
    sess.conversation_history = [ai]
    h.session_manager.get_session.return_value = sess
    h.session_manager.get_conversation_history.return_value = [ai]
    return ai


@pytest.mark.asyncio
async def test_positive_payload_matches_js_contract(handler):
    """T3: the exact JS payload for a 👍 click is accepted by the handler.

    Mirrors ``_handlePositiveClick`` → ``_sendFeedback`` in
    ``chat-client.js`` (rating value comes from the ``Rating`` enum).
    """
    ai = _seed(handler)
    handler.feedback_store.create.return_value = _entry(id=uuid4())

    js_payload = {
        "type": "feedback",
        "message_id": ai.message_id,
        "rating": "positive",
    }

    # _handle_feedback_message receives the inner dict (the dispatcher
    # has already stripped the outer envelope), so we drop ``type``.
    await handler._handle_feedback_message(
        MagicMock(),
        {k: v for k, v in js_payload.items() if k != "type"},
    )

    assert handler.feedback_store.create.await_count == 1
    assert handler._sent[-1]["type"] == "feedback_ack"
    # Persisted rating must match the enum value the JS hard-codes.
    created = handler.feedback_store.create.await_args.args[0]
    assert created.rating is Rating.POSITIVE


@pytest.mark.asyncio
async def test_negative_payload_with_correction_matches_js_contract(handler):
    """T4: the exact JS payload for a 👎 submit (with both optional
    free-text fields filled in) is accepted by the handler.

    Mirrors ``_submitCorrectionForm`` in ``chat-client.js``. The rating
    stays ``"negative"`` whether or not the user fills in
    ``correction_text``; the optional fix text is an orthogonal signal
    carried alongside the rating.
    """
    ai = _seed(handler)
    handler.feedback_store.create.return_value = _entry(
        id=uuid4(),
        rating=Rating.NEGATIVE,
    )

    js_payload = {
        "type": "feedback",
        "message_id": ai.message_id,
        "rating": "negative",
        "correction_text": "The correct answer is X.",
        "user_comment": "The bot hallucinated step 3.",
    }

    await handler._handle_feedback_message(
        MagicMock(),
        {k: v for k, v in js_payload.items() if k != "type"},
    )

    assert handler.feedback_store.create.await_count == 1
    assert handler._sent[-1]["type"] == "feedback_ack"
    created = handler.feedback_store.create.await_args.args[0]
    assert created.correction_text == "The correct answer is X."
    assert created.user_comment == "The bot hallucinated step 3."


@pytest.mark.asyncio
async def test_negative_payload_without_optional_fields_matches_js_contract(handler):
    """T4: when the user submits 👎 with both textareas empty, the JS
    omits ``correction_text``/``user_comment`` from the payload entirely
    (rather than sending empty strings). Verify the handler accepts
    that minimal shape.
    """
    ai = _seed(handler)
    handler.feedback_store.create.return_value = _entry(
        id=uuid4(),
        rating=Rating.NEGATIVE,
    )

    js_payload = {
        "type": "feedback",
        "message_id": ai.message_id,
        "rating": "negative",
    }

    await handler._handle_feedback_message(
        MagicMock(),
        {k: v for k, v in js_payload.items() if k != "type"},
    )

    assert handler.feedback_store.create.await_count == 1
    assert handler._sent[-1]["type"] == "feedback_ack"
    created = handler.feedback_store.create.await_args.args[0]
    assert created.correction_text is None
    assert created.user_comment is None
