"""Tests for ChatManager orchestration layer.

Tests that ChatManager correctly coordinates:
- Message preprocessing (MessagePreprocessor — tool truncation, single-message
  truncation, history-total truncation, orphaned tool-result cleanup)
- LLM formatting and calling (BedrockClient)
- Context-window error recovery
- Tool call loop via ToolManager
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from auto_bedrock_chat_fastapi import ChatCompletionResult, ChatConfig
from auto_bedrock_chat_fastapi.chat_manager import ChatManager
from auto_bedrock_chat_fastapi.exceptions import BedrockClientError, ContextWindowExceededError, LLMClientError
from auto_bedrock_chat_fastapi.message_preprocessor import MessagePreprocessor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    """Default ChatConfig for tests."""
    return ChatConfig()


@pytest.fixture
def mock_llm_client():
    """Mock LLM client that mimics BedrockClient interface."""
    client = MagicMock()
    # format_messages is sync
    client.format_messages = MagicMock(side_effect=lambda msgs, **kwargs: msgs)
    # chat_completion is async
    client.chat_completion = AsyncMock(
        return_value={
            "content": "Hello! How can I help you?",
            "role": "assistant",
            "tool_calls": [],
            "metadata": {},
        }
    )
    return client


@pytest.fixture
def chat_manager(mock_llm_client, config):
    """ChatManager wired with real components and a mock LLM client."""
    return ChatManager(
        llm_client=mock_llm_client,
        config=config,
    )


@pytest.fixture
def mock_tool_manager():
    """Mock ToolManager with cached tools_desc and async execute_tool_calls."""
    tm = MagicMock()
    tm.tools_desc = {"tools": [{"name": "get_weather"}]}
    tm.execute_tool_calls = AsyncMock(return_value=[])
    return tm


@pytest.fixture
def chat_manager_with_tools(mock_llm_client, config, mock_tool_manager):
    """ChatManager wired with mock components including a ToolManager."""
    return ChatManager(
        llm_client=mock_llm_client,
        config=config,
        tool_manager=mock_tool_manager,
    )


def _make_messages(count: int, include_system: bool = True) -> list:
    """Helper to build a conversation of *count* messages."""
    messages = []
    if include_system:
        messages.append({"role": "system", "content": "You are a helpful assistant."})
    idx = 0
    while len(messages) < count:
        messages.append({"role": "user", "content": f"User message {idx}"})
        if len(messages) < count:
            messages.append({"role": "assistant", "content": f"Assistant response {idx}"})
        idx += 1
    return messages


# ===========================================================================
# TestChatManagerConstruction
# ===========================================================================


class TestChatManagerConstruction:
    """Test ChatManager initialization."""

    def test_constructor_stores_dependencies(self, mock_llm_client, config):
        """ChatManager stores all injected dependencies."""
        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
        )
        assert mgr.llm_client is mock_llm_client
        assert mgr.config is config
        assert isinstance(mgr.message_preprocessor, MessagePreprocessor)


# ===========================================================================
# TestChatCompletion — happy path
# ===========================================================================


class TestChatCompletionHappyPath:
    """Test the full chat_completion pipeline with no errors."""

    @pytest.mark.asyncio
    async def test_returns_chat_completion_result(self, chat_manager):
        """chat_completion returns a ChatCompletionResult."""
        messages = _make_messages(4)
        result = await chat_manager.chat_completion(messages=messages)

        assert isinstance(result, ChatCompletionResult)

    @pytest.mark.asyncio
    async def test_response_contains_llm_output(self, chat_manager, mock_llm_client):
        """The response field carries the LLM output."""
        messages = _make_messages(4)
        result = await chat_manager.chat_completion(messages=messages)

        assert result.response["content"] == "Hello! How can I help you?"
        assert result.response["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_messages_passed_through(self, chat_manager, mock_llm_client):
        """Messages are passed through the pipeline to the LLM."""
        messages = _make_messages(4)
        await chat_manager.chat_completion(messages=messages)

        # LLM client should have been called
        mock_llm_client.chat_completion.assert_awaited_once()
        # format_messages should have been called
        mock_llm_client.format_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_metadata_populated(self, chat_manager):
        """Metadata includes expected keys."""
        messages = _make_messages(4)
        result = await chat_manager.chat_completion(messages=messages)

        assert "original_message_count" in result.metadata
        assert result.metadata["original_message_count"] == 4
        assert "final_message_count" in result.metadata
        assert result.metadata["context_window_retries"] == 0

    @pytest.mark.asyncio
    async def test_tool_results_empty_without_tool_loop(self, chat_manager):
        """tool_results is empty when no tool loop runs (Task 2.3)."""
        messages = _make_messages(4)
        result = await chat_manager.chat_completion(messages=messages)

        assert result.tool_results == []

    @pytest.mark.asyncio
    async def test_llm_params_forwarded(self, chat_manager, mock_llm_client):
        """Extra LLM params are forwarded to llm_client.chat_completion."""
        messages = _make_messages(4)
        await chat_manager.chat_completion(
            messages=messages,
            model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            temperature=0.5,
            max_tokens=1000,
        )

        call_kwargs = mock_llm_client.chat_completion.call_args
        assert call_kwargs.kwargs.get("model_id") == "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        assert call_kwargs.kwargs.get("temperature") == 0.5
        assert call_kwargs.kwargs.get("max_tokens") == 1000

    @pytest.mark.asyncio
    async def test_tools_desc_forwarded(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """tools_desc from tool_manager is forwarded to llm_client.chat_completion."""
        messages = _make_messages(4)
        mock_tool_manager.tools_desc = {"tools": [{"name": "get_weather"}]}
        await chat_manager_with_tools.chat_completion(messages=messages)

        call_kwargs = mock_llm_client.chat_completion.call_args
        assert call_kwargs.kwargs.get("tools_desc") == {"tools": [{"name": "get_weather"}]}


# ===========================================================================
# TestOrphanedToolCleanup
# ===========================================================================


class TestOrphanedToolCleanup:
    """Test: orphaned tool result cleanup is no longer part of the pipeline."""

    @pytest.mark.asyncio
    async def test_orphaned_tool_results_passed_through(self, chat_manager, mock_llm_client):
        """Orphaned tool results are passed through (cleanup removed from pipeline)."""
        messages = [
            {"role": "system", "content": "System"},
            # Orphaned tool_result — no preceding assistant with tool_calls
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "orphan_id_123",
                        "content": "Some orphaned data",
                    }
                ],
            },
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

        await chat_manager.chat_completion(messages=messages)

        # The formatted messages may or may not contain the orphan —
        # we just verify the pipeline doesn't crash
        formatted_msgs = mock_llm_client.format_messages.call_args[0][0]
        assert any(msg.get("role") == "user" for msg in formatted_msgs)

    @pytest.mark.asyncio
    async def test_valid_tool_pairs_preserved(self, chat_manager, mock_llm_client):
        """Valid tool_use + tool_result pairs are kept."""
        messages = [
            {"role": "system", "content": "System"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "valid_id", "name": "func", "input": {}}],
            },
            {
                "role": "user",
                "tool_results": [{"tool_call_id": "valid_id", "content": "result"}],
            },
            {"role": "user", "content": "Thanks"},
        ]

        await chat_manager.chat_completion(messages=messages)

        formatted_msgs = mock_llm_client.format_messages.call_args[0][0]
        # The tool pair should still be present
        has_tool_call = any(msg.get("tool_calls") for msg in formatted_msgs if msg.get("role") == "assistant")
        has_tool_result = any(msg.get("tool_results") for msg in formatted_msgs if msg.get("role") == "user")
        assert has_tool_call, "Valid tool_use should be preserved"
        assert has_tool_result, "Valid tool_result should be preserved"


# ===========================================================================
# TestMessagePreprocessing
# ===========================================================================


class TestMessagePreprocessing:
    """Test Step 3: tool truncation + message chunking."""

    @pytest.mark.asyncio
    async def test_large_tool_results_truncated(self, mock_llm_client, config):
        """Large tool results in history are truncated."""
        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
        )
        # Override with test-specific preprocessor configured for low thresholds
        test_config = ChatConfig(
            BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=1000,
            BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=500,
            BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD=650_000,
            BEDROCK_HISTORY_MSG_LENGTH_THRESHOLD=1000,
            BEDROCK_HISTORY_MSG_TRUNCATION_TARGET=500,
        )
        mgr.message_preprocessor = MessagePreprocessor(config=test_config)

        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "tc1", "name": "func", "input": {}}],
            },
            {
                "role": "tool",
                "content": "Tool results (round 1)",
                "tool_calls": [{"id": "tc1", "name": "func", "input": {}}],
                "tool_results": [{"tool_call_id": "tc1", "result": "X" * 5000}],
            },
            {"role": "user", "content": "Thanks"},
        ]

        await mgr.chat_completion(messages=messages)

        # The tool_results payload should have been truncated
        formatted_msgs = mock_llm_client.format_messages.call_args[0][0]
        tool_msgs = [m for m in formatted_msgs if m.get("role") == "tool"]
        for tm in tool_msgs:
            tool_results = tm.get("tool_results", [])
            for tr in tool_results:
                result_text = tr.get("result", "")
                assert len(result_text) < 5000, "Tool result should have been truncated"


# ===========================================================================
# TestContextWindowRecovery
# ===========================================================================


class TestContextWindowRecovery:
    """Test Step 5: context-window error recovery layers."""

    @pytest.mark.asyncio
    async def test_context_window_error_triggers_reduction(self, chat_manager, mock_llm_client):
        """ContextWindowExceededError triggers aggressive reduction + retry."""
        # First call raises, second succeeds
        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                ContextWindowExceededError("Too large"),
                {"content": "Recovery response", "role": "assistant", "tool_calls": [], "metadata": {}},
            ]
        )

        messages = _make_messages(12)
        result = await chat_manager.chat_completion(messages=messages)

        assert result.response["content"] == "Recovery response"
        assert result.metadata["context_window_retries"] == 1
        assert mock_llm_client.chat_completion.await_count == 2

    @pytest.mark.asyncio
    async def test_context_window_graceful_degradation_when_retry_fails(self, chat_manager, mock_llm_client):
        """With default config (graceful_degradation=True), a synthetic response is returned."""
        mock_llm_client.chat_completion = AsyncMock(side_effect=ContextWindowExceededError("Still too large"))

        messages = _make_messages(4)
        result = await chat_manager.chat_completion(messages=messages)

        # Graceful degradation kicks in (default is True)
        assert "trouble processing" in result.response["content"]
        assert result.metadata.get("graceful_degradation_used") is True

    @pytest.mark.asyncio
    async def test_context_window_raises_when_degradation_disabled(self, mock_llm_client):
        """With graceful_degradation=False, ContextWindowExceededError propagates."""
        config = ChatConfig(BEDROCK_GRACEFUL_DEGRADATION=False)
        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
        )

        mock_llm_client.chat_completion = AsyncMock(side_effect=ContextWindowExceededError("Still too large"))

        with pytest.raises(ContextWindowExceededError):
            await mgr.chat_completion(messages=_make_messages(4))

    @pytest.mark.asyncio
    async def test_non_context_errors_propagate_immediately(self, chat_manager, mock_llm_client):
        """Non-context-window LLM errors on Layer 1 propagate without reduction/retry."""
        mock_llm_client.chat_completion = AsyncMock(side_effect=BedrockClientError("Some other error"))

        messages = _make_messages(4)
        with pytest.raises(LLMClientError):
            await chat_manager.chat_completion(messages=messages)

        # Should only have been called once (no retry)
        assert mock_llm_client.chat_completion.await_count == 1


# ===========================================================================
# TestAggressiveMessageReduction
# ===========================================================================


class TestAggressiveMessageReduction:
    """Test _aggressive_message_reduction helper."""

    def test_keeps_system_and_recent(self, chat_manager):
        """Keeps system message + last N non-system messages."""
        messages = _make_messages(20, include_system=True)
        reduced = chat_manager._aggressive_message_reduction(messages)

        # Should have system + 4 recent
        assert reduced[0]["role"] == "system"
        assert len(reduced) == 5  # 1 system + 4 recent

    def test_keeps_all_if_under_limit(self, chat_manager):
        """If message count is already small, keeps all."""
        messages = _make_messages(3, include_system=True)
        reduced = chat_manager._aggressive_message_reduction(messages)

        # All kept: 1 system + 2 non-system
        assert len(reduced) == 3

    def test_no_system_message(self, chat_manager):
        """Works correctly when there's no system message."""
        messages = _make_messages(10, include_system=False)
        reduced = chat_manager._aggressive_message_reduction(messages)

        # Should keep last 4
        assert len(reduced) == 4
        assert reduced[0]["role"] != "system"


