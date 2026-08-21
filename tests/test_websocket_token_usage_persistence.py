"""Tests for wiring token-usage collaborators into the ``chat_graph.ainvoke()``
call from ``WebSocketChatHandler._handle_chat_message`` (XMGPLAT-11215).

Token-usage *recording* itself was moved into ``token_usage_node`` -- a graph
node that runs for every ``chat_graph`` caller, not just this handler. See
``tests/test_token_usage_node.py`` for node-level behavior (no-op cases,
model_id/session_id fallbacks, failure swallowing) and
``TestTokenUsageGraphIntegration`` in ``tests/test_graph_basic.py`` for a full
non-WebSocket ``ainvoke()`` exercise. This file only verifies that the
handler passes ``token_usage_store`` / ``session_id`` / ``user_id`` into
``config["configurable"]`` correctly, and that response delivery is
unaffected by whether a store is configured.
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
    ``graph_state`` from the chat graph. Returns ``(handler, chat_graph)``
    so tests can inspect the ``ainvoke()`` call args."""
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
    return handler, chat_graph


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


def _graph_state():
    return {
        "messages": [_assistant_message()],
        "metadata": {
            "tool_call_rounds": 0,
            "total_tool_calls": 0,
            "preprocessing_applied": False,
            "input_tokens": 120,
            "output_tokens": 240,
        },
        "kb_results": [],
    }


async def _drive(handler):
    websocket = MagicMock()
    websocket.send_json = AsyncMock()
    await handler._handle_chat_message(websocket, {"message": "hello"})
    sent = [call.args[0] for call in websocket.send_json.call_args_list]
    return sent


def test_ainvoke_configurable_includes_token_usage_store_and_session_info():
    token_usage_store = MagicMock()
    handler, chat_graph = _make_handler(_graph_state(), token_usage_store=token_usage_store)

    sent = asyncio.run(_drive(handler))

    ai_responses = [m for m in sent if m.get("type") == "ai_response"]
    assert ai_responses, f"no ai_response sent; got {sent}"

    chat_graph.ainvoke.assert_awaited_once()
    _, kwargs = chat_graph.ainvoke.await_args
    configurable = kwargs["config"]["configurable"]
    assert configurable["token_usage_store"] is token_usage_store
    assert configurable["session_id"] == "session-123"
    assert configurable["user_id"] == "alice"


def test_ainvoke_configurable_token_usage_store_none_when_unconfigured():
    """Handler must pass token_usage_store=None through cleanly (the default
    / disabled case) rather than omitting the key or raising."""
    handler, chat_graph = _make_handler(_graph_state(), token_usage_store=None)

    sent = asyncio.run(_drive(handler))

    ai_responses = [m for m in sent if m.get("type") == "ai_response"]
    assert ai_responses, f"no ai_response sent; got {sent}"

    chat_graph.ainvoke.assert_awaited_once()
    _, kwargs = chat_graph.ainvoke.await_args
    configurable = kwargs["config"]["configurable"]
    assert configurable["token_usage_store"] is None
    # session_id/user_id are passed through regardless of whether a store is
    # configured -- token_usage_node itself decides whether to act on them.
    assert configurable["session_id"] == "session-123"
    assert configurable["user_id"] == "alice"
