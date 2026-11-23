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

from autolangchat.history import with_final_usage  # noqa: E402
from autolangchat.usage.ledger import UsageLedger  # noqa: E402
from autolangchat.websocket_handler import WebSocketChatHandler, _PendingTurnUsage  # noqa: E402


def _make_handler(graph_state, token_usage_store=None):
    """Build a handler with all collaborators mocked, wired to return
    ``graph_state`` from the chat graph. Returns ``(handler, chat_graph)``
    so tests can inspect the ``ainvoke()`` call args."""
    config = MagicMock()
    config.model_copy.return_value = config
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


def _failing_graph_handler(store):
    handler, chat_graph = _make_handler(_graph_state(), token_usage_store=store)
    handler.config.token_usage_enabled = True
    handler.config.token_usage_write_timeout = 1.0
    handler.config.pricing_table = None

    async def _fail(state, config):
        config["configurable"]["usage_ledger"].record(
            purpose="chat", provider="anthropic", model_id="anthropic.unpriced", input_tokens=100, output_tokens=10
        )
        raise RuntimeError("bedrock down")

    chat_graph.ainvoke = AsyncMock(side_effect=_fail)
    return handler


def test_calls_made_before_the_graph_failed_are_persisted_as_a_final_turn():
    store = MagicMock()
    store.record_turn = AsyncMock()
    store.record_events = AsyncMock()
    handler = _failing_graph_handler(store)

    sent = asyncio.run(_drive(handler))

    assert [m for m in sent if m.get("type") == "ai_response"][0]["error"] is True
    _, kwargs = store.record_turn.await_args
    assert (kwargs["input_tokens"], kwargs["output_tokens"], kwargs["final"]) == (100, 10, True)
    (events,) = store.record_events.await_args.args
    assert len(events) == 1


def test_a_failed_graph_without_recorded_calls_writes_nothing():
    store = MagicMock()
    store.record_turn = AsyncMock()
    handler = _failing_graph_handler(store)
    handler.chat_graph.ainvoke = AsyncMock(side_effect=RuntimeError("bedrock down"))

    asyncio.run(_drive(handler))

    store.record_turn.assert_not_awaited()


def test_a_completed_graph_is_not_finalized_again_by_the_handler():
    store = MagicMock()
    store.record_turn = AsyncMock()
    handler, _ = _make_handler(_graph_state(), token_usage_store=store)

    asyncio.run(_drive(handler))

    store.record_turn.assert_not_awaited()


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


def _checkpointed_history():
    pending = {"role": "assistant", "content": "a", "metadata": {"message_id": "msg-abc", "usage": {"final": False}}}
    other = {"role": "assistant", "content": "b", "metadata": {"message_id": "msg-other", "usage": {"final": True}}}
    return [{"role": "user", "content": "q"}, pending, other]


def test_final_usage_is_kept_with_the_conversation_and_the_checkpoint_is_left_alone():
    handler = _title_handler()
    handler.conversation_store.set_final_usage = AsyncMock()
    handler.chat_graph.aupdate_state = AsyncMock()
    turn_usage = _turn_usage(_ledger_with_chat_and_summaries(), enabled=False)._replace(conversation_id="conv-1")

    asyncio.run(_run_title(handler, turn_usage))

    (conversation_id, message_id, usage), _ = handler.conversation_store.set_final_usage.await_args
    assert (conversation_id, message_id) == ("conv-1", "msg-abc")
    assert (usage["input_tokens"], usage["final"]) == (550, True)
    handler.chat_graph.aupdate_state.assert_not_awaited()


def test_usage_updated_is_still_sent_when_the_final_usage_cannot_be_kept():
    handler = _title_handler()
    handler.conversation_store.set_final_usage = AsyncMock(side_effect=RuntimeError("db down"))
    turn_usage = _turn_usage(_ledger_with_chat_and_summaries(), enabled=False)._replace(conversation_id="conv-1")

    sent = asyncio.run(_run_title(handler, turn_usage))

    assert [m["usage"]["final"] for m in sent if m.get("type") == "usage_updated"] == [True]


def test_loaded_history_shows_the_kept_final_usage_in_place_of_the_pending_one():
    handler = _title_handler()
    handler.conversation_store.get_final_usage = AsyncMock(return_value={"msg-abc": {"final": True}})
    history = _checkpointed_history()

    shown = with_final_usage(history, asyncio.run(handler._final_usage("conv-1")))

    assert shown[1]["metadata"] == {"message_id": "msg-abc", "usage": {"final": True}}
    assert (shown[0], shown[2]) == (history[0], history[2])
    # The checkpointed messages themselves are not modified.
    assert history[1]["metadata"]["usage"] == {"final": False}


def test_history_is_unchanged_when_the_final_usage_cannot_be_read():
    handler = _title_handler()
    handler.conversation_store.get_final_usage = AsyncMock(side_effect=RuntimeError("db down"))
    history = _checkpointed_history()

    assert with_final_usage(history, asyncio.run(handler._final_usage("conv-1"))) == history