# ===========================================================================
# TestFallbackModel
# ===========================================================================


class TestFallbackModel:
    """Test Layer 3: fallback model recovery."""

    @pytest.fixture
    def config_with_fallback(self):
        """Config with fallback model set and graceful degradation disabled."""
        return ChatConfig(
            BEDROCK_FALLBACK_MODEL="anthropic.claude-v2",
            BEDROCK_GRACEFUL_DEGRADATION=False,
        )

    @pytest.fixture
    def mgr_with_fallback(
        self,
        mock_llm_client,
        config_with_fallback,
    ):
        return ChatManager(
            llm_client=mock_llm_client,
            config=config_with_fallback,
        )

    @pytest.mark.asyncio
    async def test_fallback_model_used_after_retry_fails(self, mgr_with_fallback, mock_llm_client):
        """When primary + retry fail, fallback model is attempted."""
        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                ContextWindowExceededError("Primary too large"),
                ContextWindowExceededError("Reduced still too large"),
                {"content": "Fallback answer", "role": "assistant", "tool_calls": [], "metadata": {}},
            ]
        )

        result = await mgr_with_fallback.chat_completion(messages=_make_messages(4))

        assert result.response["content"] == "Fallback answer"
        assert result.metadata.get("fallback_model_used") is True
        assert mock_llm_client.chat_completion.await_count == 3

        # Verify the 3rd call used fallback model_id
        third_call = mock_llm_client.chat_completion.call_args_list[2]
        assert third_call.kwargs.get("model_id") == "anthropic.claude-v2"

    @pytest.mark.asyncio
    async def test_fallback_model_not_used_when_not_configured(self, mock_llm_client):
        """When fallback_model is None, Layer 3 is skipped."""
        config = ChatConfig(BEDROCK_FALLBACK_MODEL=None, BEDROCK_GRACEFUL_DEGRADATION=False)
        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
        )

        mock_llm_client.chat_completion = AsyncMock(side_effect=ContextWindowExceededError("Failed"))

        with pytest.raises(ContextWindowExceededError):
            await mgr.chat_completion(messages=_make_messages(4))

        # Only 2 calls: primary + retry. No fallback attempt.
        assert mock_llm_client.chat_completion.await_count == 2

    @pytest.mark.asyncio
    async def test_fallback_model_also_fails_raises(self, mgr_with_fallback, mock_llm_client):
        """When fallback also fails and degradation is off, error propagates."""
        mock_llm_client.chat_completion = AsyncMock(side_effect=ContextWindowExceededError("Nope"))

        with pytest.raises(ContextWindowExceededError):
            await mgr_with_fallback.chat_completion(messages=_make_messages(4))

        # All 3 layers called: primary, retry, fallback
        assert mock_llm_client.chat_completion.await_count == 3

    @pytest.mark.asyncio
    async def test_llm_client_error_triggers_fallback(self, mgr_with_fallback, mock_llm_client):
        """Non-context-window LLMClientError on Layer 2 also triggers fallback."""
        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                ContextWindowExceededError("Big"),  # Layer 1 → triggers reduction
                BedrockClientError("Model throttled"),  # Layer 2 fails
                {"content": "OK", "role": "assistant", "tool_calls": [], "metadata": {}},
            ]
        )

        result = await mgr_with_fallback.chat_completion(messages=_make_messages(4))

        assert result.response["content"] == "OK"
        assert result.metadata.get("fallback_model_used") is True


