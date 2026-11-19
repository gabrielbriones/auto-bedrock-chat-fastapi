"""Tests for token_usage_node — the terminal graph node that persists
per-turn token usage after citation_boost (XMGPLAT-11215).

Follows the same "optional post-turn node, no-op unless configured" shape
as citation_boost_node (see TestCitationBoostNode in
tests/test_kb_credibility_signals.py). This runs for *every* chat_graph
caller, not just WebSocketChatHandler -- see TestTokenUsageGraphIntegration
in tests/test_graph_basic.py for a full non-WebSocket ainvoke() exercise,
and tests/test_websocket_token_usage_persistence.py for the WebSocket
handler's ainvoke()-configurable wiring.
"""

import asyncio
import uuid
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from autolangchat.graph.nodes.token_usage import token_usage_node
from autolangchat.usage.ledger import UsageLedger


class _TokenUsageConfig:
    token_usage_enabled = True
    model_id = "us.anthropic.claude-sonnet-4-6"


def _store(record_turn: Optional[AsyncMock] = None) -> MagicMock:
    store = MagicMock()
    store.record_turn = record_turn or AsyncMock()
    store.record_events = AsyncMock()
    return store


def _usage(result: Dict[str, Any]) -> Dict[str, Any]:
    return result["messages"][-1]["metadata"]["usage"]


def _state(
    input_tokens: Optional[int] = 120,
    output_tokens: Optional[int] = 240,
    model_id: Optional[str] = "from-llm",
    message_id: Optional[str] = "msg-abc",
    cache_read_tokens: Optional[int] = None,
    cache_write_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"model_id": model_id}
    if input_tokens is not None:
        metadata["input_tokens"] = input_tokens
    if output_tokens is not None:
        metadata["output_tokens"] = output_tokens
    if cache_read_tokens is not None:
        metadata["cache_read_tokens"] = cache_read_tokens
    if cache_write_tokens is not None:
        metadata["cache_write_tokens"] = cache_write_tokens
    message_metadata = {"message_id": message_id} if message_id is not None else {}
    return {
        "messages": [{"role": "assistant", "content": "hi", "metadata": message_metadata}],
        "metadata": metadata,
    }


