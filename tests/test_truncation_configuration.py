"""Tests for configurable tool result truncation thresholds"""

from auto_bedrock_chat_fastapi.config import ChatConfig


class TestTruncationConfiguration:
    """Test configuration of truncation thresholds"""

    def test_default_configuration_values(self):
        """Verify default configuration values match specifications"""
        config = ChatConfig()

        # Verify new response thresholds (conservative defaults: 500K → 425K)
        assert config.single_msg_length_threshold == 500_000
        assert config.single_msg_truncation_target == 425_000

        # Verify history thresholds (defaults: 100K → 85K)
        assert config.history_msg_length_threshold == 100_000
        assert config.history_msg_truncation_target == 85_000

    def test_configuration_maintains_tier_ratio(self):
        """Test that default configuration maintains proper tier ratios"""
        config = ChatConfig()

        # Verify recommended 5x ratio between new response and history thresholds
        # (not enforced, but recommended for balanced truncation behavior)
        ratio = config.single_msg_length_threshold / config.history_msg_length_threshold
        assert ratio == 5  # Recommended but not required

        # Both tiers MUST maintain 85% target/threshold ratio for consistent truncation behavior
        new_response_ratio = config.single_msg_truncation_target / config.single_msg_length_threshold
        history_ratio = config.history_msg_truncation_target / config.history_msg_length_threshold

        assert new_response_ratio == 0.85
        assert history_ratio == 0.85

    def test_configuration_fields_are_positive(self):
        """Verify all truncation threshold values are positive integers"""
        config = ChatConfig()

        assert config.single_msg_length_threshold > 0
        assert config.single_msg_truncation_target > 0
        assert config.history_msg_length_threshold > 0
        assert config.history_msg_truncation_target > 0

    def test_configuration_targets_less_than_thresholds(self):
        """Verify target sizes are less than their corresponding thresholds"""
        config = ChatConfig()

        assert config.single_msg_truncation_target < config.single_msg_length_threshold
        assert config.history_msg_truncation_target < config.history_msg_length_threshold

    def test_configuration_via_environment_variables(self, monkeypatch):
        """Test that environment variables can configure truncation thresholds"""
        # Set custom environment variables
        monkeypatch.setenv("BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD", "1500000")
        monkeypatch.setenv("BEDROCK_SINGLE_MSG_TRUNCATION_TARGET", "1275000")
        monkeypatch.setenv("BEDROCK_HISTORY_MSG_LENGTH_THRESHOLD", "150000")
        monkeypatch.setenv("BEDROCK_HISTORY_MSG_TRUNCATION_TARGET", "127500")

        # Create config - pydantic-settings will read from environment
        config = ChatConfig()

        # Verify the environment variables were applied
        assert config.single_msg_length_threshold == 1_500_000
        assert config.single_msg_truncation_target == 1_275_000
        assert config.history_msg_length_threshold == 150_000
        assert config.history_msg_truncation_target == 127_500