# ===========================================================================
# TestGracefulDegradation
# ===========================================================================


class TestGracefulDegradation:
    """Test Layer 4: graceful degradation."""

    @pytest.mark.asyncio
    async def test_graceful_degradation_returns_apology(self, chat_manager, mock_llm_client):
        """When all else fails and degradation is on, user gets a polite message."""
        # Default config has graceful_degradation=True, fallback_model=None
        mock_llm_client.chat_completion = AsyncMock(side_effect=ContextWindowExceededError("Hopeless"))

        result = await chat_manager.chat_completion(messages=_make_messages(4))

        assert "trouble processing" in result.response["content"]
        assert result.response["tool_calls"] == []
        assert result.response["metadata"]["degraded"] is True
        assert "Hopeless" in result.response["metadata"]["error"]
        assert result.metadata.get("graceful_degradation_used") is True

    @pytest.mark.asyncio
    async def test_graceful_degradation_with_fallback_both_fail(self, mock_llm_client):
        """Fallback model fails → graceful degradation catches it."""
        config = ChatConfig(
            BEDROCK_FALLBACK_MODEL="anthropic.claude-v2",
            BEDROCK_GRACEFUL_DEGRADATION=True,
        )
        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
        )

        mock_llm_client.chat_completion = AsyncMock(side_effect=ContextWindowExceededError("All fail"))

        result = await mgr.chat_completion(messages=_make_messages(4))

        assert "trouble processing" in result.response["content"]
        assert result.metadata.get("fallback_model_used") is True
        assert result.metadata.get("graceful_degradation_used") is True

    def test_graceful_degradation_response_shape(self):
        """The synthetic response has the correct shape."""
        resp = ChatManager._graceful_degradation_response(ValueError("test error"))

        assert resp["role"] == "assistant"
        assert resp["tool_calls"] == []
        assert "trouble processing" in resp["content"]
        assert resp["metadata"]["degraded"] is True
        assert "test error" in resp["metadata"]["error"]

    @pytest.mark.asyncio
    async def test_graceful_degradation_disabled_raises(self, mock_llm_client):
        """graceful_degradation=False → error propagates."""
        config = ChatConfig(BEDROCK_GRACEFUL_DEGRADATION=False)
        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
        )

        mock_llm_client.chat_completion = AsyncMock(side_effect=ContextWindowExceededError("Boom"))

        with pytest.raises(ContextWindowExceededError):
            await mgr.chat_completion(messages=_make_messages(4))


