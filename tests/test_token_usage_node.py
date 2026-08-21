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

import uuid
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from autolangchat.graph.nodes.token_usage import token_usage_node


class _TokenUsageConfig:
    token_usage_enabled = True
    model_id = "us.anthropic.claude-sonnet-4-6"


def _state(
    input_tokens: Optional[int] = 120,
    output_tokens: Optional[int] = 240,
    model_id: Optional[str] = "from-llm",
    message_id: Optional[str] = "msg-abc",
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"model_id": model_id}
    if input_tokens is not None:
        metadata["input_tokens"] = input_tokens
    if output_tokens is not None:
        metadata["output_tokens"] = output_tokens
    message_metadata = {"message_id": message_id} if message_id is not None else {}
    return {
        "messages": [{"role": "assistant", "content": "hi", "metadata": message_metadata}],
        "metadata": metadata,
    }


class TestTokenUsageNode:
    @pytest.mark.asyncio
    async def test_records_when_enabled_and_store_present(self):
        store = MagicMock()
        store.record_turn = AsyncMock()
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

        assert result == {}
        store.record_turn.assert_awaited_once()
        _, kwargs = store.record_turn.await_args
        assert kwargs["turn_id"] == "msg-abc"
        assert kwargs["session_id"] == "session-1"
        assert kwargs["user_id"] == "alice"
        assert kwargs["model_id"] == "from-llm"
        assert kwargs["input_tokens"] == 120
        assert kwargs["output_tokens"] == 240

    @pytest.mark.asyncio
    async def test_does_not_fire_when_disabled(self):
        store = MagicMock()
        store.record_turn = AsyncMock()

        class _Disabled(_TokenUsageConfig):
            token_usage_enabled = False

        config = {"configurable": {"chat_config": _Disabled(), "token_usage_store": store}}

        result = await token_usage_node(_state(), config)

        assert result == {}
        store.record_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_fire_when_no_store(self):
        config = {"configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": None}}

        result = await token_usage_node(_state(), config)

        assert result == {}

    @pytest.mark.asyncio
    async def test_does_not_fire_when_no_chat_config(self):
        store = MagicMock()
        store.record_turn = AsyncMock()
        config = {"configurable": {"chat_config": None, "token_usage_store": store}}

        result = await token_usage_node(_state(), config)

        assert result == {}
        store.record_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_fire_when_token_counts_missing(self):
        store = MagicMock()
        store.record_turn = AsyncMock()
        config = {"configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store}}

        result = await token_usage_node(_state(input_tokens=None, output_tokens=None), config)

        assert result == {}
        store.record_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_fire_when_only_output_tokens_missing(self):
        store = MagicMock()
        store.record_turn = AsyncMock()
        config = {"configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store}}

        await token_usage_node(_state(output_tokens=None), config)

        store.record_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_session_id_falls_back_to_thread_id_and_user_id_defaults_none(self):
        store = MagicMock()
        store.record_turn = AsyncMock()
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
        store = MagicMock()
        store.record_turn = AsyncMock()
        config = {"configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store}}

        result = await token_usage_node(_state(), config)

        assert result == {}
        store.record_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_message_id_falls_back_to_generated_uuid_when_missing(self):
        store = MagicMock()
        store.record_turn = AsyncMock()
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }

        await token_usage_node(_state(message_id=None), config)

        _, kwargs = store.record_turn.await_args
        # A fresh id was generated rather than skipping the call entirely.
        assert uuid.UUID(kwargs["turn_id"])

    @pytest.mark.asyncio
    async def test_model_id_falls_back_to_chat_config_when_missing_from_metadata(self):
        store = MagicMock()
        store.record_turn = AsyncMock()
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }

        await token_usage_node(_state(model_id=None), config)

        _, kwargs = store.record_turn.await_args
        assert kwargs["model_id"] == _TokenUsageConfig.model_id

    @pytest.mark.asyncio
    async def test_record_turn_failure_is_swallowed(self):
        store = MagicMock()
        store.record_turn = AsyncMock(side_effect=RuntimeError("db unavailable"))
        config = {
            "configurable": {"chat_config": _TokenUsageConfig(), "token_usage_store": store, "thread_id": "thread-1"}
        }

        # Must not raise even though record_turn fails.
        result = await token_usage_node(_state(), config)

        assert result == {}
        store.record_turn.assert_awaited_once()
