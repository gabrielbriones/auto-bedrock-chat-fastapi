"""Phase 1 — basic graph round-trip tests.

Tests the full graph pipeline (preprocess → llm → END) using a mocked
ChatBedrockConverse so no real AWS credentials are needed.
"""

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.graph.graph import build_chat_graph
from autolangchat.graph.routing import should_continue
from autolangchat.graph.state import ChatState

# ---------------------------------------------------------------------------
# Minimal ChatConfig stub
# ---------------------------------------------------------------------------


class _FakeChatConfig:
    model_id = "us.anthropic.claude-sonnet-5"
    fallback_model = None
    aws_region = "us-east-1"
    temperature = 0.7
    max_tokens = 1024
    top_p = 0.9
    checkpoint_postgres_url = None
    # MessagePreprocessor thresholds (must be present for preprocess node)
    enable_ai_summarization = False
    single_msg_length_threshold = 500_000
    single_msg_truncation_target = 425_000
    history_total_length_threshold = 650_000
    history_msg_length_threshold = 100_000
    history_msg_truncation_target = 85_000
    max_truncation_recursion = 3

    def get_system_prompt(self) -> str:
        return ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_config():
    return _FakeChatConfig()


def _make_ai_message(content: str, usage: Dict | None = None):
    """Build a minimal mock AIMessage with usage_metadata."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = []
    msg.response_metadata = {"model_id": _FakeChatConfig.model_id}
    msg.usage_metadata = usage or {"input_tokens": 10, "output_tokens": 20}
    # Support chunk addition (used by streaming accumulator)
    msg.__add__ = lambda self, other: self
    return msg


# ---------------------------------------------------------------------------
# should_continue edge function
# ---------------------------------------------------------------------------


class TestShouldContinue:
    def test_no_tool_calls_returns_end(self):
        state: ChatState = {
            "messages": [{"role": "assistant", "content": "hello", "tool_calls": []}],
            "metadata": {},
        }
        assert should_continue(state) == "__end__"

    def test_tool_calls_present_returns_tools(self):
        state: ChatState = {
            "messages": [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"name": "get_jobs", "args": {}}],
                }
            ],
            "metadata": {},
        }
        assert should_continue(state) == "tools"

    def test_empty_messages_returns_end(self):
        assert should_continue({"messages": [], "metadata": {}}) == "__end__"


# ---------------------------------------------------------------------------
# Graph round-trip (mocked LLM)
# ---------------------------------------------------------------------------


class TestGraphRoundTrip:
    @pytest.mark.asyncio
    async def test_basic_invocation(self, fake_config):
        """Graph returns an assistant message after a single user turn."""
        ai_response = _make_ai_message("Hello, I can help with that.")

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.ainvoke = AsyncMock(return_value=ai_response)

            graph = build_chat_graph(fake_config)
            result = await graph.ainvoke(
                {
                    "messages": [{"role": "user", "content": "Hello"}],
                    "metadata": {},
                },
                config={"configurable": {"thread_id": "test-session-1"}},
            )

        messages = result["messages"]
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["content"] == "Hello, I can help with that."

    @pytest.mark.asyncio
    async def test_usage_metadata_surfaced(self, fake_config):
        """Token counts from AIMessage.usage_metadata appear in graph metadata."""
        ai_response = _make_ai_message("hi", usage={"input_tokens": 42, "output_tokens": 7})

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.ainvoke = AsyncMock(return_value=ai_response)

            graph = build_chat_graph(fake_config)
            result = await graph.ainvoke(
                {
                    "messages": [{"role": "user", "content": "hi"}],
                    "metadata": {},
                },
                config={"configurable": {"thread_id": "test-session-tokens"}},
            )

        assert result["metadata"]["input_tokens"] == 42
        assert result["metadata"]["output_tokens"] == 7

    @pytest.mark.asyncio
    async def test_on_progress_called_during_streaming(self, fake_config):
        """on_progress callback is invoked at least once while streaming."""
        chunks = [MagicMock(content="Hello"), MagicMock(content=", world")]
        for c in chunks:
            c.tool_calls = []
            c.usage_metadata = None
            c.response_metadata = {}
            c.__add__ = lambda self, other: self

        progress_calls: List[Dict] = []

        async def on_progress(msg):
            progress_calls.append(msg)

        async def _fake_astream(messages):
            for c in chunks:
                yield c

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.astream = _fake_astream
            # ainvoke fallback shouldn't be called when astream yields chunks
            instance.ainvoke = AsyncMock(side_effect=AssertionError("ainvoke called unexpectedly"))

            graph = build_chat_graph(fake_config)
            await graph.ainvoke(
                {
                    "messages": [{"role": "user", "content": "stream me"}],
                    "metadata": {},
                },
                config={"configurable": {"thread_id": "test-session-stream", "on_progress": on_progress}},
            )

        assert len(progress_calls) > 0
        assert all(c["type"] == "typing" for c in progress_calls)

    @pytest.mark.asyncio
    async def test_fallback_model_on_context_window_error(self, fake_config):
        """Node retries with fallback_model when primary raises a context-window error."""
        fake_config.fallback_model = "us.anthropic.claude-3-haiku"
        fallback_response = _make_ai_message("fallback answer")

        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            inst = MagicMock()
            if call_count["n"] == 1:
                # First call: primary model → context window error
                inst.ainvoke = AsyncMock(side_effect=Exception("input is too long for the model"))
                inst.astream = _make_empty_astream()
            else:
                # Second call: fallback model → success
                inst.ainvoke = AsyncMock(return_value=fallback_response)
                inst.astream = _make_empty_astream()
            return inst

        with patch(
            "autolangchat.graph.nodes.llm_call.ChatBedrockConverse",
            side_effect=side_effect,
        ):
            graph = build_chat_graph(fake_config)
            result = await graph.ainvoke(
                {
                    "messages": [{"role": "user", "content": "very long message"}],
                    "metadata": {},
                },
                config={"configurable": {"thread_id": "test-session-fallback"}},
            )

        assert result["messages"][-1]["content"] == "fallback answer"
        assert result["metadata"]["fallback_model_used"] is True
        assert call_count["n"] == 2


def _make_empty_astream():
    """Return an async generator that yields nothing (simulates no streaming)."""

    async def _astream(messages):
        return
        yield  # make it a generator

    return _astream