# ===========================================================================
# TestPipelineOrdering
# ===========================================================================


class TestPipelineOrdering:
    """Test that pipeline steps execute in the correct order."""

    @pytest.mark.asyncio
    async def test_steps_execute_in_order(self, mock_llm_client, config):
        """Verify preprocess → format → call ordering."""
        call_order = []

        # Spy on MessagePreprocessor.preprocess_messages (async)
        preprocessor = MagicMock(spec=MessagePreprocessor)
        preprocessor.preprocess_messages = AsyncMock(
            side_effect=lambda msgs, **kw: (call_order.append("preprocess"), msgs)[1]
        )

        mock_llm_client.format_messages = MagicMock(
            side_effect=lambda msgs, **kwargs: (call_order.append("format"), msgs)[1]
        )
        mock_llm_client.chat_completion = AsyncMock(
            side_effect=lambda **kw: (
                call_order.append("llm_call"),
                {"content": "ok", "role": "assistant", "tool_calls": [], "metadata": {}},
            )[1]
        )

        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
        )
        # Replace internal preprocessor with our spy
        mgr.message_preprocessor = preprocessor

        await mgr.chat_completion(messages=_make_messages(4))

        assert call_order == ["preprocess", "format", "llm_call"]


# ===========================================================================
# TestChatManagerImport
# ===========================================================================


class TestChatManagerImport:
    """Test that ChatManager is properly exported."""

    def test_importable_from_package(self):
        """ChatManager can be imported from the top-level package."""
        from auto_bedrock_chat_fastapi import ChatManager as CM

        assert CM is ChatManager

    def test_importable_from_module(self):
        """ChatManager can be imported from chat_manager module."""
        from auto_bedrock_chat_fastapi.chat_manager import ChatManager as CM

        assert CM is not None


# ===========================================================================
# TestToolCallLoop
# ===========================================================================