class TestTokenUsageNode:
    @pytest.mark.asyncio
    async def test_records_when_enabled_and_store_present(self):
        store = _store()
        config = {
            "configurable": {
                "chat_config": _TokenUsageConfig(),
                "token_usage_store": store,
                "thread_id": "thread-1",
                "session_id": "session-1",
                "user_id": "alice",
            }
        }

        result = await token_usage_node(_state(), config)

        assert _usage(result) == {
            "input_tokens": 120,
            "output_tokens": 240,
            "cache_read": 0,
            "cache_write": 0,
            "by_purpose": {"chat": {"input_tokens": 120, "output_tokens": 240}},
            "estimated_cost": None,
            "currency": None,
            "final": True,
        }
        assert result["messages"][-1]["metadata"]["message_id"] == "msg-abc"
        store.record_turn.assert_awaited_once()
        _, kwargs = store.record_turn.await_args
        assert kwargs["turn_id"] == "msg-abc"
        assert kwargs["session_id"] == "session-1"
        assert kwargs["user_id"] == "alice"
        assert kwargs["model_id"] == "from-llm"
        assert kwargs["input_tokens"] == 120
        assert kwargs["output_tokens"] == 240
        # No cache info in the turn metadata -> recorded as 0.
        assert kwargs["cache_read_tokens"] == 0
        assert kwargs["cache_write_tokens"] == 0
        assert kwargs["final"] is True
        (events,) = store.record_events.await_args.args
        assert [(e.turn_id, e.purpose) for e in events] == [("msg-abc", "chat")]

    @pytest.mark.asyncio
    async def test_forwards_cache_token_counts_from_metadata(self):
        store = _store()
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }

        await token_usage_node(
            _state(input_tokens=1000, output_tokens=50, cache_read_tokens=800, cache_write_tokens=150), config
        )

        _, kwargs = store.record_turn.await_args
        # input_tokens is the total (cache tokens included) and is passed
        # through unchanged; the cache counters are forwarded as subsets.
        assert kwargs["input_tokens"] == 1000
        assert kwargs["cache_read_tokens"] == 800
        assert kwargs["cache_write_tokens"] == 150

    @pytest.mark.asyncio
    async def test_null_cache_token_counts_are_recorded_as_zero(self):
        store = _store()
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }
        state = _state()
        state["metadata"]["cache_read_tokens"] = None
        state["metadata"]["cache_write_tokens"] = None

        await token_usage_node(state, config)

        _, kwargs = store.record_turn.await_args
        assert kwargs["cache_read_tokens"] == 0
        assert kwargs["cache_write_tokens"] == 0

    @pytest.mark.asyncio
    async def test_does_not_fire_when_disabled(self):
        store = _store()

        class _Disabled(_TokenUsageConfig):
            token_usage_enabled = False

        config = {"configurable": {"chat_config": _Disabled(), "token_usage_store": store}}

        result = await token_usage_node(_state(), config)

        # Usage is still measured for the client; nothing is written.
        assert _usage(result)["input_tokens"] == 120
        store.record_turn.assert_not_awaited()
        store.record_events.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_fire_when_no_store(self):
        config = {"configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": None}}

        result = await token_usage_node(_state(), config)

        assert _usage(result)["output_tokens"] == 240

    @pytest.mark.asyncio
    async def test_does_not_fire_when_no_chat_config(self):
        store = _store()
        config = {"configurable": {"chat_config": None, "token_usage_store": store}}

        result = await token_usage_node(_state(), config)

        assert result == {}
        store.record_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_fire_when_token_counts_missing(self):
        store = _store()
        config = {"configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store}}

        result = await token_usage_node(_state(input_tokens=None, output_tokens=None), config)

        assert result == {}
        store.record_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_fire_when_only_output_tokens_missing(self):
        store = _store()
        config = {"configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store}}

        await token_usage_node(_state(output_tokens=None), config)

        store.record_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_session_id_falls_back_to_thread_id_and_user_id_defaults_none(self):
        store = _store()
        config = {
            "configurable": {
                "chat_config": _TokenUsageConfig(),
                "token_usage_store": store,
                "thread_id": "thread-42",
            }
        }

        await token_usage_node(_state(), config)

        _, kwargs = store.record_turn.await_args
        assert kwargs["session_id"] == "thread-42"
        assert kwargs["user_id"] is None

    @pytest.mark.asyncio
    async def test_does_not_fire_when_session_id_and_thread_id_both_missing(self):
        """record_turn requires a non-null session_id (NOT NULL column); with
        neither session_id nor thread_id supplied, the node must no-op rather
        than call record_turn(session_id=None) and rely on the store raising."""
        store = _store()
        config = {"configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store}}

        result = await token_usage_node(_state(), config)

        assert "usage" in result["messages"][-1]["metadata"]
        store.record_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_message_id_falls_back_to_generated_uuid_when_missing(self):
        store = _store()
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }

        result = await token_usage_node(_state(message_id=None), config)

        _, kwargs = store.record_turn.await_args
        # A fresh id was generated rather than skipping the call entirely.
        assert uuid.UUID(kwargs["turn_id"])
        # The generated id is written back to state so callers with their own
        # independent message_id fallback (e.g. websocket_handler.py) see the
        # same value that was just persisted as turn_id.
        written_back_id = result["messages"][-1]["metadata"]["message_id"]
        assert written_back_id == kwargs["turn_id"]

    @pytest.mark.asyncio
    async def test_model_id_falls_back_to_chat_config_when_missing_from_metadata(self):
        store = _store()
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }

        await token_usage_node(_state(model_id=None), config)

        _, kwargs = store.record_turn.await_args
        assert kwargs["model_id"] == _TokenUsageConfig.model_id

    @pytest.mark.asyncio
    async def test_record_turn_failure_is_swallowed(self):
        store = _store(AsyncMock(side_effect=RuntimeError("db unavailable")))
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }

        # Must not raise even though record_turn fails.
        result = await token_usage_node(_state(), config)

        assert _usage(result)["input_tokens"] == 120
        store.record_turn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_write_is_retried_on_the_next_successful_turn(self):
        store = _store(AsyncMock(side_effect=[RuntimeError("db unavailable"), None, None]))
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }

        await token_usage_node(_state(message_id="turn-1"), config)
        await token_usage_node(_state(message_id="turn-2"), config)

        turn_ids = [call.kwargs["turn_id"] for call in store.record_turn.await_args_list]
        assert turn_ids == ["turn-1", "turn-2", "turn-1"]

    @pytest.mark.asyncio
    async def test_a_successful_write_replays_only_one_queued_write(self):
        store = _store(AsyncMock(side_effect=[RuntimeError("down")] * 3 + [None] * 3))
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }

        for turn in ("turn-1", "turn-2", "turn-3", "turn-4"):
            await token_usage_node(_state(message_id=turn), config)

        turn_ids = [call.kwargs["turn_id"] for call in store.record_turn.await_args_list]
        assert turn_ids == ["turn-1", "turn-2", "turn-3", "turn-4", "turn-1"]

    @pytest.mark.asyncio
    async def test_no_op_state_update_when_messages_empty(self):
        """Edge case: no messages at all -- must not raise on messages[:-1]
        and must not fabricate a messages list out of nothing."""
        store = _store()
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }
        state = _state(message_id=None)
        state["messages"] = []

        result = await token_usage_node(state, config)

        assert result == {}
        store.record_turn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_record_turn_timeout_is_bounded_and_swallowed(self):
        """A hung store write must not block the turn indefinitely --
        record_turn() is bounded by chat_config.token_usage_write_timeout."""
        store = MagicMock()
        store.record_events = AsyncMock()

        async def _hang(**kwargs):
            await asyncio.sleep(10)

        store.record_turn = AsyncMock(side_effect=_hang)

        class _FastTimeoutConfig(_TokenUsageConfig):
            token_usage_write_timeout = 0.01

        config = {
            "configurable": {
                "chat_config": _FastTimeoutConfig(),
                "token_usage_store": store,
                "thread_id": "thread-1",
            }
        }

        # Must not raise and must not take anywhere near the 10s sleep.
        result = await asyncio.wait_for(token_usage_node(_state(), config), timeout=2)

        assert _usage(result)["input_tokens"] == 120

    @pytest.mark.asyncio
    async def test_record_turn_timeout_defaults_when_chat_config_lacks_field(self):
        """chat_config stubs that predate token_usage_write_timeout (e.g. in
        other tests) must still work via the getattr() default."""
        store = _store()
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }

        result = await token_usage_node(_state(), config)

        assert _usage(result)["input_tokens"] == 120
        store.record_turn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cost_is_unknown_without_a_ledger_even_for_a_priced_model(self):
        """The metadata totals cannot say which model or cache TTL each round used."""
        store = _store()
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }

        result = await token_usage_node(_state(model_id="anthropic.claude-sonnet-4-5-20250929-v1:0"), config)

        assert (_usage(result)["estimated_cost"], _usage(result)["currency"]) == (None, None)
        assert _usage(result)["input_tokens"] == 120
        _, kwargs = store.record_turn.await_args
        assert (kwargs["estimated_cost_micros"], kwargs["currency"]) == (None, None)


