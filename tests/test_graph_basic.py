"""Basic graph round-trip tests.

Tests the full graph pipeline (preprocess → llm → END) using a mocked
ChatBedrockConverse so no real AWS credentials are needed.
"""

import asyncio
import functools
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
        """Node retries with fallback_model when primary raises a context-window
        error and the emergency re-truncation retry (same model) also fails."""
        fake_config.fallback_model = "us.anthropic.claude-3-haiku"
        fallback_response = _make_ai_message("fallback answer")

        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            inst = MagicMock()
            if call_count["n"] <= 2:
                # Call 1: primary model -> context window error.
                # Call 2: emergency re-truncation retry (same model) -> still fails.
                inst.ainvoke = AsyncMock(side_effect=Exception("input is too long for the model"))
                inst.astream = _make_empty_astream()
            else:
                # Call 3: fallback model -> success
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
        # Emergency re-truncation was attempted (and itself failed) before
        # falling back to fallback_model -- the flag reflects that the safety
        # net was engaged, not just whether its own retry call succeeded.
        assert result["metadata"]["emergency_retruncation_applied"] is True
        assert call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_emergency_retruncation_recovers_without_fallback(self, fake_config):
        """Node recovers via emergency re-truncation (same model) when the
        first attempt raises a context-window error, without needing a
        fallback_model at all."""
        fake_config.fallback_model = None
        recovered_response = _make_ai_message("recovered answer")

        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            inst = MagicMock()
            if call_count["n"] == 1:
                # First call: primary model -> context window error
                inst.ainvoke = AsyncMock(side_effect=Exception("input is too long for the model"))
                inst.astream = _make_empty_astream()
            else:
                # Second call: emergency re-truncation retry (same model) -> success
                inst.ainvoke = AsyncMock(return_value=recovered_response)
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
                config={"configurable": {"thread_id": "test-session-retruncation"}},
            )

        assert result["messages"][-1]["content"] == "recovered answer"
        assert result["metadata"]["fallback_model_used"] is False
        assert result["metadata"]["emergency_retruncation_applied"] is True
        assert call_count["n"] == 2


def _make_empty_astream():
    """Return an async generator that yields nothing (simulates no streaming)."""

    async def _astream(messages):
        return
        yield  # make it a generator

    return _astream


# ---------------------------------------------------------------------------
# Token usage — non-WebSocket ainvoke() call path (XMGPLAT-11215)
# ---------------------------------------------------------------------------