class TestToolCallLoop:
    """Tests for ChatManager._run_tool_call_loop via chat_completion."""

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _tool_response(content="I used a tool", tool_calls=None):
        """Build a mock LLM response with tool_calls."""
        return {
            "content": content,
            "role": "assistant",
            "tool_calls": tool_calls or [],
            "metadata": {},
        }

    @staticmethod
    def _final_response(content="Here is the final answer"):
        return {
            "content": content,
            "role": "assistant",
            "tool_calls": [],
            "metadata": {},
        }

    @staticmethod
    def _sample_tool_calls():
        return [
            {"id": "tc_1", "name": "get_weather", "arguments": {"city": "Seattle"}},
        ]

    @staticmethod
    def _sample_tool_results():
        return [
            {"tool_call_id": "tc_1", "name": "get_weather", "result": {"temp": 55}},
        ]

    # ---- no tool_manager → tools disabled --------------------------------

    async def test_no_tool_manager_returns_immediately(self, chat_manager, mock_llm_client):
        """When tool_manager is None, tool_calls in response are ignored."""
        mock_llm_client.chat_completion = AsyncMock(
            return_value=self._tool_response(
                tool_calls=self._sample_tool_calls(),
            )
        )

        result = await chat_manager.chat_completion(
            messages=_make_messages(3),
        )

        # Response still has tool_calls but no loop was run
        assert result.response.get("tool_calls") == self._sample_tool_calls()
        assert result.tool_results == []
        assert "tool_call_rounds" not in result.metadata

    # ---- single-round tool call ------------------------------------------

    async def test_single_round_tool_call(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """Single-round: LLM returns tool_calls → tool_manager runs → LLM final."""
        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=self._sample_tool_results())

        # First call returns tool calls, second call returns final answer
        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response(tool_calls=self._sample_tool_calls()),
                self._final_response("Weather is 55F"),
            ]
        )

        result = await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
        )

        assert result.response["content"] == "Weather is 55F"
        assert result.tool_results == self._sample_tool_results()
        assert result.metadata["tool_call_rounds"] == 1
        assert result.metadata["total_tool_calls"] == 1
        mock_tool_manager.execute_tool_calls.assert_awaited_once()

    # ---- multi-round tool calls ------------------------------------------

    async def test_multi_round_tool_calls(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """Two rounds of tool calls before final response."""
        tc_round1 = [{"id": "tc_1", "name": "search", "arguments": {"q": "foo"}}]
        tr_round1 = [{"tool_call_id": "tc_1", "name": "search", "result": "bar"}]
        tc_round2 = [{"id": "tc_2", "name": "lookup", "arguments": {"id": "bar"}}]
        tr_round2 = [{"tool_call_id": "tc_2", "name": "lookup", "result": "details"}]

        mock_tool_manager.execute_tool_calls = AsyncMock(side_effect=[tr_round1, tr_round2])

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response("thinking", tool_calls=tc_round1),
                self._tool_response("looking up", tool_calls=tc_round2),
                self._final_response("Found it: details"),
            ]
        )

        result = await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
        )

        assert result.response["content"] == "Found it: details"
        assert len(result.tool_results) == 2
        assert result.metadata["tool_call_rounds"] == 2
        assert mock_tool_manager.execute_tool_calls.await_count == 2

    # ---- messages grow with tool history ---------------------------------

    async def test_messages_include_tool_history(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """After one round, messages list includes assistant + tool messages."""
        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=self._sample_tool_results())

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response(
                    content="Let me check",
                    tool_calls=self._sample_tool_calls(),
                ),
                self._final_response("Done"),
            ]
        )

        initial = _make_messages(3)
        initial_count = len(initial)

        result = await chat_manager_with_tools.chat_completion(
            messages=initial,
        )

        # Should have original + assistant + tool messages
        assert len(result.messages) >= initial_count + 2

        # Check assistant message was appended
        assistant_msgs = [m for m in result.messages if m.get("tool_calls")]
        assert len(assistant_msgs) >= 1
        assert assistant_msgs[0]["content"] == "Let me check"

        # Check tool result message was appended
        tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        assert tool_msgs[0]["tool_results"] == self._sample_tool_results()

    # ---- on_progress callback --------------------------------------------

    async def test_on_progress_called(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """on_progress callback is called each round with typing message."""
        on_progress = AsyncMock()
        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=self._sample_tool_results())

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response(
                    content="Checking weather",
                    tool_calls=self._sample_tool_calls(),
                ),
                self._final_response("Done"),
            ]
        )

        await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
            on_progress=on_progress,
        )

        on_progress.assert_awaited_once()
        call_arg = on_progress.call_args[0][0]
        assert call_arg["type"] == "typing"
        assert call_arg["message"] == "Checking weather"
        assert "timestamp" in call_arg

    async def test_on_progress_default_message_when_content_none(
        self, chat_manager_with_tools, mock_llm_client, mock_tool_manager
    ):
        """When response content is None, progress message uses default."""
        on_progress = AsyncMock()
        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=self._sample_tool_results())

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response(
                    content=None,
                    tool_calls=self._sample_tool_calls(),
                ),
                self._final_response("Done"),
            ]
        )

        await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
            on_progress=on_progress,
        )

        call_arg = on_progress.call_args[0][0]
        assert call_arg["message"] == "Working on your request..."

    async def test_on_progress_none_is_fine(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """on_progress=None does not cause errors."""
        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=self._sample_tool_results())

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response(tool_calls=self._sample_tool_calls()),
                self._final_response("Done"),
            ]
        )

        result = await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
            on_progress=None,
        )

        assert result.response["content"] == "Done"

    # ---- max tool call rounds --------------------------------------------

    async def test_max_rounds_exceeded(self, config, mock_llm_client):
        """When rounds hit max_tool_call_rounds, loop stops with note."""
        config.max_tool_call_rounds = 2

        tm = MagicMock()
        tm.tools_desc = {"tools": [{"name": "fn"}]}

        tc = self._sample_tool_calls()
        tr = self._sample_tool_results()
        tm.execute_tool_calls = AsyncMock(return_value=tr)

        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
            tool_manager=tm,
        )

        # LLM always returns tool_calls — never gives a final answer
        mock_llm_client.chat_completion = AsyncMock(
            return_value=self._tool_response(content="Still working", tool_calls=tc)
        )

        result = await mgr.chat_completion(
            messages=_make_messages(3),
        )

        assert result.metadata["tool_call_rounds"] == 2
        assert "[Note: Reached maximum tool call limit of 2 rounds]" in result.response["content"]
        assert result.response["tool_calls"] == []

    # ---- Llama placeholder detection -------------------------------------

    async def test_llama_placeholder_detection(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """Llama placeholder response ends the loop and clears content."""
        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=self._sample_tool_results())

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response(tool_calls=self._sample_tool_calls()),
                # Llama placeholder — no tool_calls
                {
                    "content": "Tool results (round 1)",
                    "role": "assistant",
                    "tool_calls": [],
                    "metadata": {},
                },
            ]
        )

        result = await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
        )

        # Content should be cleared
        assert result.response["content"] == ""
        assert result.metadata["tool_call_rounds"] == 1

    async def test_llama_placeholder_with_tool_calls_continues(
        self, chat_manager_with_tools, mock_llm_client, mock_tool_manager
    ):
        """If Llama returns placeholder-like text WITH tool_calls, continue looping."""
        tc = self._sample_tool_calls()
        tr = self._sample_tool_results()
        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=tr)

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response(tool_calls=tc),
                # Placeholder-like content BUT has tool_calls → continue
                {
                    "content": "Tool results (round 1)",
                    "role": "assistant",
                    "tool_calls": tc,
                    "metadata": {},
                },
                self._final_response("Final"),
            ]
        )

        result = await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
        )

        assert result.response["content"] == "Final"
        assert result.metadata["tool_call_rounds"] == 2

    # ---- no tool_calls in initial response → no loop ---------------------

    async def test_no_tool_calls_skips_loop(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """When response has no tool_calls, no loop runs even with tool_manager."""

        mock_llm_client.chat_completion = AsyncMock(return_value=self._final_response("Simple answer"))

        result = await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
        )

        assert result.response["content"] == "Simple answer"
        assert result.tool_results == []
        mock_tool_manager.execute_tool_calls.assert_not_awaited()
        assert "tool_call_rounds" not in result.metadata

    # ---- empty tool_calls list → no loop ---------------------------------

    async def test_empty_tool_calls_list_skips_loop(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """tool_calls=[] is falsy, so no loop runs."""

        mock_llm_client.chat_completion = AsyncMock(return_value=self._tool_response(content="hi", tool_calls=[]))

        result = await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
        )

        assert result.tool_results == []
        mock_tool_manager.execute_tool_calls.assert_not_awaited()

    # ---- preprocessing re-run each round ---------------------------------

    async def test_preprocessing_rerun_each_round(self, mock_llm_client, config):
        """Each tool call round re-runs preprocess → format pipeline."""
        call_order = []

        preprocessor = MagicMock(spec=MessagePreprocessor)
        preprocessor.preprocess_messages = AsyncMock(
            side_effect=lambda msgs, **kw: (call_order.append("preprocess"), msgs)[1]
        )

        mock_llm_client.format_messages = MagicMock(
            side_effect=lambda msgs, **kwargs: (call_order.append("format"), msgs)[1]
        )

        tc = [{"id": "tc_1", "name": "fn", "arguments": {}}]
        tr = [{"tool_call_id": "tc_1", "name": "fn", "result": "ok"}]

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                # Initial call
                {"content": "", "role": "assistant", "tool_calls": tc, "metadata": {}},
                # After tool round 1
                {"content": "Done", "role": "assistant", "tool_calls": [], "metadata": {}},
            ]
        )

        tm = MagicMock()
        tm.tools_desc = {"tools": [{"name": "fn"}]}
        tm.execute_tool_calls = AsyncMock(return_value=tr)

        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
            tool_manager=tm,
        )
        # Replace internal preprocessor with our spy
        mgr.message_preprocessor = preprocessor

        await mgr.chat_completion(
            messages=_make_messages(3),
        )

        # Initial pipeline: preprocess, format, llm_call
        # Tool round 1 pipeline: preprocess, format, llm_call
        # So we should see the preprocessing steps TWICE
        assert call_order.count("preprocess") == 2
        assert call_order.count("format") == 2

    # ---- multiple tool calls in a single round ---------------------------

    async def test_multiple_tools_in_single_round(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """Multiple tool calls in one round are all executed."""
        tc = [
            {"id": "tc_1", "name": "get_weather", "arguments": {"city": "NYC"}},
            {"id": "tc_2", "name": "get_time", "arguments": {"tz": "EST"}},
        ]
        tr = [
            {"tool_call_id": "tc_1", "name": "get_weather", "result": "72F"},
            {"tool_call_id": "tc_2", "name": "get_time", "result": "3pm"},
        ]

        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=tr)

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response(tool_calls=tc),
                self._final_response("Weather 72F, time 3pm"),
            ]
        )

        result = await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
        )

        mock_tool_manager.execute_tool_calls.assert_awaited_once()
        assert len(result.tool_results) == 2
        assert result.metadata["total_tool_calls"] == 2

    # ---- tool call result structure --------------------------------------

    async def test_tool_result_structure(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """ChatCompletionResult.tool_results is properly populated."""
        tc = self._sample_tool_calls()
        tr = self._sample_tool_results()

        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=tr)
        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response(tool_calls=tc),
                self._final_response("Done"),
            ]
        )

        result = await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
        )

        assert isinstance(result, ChatCompletionResult)
        assert result.tool_results == tr
        assert result.metadata["tool_call_rounds"] == 1
        assert result.metadata["total_tool_calls"] == 1


