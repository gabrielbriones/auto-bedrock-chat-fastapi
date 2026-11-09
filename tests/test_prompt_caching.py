"""XMGPLAT-11867 — Bedrock prompt caching.

Covers :mod:`autolangchat.prompt_caching` (cachePoint construction, gating,
checkpoint limits, TTL) and its wiring through ``rag_node`` /
``llm_call_node``: the Converse payload is captured at the botocore boundary
and compared against the pre-caching snapshot (parity) or checked for the
expected cachePoint placement.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import botocore.session
import pytest
from botocore.validate import ParamValidator
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from autolangchat.graph.nodes.llm_call import llm_call_node
from autolangchat.prompt_caching import (
    SYSTEM_PREFIX_CHARS_KEY,
    apply_cache_points,
    build_system_message,
    cache_point_block,
    prompt_caching_active,
    system_prefix_chars,
)

from .prompt_caching_payloads import (
    BASE_PROMPT,
    CACHING_MODEL,
    NON_CACHING_MODEL,
    SCENARIOS,
    SNAPSHOT_PATH,
    capture_payload,
    chat_config,
)

SONNET_5 = "global.anthropic.claude-sonnet-5"
NOVA_PRO = "us.amazon.nova-pro-v1:0"
LLAMA = "meta.llama3-3-70b-instruct-v1:0"

# ~1,500 tokens: above Sonnet 5's 1,024-token minimum on its own.
LONG_TEXT = "stable instructions " * 300


@pytest.fixture(autouse=True)
def _fake_aws_credentials(monkeypatch):
    # ChatBedrockConverse builds a real boto3 client; the API call itself is patched.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


def _cache_points(content):
    return [block for block in content if isinstance(block, dict) and "cachePoint" in block]


def _payload_cache_points(payload):
    points = [("system", i) for i, block in enumerate(payload["system"]) if "cachePoint" in block]
    for index, message in enumerate(payload["messages"]):
        points += [(index, i) for i, block in enumerate(message["content"]) if "cachePoint" in block]
    return points


def _tool_loop(rounds: int, text: str = "x"):
    """System + user + ``rounds`` assistant/tool rounds, as LangChain messages."""
    messages = [SystemMessage(content=LONG_TEXT), HumanMessage(content=text)]
    for i in range(rounds):
        call = {"name": "lookup", "args": {"q": i}, "id": f"call-{i}", "type": "tool_call"}
        messages += [
            AIMessage(content="", tool_calls=[call]),
            ToolMessage(content=f"result {i}", tool_call_id=f"call-{i}"),
        ]
    return messages


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


class TestGating:
    def test_off_by_default(self):
        from autolangchat.config import ChatConfig

        # Declared defaults, not ChatConfig(): that would read the env/.env.
        assert ChatConfig.model_fields["prompt_caching_enabled"].default is False
        assert ChatConfig.model_fields["prompt_cache_ttl"].default == "5m"
        assert not prompt_caching_active(chat_config(SONNET_5), SONNET_5)

    def test_needs_flag_and_supporting_model(self):
        enabled = chat_config(SONNET_5, prompt_caching_enabled=True)
        assert prompt_caching_active(enabled, SONNET_5)
        assert not prompt_caching_active(enabled, LLAMA)
        assert not prompt_caching_active(enabled, None)
        assert not prompt_caching_active(None, SONNET_5)

    def test_ttl_is_validated(self):
        from pydantic import ValidationError

        from autolangchat.config import ChatConfig

        assert ChatConfig(AUTOCHAT_PROMPT_CACHE_TTL="1h").prompt_cache_ttl == "1h"
        with pytest.raises(ValidationError):
            ChatConfig(AUTOCHAT_PROMPT_CACHE_TTL="10m")


# ---------------------------------------------------------------------------
# Block construction
# ---------------------------------------------------------------------------


class TestCachePointBlock:
    def test_default_ttl_is_omitted(self):
        assert cache_point_block(SONNET_5, "5m") == {"cachePoint": {"type": "default"}}
        assert cache_point_block(SONNET_5, None) == {"cachePoint": {"type": "default"}}

    def test_one_hour_ttl_on_supporting_model(self):
        assert cache_point_block(SONNET_5, "1h") == {"cachePoint": {"type": "default", "ttl": "1h"}}

    def test_one_hour_ttl_falls_back_on_five_minute_only_model(self):
        assert cache_point_block(NOVA_PRO, "1h") == {"cachePoint": {"type": "default"}}


class TestSystemMessage:
    def test_stable_first_then_context(self):
        message = build_system_message("BASE", ["KB", None, "AUTH"])
        assert message["content"] == "BASE\n\nKB\n\nAUTH"
        assert message["metadata"] == {SYSTEM_PREFIX_CHARS_KEY: 4}
        assert system_prefix_chars([message, {"role": "user", "content": "q"}]) == 4

    def test_prefix_chars_ignores_invalid_metadata(self):
        assert system_prefix_chars([{"role": "system", "content": "x"}]) is None
        assert (
            system_prefix_chars([{"role": "system", "content": "x", "metadata": {SYSTEM_PREFIX_CHARS_KEY: True}}])
            is None
        )
        assert system_prefix_chars([{"role": "user", "content": "x"}]) is None

    def test_system_split_into_stable_cache_point_variable(self):
        messages = [SystemMessage(content=LONG_TEXT + "\n\nKB CONTEXT"), HumanMessage(content="q")]
        apply_cache_points(messages, model_id=SONNET_5, system_prefix=len(LONG_TEXT))

        assert messages[0].content == [
            {"type": "text", "text": LONG_TEXT},
            {"cachePoint": {"type": "default"}},
            {"type": "text", "text": "\n\nKB CONTEXT"},
        ]

    def test_whole_system_cached_without_split_metadata(self):
        messages = [SystemMessage(content=LONG_TEXT), HumanMessage(content="q")]
        apply_cache_points(messages, model_id=SONNET_5, ttl="1h")

        assert messages[0].content == [
            {"type": "text", "text": LONG_TEXT},
            {"cachePoint": {"type": "default", "ttl": "1h"}},
        ]

    def test_tool_schemas_count_towards_the_system_prefix(self):
        from langchain_core.tools import tool

        @tool
        def big_tool(query: str) -> str:
            """A tool with a long description."""
            return query

        big_tool.description = "describes the tool " * 300
        short_system = "short stable prompt"

        without_tools = [SystemMessage(content=short_system), HumanMessage(content="q")]
        apply_cache_points(without_tools, model_id=SONNET_5)
        assert isinstance(without_tools[0].content, str)

        with_tools = [SystemMessage(content=short_system), HumanMessage(content="q")]
        apply_cache_points(with_tools, model_id=SONNET_5, tools=[big_tool])
        assert _cache_points(with_tools[0].content)


# ---------------------------------------------------------------------------
# History-first checkpoints and limits
# ---------------------------------------------------------------------------


class TestHistoryCheckpoints:
    def test_below_minimum_nothing_is_placed(self):
        messages = [SystemMessage(content="tiny"), HumanMessage(content="hi")]
        assert apply_cache_points(messages, model_id=SONNET_5) == 0
        assert messages[0].content == "tiny"
        assert messages[1].content == "hi"

    def test_round_two_caches_the_previous_round_and_the_new_tail(self):
        messages = _tool_loop(rounds=1)
        assert apply_cache_points(messages, model_id=SONNET_5) == 3

        assert _cache_points(messages[0].content)  # system
        assert _cache_points(messages[1].content)  # end of round 1's request
        assert messages[2].content == ""  # assistant turns are never marked
        assert _cache_points(messages[3].content)  # new tail (tool result)

    def test_checkpoint_limit_keeps_the_newest_boundaries(self):
        messages = _tool_loop(rounds=5)
        assert apply_cache_points(messages, model_id=SONNET_5) == 4

        marked = [i for i, m in enumerate(messages) if isinstance(m.content, list) and _cache_points(m.content)]
        # system + the three newest request boundaries (tool results of rounds 5, 4, 3)
        assert marked == [0, 7, 9, 11]

    def test_respects_a_smaller_checkpoint_limit(self):
        with patch.dict(
            "autolangchat.model_capabilities.CAPABILITY_OVERRIDES",
            {"anthropic.claude-sonnet-5": {"prompt_cache_max_checkpoints": 2}},
        ):
            messages = _tool_loop(rounds=5)
            assert apply_cache_points(messages, model_id=SONNET_5) == 2
        assert _cache_points(messages[0].content)
        assert _cache_points(messages[-1].content)

    def test_history_cached_when_system_prompt_alone_is_too_small(self):
        messages = [SystemMessage(content="tiny"), HumanMessage(content=LONG_TEXT)]
        assert apply_cache_points(messages, model_id=SONNET_5) == 1
        assert messages[0].content == "tiny"
        assert messages[1].content == [{"type": "text", "text": LONG_TEXT}, {"cachePoint": {"type": "default"}}]

    def test_nova_skips_tool_results_and_oversized_prefixes(self):
        messages = _tool_loop(rounds=1)
        apply_cache_points(messages, model_id=NOVA_PRO)
        assert isinstance(messages[3].content, str)  # tool result left alone
        assert _cache_points(messages[1].content)

        huge = [SystemMessage(content="x " * 60_000), HumanMessage(content="q")]
        assert apply_cache_points(huge, model_id=NOVA_PRO) == 0

    def test_nova_skips_a_user_message_merged_into_a_tool_result(self):
        messages = _tool_loop(rounds=1) + [HumanMessage(content="follow-up")]
        apply_cache_points(messages, model_id=NOVA_PRO)
        assert messages[-1].content == "follow-up"
        assert isinstance(messages[-2].content, str)

    def test_unsupported_model_gets_nothing(self):
        messages = _tool_loop(rounds=2)
        assert apply_cache_points(messages, model_id=LLAMA) == 0


# ---------------------------------------------------------------------------
# End-to-end payloads (rag_node + llm_call_node -> botocore)
# ---------------------------------------------------------------------------


_SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text())
_CONVERSE_INPUT = (
    botocore.session.get_session().get_service_model("bedrock-runtime").operation_model("Converse").input_shape
)


class TestPayloadParity:
    """With caching off (or unsupported) the request matches the pre-change snapshot."""

    @pytest.mark.parametrize("model_id", [CACHING_MODEL, NON_CACHING_MODEL])
    @pytest.mark.parametrize("scenario", list(SCENARIOS))
    async def test_flag_off_matches_snapshot(self, model_id, scenario):
        payload = await capture_payload(scenario, chat_config(model_id, prompt_caching_enabled=False))
        assert payload == _SNAPSHOT[f"{model_id}::{scenario}"]

    @pytest.mark.parametrize("scenario", list(SCENARIOS))
    async def test_unsupported_model_with_flag_on_matches_snapshot(self, scenario):
        config = chat_config(NON_CACHING_MODEL, prompt_caching_enabled=True, prompt_cache_ttl="1h")
        payload = await capture_payload(scenario, config)
        assert payload == _SNAPSHOT[f"{NON_CACHING_MODEL}::{scenario}"]


class TestCachingPayload:
    @pytest.mark.parametrize("scenario", list(SCENARIOS))
    async def test_payload_is_valid_and_within_limits(self, scenario):
        payload = await capture_payload(scenario, chat_config(CACHING_MODEL, prompt_caching_enabled=True))

        assert not ParamValidator().validate(payload, _CONVERSE_INPUT).has_errors()
        points = _payload_cache_points(payload)
        assert 1 <= len(points) <= 4
        # Stable prompt first, cachePoint right after it.
        assert payload["system"][0]["text"] == BASE_PROMPT
        assert payload["system"][1] == {"cachePoint": {"type": "default"}}

    async def test_retrieval_turn_keeps_context_after_the_cached_prefix(self):
        payload = await capture_payload(
            "rag_and_auth_first_turn", chat_config(CACHING_MODEL, prompt_caching_enabled=True)
        )
        variable = payload["system"][2]["text"]
        assert variable.startswith("\n\nRELEVANT KNOWLEDGE BASE CONTEXT:")
        assert "AUTHENTICATED USER" in variable

    async def test_tool_round_two_marks_the_tool_result(self):
        payload = await capture_payload("rag_tool_round_two", chat_config(CACHING_MODEL, prompt_caching_enabled=True))
        tail = payload["messages"][-1]["content"]
        assert "toolResult" in tail[0]
        assert tail[-1] == {"cachePoint": {"type": "default"}}

    async def test_one_hour_ttl_reaches_every_cache_point(self):
        payload = await capture_payload(
            "rag_follow_up_turn", chat_config(CACHING_MODEL, prompt_caching_enabled=True, prompt_cache_ttl="1h")
        )
        assert not ParamValidator().validate(payload, _CONVERSE_INPUT).has_errors()
        points = _payload_cache_points(payload)
        assert len(points) == 4
        assert all(
            (payload["system"][i] if where == "system" else payload["messages"][where]["content"][i])["cachePoint"]
            == {"type": "default", "ttl": "1h"}
            for where, i in points
        )

    async def test_changing_the_system_prompt_changes_the_cached_prefix(self):
        first = await capture_payload("plain_first_turn", chat_config(CACHING_MODEL, prompt_caching_enabled=True))
        revised = await capture_payload(
            "plain_first_turn",
            chat_config(CACHING_MODEL, prompt_caching_enabled=True, system_prompt=BASE_PROMPT + " Revised."),
        )
        assert first["system"][0]["text"] != revised["system"][0]["text"]


# ---------------------------------------------------------------------------
# Usage: streaming and non-streaming report the same cache counts
# ---------------------------------------------------------------------------

_USAGE = {
    "inputTokens": 40,
    "outputTokens": 7,
    "totalTokens": 3047,
    "cacheReadInputTokens": 2500,
    "cacheWriteInputTokens": 500,
}


def _fake_bedrock(operation_name, api_params):
    if operation_name == "Converse":
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": dict(_USAGE),
            "metrics": {"latencyMs": 5},
        }
    assert operation_name == "ConverseStream"
    return {
        "stream": [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"delta": {"text": "ok"}, "contentBlockIndex": 0}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": dict(_USAGE), "metrics": {"latencyMs": 5}}},
        ]
    }


async def _run_llm_call(on_progress):
    config = chat_config(CACHING_MODEL, prompt_caching_enabled=True, langchain_tools=None)
    state = {"messages": [{"role": "user", "content": "hi"}], "metadata": {}}
    operations = []

    def _api_call(_client, operation_name, api_params):
        operations.append(operation_name)
        return _fake_bedrock(operation_name, api_params)

    with patch("botocore.client.BaseClient._make_api_call", _api_call):
        result = await llm_call_node(state, {"configurable": {"chat_config": config, "on_progress": on_progress}})
    return result, operations


class TestUsageParity:
    async def test_streaming_and_non_streaming_report_identical_cache_usage(self):
        async def on_progress(_event):
            return None

        streamed, streamed_ops = await _run_llm_call(on_progress)
        invoked, invoked_ops = await _run_llm_call(None)
        assert streamed_ops == ["ConverseStream"]
        assert invoked_ops == ["Converse"]

        expected_usage = {
            "input_tokens": 3040,  # 40 uncached + 2500 read + 500 written
            "output_tokens": 7,
            "cache_read_tokens": 2500,
            "cache_write_tokens": 500,
        }
        for result in (streamed, invoked):
            assert result["messages"][-1]["metadata"]["usage"] == expected_usage
            assert {key: result["metadata"][key] for key in expected_usage} == expected_usage
