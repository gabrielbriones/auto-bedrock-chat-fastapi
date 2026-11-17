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

from autolangchat.usage.ledger import UsageLedger  # noqa: E402
from autolangchat.websocket_handler import WebSocketChatHandler, _PendingTurnUsage  # noqa: E402


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


def test_ainvoke_configurable_includes_a_fresh_usage_ledger():
    handler, chat_graph = _make_handler(_graph_state())

    asyncio.run(_drive(handler))

    _, kwargs = chat_graph.ainvoke.await_args
    configurable = kwargs["config"]["configurable"]
    assert isinstance(configurable["usage_ledger"], UsageLedger)
    assert configurable["usage_ledger"].events == []
    # No conversation was created, so no title call will follow this turn.
    assert configurable["usage_finalized_later"] is False


def test_ai_response_metadata_carries_the_turn_usage_summary():
    state = _graph_state()
    summary = {
        "input_tokens": 300,
        "output_tokens": 40,
        "cache_read": 100,
        "cache_write": 0,
        "estimated_cost": 0.0015,
        "currency": "USD",
        "final": False,
    }
    state["messages"][-1]["metadata"]["usage"] = summary
    handler, _ = _make_handler(state)

    sent = asyncio.run(_drive(handler))

    (ai_response,) = [m for m in sent if m.get("type") == "ai_response"]
    assert ai_response["message_id"] == "msg-abc"
    assert ai_response["metadata"]["usage"] == summary


# ---------------------------------------------------------------------------
# Late ``usage_updated`` after the title call
# ---------------------------------------------------------------------------


def _ledger_with_chat_and_summaries():
    """Two tool rounds plus three summarization chunks: five events before the title."""
    ledger = UsageLedger("msg-abc")
    for purpose in ("summarization", "summarization", "summarization", "chat", "chat"):
        ledger.record(
            purpose=purpose, provider="anthropic", model_id="anthropic.unpriced", input_tokens=100, output_tokens=10
        )
    return ledger


class _TitleLLM:
    async def ainvoke(self, messages):
        return SimpleNamespace(content="Okta SSO Setup", usage_metadata={"input_tokens": 50, "output_tokens": 5})


def _title_handler(token_usage_store=None):
    handler, _ = _make_handler(_graph_state(), token_usage_store=token_usage_store)
    handler.config.conversation_title_model_id = "anthropic.title-model"
    handler.conversation_store = MagicMock()
    handler.conversation_store.get_conversation = AsyncMock(return_value={"title": None})
    handler.conversation_store.set_title_if_unset = AsyncMock(return_value=True)
    handler._build_title_llm_client = lambda: _TitleLLM()
    return handler


def _turn_usage(ledger, enabled):
    chat_config = SimpleNamespace(pricing_table=None, token_usage_enabled=enabled, token_usage_write_timeout=1.0)
    return _PendingTurnUsage(
        ledger=ledger, session_id="session-123", user_id="alice", model_id="anthropic.unpriced", chat_config=chat_config
    )


async def _run_title(handler, turn_usage):
    websocket = MagicMock()
    websocket.send_json = AsyncMock()
    await handler._generate_and_apply_title(
        websocket, "conv-1", [{"role": "user", "content": "hi"}], turn_usage=turn_usage
    )
    return [call.args[0] for call in websocket.send_json.call_args_list]


def test_title_call_is_added_to_the_turn_and_sent_as_final_usage_updated():
    store = MagicMock()
    store.record_turn = AsyncMock()
    store.record_events = AsyncMock()
    handler = _title_handler(store)
    ledger = _ledger_with_chat_and_summaries()

    sent = asyncio.run(_run_title(handler, _turn_usage(ledger, enabled=True)))

    assert [e.purpose for e in ledger.events][-1] == "title"
    (usage_updated,) = [m for m in sent if m.get("type") == "usage_updated"]
    assert usage_updated["message_id"] == "msg-abc"
    # Sum of all six events: 5 x (100, 10) + title (50, 5).
    assert usage_updated["usage"]["input_tokens"] == 550
    assert usage_updated["usage"]["output_tokens"] == 55
    assert usage_updated["usage"]["final"] is True
    assert usage_updated["usage"]["estimated_cost"] is None
    _, kwargs = store.record_turn.await_args
    assert (kwargs["turn_id"], kwargs["input_tokens"], kwargs["final"]) == ("msg-abc", 550, True)
    (events,) = store.record_events.await_args.args
    assert len(events) == 6


def test_usage_updated_is_sent_without_writing_when_persistence_is_disabled():
    store = MagicMock()
    store.record_turn = AsyncMock()
    store.record_events = AsyncMock()
    handler = _title_handler(store)

    sent = asyncio.run(_run_title(handler, _turn_usage(_ledger_with_chat_and_summaries(), enabled=False)))

    assert [m["usage"]["input_tokens"] for m in sent if m.get("type") == "usage_updated"] == [550]
    store.record_turn.assert_not_awaited()
    store.record_events.assert_not_awaited()


def test_turn_is_finalized_even_when_the_title_was_already_set():
    handler = _title_handler()
    handler.conversation_store.get_conversation = AsyncMock(return_value={"title": "Renamed"})
    ledger = _ledger_with_chat_and_summaries()

    sent = asyncio.run(_run_title(handler, _turn_usage(ledger, enabled=True)))

    (usage_updated,) = [m for m in sent if m.get("type") == "usage_updated"]
    assert usage_updated["usage"]["input_tokens"] == 500
    assert usage_updated["usage"]["final"] is True