# ===========================================================================
# TestPreprocessingMetadata — covers lines 276-277 (message count change)
# ===========================================================================


class TestPreprocessingMetadata:
    """Test that preprocessing_applied metadata is set when preprocessing changes message count."""

    @pytest.mark.asyncio
    async def test_preprocessing_sets_preprocessing_applied(self, mock_llm_client, config):
        """When preprocessing changes message count, metadata['preprocessing_applied'] becomes True."""
        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
        )

        # Mock preprocess_messages to return fewer messages (simulating truncation)
        original_messages = [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "A" * 100},
        ]

        async def _mock_preprocess(msgs, **kw):
            # Return only the system message (fewer than input)
            return [msgs[0]]

        mgr.message_preprocessor.preprocess_messages = _mock_preprocess

        result = await mgr.chat_completion(messages=original_messages)

        assert result.metadata["preprocessing_applied"] is True

    @pytest.mark.asyncio
    async def test_no_chunking_preprocessing_applied_stays_false(self, chat_manager):
        """When no chunking happens, preprocessing_applied remains False."""
        messages = [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "Short message"},
        ]

        result = await chat_manager.chat_completion(messages=messages)

        assert result.metadata.get("preprocessing_applied", False) is False


# ===========================================================================
# TestToolExecutorErrors — tool executor failure handling
# ===========================================================================