class TestTokenUsageGraphIntegration:
    """token_usage_node must record usage for *any* direct chat_graph.ainvoke()
    caller (e.g. qa/test.py, a batch job), not just WebSocketChatHandler."""

    @pytest.mark.asyncio
    async def test_direct_ainvoke_call_records_token_usage(self, fake_config):
        class _TokenUsageEnabledConfig(_FakeChatConfig):
            token_usage_enabled = True

        ai_response = _make_ai_message("hi", usage={"input_tokens": 15, "output_tokens": 25})
        token_usage_store = MagicMock()
        token_usage_store.record_turn = AsyncMock()

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.ainvoke = AsyncMock(return_value=ai_response)

            graph = build_chat_graph(_TokenUsageEnabledConfig())
            await graph.ainvoke(
                {
                    "messages": [{"role": "user", "content": "hi"}],
                    "metadata": {},
                },
                config={
                    "configurable": {
                        "thread_id": "batch-job-1",
                        "token_usage_store": token_usage_store,
                    }
                },
            )

        token_usage_store.record_turn.assert_awaited_once()
        _, kwargs = token_usage_store.record_turn.await_args
        assert kwargs["session_id"] == "batch-job-1"  # falls back to thread_id
        assert kwargs["user_id"] is None
        assert kwargs["input_tokens"] == 15
        assert kwargs["output_tokens"] == 25

    @pytest.mark.asyncio
    async def test_build_chat_graph_wires_token_usage_store_without_per_call_configurable(self, fake_config):
        """token_usage_store passed to build_chat_graph() itself (mirrors how
        plugin.py wires AutoLangChatPlugin._token_usage_store) must be used
        automatically -- callers should not have to repeat it on every
        ainvoke()'s configurable dict."""

        class _TokenUsageEnabledConfig(_FakeChatConfig):
            token_usage_enabled = True

        ai_response = _make_ai_message("hi", usage={"input_tokens": 5, "output_tokens": 10})
        token_usage_store = MagicMock()
        token_usage_store.record_turn = AsyncMock()

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.ainvoke = AsyncMock(return_value=ai_response)

            graph = build_chat_graph(_TokenUsageEnabledConfig(), token_usage_store=token_usage_store)
            # Note: no "token_usage_store" key here -- only build_chat_graph() was told about it.
            await graph.ainvoke(
                {
                    "messages": [{"role": "user", "content": "hi"}],
                    "metadata": {},
                },
                config={"configurable": {"thread_id": "workload-analyzer-job-1"}},
            )

        token_usage_store.record_turn.assert_awaited_once()
        _, kwargs = token_usage_store.record_turn.await_args
        assert kwargs["session_id"] == "workload-analyzer-job-1"

    @pytest.mark.asyncio
    async def test_token_usage_store_callable_reflects_runtime_disablement(self, fake_config):
        """token_usage_store may be a zero-arg callable (e.g. a plugin's
        ``lambda: self._token_usage_store``) instead of a frozen instance, so
        that a store disabled *after* the graph is built (e.g. a startup
        open() failure sets the plugin's reference to None) is respected by
        every graph caller instead of the stale pre-failure instance being
        used forever."""

        class _TokenUsageEnabledConfig(_FakeChatConfig):
            token_usage_enabled = True

        ai_response = _make_ai_message("hi", usage={"input_tokens": 5, "output_tokens": 10})
        live_store = MagicMock()
        live_store.record_turn = AsyncMock()

        # Mutable holder mimicking AutoLangChatPlugin._token_usage_store.
        holder = {"store": live_store}

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.ainvoke = AsyncMock(return_value=ai_response)

            graph = build_chat_graph(_TokenUsageEnabledConfig(), token_usage_store=lambda: holder["store"])

            await graph.ainvoke(
                {"messages": [{"role": "user", "content": "hi"}], "metadata": {}},
                config={"configurable": {"thread_id": "job-1"}},
            )
            live_store.record_turn.assert_awaited_once()

            # Simulate the store being disabled at runtime (e.g. open() failed).
            holder["store"] = None

            # Must no-op, not keep using the now-stale live_store reference.
            await graph.ainvoke(
                {"messages": [{"role": "user", "content": "hi"}], "metadata": {}},
                config={"configurable": {"thread_id": "job-2"}},
            )
            live_store.record_turn.assert_awaited_once()  # still just the one call from job-1

    @pytest.mark.asyncio
    async def test_token_usage_store_functools_partial_provider_is_resolved(self, fake_config):
        """functools.partial(...) must also be recognized as a zero-arg
        provider, not misclassified as an already-resolved store instance
        (which would later fail when token_usage_node calls .record_turn on
        the partial object itself instead of what it resolves to)."""

        class _TokenUsageEnabledConfig(_FakeChatConfig):
            token_usage_enabled = True

        ai_response = _make_ai_message("hi", usage={"input_tokens": 5, "output_tokens": 10})
        live_store = MagicMock()
        live_store.record_turn = AsyncMock()
        holder = {"store": live_store}

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.ainvoke = AsyncMock(return_value=ai_response)

            graph = build_chat_graph(
                _TokenUsageEnabledConfig(),
                token_usage_store=functools.partial(holder.__getitem__, "store"),
            )
            await graph.ainvoke(
                {"messages": [{"role": "user", "content": "hi"}], "metadata": {}},
                config={"configurable": {"thread_id": "job-1"}},
            )

        live_store.record_turn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_direct_ainvoke_call_no_op_without_token_usage_store(self, fake_config):
        """Same call path, but no token_usage_store passed -- must not raise."""

        class _TokenUsageEnabledConfig(_FakeChatConfig):
            token_usage_enabled = True

        ai_response = _make_ai_message("hi")

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.ainvoke = AsyncMock(return_value=ai_response)

            graph = build_chat_graph(_TokenUsageEnabledConfig())
            result = await graph.ainvoke(
                {
                    "messages": [{"role": "user", "content": "hi"}],
                    "metadata": {},
                },
                config={"configurable": {"thread_id": "batch-job-2"}},
            )

        assert result["messages"][-1]["role"] == "assistant"