class TestTokenUsageNodeWithLedger:
    @pytest.mark.asyncio
    async def test_summary_sums_every_ledger_event_and_aligns_message_id(self):
        ledger = UsageLedger("turn-xyz")
        for purpose in ("summarization", "chat", "chat"):
            ledger.record(
                purpose=purpose,
                provider="anthropic",
                model_id="anthropic.claude-sonnet-4-5-20250929-v1:0",
                input_tokens=1_000,
                output_tokens=100,
                cache_read_tokens=200,
            )
        store = _store()
        config = {
            "configurable": {
                "chat_config": _TokenUsageConfig(),
                "token_usage_store": store,
                "thread_id": "thread-1",
                "usage_ledger": ledger,
                "usage_finalized_later": True,
            }
        }

        result = await token_usage_node(_state(input_tokens=1, output_tokens=1), config)

        metadata = result["messages"][-1]["metadata"]
        assert metadata["message_id"] == "turn-xyz"
        assert metadata["usage"]["input_tokens"] == 3_000
        assert metadata["usage"]["output_tokens"] == 300
        assert metadata["usage"]["cache_read"] == 600
        assert metadata["usage"]["final"] is False
        # 3 x (800 * 3 + 100 * 15 + 200 * 0.3) micros
        assert metadata["usage"]["estimated_cost"] == pytest.approx(0.01188)
        assert metadata["usage"]["currency"] == "USD"
        _, kwargs = store.record_turn.await_args
        assert kwargs["turn_id"] == "turn-xyz"
        assert kwargs["input_tokens"] == 3_000
        assert kwargs["estimated_cost_micros"] == 11_880
        assert kwargs["final"] is False
        (events,) = store.record_events.await_args.args
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_empty_ledger_still_aligns_message_id_without_usage(self):
        config = {"configurable": {"chat_config": _TokenUsageConfig(), "usage_ledger": UsageLedger("turn-xyz")}}

        result = await token_usage_node(_state(), config)

        metadata = result["messages"][-1]["metadata"]
        assert metadata["message_id"] == "turn-xyz"
        assert "usage" not in metadata