class TestToolExecutorErrors:
    """Test error propagation when tool_manager.execute_tool_calls raises."""

    @staticmethod
    def _tool_response(**overrides):
        base = {
            "content": "Calling tool...",
            "role": "assistant",
            "tool_calls": [{"id": "tc1", "name": "func", "input": {}}],
            "metadata": {},
        }
        base.update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_tool_manager_exception_propagates(self, chat_manager_with_tools, mock_llm_client, mock_tool_manager):
        """If tool_manager.execute_tool_calls raises, the exception propagates."""
        mock_llm_client.chat_completion = AsyncMock(return_value=self._tool_response())
        mock_tool_manager.execute_tool_calls = AsyncMock(side_effect=RuntimeError("HTTP 500 from API"))

        with pytest.raises(RuntimeError, match="HTTP 500"):
            await chat_manager_with_tools.chat_completion(
                messages=_make_messages(3),
            )

    @pytest.mark.asyncio
    async def test_context_window_error_during_tool_loop(self, config, mock_llm_client):
        """ContextWindowExceededError during a tool-loop round triggers recovery."""
        config.graceful_degradation = True

        tool_calls = [{"id": "tc1", "name": "func", "input": {}}]
        tool_results = [{"tool_call_id": "tc1", "content": "result"}]

        tm = MagicMock()
        tm.tools_desc = {"tools": [{"name": "func"}]}
        tm.execute_tool_calls = AsyncMock(return_value=tool_results)

        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
            tool_manager=tm,
        )

        # First call returns tool_calls.  Second call (in loop) hits context
        # window error.  Recovery path: Layer 1 raises ContextWindowExceeded,
        # then Layer 2 retries with reduced messages (also fails), then
        # graceful degradation kicks in.
        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                {"content": "Calling tool", "role": "assistant", "tool_calls": tool_calls, "metadata": {}},
                ContextWindowExceededError("too big"),
                ContextWindowExceededError("still too big"),  # Layer 2 retry also fails
            ]
        )

        result = await mgr.chat_completion(
            messages=_make_messages(3),
        )

        # Graceful degradation should have kicked in
        assert result.metadata.get("graceful_degradation_used") is True
        assert result.metadata["context_window_retries"] >= 1


# ===========================================================================
# TestNoToolCallMetadata — verify metadata when tools are absent
# ===========================================================================


class TestNoToolCallMetadata:
    """Verify metadata fields when no tool calls happen."""

    @pytest.mark.asyncio
    async def test_tool_call_rounds_zero_without_tools(self, chat_manager):
        """tool_call_rounds should not be in metadata when no tool loop ran."""
        result = await chat_manager.chat_completion(messages=_make_messages(3))

        # No tool loop means no "tool_call_rounds" key at all
        assert "tool_call_rounds" not in result.metadata

    @pytest.mark.asyncio
    async def test_final_message_count_in_metadata(self, chat_manager):
        """final_message_count is always set."""
        msgs = _make_messages(5)
        result = await chat_manager.chat_completion(messages=msgs)

        assert "final_message_count" in result.metadata
        assert result.metadata["original_message_count"] == 5

    @pytest.mark.asyncio
    async def test_tool_results_empty_list_without_tool_loop(self, chat_manager):
        """tool_results is an empty list when no tool calls are made."""
        result = await chat_manager.chat_completion(messages=_make_messages(3))

        assert result.tool_results == []
        assert isinstance(result.tool_results, list)


# ===========================================================================
# TestLLMClientErrorPropagation
# ===========================================================================


class TestLLMClientErrorPropagation:
    """Test that non-context-window LLMClientErrors propagate correctly."""

    @pytest.mark.asyncio
    async def test_llm_error_no_fallback_no_degradation(self, mock_llm_client, config):
        """LLMClientError propagates when no fallback or degradation."""
        config.fallback_model = None
        config.graceful_degradation = False

        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
        )

        mock_llm_client.chat_completion = AsyncMock(side_effect=BedrockClientError("Service unavailable"))

        with pytest.raises(LLMClientError, match="Service unavailable"):
            await mgr.chat_completion(messages=_make_messages(3))

    @pytest.mark.asyncio
    async def test_llm_error_with_degradation(self, mock_llm_client, config):
        """LLMClientError on retry uses graceful degradation when enabled.

        Layer 1 catches only ContextWindowExceededError, so we trigger that
        first, then Layer 2 raises BedrockClientError (an LLMClientError
        subclass) which is caught and flows through to graceful degradation.
        """
        config.fallback_model = None
        config.graceful_degradation = True

        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
        )

        # Layer 1: ContextWindowExceeded triggers reduction
        # Layer 2: BedrockClientError caught → graceful degradation
        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                ContextWindowExceededError("too big"),
                BedrockClientError("Service unavailable"),
            ]
        )

        result = await mgr.chat_completion(messages=_make_messages(3))
        assert "sorry" in result.response["content"].lower()
        assert result.metadata.get("graceful_degradation_used") is True


