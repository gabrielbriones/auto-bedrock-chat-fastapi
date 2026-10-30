"""Tests for the ContextBudget estimator (XMGPLAT-11843)."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from autolangchat import context_budget as cb
from autolangchat.config import ChatConfig
from autolangchat.context_budget import (
    ContextBudget,
    chars_per_token,
    classify_content,
    estimate_message_parts,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_text,
    estimate_tokens,
    estimate_tool_schema_tokens,
    provider_for_model,
)
from autolangchat.defaults import (
    CONTEXT_BUDGET_CHARS_PER_TOKEN,
    CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS,
    CONTEXT_BUDGET_FALLBACK_CHARS_PER_TOKEN,
    CONTEXT_BUDGET_KIND_JSON,
    CONTEXT_BUDGET_KIND_LOG_TABULAR,
    CONTEXT_BUDGET_KIND_PROSE,
    CONTEXT_BUDGET_KINDS,
    CONTEXT_BUDGET_PER_MESSAGE_OVERHEAD_TOKENS,
    DEFAULT_MAX_INPUT_TOKENS_UNKNOWN,
)

OVERHEAD = CONTEXT_BUDGET_PER_MESSAGE_OVERHEAD_TOKENS
CLAUDE = "us.anthropic.claude-sonnet-5"
QWEN = "qwen.qwen3-32b-v1:0"

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "context_budget"
CALIBRATION = json.loads((FIXTURES_DIR / "calibration.json").read_text(encoding="utf-8"))
FIXTURE_KINDS = {name: meta["kind"] for name, meta in CALIBRATION["fixtures"].items()}

# Allowed estimate error vs. real Bedrock input tokens. Underestimating is
# the dangerous direction (payload sent, model rejects it), so it is held to
# 5% for every measured model. Overestimating only wastes budget; it is
# checked against each provider's densest model (the one the ratio is set
# from), since lighter tokenizers in the same family are over-estimated by
# design. log_tabular spans mixed logs and digit-heavy CSV, which differ by
# ~40% in density, so one ratio cannot fit both within 25%.
MAX_UNDERESTIMATE = 0.05
MAX_OVERESTIMATE = {
    CONTEXT_BUDGET_KIND_PROSE: 0.25,
    CONTEXT_BUDGET_KIND_JSON: 0.25,
    CONTEXT_BUDGET_KIND_LOG_TABULAR: 0.50,
}


def _calibration_cases():
    for provider, entry in sorted(CALIBRATION["providers"].items()):
        for run in entry["models"]:
            for fixture in run["fixtures"]:
                yield pytest.param(provider, run, fixture, id=f"{run['model_id']}-{fixture}")


@pytest.fixture
def config():
    return ChatConfig(_env_file=None)


@pytest.fixture(autouse=True)
def _reset_unknown_model_warnings():
    cb._warned_unknown_models.clear()
    yield
    cb._warned_unknown_models.clear()


# ── Classifier ──────────────────────────────────────────


class TestClassifyContent:
    def test_prose(self):
        text = "The quick brown fox jumps over the lazy dog. " * 20
        assert classify_content(text) == CONTEXT_BUDGET_KIND_PROSE

    def test_json_object_and_array(self):
        assert classify_content('{"a": 1, "b": [1, 2]}') == CONTEXT_BUDGET_KIND_JSON
        assert classify_content('  [{"a": 1}, {"a": 2}]') == CONTEXT_BUDGET_KIND_JSON

    def test_non_string_payload_is_json(self):
        assert classify_content({"items": [1, 2, 3]}) == CONTEXT_BUDGET_KIND_JSON

    def test_malformed_object_still_json(self):
        assert classify_content('{"a": 1, "b": ') == CONTEXT_BUDGET_KIND_JSON

    def test_bracketed_log_is_not_json(self):
        text = "\n".join(f"[INFO] 2026-09-{d:02d} 12:00:{d:02d} worker-{d} started job {d * 17}" for d in range(1, 20))
        assert classify_content(text) == CONTEXT_BUDGET_KIND_LOG_TABULAR

    def test_large_bracketed_log_is_not_json(self):
        line = "[INFO] request served in 12ms by worker 3\n"
        text = line * (CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS // len(line) + 10)
        assert len(text) > CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS
        assert classify_content(text) == CONTEXT_BUDGET_KIND_LOG_TABULAR

    def test_large_json_array_beyond_prefix(self):
        text = json.dumps([{"id": i, "name": f"item-{i}", "value": i * 1.5} for i in range(5000)])
        assert len(text) > CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS
        assert classify_content(text) == CONTEXT_BUDGET_KIND_JSON

    @pytest.mark.parametrize("literal", ["true", "false", "null"])
    def test_large_json_array_of_literals_beyond_prefix(self, literal):
        text = "[" + ",".join([literal] * (CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS // len(literal) + 1)) + "]"
        assert len(text) > CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS
        assert classify_content(text) == CONTEXT_BUDGET_KIND_JSON

    @pytest.mark.parametrize("value", ["123", "-4.5", "1e3", "0"])
    def test_large_json_array_of_numbers_beyond_prefix(self, value):
        text = "[" + ", ".join([value] * (CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS // len(value) + 1)) + "]"
        assert len(text) > CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS
        assert classify_content(text) == CONTEXT_BUDGET_KIND_JSON

    def test_numeric_json_array_at_prefix_limit(self):
        values = []
        while len(json.dumps(values)) <= CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS:
            values.append(123)
        below = json.dumps(values[:-1])
        above = json.dumps(values)
        assert len(below) <= CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS < len(above)
        assert classify_content(below) == CONTEXT_BUDGET_KIND_JSON
        assert classify_content(above) == CONTEXT_BUDGET_KIND_JSON

    @pytest.mark.parametrize(
        "line",
        [
            "[2026-10-15 12:00:01] worker 3 served request in 12ms\n",
            "[12:00:01] worker 3 served request in 12ms\n",
            "[    0.123456] kernel: usb 1-1: new device found\n",
            "[123] worker 3 served request in 12ms\n",
        ],
    )
    def test_large_bracketed_log_starting_with_number_is_not_json(self, line):
        text = line * (CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS // len(line) + 10)
        assert len(text) > CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS
        assert classify_content(text) == CONTEXT_BUDGET_KIND_LOG_TABULAR

    def test_large_bracketed_log_starting_with_n_is_not_json(self):
        text = "[notice] background sync completed\n" * 3000
        assert len(text) > CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS
        assert classify_content(text) == CONTEXT_BUDGET_KIND_LOG_TABULAR

    def test_csv_is_log_tabular(self):
        rows = ["ts,host,cpu,mem"] + [f"2026-09-01 00:00:{i:02d},h{i},{i * 1.5:.1f},{i * 2}" for i in range(40)]
        assert classify_content("\n".join(rows)) == CONTEXT_BUDGET_KIND_LOG_TABULAR

    def test_many_short_lines_is_log_tabular(self):
        text = "\n".join(f"worker alpha ready line {chr(97 + i % 26)}" for i in range(30))
        assert classify_content(text) == CONTEXT_BUDGET_KIND_LOG_TABULAR

    def test_binary_like_is_log_tabular(self):
        assert classify_content(bytes(range(256)) * 4) == CONTEXT_BUDGET_KIND_LOG_TABULAR

    @pytest.mark.parametrize("value", ["", None, b""])
    def test_empty_is_prose(self, value):
        assert classify_content(value) == CONTEXT_BUDGET_KIND_PROSE

    def test_bytes_are_decoded(self):
        assert classify_content(b'{"a": 1}') == CONTEXT_BUDGET_KIND_JSON

    def test_only_prefix_is_inspected(self):
        prose = "Plain words in ordinary sentences keep going here. " * (
            CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS // 50 + 1
        )
        text = prose[:CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS] + "1234567890,;:" * 100_000
        assert classify_content(text) == CONTEXT_BUDGET_KIND_PROSE

    @pytest.mark.parametrize("fixture", sorted(FIXTURE_KINDS))
    def test_calibration_fixtures_classified_as_their_kind(self, fixture):
        text = (FIXTURES_DIR / fixture).read_text(encoding="utf-8")
        assert classify_content(text) == FIXTURE_KINDS[fixture]


# ── Ratios ──────────────────────────────────────────────


class TestRatios:
    @pytest.mark.parametrize(
        "model_id, provider",
        [
            (CLAUDE, "anthropic"),
            ("global.anthropic.claude-opus-4-8", "anthropic"),
            ("eu.amazon.nova-pro-v1:0", "amazon"),
            ("anthropic.claude-3-haiku-20240307-v1:0", "anthropic"),
            (QWEN, "qwen"),
            ("", ""),
        ],
    )
    def test_provider_for_model(self, model_id, provider):
        assert provider_for_model(model_id) == provider

    def test_every_provider_has_every_kind(self):
        for provider, ratios in CONTEXT_BUDGET_CHARS_PER_TOKEN.items():
            assert set(ratios) == set(CONTEXT_BUDGET_KINDS), provider
            assert all(r > 0 for r in ratios.values()), provider

    def test_ratios_differ_by_provider_and_kind(self):
        assert chars_per_token(CONTEXT_BUDGET_KIND_PROSE, "anthropic") < chars_per_token(
            CONTEXT_BUDGET_KIND_PROSE, "openai"
        )
        for provider in CONTEXT_BUDGET_CHARS_PER_TOKEN:
            assert chars_per_token(CONTEXT_BUDGET_KIND_JSON, provider) < chars_per_token(
                CONTEXT_BUDGET_KIND_PROSE, provider
            )

    def test_fallback_is_minimum_per_kind(self):
        for kind in CONTEXT_BUDGET_KINDS:
            expected = min(r[kind] for r in CONTEXT_BUDGET_CHARS_PER_TOKEN.values())
            assert CONTEXT_BUDGET_FALLBACK_CHARS_PER_TOKEN[kind] == expected
            assert chars_per_token(kind, "not-a-provider") == expected
            assert chars_per_token(kind, None) == expected

    def test_unknown_kind_uses_densest_ratio(self):
        assert chars_per_token("weird", "anthropic") == min(CONTEXT_BUDGET_CHARS_PER_TOKEN["anthropic"].values())

    def test_every_calibrated_provider_has_ratios(self):
        assert set(CALIBRATION["providers"]) <= set(CONTEXT_BUDGET_CHARS_PER_TOKEN)


# ── Calibration tolerance ───────────────────────────────


class TestCalibrationTolerance:
    @pytest.mark.parametrize("provider, run, fixture", list(_calibration_cases()))
    def test_no_underestimate(self, provider, run, fixture):
        measured = run["fixtures"][fixture]
        text = (FIXTURES_DIR / fixture).read_text(encoding="utf-8")
        assert len(text) == measured["chars"], "fixture changed since calibration; re-calibrate"
        estimate = estimate_tokens(text, kind=FIXTURE_KINDS[fixture], provider=provider)
        assert estimate >= measured["fixture_tokens"] * (
            1 - MAX_UNDERESTIMATE
        ), f"{run['model_id']} {fixture}: estimate {estimate} vs real {measured['fixture_tokens']}"

    @pytest.mark.parametrize("provider", sorted(CALIBRATION["providers"]))
    @pytest.mark.parametrize("fixture", sorted(FIXTURE_KINDS))
    def test_overestimate_bounded_for_densest_model(self, provider, fixture):
        kind = FIXTURE_KINDS[fixture]
        runs = CALIBRATION["providers"][provider]["models"]
        text = (FIXTURES_DIR / fixture).read_text(encoding="utf-8")
        real = max(r["fixtures"][fixture]["fixture_tokens"] for r in runs)
        estimate = estimate_tokens(text, kind=kind, provider=provider)
        assert estimate <= real * (1 + MAX_OVERESTIMATE[kind]), f"{provider} {fixture}: {estimate} vs {real}"


# ── Token estimation ────────────────────────────────────


class TestEstimateTokens:
    def test_empty_is_zero(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens(None) == 0

    def test_rounds_up(self):
        ratio = chars_per_token(CONTEXT_BUDGET_KIND_PROSE, "anthropic")
        text = "a" * (int(ratio * 10) + 1)
        assert estimate_tokens(text, kind=CONTEXT_BUDGET_KIND_PROSE, provider="anthropic") == math.ceil(
            len(text) / ratio
        )

    def test_dense_content_costs_more_than_prose(self):
        text = "x" * 10_000
        prose = estimate_tokens(text, kind=CONTEXT_BUDGET_KIND_PROSE, provider="openai")
        dense = estimate_tokens(text, kind=CONTEXT_BUDGET_KIND_LOG_TABULAR, provider="openai")
        assert dense > prose

    def test_message_overhead_and_content(self):
        msg = {"role": "user", "content": "hello there"}
        assert estimate_message_tokens(msg, "anthropic") == OVERHEAD + estimate_tokens("hello there", None, "anthropic")

    def test_assistant_tool_calls_counted(self):
        calls = [{"id": "t1", "name": "get_job", "args": {"job_id": "abc"}}]
        plain = estimate_message_tokens({"role": "assistant", "content": "ok"}, "anthropic")
        with_calls = estimate_message_tokens({"role": "assistant", "content": "ok", "tool_calls": calls}, "anthropic")
        assert with_calls == plain + estimate_tokens(calls, kind=CONTEXT_BUDGET_KIND_JSON, provider="anthropic")

    def test_tool_use_block_mirrored_in_tool_calls_counted_once(self):
        args = {"query": "x" * 5000}
        calls = [{"id": "t1", "name": "search", "args": args, "type": "tool_call"}]
        text_block = {"type": "text", "text": "Searching"}
        tool_use = {"type": "tool_use", "id": "t1", "name": "search", "input": args}
        dual = AIMessage(content=[text_block, tool_use], tool_calls=calls)
        calls_only = AIMessage(content=[text_block], tool_calls=calls)
        assert estimate_message_tokens(dual, "anthropic") == estimate_message_tokens(calls_only, "anthropic")

    def test_unmatched_tool_use_block_still_counted(self):
        calls = [{"id": "t1", "name": "search", "args": {"q": "a"}, "type": "tool_call"}]
        orphan = {"type": "tool_use", "id": "t2", "name": "search", "input": {"q": "b" * 2000}}
        with_orphan = AIMessage(content=[orphan], tool_calls=calls)
        without = AIMessage(content=[], tool_calls=calls)
        assert estimate_message_tokens(with_orphan, "anthropic") == estimate_message_tokens(
            without, "anthropic"
        ) + estimate_tokens(orphan, kind=CONTEXT_BUDGET_KIND_JSON, provider="anthropic")

    def test_tool_message_expands_per_result(self):
        results = [
            {"tool_call_id": "t1", "name": "a", "result": '{"x": 1}'},
            {"tool_call_id": "t2", "name": "b", "error": "boom"},
        ]
        total = estimate_message_tokens({"role": "tool", "tool_results": results}, "anthropic")
        single = estimate_message_tokens({"role": "tool", "tool_results": results[:1]}, "anthropic")
        assert total > single >= OVERHEAD + 1
        assert total >= 2 * OVERHEAD

    def test_langchain_messages(self):
        msgs = [HumanMessage(content="hi"), AIMessage(content=[{"type": "text", "text": "hello"}])]
        assert estimate_messages_tokens(msgs, "anthropic") == (
            2 * OVERHEAD + estimate_tokens("hi", None, "anthropic") + estimate_tokens("hello", None, "anthropic")
        )

    def test_messages_none_is_zero(self):
        assert estimate_messages_tokens(None) == 0

    def test_tool_schema_tokens(self):
        @tool
        def lookup_job(job_id: str) -> str:
            """Look up an ISS job by id."""
            return job_id

        assert estimate_tool_schema_tokens([lookup_job], "anthropic") > 0
        assert estimate_tool_schema_tokens(None) == 0


# ── Estimate details ────────────────────────────────────


class TestEstimateDetails:
    @pytest.mark.parametrize("text", ["", "plain prose " * 50, '{"a": [1, 2, 3]}' * 40, "2026-10-29 INFO 12 34\n" * 80])
    def test_estimate_text_matches_estimate_tokens_and_reports_its_inputs(self, text):
        estimate = estimate_text(text, provider="anthropic")

        assert estimate.tokens == estimate_tokens(text, provider="anthropic")
        assert estimate.chars == len(text)
        assert estimate.kind == (classify_content(text) if text else CONTEXT_BUDGET_KIND_PROSE)
        assert estimate.chars_per_token == chars_per_token(estimate.kind, "anthropic")

    def test_estimate_text_honours_an_explicit_kind(self):
        estimate = estimate_text("word " * 100, kind=CONTEXT_BUDGET_KIND_JSON, provider="qwen")

        assert estimate.kind == CONTEXT_BUDGET_KIND_JSON
        assert estimate.chars_per_token == chars_per_token(CONTEXT_BUDGET_KIND_JSON, "qwen")

    def test_tool_message_has_one_part_per_result_labelled_by_tool_name(self):
        message = {
            "role": "tool",
            "content": "[]",
            "tool_results": [
                {"tool_call_id": "c1", "name": "get_job", "result": {"job": "x" * 5000}},
                {"tool_call_id": "c2", "name": "boom", "error": "failed"},
                "not a dict",
                {"tool_call_id": "c3", "result": "unnamed"},
            ],
        }

        parts = estimate_message_parts(message, "anthropic")

        assert [part.label for part in parts] == ["get_job", "boom", "result", "result"]
        assert sum(part.tokens for part in parts) == estimate_message_tokens(message, "anthropic")
        # The characters and kind describe the text the model receives, as in estimate_message_tokens.
        assert parts[0].chars == len(str(message["tool_results"][0]["result"]))
        assert parts[0].kind == CONTEXT_BUDGET_KIND_JSON
        assert parts[1].chars == len("failed")
        assert parts[0].tokens > parts[1].tokens

    def test_tool_message_without_results_has_no_parts(self):
        assert estimate_message_parts({"role": "tool", "content": "x"}) == []
        assert estimate_message_parts({"role": "tool", "tool_results": []}) == []
        assert estimate_message_tokens({"role": "tool", "content": "x"}) == 0

    @pytest.mark.parametrize(
        "text", ["plain prose " * 400, '{"a": [1, 2, 3]}' * 400, "stat name,core 0,core 1\n" * 400]
    )
    def test_user_message_is_one_content_part_describing_its_text(self, text):
        parts = estimate_message_parts({"role": "user", "content": text}, "anthropic")
        text_estimate = estimate_text(text, provider="anthropic")

        assert len(parts) == 1
        [part] = parts
        assert part.label == "content"
        assert (part.chars, part.kind, part.chars_per_token) == (
            len(text),
            text_estimate.kind,
            text_estimate.chars_per_token,
        )
        # The framing of the message is charged to its first (here only) part.
        assert part.tokens == OVERHEAD + text_estimate.tokens
        assert estimate_message_tokens({"role": "user", "content": text}, "anthropic") == part.tokens

    def test_assistant_message_parts_are_its_content_and_its_tool_calls(self):
        tool_calls = [{"id": "t1", "name": "get_jobs", "args": {"limit": 5}}]
        message = {"role": "assistant", "content": "Let me look. " * 20, "tool_calls": tool_calls}

        content, calls = estimate_message_parts(message, "anthropic")

        calls_estimate = estimate_text(tool_calls, kind=CONTEXT_BUDGET_KIND_JSON, provider="anthropic")
        assert (content.label, calls.label) == ("content", "tool_calls")
        assert content.tokens == OVERHEAD + estimate_text(message["content"], provider="anthropic").tokens
        assert (calls.chars, calls.kind, calls.tokens) == (
            calls_estimate.chars,
            CONTEXT_BUDGET_KIND_JSON,
            calls_estimate.tokens,
        )
        assert content.tokens + calls.tokens == estimate_message_tokens(message, "anthropic")

    def test_framing_is_charged_to_the_first_part_even_when_the_content_is_empty(self):
        tool_calls = [{"id": "t1", "name": "get_jobs", "args": {}}]

        [calls] = estimate_message_parts({"role": "assistant", "content": "", "tool_calls": tool_calls}, "anthropic")

        assert calls.label == "tool_calls"
        assert (
            calls.tokens
            == OVERHEAD + estimate_text(tool_calls, kind=CONTEXT_BUDGET_KIND_JSON, provider="anthropic").tokens
        )

    def test_block_content_has_a_part_per_block_and_skips_tool_use_blocks_of_the_tool_calls(self):
        tool_calls = [{"id": "t1", "name": "lookup", "args": {"q": "x"}}]
        message = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "plain " * 50},
                {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": "x"}},
                {"type": "image", "source": {"data": "abc"}},
            ],
            "tool_calls": tool_calls,
        }

        parts = estimate_message_parts(message, "anthropic")

        # The tool_use block is charged once, through tool_calls, as Bedrock merges them by id.
        assert [part.label for part in parts] == ["content[0]", "content[2]", "tool_calls"]
        assert parts[0].kind == classify_content("plain " * 50)
        assert parts[1].kind == CONTEXT_BUDGET_KIND_JSON
        assert sum(part.tokens for part in parts) == estimate_message_tokens(message, "anthropic")

    @pytest.mark.parametrize(
        "message", [{"role": "user"}, {"role": "user", "content": None}, {"role": "user", "content": ""}]
    )
    def test_empty_message_is_a_single_empty_part_that_only_costs_its_framing(self, message):
        [part] = estimate_message_parts(message, "anthropic")

        assert (part.label, part.chars, part.tokens) == ("content", 0, OVERHEAD)

    def test_langchain_messages_have_the_same_parts_as_their_dicts(self):
        text = "hello world " * 30

        human = estimate_message_parts(HumanMessage(content=text), "anthropic")

        assert human == estimate_message_parts({"role": "user", "content": text}, "anthropic")
        call = {"id": "t1", "name": "f", "args": {}}
        ai = estimate_message_parts(AIMessage(content=text, tool_calls=[call]), "anthropic")
        assert [part.label for part in ai] == ["content", "tool_calls"]


# ── ContextBudget ───────────────────────────────────────


class TestContextBudget:
    def test_components_and_remaining(self):
        budget = ContextBudget.for_model(QWEN, max_tokens=1000, messages=[{"role": "user", "content": "hi"}])
        assert budget.max_input_tokens == 32_768
        assert budget.max_input_tokens_known is True
        assert budget.reserved_output_tokens == 1000
        assert budget.safety_margin_tokens == math.ceil(32_768 * 0.10)
        assert budget.message_tokens > 0
        assert budget.remaining_tokens == (
            32_768 - 1000 - budget.tool_schema_tokens - budget.message_tokens - budget.safety_margin_tokens
        )

    def test_smaller_cap_gives_smaller_budget(self):
        big = ContextBudget.for_model(CLAUDE, max_tokens=4096)
        small = ContextBudget.for_model(QWEN, max_tokens=4096)
        assert small.max_input_tokens < big.max_input_tokens
        assert small.remaining_tokens < big.remaining_tokens

    def test_reserved_output_clamped_to_model_cap(self):
        budget = ContextBudget.for_model(QWEN, max_tokens=10_000_000)
        assert budget.reserved_output_tokens == 16_384

    def test_reserved_output_defaults_to_model_cap(self):
        assert ContextBudget.for_model(QWEN, max_tokens=None).reserved_output_tokens == 16_384

    @pytest.mark.parametrize("fraction, expected", [(0.0, 0), (0.25, 8192), (-1, 0), (2.0, 32_768)])
    def test_safety_margin(self, fraction, expected):
        assert ContextBudget.for_model(QWEN, max_tokens=1, safety_fraction=fraction).safety_margin_tokens == expected

    def test_tool_schemas_reserved_only_when_tool_calling(self):
        tools = [{"name": "t", "description": "d" * 400, "parameters": {"type": "object", "properties": {}}}]
        assert ContextBudget.for_model(CLAUDE, tools=tools).tool_schema_tokens > 0
        with patch.object(cb, "supports_tool_calling", return_value=False):
            assert ContextBudget.for_model(CLAUDE, tools=tools).tool_schema_tokens == 0

    def test_unknown_model_uses_conservative_cap_and_warns_once(self, caplog):
        with caplog.at_level(logging.WARNING, logger="autolangchat.context_budget"):
            first = ContextBudget.for_model("acme.mystery-model-v1", max_tokens=100)
            ContextBudget.for_model("acme.mystery-model-v1", max_tokens=100)
        assert first.max_input_tokens == DEFAULT_MAX_INPUT_TOKENS_UNKNOWN
        assert first.max_input_tokens_known is False
        assert first.provider == "acme"
        warnings = [r for r in caplog.records if "mystery-model" in r.getMessage()]
        assert len(warnings) == 1

    def test_profile_failure_falls_back(self):
        with patch.object(cb, "get_model_profile", return_value={}):
            budget = ContextBudget.for_model(CLAUDE, max_tokens=100)
        assert budget.max_input_tokens == DEFAULT_MAX_INPUT_TOKENS_UNKNOWN

    def test_remaining_never_negative(self):
        huge = [{"role": "user", "content": "word " * 200_000}]
        assert ContextBudget.for_model(QWEN, messages=huge).remaining_tokens == 0

    def test_for_turn_uses_runtime_override(self, config):
        turn = config.model_copy(update={"model_id": QWEN, "max_tokens": 2000})
        budget = ContextBudget.for_turn(turn, messages=[{"role": "user", "content": "hi"}])
        assert budget.model_id == QWEN
        assert budget.provider == "qwen"
        assert budget.max_input_tokens == 32_768
        assert budget.reserved_output_tokens == 2000
        assert budget.safety_margin_tokens == math.ceil(32_768 * config.context_budget_safety_fraction)

    def test_for_turn_uses_configured_safety_fraction(self, config):
        turn = config.model_copy(update={"model_id": QWEN, "context_budget_safety_fraction": 0.5})
        assert ContextBudget.for_turn(turn).safety_margin_tokens == 16_384

    def test_for_model_with_fallback_model(self, config):
        turn = config.model_copy(update={"model_id": CLAUDE, "fallback_model": QWEN})
        primary = ContextBudget.for_turn(turn)
        fallback = ContextBudget.for_model(turn.fallback_model, max_tokens=turn.max_tokens)
        assert fallback.model_id == QWEN
        assert fallback.remaining_tokens < primary.remaining_tokens

    def test_fits_boundary(self):
        budget = ContextBudget.for_model(QWEN, max_tokens=1000)
        ratio = chars_per_token(CONTEXT_BUDGET_KIND_PROSE, "qwen")
        max_chars = int((budget.remaining_tokens - OVERHEAD) * ratio)
        assert budget.fits("a" * max_chars, kind=CONTEXT_BUDGET_KIND_PROSE)
        assert not budget.fits("a" * (max_chars + int(ratio) + 1), kind=CONTEXT_BUDGET_KIND_PROSE)

    @pytest.mark.parametrize("kind", CONTEXT_BUDGET_KINDS)
    def test_allowance_for_fits_exactly(self, kind):
        budget = ContextBudget.for_model(QWEN, max_tokens=1000)
        allowance = budget.allowance_for(kind)
        assert allowance > 0
        assert budget.fits("a" * allowance, kind=kind)
        assert not budget.fits("a" * (allowance + math.ceil(chars_per_token(kind, "qwen")) + 1), kind=kind)

    def test_allowance_denser_kind_is_smaller(self):
        budget = ContextBudget.for_model(QWEN, max_tokens=1000)
        assert budget.allowance_for(CONTEXT_BUDGET_KIND_LOG_TABULAR) < budget.allowance_for(CONTEXT_BUDGET_KIND_PROSE)

    def test_allowance_zero_when_full(self):
        huge = [{"role": "user", "content": "word " * 200_000}]
        assert ContextBudget.for_model(QWEN, messages=huge).allowance_for(CONTEXT_BUDGET_KIND_PROSE) == 0

    def test_allowance_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            ContextBudget.for_model(QWEN).allowance_for("binary")

    def test_as_dict(self):
        data = ContextBudget.for_model(QWEN, max_tokens=1000).as_dict()
        assert data["model_id"] == QWEN
        assert data["remaining_tokens"] == ContextBudget.for_model(QWEN, max_tokens=1000).remaining_tokens

    def test_budget_is_immutable(self):
        budget = ContextBudget.for_model(QWEN)
        with pytest.raises(AttributeError):
            budget.message_tokens = 0  # type: ignore[misc]
