"""Tests for configurable tool result truncation thresholds"""

import pytest

from auto_bedrock_chat_fastapi.config import ChatConfig


class TestTruncationConfiguration:
    """Test configuration of truncation thresholds"""

    def test_default_configuration_values(self):
        """Verify default configuration values match specifications"""
        config = ChatConfig()

        # Verify new response thresholds (actual implementation defaults: 500K → 425K)
        assert config.tool_result_new_response_threshold == 500_000
        assert config.tool_result_new_response_target == 425_000

        # Verify history thresholds (actual implementation defaults: 50K → 42.5K)
        assert config.tool_result_history_threshold == 50_000
        assert config.tool_result_history_target == 42_500

    def test_configuration_maintains_tier_ratio(self):
        """Test that default configuration maintains proper tier ratios"""
        config = ChatConfig()

        # New response threshold should be 10x history threshold
        assert config.tool_result_new_response_threshold / config.tool_result_history_threshold == 10
        
        # Both tiers should maintain 85% target/threshold ratio
        new_response_ratio = config.tool_result_new_response_target / config.tool_result_new_response_threshold
        history_ratio = config.tool_result_history_target / config.tool_result_history_threshold
        
        assert new_response_ratio == 0.85
        assert history_ratio == 0.85

    def test_configuration_fields_are_positive(self):
        """Verify all truncation threshold values are positive integers"""
        config = ChatConfig()
        
        assert config.tool_result_new_response_threshold > 0
        assert config.tool_result_new_response_target > 0
        assert config.tool_result_history_threshold > 0
        assert config.tool_result_history_target > 0

    def test_configuration_targets_less_than_thresholds(self):
        """Verify target sizes are less than their corresponding thresholds"""
        config = ChatConfig()
        
        assert config.tool_result_new_response_target < config.tool_result_new_response_threshold
        assert config.tool_result_history_target < config.tool_result_history_threshold

    def test_configuration_via_environment_variables(self, monkeypatch):
        """Test that environment variables can configure truncation thresholds"""
        # Set custom environment variables
        monkeypatch.setenv('BEDROCK_TOOL_RESULT_NEW_RESPONSE_THRESHOLD', '1500000')
        monkeypatch.setenv('BEDROCK_TOOL_RESULT_NEW_RESPONSE_TARGET', '1275000')
        monkeypatch.setenv('BEDROCK_TOOL_RESULT_HISTORY_THRESHOLD', '150000')
        monkeypatch.setenv('BEDROCK_TOOL_RESULT_HISTORY_TARGET', '127500')
        
        # Create config - pydantic-settings will read from environment
        config = ChatConfig()
        
        # Verify the environment variables were applied
        assert config.tool_result_new_response_threshold == 1_500_000
        assert config.tool_result_new_response_target == 1_275_000
        assert config.tool_result_history_threshold == 150_000
        assert config.tool_result_history_target == 127_500