# ===========================================================================
# TestChatCompletionResultStructure — thorough shape checks
# ===========================================================================


class TestChatCompletionResultStructure:
    """Thoroughly verify ChatCompletionResult shape in various scenarios."""

    @pytest.mark.asyncio
    async def test_result_with_tool_calls_has_all_fields(
        self, chat_manager_with_tools, mock_llm_client, mock_tool_manager
    ):
        """After a tool loop, result has messages, response, tool_results, metadata."""
        tc = [{"id": "tc1", "name": "fn", "input": {}}]
        tr = [{"tool_call_id": "tc1", "content": "ok"}]
        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=tr)

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                {"content": "Calling fn", "role": "assistant", "tool_calls": tc, "metadata": {}},
                {"content": "Done!", "role": "assistant", "tool_calls": [], "metadata": {}},
            ]
        )

        result = await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
        )

        assert isinstance(result, ChatCompletionResult)
        assert isinstance(result.messages, list)
        assert isinstance(result.response, dict)
        assert isinstance(result.tool_results, list)
        assert isinstance(result.metadata, dict)
        assert result.response["content"] == "Done!"
        assert result.metadata["tool_call_rounds"] == 1
        assert result.metadata["total_tool_calls"] == 1

    @pytest.mark.asyncio
    async def test_result_messages_include_original_plus_tool_history(
        self, chat_manager_with_tools, mock_llm_client, mock_tool_manager
    ):
        """Result messages include original msgs plus assistant+tool msgs from loop."""
        tc = [{"id": "tc1", "name": "fn", "input": {}}]
        tr = [{"tool_call_id": "tc1", "content": "ok"}]
        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=tr)

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                {"content": "Calling fn", "role": "assistant", "tool_calls": tc, "metadata": {}},
                {"content": "Final answer", "role": "assistant", "tool_calls": [], "metadata": {}},
            ]
        )

        input_messages = _make_messages(3)
        result = await chat_manager_with_tools.chat_completion(
            messages=input_messages,
        )

        # Should have original messages + assistant (tool_calls) + tool (results)
        roles = [m["role"] for m in result.messages]
        assert "tool" in roles, "Should include tool-result messages from loop"
        assert roles.count("assistant") >= 1


# ===========================================================================
# TestToolCallLoopEdgeCases — additional edge case coverage
# ===========================================================================


class TestToolCallLoopEdgeCases:
    """Additional edge cases for the tool call loop."""

    @staticmethod
    def _tool_response(content="Calling tool", tool_calls=None):
        return {
            "content": content,
            "role": "assistant",
            "tool_calls": tool_calls or [{"id": "tc1", "name": "fn", "input": {}}],
            "metadata": {},
        }

    @staticmethod
    def _final_response(content="Done"):
        return {
            "content": content,
            "role": "assistant",
            "tool_calls": [],
            "metadata": {},
        }

    @pytest.mark.asyncio
    async def test_tool_manager_returns_empty_results(
        self, chat_manager_with_tools, mock_llm_client, mock_tool_manager
    ):
        """Tool manager returning empty list still completes properly."""
        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response(),
                self._final_response("No tool data"),
            ]
        )
        mock_tool_manager.execute_tool_calls = AsyncMock(return_value=[])

        result = await chat_manager_with_tools.chat_completion(
            messages=_make_messages(3),
        )

        assert result.tool_results == []
        assert result.metadata["tool_call_rounds"] == 1
        assert result.response["content"] == "No tool data"

    @pytest.mark.asyncio
    async def test_tools_desc_none_still_allows_tool_calls(self, mock_llm_client, config):
        """Even with tools_desc=None on tool_manager, if LLM returns tool_calls the loop executes."""
        tm = MagicMock()
        tm.tools_desc = None  # No tools_desc but tool_manager is set
        tr = [{"tool_call_id": "tc1", "content": "result"}]
        tm.execute_tool_calls = AsyncMock(return_value=tr)

        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
            tool_manager=tm,
        )

        mock_llm_client.chat_completion = AsyncMock(
            side_effect=[
                self._tool_response(),
                self._final_response("After tool"),
            ]
        )

        result = await mgr.chat_completion(
            messages=_make_messages(3),
        )

        assert result.metadata["tool_call_rounds"] == 1
        assert result.tool_results == tr

    @pytest.mark.asyncio
    async def test_max_rounds_exactly_one(self, mock_llm_client):
        """When max_tool_call_rounds=1, only one round executes even if LLM wants more."""
        config = ChatConfig()
        config.max_tool_call_rounds = 1

        tm = MagicMock()
        tm.tools_desc = {"tools": [{"name": "fn"}]}
        tr = [{"tool_call_id": "tc1", "content": "result"}]
        tm.execute_tool_calls = AsyncMock(return_value=tr)

        mgr = ChatManager(
            llm_client=mock_llm_client,
            config=config,
            tool_manager=tm,
        )

        # LLM returns tool_calls both times (wants 2 rounds), but max is 1
        mock_llm_client.chat_completion = AsyncMock(return_value=self._tool_response())

        result = await mgr.chat_completion(
            messages=_make_messages(3),
        )

        assert result.metadata["tool_call_rounds"] == 1
        assert "[Note: Reached maximum tool call limit of 1 rounds]" in result.response["content"]
