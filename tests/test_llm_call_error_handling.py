"""XMGPLAT-11193 Part 2 — Bedrock error handling in ``llm_call``.

Covers the two failure paths added for the "only Claude Sonnet 5 works" bug:

* raw Bedrock failures are wrapped in :class:`ModelInvocationError` so the
  chat UI shows the real reason instead of the generic "trouble with the AI
  model" bucket (acceptance criterion 8);
* a model that Bedrock says can only be invoked through a cross-region
  inference profile is retried once with a region-prefixed id derived from
  ``chat_config.aws_region``.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.config import ChatConfig
from autolangchat.exceptions import ContextWindowExceededError, ModelInvocationError
from autolangchat.graph.nodes.llm_call import (
    _infer_profile_prefix_for_region,
    _requires_inference_profile,
    _retry_model_id_with_inference_profile,
    _wrap_model_invocation_error,
    llm_call_node,
)

LLAMA_3_3 = "meta.llama3-3-70b-instruct-v1:0"
INFERENCE_PROFILE_ERROR = (
    "An error occurred (ValidationException): Invocation of model ID "
    f"{LLAMA_3_3} with on-demand throughput isn't supported. Retry your "
    "request with the ID or ARN of an inference profile that contains this model."
)
# The real boto3/Bedrock exception text uses a typographic right single
# quotation mark (U+2019) in "isn\u2019t", not a plain ASCII apostrophe --
# regression fixture for the bug where the detection phrase silently never
# matched real errors (only the ASCII-apostrophe test fixture above).
INFERENCE_PROFILE_ERROR_CURLY_QUOTE = (
    "An error occurred (ValidationException) when calling the Converse operation: "
    f"Invocation of model ID {LLAMA_3_3} with on-demand throughput isn\u2019t supported. "
    "Retry your request with the ID or ARN of an inference profile that contains this model."
)


def _config(**overrides) -> ChatConfig:
    return ChatConfig().model_copy(update=overrides)


def _state():
    return {"messages": [{"role": "user", "content": "hi"}], "metadata": {}}


def _runnable_config(chat_config):
    return {"configurable": {"chat_config": chat_config}}


def _ai_message(content: str = "hello", stop_reason: str | None = None):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = []
    msg.usage_metadata = None
    msg.response_metadata = {"stopReason": stop_reason} if stop_reason else {}
    return msg


class TestInferenceProfileDetection:
    def test_detects_bedrock_on_demand_throughput_error(self):
        assert _requires_inference_profile(Exception(INFERENCE_PROFILE_ERROR)) is True

    def test_ignores_unrelated_errors(self):
        assert _requires_inference_profile(Exception("AccessDeniedException")) is False

    def test_detects_real_bedrock_curly_apostrophe_error(self):
        # Regression test: the real Bedrock message uses "isn\u2019t" (curly
        # apostrophe), not "isn't" (ASCII apostrophe).
        assert _requires_inference_profile(Exception(INFERENCE_PROFILE_ERROR_CURLY_QUOTE)) is True

    @pytest.mark.parametrize(
        "region,expected",
        [
            ("us-west-2", "us"),
            ("eu-central-1", "eu"),
            ("ap-northeast-1", "jp"),
            ("ap-southeast-2", "au"),
            ("sa-east-1", "us"),  # unknown region falls back to the broadest profile
            (None, "us"),
        ],
    )
    def test_profile_prefix_for_region(self, region, expected):
        assert _infer_profile_prefix_for_region(region) == expected

    def test_bare_model_id_gets_a_region_prefix(self):
        retry_id = _retry_model_id_with_inference_profile(LLAMA_3_3, _config(aws_region="eu-west-1"))
        assert retry_id == f"eu.{LLAMA_3_3}"

    def test_already_prefixed_model_id_has_nothing_to_retry_with(self):
        assert _retry_model_id_with_inference_profile(f"us.{LLAMA_3_3}", _config()) is None


class TestWrapModelInvocationError:
    def test_carries_model_id_and_raw_reason(self):
        config = _config()
        err = _wrap_model_invocation_error(LLAMA_3_3, Exception("boom"), config)

        assert isinstance(err, ModelInvocationError)
        assert err.model_id == LLAMA_3_3
        assert err.reason == "boom"
        assert LLAMA_3_3 in str(err)
        assert config.get_model_display_name(LLAMA_3_3) in str(err)

    def test_adds_a_hint_for_the_inference_profile_case(self):
        err = _wrap_model_invocation_error(LLAMA_3_3, Exception(INFERENCE_PROFILE_ERROR), _config())
        assert "inference profile" in str(err).lower()

    def test_no_hint_for_unrelated_errors(self):
        err = _wrap_model_invocation_error(LLAMA_3_3, Exception("AccessDeniedException"), _config())
        assert "cross-region inference" not in str(err)


class TestLLMCallNodeErrorPaths:
    @pytest.mark.asyncio
    async def test_bedrock_failure_is_wrapped(self):
        chat_config = _config(model_id=LLAMA_3_3, fallback_model=None)
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=Exception("AccessDeniedException"))

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            with pytest.raises(ModelInvocationError) as exc_info:
                await llm_call_node(_state(), _runnable_config(chat_config))

        assert exc_info.value.model_id == LLAMA_3_3
        assert "AccessDeniedException" in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_retries_once_with_an_inference_profile_id(self):
        chat_config = _config(model_id=LLAMA_3_3, aws_region="us-west-2", fallback_model=None)
        failing_llm = MagicMock()
        failing_llm.ainvoke = AsyncMock(side_effect=Exception(INFERENCE_PROFILE_ERROR))
        working_llm = MagicMock()
        working_llm.ainvoke = AsyncMock(return_value=_ai_message())

        with patch(
            "autolangchat.graph.nodes.llm_call._build_llm",
            side_effect=[failing_llm, working_llm],
        ) as mock_build:
            result = await llm_call_node(_state(), _runnable_config(chat_config))

        assert mock_build.call_args_list[1].args[0] == f"us.{LLAMA_3_3}"
        assert result["metadata"]["inference_profile_model_id"] == f"us.{LLAMA_3_3}"
        assert result["messages"][-1]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_failed_inference_profile_retry_is_wrapped(self):
        chat_config = _config(model_id=LLAMA_3_3, fallback_model=None)
        failing_llm = MagicMock()
        failing_llm.ainvoke = AsyncMock(side_effect=Exception(INFERENCE_PROFILE_ERROR))

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=failing_llm):
            with pytest.raises(ModelInvocationError) as exc_info:
                await llm_call_node(_state(), _runnable_config(chat_config))

        # The user-facing error names the model they actually selected, not
        # the internal retry id.
        assert exc_info.value.model_id == LLAMA_3_3

    @pytest.mark.asyncio
    async def test_context_window_error_still_uses_the_fallback_model(self):
        chat_config = _config(model_id=LLAMA_3_3, fallback_model="us.anthropic.claude-sonnet-5")
        failing_llm = MagicMock()
        failing_llm.ainvoke = AsyncMock(side_effect=Exception("Input is too long for requested model"))
        working_llm = MagicMock()
        working_llm.ainvoke = AsyncMock(return_value=_ai_message())

        # A context-window error goes through emergency re-truncation on the
        # primary model first; only when that retry also overflows does the
        # fallback model get used. Hence three clients: primary, re-truncated
        # primary, fallback.
        with patch(
            "autolangchat.graph.nodes.llm_call._build_llm",
            side_effect=[failing_llm, failing_llm, working_llm],
        ):
            result = await llm_call_node(_state(), _runnable_config(chat_config))

        assert result["metadata"]["emergency_retruncation_applied"] is True
        assert result["metadata"]["fallback_model_used"] is True
        assert result["metadata"]["fallback_model"] == chat_config.fallback_model

    @pytest.mark.asyncio
    async def test_both_models_failing_raises_context_window_error(self):
        chat_config = _config(model_id=LLAMA_3_3, fallback_model="us.anthropic.claude-sonnet-5")
        failing_llm = MagicMock()
        failing_llm.ainvoke = AsyncMock(side_effect=Exception("Input is too long for requested model"))

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=failing_llm):
            with pytest.raises(ContextWindowExceededError):
                await llm_call_node(_state(), _runnable_config(chat_config))


class TestStopReasonMetadata:
    """XMGPLAT-11208: Bedrock Converse's response_metadata["stopReason"] must
    be surfaced into graph_result["metadata"]["stop_reason"] so callers (e.g.
    Workload Analyzer's Stage 2 report generator) can distinguish a genuine
    empty response from a truncated one."""

    @pytest.mark.asyncio
    async def test_stop_reason_is_surfaced_into_top_level_metadata(self):
        chat_config = _config(model_id="us.anthropic.claude-sonnet-5")
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=_ai_message(stop_reason="max_tokens"))

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            result = await llm_call_node(_state(), _runnable_config(chat_config))

        assert result["metadata"]["stop_reason"] == "max_tokens"
        assert result["messages"][-1]["metadata"]["stop_reason"] == "max_tokens"

    @pytest.mark.asyncio
    async def test_missing_stop_reason_surfaces_as_none(self):
        chat_config = _config(model_id="us.anthropic.claude-sonnet-5")
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=_ai_message())

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            result = await llm_call_node(_state(), _runnable_config(chat_config))

        assert result["metadata"]["stop_reason"] is None

    @pytest.mark.asyncio
    async def test_anomalous_stop_reason_logs_a_warning(self, caplog):
        chat_config = _config(model_id="us.anthropic.claude-sonnet-5")
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=_ai_message(stop_reason="max_tokens"))

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            with caplog.at_level(logging.WARNING, logger="autolangchat.graph.nodes.llm_call"):
                await llm_call_node(_state(), _runnable_config(chat_config))

        assert any("max_tokens" in record.message for record in caplog.records)
        assert all(record.levelno == logging.WARNING for record in caplog.records)

    @pytest.mark.asyncio
    async def test_expected_stop_reasons_do_not_log_a_warning(self, caplog):
        chat_config = _config(model_id="us.anthropic.claude-sonnet-5")
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=_ai_message(stop_reason="end_turn"))

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            with caplog.at_level(logging.WARNING, logger="autolangchat.graph.nodes.llm_call"):
                await llm_call_node(_state(), _runnable_config(chat_config))

        assert caplog.records == []


class TestTruncatedEmptyResponseFallback:
    """XMGPLAT-11208 follow-up: a model can exhaust its entire max_tokens
    budget on hidden reasoning before emitting any visible text (observed
    with reasoning-style models like claude-sonnet-5), returning an empty
    message with no indication to the end user of what happened. When that
    happens, the empty content is replaced with a friendly explanation."""

    @pytest.mark.asyncio
    async def test_empty_response_with_max_tokens_gets_a_friendly_message(self):
        from autolangchat.graph.nodes.llm_call import _TRUNCATED_EMPTY_RESPONSE_MESSAGE

        chat_config = _config(model_id="us.anthropic.claude-sonnet-5")
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=_ai_message(content="", stop_reason="max_tokens"))

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            result = await llm_call_node(_state(), _runnable_config(chat_config))

        assert result["messages"][-1]["content"] == _TRUNCATED_EMPTY_RESPONSE_MESSAGE
        assert result["metadata"]["truncated_empty_response"] is True

    @pytest.mark.asyncio
    async def test_empty_response_fallback_logs_at_info_level(self, caplog):
        chat_config = _config(model_id="us.anthropic.claude-sonnet-5")
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=_ai_message(content="", stop_reason="max_tokens"))

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            with caplog.at_level(logging.INFO, logger="autolangchat.graph.nodes.llm_call"):
                await llm_call_node(_state(), _runnable_config(chat_config))

        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("placeholder" in record.message for record in info_records)

    @pytest.mark.asyncio
    async def test_non_empty_response_with_max_tokens_is_left_untouched(self):
        chat_config = _config(model_id="us.anthropic.claude-sonnet-5")
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=_ai_message(content="partial answer", stop_reason="max_tokens"))

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            result = await llm_call_node(_state(), _runnable_config(chat_config))

        assert result["messages"][-1]["content"] == "partial answer"
        assert "truncated_empty_response" not in result["metadata"]

    @pytest.mark.asyncio
    async def test_empty_response_with_normal_stop_reason_is_left_untouched(self):
        chat_config = _config(model_id="us.anthropic.claude-sonnet-5")
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=_ai_message(content="", stop_reason="end_turn"))

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            result = await llm_call_node(_state(), _runnable_config(chat_config))

        assert result["messages"][-1]["content"] == ""
        assert "truncated_empty_response" not in result["metadata"]


class TestUnexecutedToolCallDetection:
    """XMGPLAT-11193 follow-up: a model (observed with Llama 3.3) can emit a
    tool call as plain response text instead of Bedrock executing it as a
    structured toolUse block. It must be recovered into a real tool call so
    the graph routes to the tools node -- the behaviour the pre-LangGraph
    LlamaParser provided via regex -- not returned as the final answer."""

    def _tool(self, name: str) -> MagicMock:
        tool = MagicMock()
        tool.name = name
        return tool

    def test_extracts_bare_json(self):
        from autolangchat.graph.nodes.llm_call import _extract_json_object

        assert _extract_json_object('{"name": "x", "parameters": {}}') == {"name": "x", "parameters": {}}

    def test_extracts_json_wrapped_in_prose(self):
        from autolangchat.graph.nodes.llm_call import _extract_json_object

        text = 'Sure, calling it now: {"name": "x", "parameters": {"a": 1}} done.'
        assert _extract_json_object(text) == {"name": "x", "parameters": {"a": 1}}

    def test_returns_none_for_plain_text(self):
        from autolangchat.graph.nodes.llm_call import _extract_json_object

        assert _extract_json_object("Here is your workload analysis report.") is None

    def test_detects_matching_tool_call_shape(self):
        from autolangchat.graph.nodes.llm_call import _recover_tool_calls_from_text

        content = '{"type": "function", "name": "download_file", "parameters": {"job_id": "abc"}}'
        recovered = _recover_tool_calls_from_text(content, {"download_file"})
        assert len(recovered) == 1
        assert recovered[0]["name"] == "download_file"
        assert recovered[0]["args"] == {"job_id": "abc"}
        assert recovered[0]["type"] == "tool_call"
        assert recovered[0]["id"]

    def test_recovers_openai_style_arguments_key(self):
        from autolangchat.graph.nodes.llm_call import _recover_tool_calls_from_text

        content = '{"name": "download_file", "arguments": {"job_id": "abc"}}'
        recovered = _recover_tool_calls_from_text(content, {"download_file"})
        assert recovered[0]["args"] == {"job_id": "abc"}

    def test_recovers_arguments_encoded_as_json_string(self):
        from autolangchat.graph.nodes.llm_call import _recover_tool_calls_from_text

        content = '{"name": "download_file", "arguments": "{\\"job_id\\": \\"abc\\"}"}'
        recovered = _recover_tool_calls_from_text(content, {"download_file"})
        assert recovered[0]["args"] == {"job_id": "abc"}

    def test_recovered_ids_are_unique(self):
        from autolangchat.graph.nodes.llm_call import _recover_tool_calls_from_text

        content = (
            '[{"type": "function", "name": "download_file", "parameters": {"f": "a"}},'
            ' {"type": "function", "name": "download_file", "parameters": {"f": "b"}}]'
        )
        recovered = _recover_tool_calls_from_text(content, {"download_file"})
        assert len(recovered) == 2
        assert recovered[0]["id"] != recovered[1]["id"]

    def test_ignores_json_naming_an_unbound_tool(self):
        from autolangchat.graph.nodes.llm_call import _recover_tool_calls_from_text

        content = '{"type": "function", "name": "some_other_tool", "parameters": {}}'
        assert _recover_tool_calls_from_text(content, {"download_file"}) == []

    def test_ignores_json_without_parameters_or_type_marker(self):
        from autolangchat.graph.nodes.llm_call import _recover_tool_calls_from_text

        # A dict that merely happens to have a "name" key matching a tool
        # name, with no function-call-shaped markers, shouldn't be recovered.
        assert _recover_tool_calls_from_text('{"name": "download_file"}', {"download_file"}) == []

    def test_ignores_plain_text(self):
        from autolangchat.graph.nodes.llm_call import _recover_tool_calls_from_text

        assert _recover_tool_calls_from_text("Just a normal answer.", {"download_file"}) == []

    def test_no_bound_tools_never_matches(self):
        from autolangchat.graph.nodes.llm_call import _recover_tool_calls_from_text

        content = '{"type": "function", "name": "download_file", "parameters": {}}'
        assert _recover_tool_calls_from_text(content, set()) == []

    @pytest.mark.asyncio
    async def test_llm_call_node_recovers_leaked_tool_call_text(self):
        chat_config = _config(model_id=LLAMA_3_3, fallback_model=None)
        chat_config.langchain_tools = [self._tool("download_file")]
        leaked = _ai_message('{"type": "function", "name": "download_file", "parameters": {"job_id": "x"}}')
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=leaked)

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            result = await llm_call_node(_state(), _runnable_config(chat_config))

        last = result["messages"][-1]
        assert len(last["tool_calls"]) == 1
        assert last["tool_calls"][0]["name"] == "download_file"
        assert last["tool_calls"][0]["args"] == {"job_id": "x"}
        # The raw JSON must not survive as the visible answer.
        assert last["content"] == ""
        assert result["metadata"]["recovered_text_tool_calls"] == 1

    @pytest.mark.asyncio
    async def test_recovered_tool_call_routes_to_tools_node(self):
        from autolangchat.graph.routing import should_continue

        chat_config = _config(model_id=LLAMA_3_3, fallback_model=None)
        chat_config.langchain_tools = [self._tool("download_file")]
        leaked = _ai_message('{"type": "function", "name": "download_file", "parameters": {"job_id": "x"}}')
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=leaked)

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            result = await llm_call_node(_state(), _runnable_config(chat_config))

        assert should_continue({"messages": result["messages"]}) == "tools"

    @pytest.mark.asyncio
    async def test_llm_call_node_passes_through_real_tool_calls(self):
        chat_config = _config(model_id=LLAMA_3_3, fallback_model=None)
        chat_config.langchain_tools = [self._tool("download_file")]
        ai_msg = _ai_message('{"type": "function", "name": "download_file", "parameters": {}}')
        ai_msg.tool_calls = [{"id": "1", "name": "download_file", "args": {"job_id": "x"}}]
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=ai_msg)

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            result = await llm_call_node(_state(), _runnable_config(chat_config))

        assert result["messages"][-1]["tool_calls"] == ai_msg.tool_calls

    @pytest.mark.asyncio
    async def test_llm_call_node_passes_through_normal_text_answers(self):
        chat_config = _config(model_id=LLAMA_3_3, fallback_model=None)
        chat_config.langchain_tools = [self._tool("download_file")]
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=_ai_message("The workload is compute-bound."))

        with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
            result = await llm_call_node(_state(), _runnable_config(chat_config))

        assert result["messages"][-1]["content"] == "The workload is compute-bound."
