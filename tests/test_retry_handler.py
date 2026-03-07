"""Tests for RetryHandler context window error detection."""

import pytest

from auto_bedrock_chat_fastapi.exceptions import BedrockClientError
from auto_bedrock_chat_fastapi.retry_handler import RetryHandler


class TestIsContextWindowError:
    """Verify is_context_window_error detects all known token-limit error patterns."""

    @pytest.fixture
    def handler(self):
        return RetryHandler(max_retries=3, max_conversation_messages=20)

    @pytest.mark.parametrize(
        "error_msg",
        [
            # Claude / Bedrock "prompt is too long" pattern
            "prompt is too long: 202444 tokens > 200000 maximum",
            "Request failed after 4 attempts: An error occurred (ValidationException) "
            "when calling the InvokeModel operation: prompt is too long: 202444 tokens "
            "> 200000 maximum",
            # Titan / older "Input is too long" pattern
            "Input is too long for the model's context window.",
            "Request failed after 4 attempts: Input is too long",
            # max_tokens issues
            "max_tokens must be at least 1",
            "got -128",
            # Request body / HTTP limits
            "length limit exceeded",
            "Failed to buffer the request body",
            # GPT-related
            "Unexpected token at position 0",
            "expecting start token",
            "BadRequestError: maximum context length exceeded",
        ],
    )
    def test_detects_context_window_errors(self, handler, error_msg):
        error = BedrockClientError(error_msg)
        assert handler.is_context_window_error(error) is True

    @pytest.mark.parametrize(
        "error_msg",
        [
            "ThrottlingException: Rate exceeded",
            "AccessDeniedException: Not authorized",
            "InternalServerError: Something went wrong",
            "Connection timeout",
        ],
    )
    def test_ignores_non_context_window_errors(self, handler, error_msg):
        error = BedrockClientError(error_msg)
        assert handler.is_context_window_error(error) is False


class TestDefaultThreshold:
    """Verify history_total_length_threshold default is 650K."""

    def test_default_history_total_length_threshold(self):
        from auto_bedrock_chat_fastapi.defaults import DEFAULT_HISTORY_TOTAL_LENGTH_THRESHOLD

        assert DEFAULT_HISTORY_TOTAL_LENGTH_THRESHOLD == 650_000

    def test_config_uses_updated_default(self):
        from auto_bedrock_chat_fastapi.config import load_config

        config = load_config()
        assert config.history_total_length_threshold == 650_000
