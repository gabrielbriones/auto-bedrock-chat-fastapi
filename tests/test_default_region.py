"""Bare model ids (the settings sidebar's "Default" region) resolve to the
configured ``default_region`` inference profile instead of being sent to
Bedrock as-is (XMGPLAT-12046)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from autolangchat.config import ChatConfig, load_config, resolve_model_region
from autolangchat.graph.nodes.llm_call import _retry_model_id_with_inference_profile

BARE_SONNET = "anthropic.claude-sonnet-5"
# A bare id with no inference-profile variant in langchain-aws's _PROFILES.
LLAMA_3_3 = "meta.llama3-3-70b-instruct-v1:0"


def _config(**env) -> ChatConfig:
    with patch.dict(os.environ, {"AUTOCHAT_MODEL_ID": f"us.{BARE_SONNET}", **env}, clear=True):
        return ChatConfig()


class TestResolveModelRegion:
    def test_bare_id_gets_the_default_region_prefix(self):
        assert resolve_model_region(BARE_SONNET, "global") == f"global.{BARE_SONNET}"
        assert resolve_model_region(BARE_SONNET, "us") == f"us.{BARE_SONNET}"

    @pytest.mark.parametrize("region", ["us", "eu", "au", "jp", "global"])
    def test_explicit_region_passes_through_unchanged(self, region):
        assert resolve_model_region(f"{region}.{BARE_SONNET}", "global") == f"{region}.{BARE_SONNET}"

    def test_bare_id_without_a_default_region_profile_is_unchanged(self):
        assert resolve_model_region(LLAMA_3_3, "global") == LLAMA_3_3

    def test_no_default_region_is_a_no_op(self):
        assert resolve_model_region(BARE_SONNET, None) == BARE_SONNET


class TestChatConfigDefaultRegion:
    def test_library_defaults_to_global(self):
        with patch.dict(os.environ, {}, clear=True):
            config = ChatConfig(_env_file=None)
        assert config.default_region == "global"
        assert config.model_id == f"global.{BARE_SONNET}"

    def test_default_region_is_normalized(self):
        assert _config(AUTOCHAT_DEFAULT_REGION=" US ").default_region == "us"

    def test_unknown_default_region_is_rejected(self):
        with pytest.raises(ValidationError, match="default_region"):
            _config(AUTOCHAT_DEFAULT_REGION="us-east-1")

    def test_bare_configured_model_id_resolves_to_default_region(self):
        assert _config(AUTOCHAT_MODEL_ID=BARE_SONNET).model_id == f"global.{BARE_SONNET}"
        assert _config(AUTOCHAT_MODEL_ID=BARE_SONNET, AUTOCHAT_DEFAULT_REGION="us").model_id == f"us.{BARE_SONNET}"

    def test_explicit_configured_region_is_kept(self):
        assert _config(AUTOCHAT_MODEL_ID=f"eu.{BARE_SONNET}").model_id == f"eu.{BARE_SONNET}"

    def test_load_config_resolves_against_the_callers_default_region(self):
        with patch.dict(os.environ, {"AUTOCHAT_FALLBACK_MODEL": BARE_SONNET}, clear=True):
            config = load_config(model_id=BARE_SONNET, default_region="us")
        assert config.default_region == "us"
        assert config.model_id == f"us.{BARE_SONNET}"
        assert config.fallback_model == f"us.{BARE_SONNET}"


class TestOverrideDefaultRegion:
    def test_default_region_override_resolves_without_rejection(self):
        config = _config(AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES="true")
        valid, rejected = config.validate_overrides({"model_id": BARE_SONNET})
        assert rejected == []
        assert valid == {"model_id": f"global.{BARE_SONNET}"}

    def test_default_region_override_honours_host_region(self):
        config = _config(AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES="true", AUTOCHAT_DEFAULT_REGION="us")
        valid, rejected = config.validate_overrides({"model_id": BARE_SONNET})
        assert rejected == []
        assert valid == {"model_id": f"us.{BARE_SONNET}"}

    def test_explicit_region_override_passes_through_unchanged(self):
        config = _config(AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES="true")
        valid, rejected = config.validate_overrides({"model_id": f"eu.{BARE_SONNET}"})
        assert rejected == []
        assert valid == {"model_id": f"eu.{BARE_SONNET}"}

    def test_bare_id_without_default_region_profile_is_left_as_is(self):
        config = _config(AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES="true")
        valid, rejected = config.validate_overrides({"model_id": LLAMA_3_3})
        assert rejected == []
        assert valid == {"model_id": LLAMA_3_3}


class TestInferenceProfileRetryUsesDefaultRegion:
    def test_retry_prefers_the_default_region_profile(self):
        config = _config(AUTOCHAT_DEFAULT_REGION="eu", AWS_REGION="us-west-2")
        assert _retry_model_id_with_inference_profile(BARE_SONNET, config) == f"eu.{BARE_SONNET}"

    def test_retry_falls_back_to_aws_region_without_a_default_region_profile(self):
        config = _config(AWS_REGION="eu-west-1")
        assert _retry_model_id_with_inference_profile(LLAMA_3_3, config) == f"eu.{LLAMA_3_3}"
