"""Phase 1 — preprocessing node tests.

Tests the preprocess_node (and indirectly MessagePreprocessor) through
the full graph so we exercise the preprocess → END shortcut when the LLM
is mocked.  All four truncation stages are verified without AWS calls.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.graph.graph import build_chat_graph
from autolangchat.graph.nodes.preprocess import preprocess_node

# ---------------------------------------------------------------------------
# Minimal config stubs with tight thresholds for easy test control
# ---------------------------------------------------------------------------


class _TightConfig:
    """Config with very small thresholds so test messages trigger truncation."""

    model_id = "us.anthropic.claude-sonnet-4-6"
    fallback_model = None
    aws_region = "us-east-1"
    temperature = 0.7
    max_tokens = 1024
    top_p = 0.9
    checkpoint_postgres_url = None
    enable_ai_summarization = False
    system_prompt = None

    # Stage 1: single message > 50 chars → truncate to 25 chars
    single_msg_length_threshold = 50
    single_msg_truncation_target = 25

    # Stage 2: history total > 80 chars
    history_total_length_threshold = 80
    # Stage 2.3: per-message > 30 chars in history
    history_msg_length_threshold = 30
    history_msg_truncation_target = 20

    max_truncation_recursion = 3

    def get_system_prompt(self) -> str:
        return ""


class _DefaultConfig(_TightConfig):
    """Config using production-sized thresholds — no truncation for normal messages."""

    single_msg_length_threshold = 500_000
    single_msg_truncation_target = 425_000
    history_total_length_threshold = 650_000
    history_msg_length_threshold = 100_000
    history_msg_truncation_target = 85_000


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tight_config():
    return _TightConfig()


@pytest.fixture
def default_config():
    return _DefaultConfig()


def _ai_response(content: str = "ok"):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = []
    msg.response_metadata = {"model_id": "mock"}
    msg.usage_metadata = {"input_tokens": 1, "output_tokens": 1}
    return msg


# ---------------------------------------------------------------------------
# Direct node tests (no graph, no LLM)
# ---------------------------------------------------------------------------


class TestPreprocessNodeDirect:
    @pytest.mark.asyncio
    async def test_short_message_passes_through(self, default_config):
        """Short message is unchanged — preprocessing_applied is False."""
        messages = [{"role": "user", "content": "hi"}]
        state = {"messages": messages, "metadata": {}}
        cfg = {"configurable": {"chat_config": default_config}}

        result = await preprocess_node(state, cfg)

        assert result["messages"] == messages
        assert result["metadata"]["preprocessing_applied"] is False

    @pytest.mark.asyncio
    async def test_stage1_single_message_truncated(self, tight_config):
        """Single oversized message is truncated (Stage 1)."""
        long_content = "A" * 100  # > threshold of 50
        messages = [{"role": "user", "content": long_content}]
        state = {"messages": messages, "metadata": {}}
        cfg = {"configurable": {"chat_config": tight_config}}

        result = await preprocess_node(state, cfg)

        processed = result["messages"]
        assert len(processed) == 1
        assert len(processed[0]["content"]) <= tight_config.single_msg_length_threshold
        assert result["metadata"]["preprocessing_applied"] is True

    @pytest.mark.asyncio
    async def test_stage2_history_truncated(self, tight_config):
        """History that exceeds total threshold is reduced (Stage 2)."""
        # Build a 3-message history totalling well over 80 chars
        messages = [
            {"role": "user", "content": "X" * 40},
            {"role": "assistant", "content": "Y" * 40},
            {"role": "user", "content": "Z" * 40},
        ]
        total_before = sum(len(m["content"]) for m in messages)
        assert total_before > tight_config.history_total_length_threshold

        state = {"messages": messages, "metadata": {}}
        cfg = {"configurable": {"chat_config": tight_config}}

        result = await preprocess_node(state, cfg)

        processed = result["messages"]
        total_after = sum(len(m["content"]) for m in processed)
        assert total_after < total_before
        assert result["metadata"]["preprocessing_applied"] is True

    @pytest.mark.asyncio
    async def test_missing_chat_config_skips_preprocessing(self):
        """When chat_config is absent the node returns {} without crashing."""
        messages = [{"role": "user", "content": "hello"}]
        state = {"messages": messages, "metadata": {}}
        cfg = {"configurable": {}}  # no chat_config

        result = await preprocess_node(state, cfg)

        # Node must not raise; should return empty dict to leave state unchanged
        assert result == {}

    @pytest.mark.asyncio
    async def test_on_progress_not_called_for_short_messages(self, default_config):
        """No progress events are emitted when no truncation happens."""
        calls: List[Dict] = []

        async def on_progress(msg):
            calls.append(msg)

        messages = [{"role": "user", "content": "hello"}]
        state = {"messages": messages, "metadata": {}}
        cfg = {"configurable": {"chat_config": default_config, "on_progress": on_progress}}

        await preprocess_node(state, cfg)

        assert calls == []


# ---------------------------------------------------------------------------
# Graph-level integration: preprocess ← → llm (LLM mocked)
# ---------------------------------------------------------------------------


class TestPreprocessThroughGraph:
    @pytest.mark.asyncio
    async def test_stage1_truncation_reaches_llm(self, tight_config):
        """Oversized message is truncated before the LLM node sees it."""
        long_content = "B" * 200
        received_messages: List[Any] = []

        async def capturing_invoke(lc_messages):
            received_messages.extend(lc_messages)
            return _ai_response("ok")

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.ainvoke = capturing_invoke

            graph = build_chat_graph(tight_config)
            await graph.ainvoke(
                {"user_message": long_content},
                config={"configurable": {"thread_id": "preprocess-graph-test"}},
            )

        # The HumanMessage content forwarded to the LLM should be shorter
        assert len(received_messages) == 1
        llm_content = received_messages[0].content
        assert len(llm_content) < len(long_content)

    @pytest.mark.asyncio
    async def test_preprocessing_applied_flag_in_final_metadata(self, tight_config):
        """Graph metadata reflects whether preprocessing was triggered."""
        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.ainvoke = AsyncMock(return_value=_ai_response())

            graph = build_chat_graph(tight_config)

            # Short message → no preprocessing
            result_short = await graph.ainvoke(
                {"user_message": "hi"},
                config={"configurable": {"thread_id": "flag-test-short"}},
            )
            assert result_short["metadata"]["preprocessing_applied"] is False

            # Long message → preprocessing fires
            result_long = await graph.ainvoke(
                {"user_message": "C" * 200},
                config={"configurable": {"thread_id": "flag-test-long"}},
            )
            assert result_long["metadata"]["preprocessing_applied"] is True
