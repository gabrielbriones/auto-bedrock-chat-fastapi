"""XMGPLAT-11193 Part 2 — per-model Bedrock capability handling.

Covers :mod:`autolangchat.model_capabilities` (profile lookup, capability
flags, ``max_tokens`` clamping and the shared kwarg builder) plus
``_build_llm()``, the main construction site that routes through it.

Before this, every ``ChatBedrockConverse`` was built with Claude-Sonnet-5
semantics baked in — ``temperature`` was always sent and ``max_tokens`` was
passed through unclamped — which made any model with a different capability
set fail the turn with a Bedrock ``ValidationException``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from autolangchat.config import ChatConfig
from autolangchat.model_capabilities import (
    build_bedrock_kwargs,
    clamp_max_tokens,
    get_max_output_tokens,
    get_model_profile,
    supports_temperature,
    supports_tool_calling,
)

# Real ids from the langchain-aws catalog, chosen to cover the three
# acceptance-criteria models. Sonnet 5 / Opus 4.8 report temperature=False;
# Llama 3.3 reports temperature=True with a much smaller output cap.
SONNET_5 = "us.anthropic.claude-sonnet-5"
OPUS_4_8 = "us.anthropic.claude-opus-4-8"
LLAMA_3_3 = "meta.llama3-3-70b-instruct-v1:0"

FAKE_PROFILES = {
    "vendor.no-temp": {"name": "No Temp", "temperature": False, "max_output_tokens": 1000},
    "vendor.with-temp": {"name": "With Temp", "temperature": True, "max_output_tokens": 1000},
    "us.vendor.prefixed-only": {"name": "Prefixed Only", "temperature": True, "max_output_tokens": 512},
    "vendor.bare-only": {"name": "Bare Only", "temperature": True, "max_output_tokens": 256},
}


def _config(**overrides) -> ChatConfig:
    return ChatConfig().model_copy(update=overrides)


class TestGetModelProfile:
    def test_known_model_returns_profile(self):
        assert get_model_profile(SONNET_5)["name"]

    def test_unknown_model_returns_empty_dict(self):
        assert get_model_profile("nope.not-a-model") == {}

    def test_prefixed_id_falls_back_to_bare_profile(self):
        # "us.meta.llama3-3-70b-instruct-v1:0" is what the inference-profile
        # retry in llm_call_node builds, and it has no _PROFILES entry of its
        # own — capabilities must still resolve via the bare id.
        assert get_model_profile(f"us.{LLAMA_3_3}") == get_model_profile(LLAMA_3_3)

    def test_bare_id_falls_back_to_prefixed_profile(self):
        with patch("autolangchat.model_capabilities._PROFILES", FAKE_PROFILES):
            assert get_model_profile("vendor.prefixed-only")["name"] == "Prefixed Only"

    def test_capability_overrides_are_merged_on_top(self):
        with (
            patch("autolangchat.model_capabilities._PROFILES", FAKE_PROFILES),
            patch.dict(
                "autolangchat.model_capabilities.CAPABILITY_OVERRIDES",
                {"vendor.no-temp": {"temperature": True}},
                clear=True,
            ),
        ):
            profile = get_model_profile("vendor.no-temp")
            assert profile["temperature"] is True
            assert profile["name"] == "No Temp"  # untouched fields survive

    def test_override_applies_through_region_prefix_fallback(self):
        # An override keyed by the bare model id must still apply when
        # looked up via a region-prefixed id that has no _PROFILES entry of
        # its own (e.g. "us.meta.llama3-3-70b-instruct-v1:0", which is what
        # llm_call_node retries with) -- and vice versa.
        with patch.dict(
            "autolangchat.model_capabilities.CAPABILITY_OVERRIDES",
            {LLAMA_3_3: {"tool_calling": False}},
            clear=True,
        ):
            assert get_model_profile(f"us.{LLAMA_3_3}")["tool_calling"] is False


class TestCapabilityFlags:
    def test_sonnet_5_and_opus_do_not_support_temperature(self):
        assert supports_temperature(SONNET_5) is False
        assert supports_temperature(OPUS_4_8) is False

    def test_llama_supports_temperature(self):
        assert supports_temperature(LLAMA_3_3) is True

    def test_unknown_model_defaults_to_supporting_temperature(self):
        assert supports_temperature("nope.not-a-model") is True

    def test_tool_calling_flag(self):
        assert supports_tool_calling(SONNET_5) is True
        with patch("autolangchat.model_capabilities._PROFILES", {"v.m": {"tool_calling": False}}):
            assert supports_tool_calling("v.m") is False

    def test_llama_supports_tool_calling(self):
        # Reverted the earlier CAPABILITY_OVERRIDES ban (XMGPLAT-11193) --
        # see _looks_like_unexecuted_tool_call in llm_call.py for the actual
        # fix for the leaked-tool-call-as-text failure mode.
        assert supports_tool_calling(LLAMA_3_3) is True

    def test_unknown_model_defaults_to_supporting_tool_calling(self):
        assert supports_tool_calling("nope.not-a-model") is True

    def test_max_output_tokens(self):
        assert get_max_output_tokens(LLAMA_3_3) == 4096
        assert get_max_output_tokens("nope.not-a-model") is None


class TestClampMaxTokens:
    def test_clamps_above_cap(self):
        assert clamp_max_tokens(LLAMA_3_3, 100_000) == 4096

    def test_leaves_value_below_cap_untouched(self):
        assert clamp_max_tokens(LLAMA_3_3, 512) == 512

    def test_unknown_model_is_not_clamped(self):
        assert clamp_max_tokens("nope.not-a-model", 99_999) == 99_999

    def test_none_passes_through(self):
        assert clamp_max_tokens(LLAMA_3_3, None) is None


class TestBuildBedrockKwargs:
    def test_omits_sampling_params_for_models_that_reject_them(self):
        kwargs = build_bedrock_kwargs(SONNET_5, _config(temperature=0.7, top_p=0.9))
        assert "temperature" not in kwargs
        assert "top_p" not in kwargs

    def test_passes_temperature_for_models_that_support_it(self):
        kwargs = build_bedrock_kwargs(LLAMA_3_3, _config(temperature=0.3, top_p=0.9))
        assert kwargs["temperature"] == 0.3
        # Bedrock Converse rejects requests carrying both.
        assert "top_p" not in kwargs

    def test_top_p_used_when_temperature_is_explicitly_omitted(self):
        kwargs = build_bedrock_kwargs(LLAMA_3_3, _config(top_p=0.5), temperature=None)
        assert "temperature" not in kwargs
        assert kwargs["top_p"] == 0.5

    def test_max_tokens_clamped_to_model_cap(self):
        kwargs = build_bedrock_kwargs(LLAMA_3_3, _config(max_tokens=100_000))
        assert kwargs["max_tokens"] == 4096

    def test_explicit_max_tokens_overrides_config(self):
        kwargs = build_bedrock_kwargs(LLAMA_3_3, _config(max_tokens=4096), max_tokens=60)
        assert kwargs["max_tokens"] == 60

    def test_max_tokens_none_omits_the_key(self):
        kwargs = build_bedrock_kwargs(LLAMA_3_3, _config(), max_tokens=None)
        assert "max_tokens" not in kwargs

    def test_region_comes_from_config_and_can_be_overridden(self):
        config = _config(aws_region="eu-central-1")
        assert build_bedrock_kwargs(LLAMA_3_3, config)["region_name"] == "eu-central-1"
        assert build_bedrock_kwargs(LLAMA_3_3, config, region_name="us-east-1")["region_name"] == "us-east-1"

    def test_works_without_a_config(self):
        kwargs = build_bedrock_kwargs(LLAMA_3_3)
        assert kwargs == {"model": LLAMA_3_3, "region_name": "us-east-1"}

    def test_explicit_credentials_included_when_both_set(self):
        config = _config(aws_access_key_id="AKIA", aws_secret_access_key="secret")
        kwargs = build_bedrock_kwargs(LLAMA_3_3, config)
        assert kwargs["aws_access_key_id"] == "AKIA"
        assert kwargs["aws_secret_access_key"] == "secret"

    def test_partial_credentials_are_ignored(self):
        kwargs = build_bedrock_kwargs(LLAMA_3_3, _config(aws_access_key_id="AKIA"))
        assert "aws_access_key_id" not in kwargs

    def test_credentials_can_be_suppressed(self):
        config = _config(aws_access_key_id="AKIA", aws_secret_access_key="secret")
        kwargs = build_bedrock_kwargs(LLAMA_3_3, config, include_credentials=False)
        assert "aws_access_key_id" not in kwargs

    def test_read_timeout_is_floored_at_the_default(self):
        kwargs = build_bedrock_kwargs(LLAMA_3_3, _config(), read_timeout=30)
        assert kwargs["config"].read_timeout == 300

    def test_read_timeout_above_the_floor_is_honoured(self):
        kwargs = build_bedrock_kwargs(LLAMA_3_3, _config(), read_timeout=900)
        assert kwargs["config"].read_timeout == 900

    def test_no_botocore_config_when_read_timeout_unset(self):
        assert "config" not in build_bedrock_kwargs(LLAMA_3_3, _config())

    def test_every_catalog_model_builds_valid_kwargs(self):
        # Acceptance criterion 7: every model in the sidebar dropdown must be
        # selectable, with unsupported params dropped and max_tokens clamped.
        config = _config(max_tokens=999_999)
        for model_id in config.get_available_models():
            kwargs = build_bedrock_kwargs(model_id, config)
            assert kwargs["model"] == model_id
            cap = get_max_output_tokens(model_id)
            if cap is not None:
                assert kwargs["max_tokens"] <= cap
            if not supports_temperature(model_id):
                assert "temperature" not in kwargs and "top_p" not in kwargs


class TestBuildLLMUsesCapabilities:
    def _build(self, model_id, config):
        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as mock_cls:
            mock_cls.return_value = MagicMock()
            from autolangchat.graph.nodes.llm_call import _build_llm

            _build_llm(model_id, config)
        return mock_cls.call_args.kwargs

    def test_temperature_dropped_for_models_that_reject_it(self):
        kwargs = self._build(SONNET_5, _config(temperature=0.7))
        assert "temperature" not in kwargs
        assert "top_p" not in kwargs

    def test_max_tokens_clamped_against_the_selected_model(self):
        kwargs = self._build(LLAMA_3_3, _config(max_tokens=64_000))
        assert kwargs["max_tokens"] == 4096

    def test_read_timeout_applied(self):
        kwargs = self._build(LLAMA_3_3, _config())
        assert kwargs["config"].read_timeout >= 300

    def test_tools_not_bound_when_model_cannot_call_tools(self):
        config = _config()
        config.langchain_tools = [MagicMock()]
        with (
            patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as mock_cls,
            patch("autolangchat.graph.nodes.llm_call.supports_tool_calling", return_value=False),
        ):
            llm = MagicMock()
            mock_cls.return_value = llm
            from autolangchat.graph.nodes.llm_call import _build_llm

            _build_llm("vendor.no-tools", config)
        llm.bind_tools.assert_not_called()

    def test_tools_bound_when_model_supports_them(self):
        config = _config()
        config.langchain_tools = [MagicMock()]
        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as mock_cls:
            llm = MagicMock()
            mock_cls.return_value = llm
            from autolangchat.graph.nodes.llm_call import _build_llm

            _build_llm(SONNET_5, config)
        llm.bind_tools.assert_called_once()
