"""Tests for per-turn token-usage persistence in the WebSocket handler (XMGPLAT-10746).

Verifies that ``WebSocketChatHandler._handle_chat_message``:

  (a) calls ``token_usage_store.record_turn(...)`` with the expected
      arguments when a store is configured and token counts are present;
  (b) still delivers the ``ai_response`` to the client (and does not
      propagate) when ``record_turn`` raises.
"""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Sibling test modules install lightweight ``autolangchat`` package stubs into
# ``sys.modules`` at import time. If any survive collection they shadow the
# real package and break the import below. Drop stub entries (manually
# created modules have ``__spec__ is None``) so Python re-imports the
# genuine packages here (mirrors test_websocket_response_metadata.py).
for _name in [n for n in list(sys.modules) if n == "autolangchat" or n.startswith("autolangchat.")]:
    if getattr(sys.modules.get(_name), "__spec__", None) is None:
        del sys.modules[_name]

from autolangchat.websocket_handler import WebSocketChatHandler  # noqa: E402


def _make_handler(graph_state, token_usage_store=None):
    """Build a handler with all collaborators mocked, wired to return
    ``graph_state`` from the chat graph."""
    config = MagicMock()
    config.timeout = 30.0
    config.model_id = "us.anthropic.claude-sonnet-4-6"
    config.require_tool_auth = False
    config.include_auth_info_in_prompts = False
    config.feedback_allow_anonymous = False

    session = SimpleNamespace(
        session_id="session-123",
        user_id="alice",
        credentials=None,
        auth_handler=None,
        metadata={},
    )

    session_manager = MagicMock()
    session_manager.get_session = AsyncMock(return_value=session)

    chat_graph = MagicMock()
    chat_graph.ainvoke = AsyncMock(return_value=graph_state)

    handler = WebSocketChatHandler(
        session_manager=session_manager,
        config=config,
        chat_graph=chat_graph,
        token_usage_store=token_usage_store,
    )
    return handler


def _assistant_message():
    return {
        "role": "assistant",
        "content": "Here is the answer.",
        "tool_calls": [],
        "tool_results": [],
        "metadata": {
            "message_id": "msg-abc",
            "model_id": "from-llm-response",
            "usage": {"input_tokens": 50, "output_tokens": 80},
            "timestamp": "2026-01-01T10:00:05",
        },
    }


def _graph_metadata():
    return {
        "tool_call_rounds": 0,
        "total_tool_calls": 0,
        "preprocessing_applied": False,
        "input_tokens": 120,
        "output_tokens": 240,
    }


def _graph_state():
    return {
        "messages": [_assistant_message()],
        "metadata": _graph_metadata(),
        "kb_results": [],
    }


def _graph_state_without_token_counts():
    """Graph state where the LLM call never surfaced usage metadata
    (e.g. a provider response that omits ``usage_metadata``)."""
    metadata = _graph_metadata()
    del metadata["input_tokens"]
    del metadata["output_tokens"]
    return {
        "messages": [_assistant_message()],
        "metadata": metadata,
        "kb_results": [],
    }


async def _drive(handler):
    websocket = MagicMock()
    websocket.send_json = AsyncMock()
    await handler._handle_chat_message(websocket, {"message": "hello"})
    sent = [call.args[0] for call in websocket.send_json.call_args_list]
    return sent


def test_record_turn_called_with_expected_args():
    token_usage_store = MagicMock()
    token_usage_store.record_turn = AsyncMock()
    handler = _make_handler(_graph_state(), token_usage_store=token_usage_store)

    sent = asyncio.run(_drive(handler))

    ai_responses = [m for m in sent if m.get("type") == "ai_response"]
    assert ai_responses, f"no ai_response sent; got {sent}"

    token_usage_store.record_turn.assert_awaited_once()
    _, kwargs = token_usage_store.record_turn.await_args
    assert kwargs["turn_id"] == "msg-abc"
    assert kwargs["session_id"] == "session-123"
    assert kwargs["user_id"] == "alice"
    assert kwargs["model_id"] == "us.anthropic.claude-sonnet-4-6"
    assert kwargs["input_tokens"] == 120
    assert kwargs["output_tokens"] == 240


def test_record_turn_not_called_when_store_unconfigured():
    handler = _make_handler(_graph_state(), token_usage_store=None)

    sent = asyncio.run(_drive(handler))

    ai_responses = [m for m in sent if m.get("type") == "ai_response"]
    assert ai_responses, f"no ai_response sent; got {sent}"
    # Nothing to assert on record_turn — there is no store — the point of
    # this test is that ``_handle_chat_message`` doesn't blow up when
    # ``token_usage_store`` is ``None`` (the default / disabled case).


def test_record_turn_not_called_when_token_counts_missing():
    """If the graph never surfaced input_tokens/output_tokens (e.g. the LLM
    response lacked usage metadata), record_turn must not be called at all
    — there's nothing meaningful to persist."""
    token_usage_store = MagicMock()
    token_usage_store.record_turn = AsyncMock()
    handler = _make_handler(_graph_state_without_token_counts(), token_usage_store=token_usage_store)

    sent = asyncio.run(_drive(handler))

    ai_responses = [m for m in sent if m.get("type") == "ai_response"]
    assert ai_responses, f"no ai_response sent; got {sent}"
    token_usage_store.record_turn.assert_not_awaited()


def test_record_turn_failure_does_not_prevent_response_delivery():
    token_usage_store = MagicMock()
    token_usage_store.record_turn = AsyncMock(side_effect=RuntimeError("db unavailable"))
    handler = _make_handler(_graph_state(), token_usage_store=token_usage_store)

    # Must not raise, even though record_turn fails.
    sent = asyncio.run(_drive(handler))

    ai_responses = [m for m in sent if m.get("type") == "ai_response"]
    assert ai_responses, f"no ai_response sent; got {sent}"
    assert ai_responses[-1].get("error") is not True
    token_usage_store.record_turn.assert_awaited_once()
