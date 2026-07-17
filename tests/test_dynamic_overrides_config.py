"""XMGPLAT-9697 Phase 1 — dynamic parameter override config & validation tests.

Covers the new `ChatConfig` fields (`enable_dynamic_overrides`,
`allowed_dynamic_overrides`, `enable_config_sidebar`) and
`ChatConfig.validate_overrides()`.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError


def _load_config(**env_overrides):
    """Import-safe helper: load ChatConfig with a clean env patch."""
    base_env = {
        "AUTOCHAT_MODEL_ID": "us.anthropic.claude-sonnet-5",
    }
    base_env.update(env_overrides)
    with patch.dict(os.environ, base_env, clear=True):
        from importlib import reload

        import autolangchat.config as cfg_mod

        reload(cfg_mod)
        return cfg_mod.ChatConfig()


class TestDynamicOverrideConfigFields:
    """New ChatConfig fields default and read correctly from env vars."""

    def test_enable_dynamic_overrides_defaults_to_false(self):
        config = _load_config()
        assert config.enable_dynamic_overrides is False

    def test_enable_dynamic_overrides_from_env_var(self):
        config = _load_config(AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES="true")
        assert config.enable_dynamic_overrides is True

    def test_allowed_dynamic_overrides_defaults_to_none(self):
        config = _load_config()
        assert config.allowed_dynamic_overrides is None

    def test_allowed_dynamic_overrides_from_env_var_comma_separated(self):
        config = _load_config(AUTOCHAT_ALLOWED_DYNAMIC_OVERRIDES="temperature,max_tokens")
        assert config.allowed_dynamic_overrides == ["temperature", "max_tokens"]

    def test_enable_config_sidebar_defaults_to_false(self):
        config = _load_config()
        assert config.enable_config_sidebar is False

    def test_enable_config_sidebar_from_env_var(self):
        config = _load_config(AUTOCHAT_ENABLE_CONFIG_SIDEBAR="true")
        assert config.enable_config_sidebar is True

    def test_available_models_defaults_to_builtin_list(self):
        config = _load_config()
        assert config.available_models is None
        assert isinstance(config.get_available_models(), list)
        assert len(config.get_available_models()) > 0

    def test_available_models_from_env_var_comma_separated(self):
        config = _load_config(AUTOCHAT_AVAILABLE_MODELS="us.anthropic.claude-sonnet-5,us.anthropic.claude-opus-4-8")
        assert config.available_models == ["us.anthropic.claude-sonnet-5", "us.anthropic.claude-opus-4-8"]
        assert config.get_available_models() == ["us.anthropic.claude-sonnet-5", "us.anthropic.claude-opus-4-8"]


class TestModelProfileRestriction:
    """model_id, fallback_model, and available_models must be known
    langchain-aws model profiles (see langchain_aws.data._profiles._PROFILES) --
    the server refuses to start (ChatConfig construction raises) otherwise."""

    def test_unknown_model_id_rejected_at_construction(self):
        with pytest.raises(ValidationError, match="not a recognized Bedrock model profile"):
            _load_config(AUTOCHAT_MODEL_ID="not-a-real-model")

    def test_known_model_id_accepted(self):
        config = _load_config(AUTOCHAT_MODEL_ID="us.anthropic.claude-opus-4-8")
        assert config.model_id == "us.anthropic.claude-opus-4-8"

    def test_unknown_fallback_model_rejected_at_construction(self):
        with pytest.raises(ValidationError, match="not a recognized Bedrock model profile"):
            _load_config(AUTOCHAT_FALLBACK_MODEL="not-a-real-model")

    def test_unknown_available_models_entry_rejected_at_construction(self):
        with pytest.raises(ValidationError, match="unrecognized model id"):
            _load_config(AUTOCHAT_AVAILABLE_MODELS="us.anthropic.claude-sonnet-5,not-a-real-model")

    def test_get_available_models_for_ui_includes_display_names(self):
        config = _load_config(AUTOCHAT_AVAILABLE_MODELS="us.anthropic.claude-sonnet-5,us.anthropic.claude-opus-4-8")
        ui_models = config.get_available_models_for_ui()

        assert {
            "id": "us.anthropic.claude-sonnet-5",
            "name": "Claude Sonnet 5 (US)",
            "supports_temperature": False,
            "max_output_tokens": 128000,
        } in ui_models
        assert {
            "id": "us.anthropic.claude-opus-4-8",
            "name": "Claude Opus 4.8 (US)",
            "supports_temperature": False,
            "max_output_tokens": 128000,
        } in ui_models

    def test_get_available_models_for_ui_supports_temperature_true_for_gpt_oss(self):
        """openai.gpt-oss-120b-1:0 supports temperature, unlike the newer Claude
        models above -- confirms supports_temperature actually varies per model
        rather than being a constant."""
        config = _load_config(AUTOCHAT_AVAILABLE_MODELS="openai.gpt-oss-120b-1:0")
        ui_models = config.get_available_models_for_ui()

        assert {
            "id": "openai.gpt-oss-120b-1:0",
            "name": "gpt-oss-120b",
            "supports_temperature": True,
            "max_output_tokens": 16384,
        } in ui_models

    def test_get_available_models_for_ui_max_output_tokens_varies_per_model(self):
        """us.anthropic.claude-sonnet-4-6 caps output at 64000 tokens, unlike
        claude-sonnet-5's 128000 -- confirms max_output_tokens actually varies
        per model rather than being a constant ceiling."""
        config = _load_config(AUTOCHAT_AVAILABLE_MODELS="us.anthropic.claude-sonnet-5,us.anthropic.claude-sonnet-4-6")
        ui_models = {m["id"]: m for m in config.get_available_models_for_ui()}

        assert ui_models["us.anthropic.claude-sonnet-5"]["max_output_tokens"] == 128000
        assert ui_models["us.anthropic.claude-sonnet-4-6"]["max_output_tokens"] == 64000

    def test_get_available_models_for_ui_always_includes_current_model_id(self):
        """Even if the configured model_id is somehow left out of available_models,
        the dropdown must still include it (with its display name)."""
        config = _load_config(
            AUTOCHAT_MODEL_ID="us.anthropic.claude-sonnet-5",
            AUTOCHAT_AVAILABLE_MODELS="us.anthropic.claude-opus-4-8",
        )
        ui_models = config.get_available_models_for_ui()

        ids = [m["id"] for m in ui_models]
        assert "us.anthropic.claude-sonnet-5" in ids

    def test_get_model_display_name_defaults_to_current_model_id(self):
        config = _load_config(AUTOCHAT_MODEL_ID="us.anthropic.claude-sonnet-5")
        assert config.get_model_display_name() == "Claude Sonnet 5 (US)"

    def test_get_model_display_name_accepts_explicit_model_id(self):
        config = _load_config(AUTOCHAT_MODEL_ID="us.anthropic.claude-sonnet-5")
        assert config.get_model_display_name("us.anthropic.claude-opus-4-8") == "Claude Opus 4.8 (US)"


class TestValidateOverridesFeatureGate:
    """The enable_dynamic_overrides master switch gates everything."""

    def test_all_overrides_rejected_when_feature_disabled(self):
        config = _load_config()
        valid, reasons = config.validate_overrides({"temperature": 0.5})
        assert valid == {}
        assert len(reasons) == 1
        assert "disabled" in reasons[0]

    def test_empty_overrides_no_op_even_when_disabled(self):
        config = _load_config()
        valid, reasons = config.validate_overrides({})
        assert valid == {}
        assert reasons == []


class TestValidateOverridesAllowlist:
    """allowed_dynamic_overrides filters which params can be set."""

    def test_all_overridable_params_allowed_when_allowlist_is_none(self):
        config = _load_config(AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES="true")
        valid, reasons = config.validate_overrides({"temperature": 0.3, "enable_rag": True})
        assert valid == {"temperature": 0.3, "enable_rag": True}
        assert reasons == []

    def test_param_not_in_allowlist_is_rejected(self):
        config = _load_config(
            AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES="true",
            AUTOCHAT_ALLOWED_DYNAMIC_OVERRIDES="temperature",
        )
        valid, reasons = config.validate_overrides({"temperature": 0.3, "max_tokens": 100})
        assert valid == {"temperature": 0.3}
        assert len(reasons) == 1
        assert "max_tokens" in reasons[0]

    def test_unknown_param_is_rejected(self):
        config = _load_config(AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES="true")
        valid, reasons = config.validate_overrides({"not_a_real_param": 1})
        assert valid == {}
        assert "not_a_real_param" in reasons[0]

    def test_max_tool_calls_is_not_overridable(self):
        """max_tool_calls is deliberately excluded from the overridable set (see plan Notes)."""
        config = _load_config(AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES="true")
        valid, reasons = config.validate_overrides({"max_tool_calls": 3})
        assert valid == {}
        assert "max_tool_calls" in reasons[0]

    def test_preserve_system_message_is_not_overridable(self):
        """preserve_system_message is deliberately excluded from the overridable set (see plan Notes)."""
        config = _load_config(AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES="true")
        valid, reasons = config.validate_overrides({"preserve_system_message": False})
        assert valid == {}
        assert "preserve_system_message" in reasons[0]


class TestValidateOverridesValueValidation:
    """Type/range validation for each overridable parameter."""

    def _enabled_config(self):
        return _load_config(AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES="true")

    def test_temperature_out_of_range_rejected(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"temperature": 1.5})
        assert valid == {}
        assert "temperature" in reasons[0]

    def test_temperature_wrong_type_rejected(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"temperature": "hot"})
        assert valid == {}
        assert "temperature" in reasons[0]

    def test_temperature_valid_value_accepted(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"temperature": 0.9})
        assert valid == {"temperature": 0.9}
        assert reasons == []

    def test_max_tokens_must_be_positive(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"max_tokens": 0})
        assert valid == {}
        assert "max_tokens" in reasons[0]

    def test_max_tokens_must_be_int_not_float(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"max_tokens": 100.5})
        assert valid == {}
        assert "max_tokens" in reasons[0]

    def test_top_p_out_of_range_rejected(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"top_p": -0.1})
        assert valid == {}
        assert "top_p" in reasons[0]

    def test_model_id_must_be_non_empty_string(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"model_id": "   "})
        assert valid == {}
        assert "model_id" in reasons[0]

    def test_model_id_valid_value_accepted(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0"})
        assert valid == {"model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0"}

    def test_model_id_override_rejected_when_not_a_known_profile(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"model_id": "not-a-real-model"})
        assert valid == {}
        assert "not a recognized Bedrock model profile" in reasons[0]

    def test_enable_ai_summarization_must_be_bool(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"enable_ai_summarization": "yes"})
        assert valid == {}
        assert "enable_ai_summarization" in reasons[0]

    def test_enable_rag_valid_bool_accepted(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"enable_rag": True})
        assert valid == {"enable_rag": True}
        assert reasons == []

    def test_kb_top_k_results_must_be_positive_int(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"kb_top_k_results": -1})
        assert valid == {}
        assert "kb_top_k_results" in reasons[0]

    def test_kb_similarity_threshold_out_of_range_rejected(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"kb_similarity_threshold": 2.0})
        assert valid == {}
        assert "kb_similarity_threshold" in reasons[0]

    def test_kb_similarity_threshold_valid_value_accepted(self):
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"kb_similarity_threshold": 0.4})
        assert valid == {"kb_similarity_threshold": 0.4}
        assert reasons == []

    def test_partial_batch_valid_and_invalid_mixed(self):
        """A batch with both valid and invalid overrides returns the valid subset
        plus rejection reasons for the invalid ones -- it doesn't abort entirely."""
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"temperature": 0.2, "max_tokens": -5, "enable_rag": True})
        assert valid == {"temperature": 0.2, "enable_rag": True}
        assert len(reasons) == 1
        assert "max_tokens" in reasons[0]

    def test_booleans_rejected_for_numeric_params(self):
        """bool is a subclass of int in Python -- make sure True/False aren't
        silently accepted for numeric params like max_tokens."""
        config = self._enabled_config()
        valid, reasons = config.validate_overrides({"max_tokens": True})
        assert valid == {}
        assert "max_tokens" in reasons[0]
