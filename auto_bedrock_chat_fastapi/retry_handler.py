"""Retry handler for Bedrock API requests with fallback strategies"""

import asyncio
import json
import logging
import random
import time
from typing import Any, Callable, Dict, List, Optional

from botocore.exceptions import BotoCoreError, ClientError

from .exceptions import BedrockClientError

logger = logging.getLogger(__name__)


class RetryHandler:
    """
    Handles retry logic and fallback strategies for Bedrock API requests.
    
    This class encapsulates:
    - Exponential backoff with jitter
    - Context window overflow detection and recovery
    - Aggressive conversation fallback for large contexts
    - Error classification and appropriate retry decisions
    """

    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        exponential_backoff: bool = True,
        max_conversation_messages: int = 20,
        preserve_system_message: bool = True,
    ):
        """
        Initialize the retry handler.

        Args:
            max_retries: Maximum number of retry attempts
            retry_delay: Base delay between retries in seconds
            exponential_backoff: Whether to use exponential backoff
            max_conversation_messages: Max messages for conversation management
            preserve_system_message: Whether to preserve system messages in fallback
        """
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.exponential_backoff = exponential_backoff
        self.max_conversation_messages = max_conversation_messages
        self.preserve_system_message = preserve_system_message

    def calculate_retry_delay(self, attempt: int) -> float:
        """
        Calculate delay for retry with exponential backoff and jitter.

        Args:
            attempt: The current attempt number (0-indexed)

        Returns:
            Delay in seconds before next retry
        """
        if self.exponential_backoff:
            delay = self.retry_delay * (2 ** attempt)
        else:
            delay = self.retry_delay

        # Add jitter (10-30% of delay)
        jitter = random.uniform(0.1, 0.3) * delay

        # Cap at 60 seconds
        return min(delay + jitter, 60.0)

    async def make_request_with_retries(
        self,
        client: Any,
        model_id: str,
        request_body: Dict[str, Any],
        on_success: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        Make Bedrock API request with retry logic.

        Args:
            client: The boto3 bedrock-runtime client
            model_id: The model ID to use
            request_body: The request body to send
            on_success: Optional callback called on successful request

        Returns:
            The response body from Bedrock

        Raises:
            BedrockClientError: If all retry attempts fail
        """
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: client.invoke_model(
                        modelId=model_id,
                        body=json.dumps(request_body),
                        contentType="application/json",
                        accept="application/json",
                    ),
                )

                # Parse response
                response_body = json.loads(response["body"].read())

                # Call success callback if provided
                if on_success:
                    on_success()

                return response_body

            except (BotoCoreError, ClientError) as e:
                last_exception = e
                error_code, error_message = self._extract_error_info(e)

                # Check for context length issues and enhance error message
                if error_code == "ValidationException" and "Input is too long" in error_message:
                    enhanced_message = (
                        f"Input is too long for the model's context window. "
                        f"Max conversation messages: {self.max_conversation_messages}. "
                        f"Consider reducing max_conversation_messages or changing conversation_strategy. "
                        f"Original error: {error_message}"
                    )
                    last_exception = BedrockClientError(enhanced_message)

                # Don't retry on certain errors
                if error_code in ["ValidationException", "AccessDeniedException"]:
                    break

                # Don't retry on last attempt
                if attempt == self.max_retries:
                    break

                # Calculate delay and retry
                delay = self.calculate_retry_delay(attempt)
                logger.warning(f"Request failed (attempt {attempt + 1}), retrying in {delay}s: {str(e)}")
                await asyncio.sleep(delay)

            except Exception as e:
                last_exception = e

                # Check if it's a timeout error that we can retry
                is_timeout = self._is_timeout_error(e)

                # Retry timeout errors, but not on last attempt
                if is_timeout and attempt < self.max_retries:
                    delay = self.calculate_retry_delay(attempt)
                    logger.warning(f"Timeout error (attempt {attempt + 1}), retrying in {delay}s: {str(e)}")
                    await asyncio.sleep(delay)
                    continue

                # Don't retry other unexpected errors
                break

        raise BedrockClientError(
            f"Request failed after {self.max_retries + 1} attempts: {str(last_exception)}"
        )

    def _extract_error_info(self, error: Exception) -> tuple:
        """
        Extract error code and message from boto exception.

        Args:
            error: The exception to extract info from

        Returns:
            Tuple of (error_code, error_message)
        """
        response = getattr(error, "response", None)
        error_code = ""
        error_message = str(error)

        if response and isinstance(response, dict):
            error_code = response.get("Error", {}).get("Code", "")

        return error_code, error_message

    def _is_timeout_error(self, error: Exception) -> bool:
        """
        Check if an exception is a timeout error.

        Args:
            error: The exception to check

        Returns:
            True if it's a timeout error, False otherwise
        """
        error_type = str(type(error))
        error_str = str(error).lower()

        return (
            "ReadTimeoutError" in error_type
            or "timeout" in error_str
            or "timed out" in error_str
        )

    def is_context_window_error(self, error: Exception) -> bool:
        """
        Check if an error is related to context window/token limits.

        Args:
            error: The exception to check

        Returns:
            True if it's a context window error, False otherwise
        """
        error_str = str(error)
        return (
            "Input is too long" in error_str
            or "max_tokens must be at least 1" in error_str
            or "got -" in error_str  # Negative max_tokens
            or "length limit exceeded" in error_str  # Request body too large
            or "Failed to buffer the request body" in error_str  # Bedrock HTTP limits
            or "Unexpected token" in error_str  # GPT tokenization issues
            or "expecting start token" in error_str  # GPT token parsing errors
            or "BadRequestError" in error_str  # Generic bad request including token errors
        )

    def aggressive_conversation_fallback(
        self,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Apply aggressive conversation management when context window is exceeded.

        This reduces the conversation to the bare minimum needed for context,
        prioritizing the system message, most recent messages, and the latest
        tool interaction if present.

        Args:
            messages: The original list of messages

        Returns:
            A reduced list of messages
        """
        # Check if we have an extremely large conversation (likely from chunking)
        total_chars = sum(len(str(msg.get("content", ""))) for msg in messages)

        if len(messages) > 50 or total_chars > 500000:
            # Ultra-aggressive fallback for request body size issues
            logger.warning(
                f"Ultra-aggressive fallback triggered: {len(messages)} messages, {total_chars:,} chars"
            )
            aggressive_limit = min(3, max(1, self.max_conversation_messages // 10))
        else:
            # Standard aggressive fallback
            aggressive_limit = max(5, self.max_conversation_messages // 3)

        result = []

        # Always preserve system message if present and configured
        if (
            self.preserve_system_message
            and messages
            and messages[0].get("role") == "system"
        ):
            result.append(messages[0])
            remaining_messages = messages[1:]
            max_remaining = aggressive_limit - 1
        else:
            remaining_messages = messages
            max_remaining = aggressive_limit

        # Filter messages, keeping track of tool-related messages
        filtered_messages = []
        last_tool_message = None
        last_tool_message_tool_use_id = None
        last_assistant_with_tool_use = None

        for msg in remaining_messages:
            role = msg.get("role", "")
            if role in ["tool", "function"]:
                # Keep track of the last tool message and its tool_use_id
                last_tool_message = msg
                last_tool_message_tool_use_id = msg.get("tool_call_id") or msg.get("tool_use_id")
            elif role == "assistant" and (
                "tool_use" in str(msg.get("content", "")) or "tool_calls" in msg
            ):
                # Keep track of most recent assistant message with tool definitions
                last_assistant_with_tool_use = msg
                filtered_messages.append(msg)
            elif "tool_call" not in msg:
                # Keep non-tool messages
                filtered_messages.append(msg)

        # Add the most recent assistant message with tool_use if we have a tool_result
        if (
            last_tool_message
            and last_assistant_with_tool_use
            and last_assistant_with_tool_use not in filtered_messages
        ):
            filtered_messages.append(last_assistant_with_tool_use)

        # Add the most recent tool message at the end if we had any
        if last_tool_message:
            filtered_messages.append(last_tool_message)

        # For ultra-aggressive mode, also filter out very long messages
        if len(messages) > 50:
            logger.info("Filtering out very large messages in ultra-aggressive mode")
            size_filtered = []
            for msg in filtered_messages:
                content_size = len(str(msg.get("content", "")))
                if content_size < 10000:
                    size_filtered.append(msg)
                else:
                    # Keep a truncated version of large messages
                    truncated_msg = msg.copy()
                    truncated_msg["content"] = (
                        str(msg.get("content", ""))[:1000] + "...[truncated due to size]"
                    )
                    size_filtered.append(truncated_msg)
            filtered_messages = size_filtered

        # Take only the most recent messages
        if len(filtered_messages) > max_remaining:
            result.extend(filtered_messages[-max_remaining:])
        else:
            result.extend(filtered_messages)

        logger.info(f"Aggressive fallback: {len(messages)} -> {len(result)} messages")
        return result

    def create_error_response(self, error_message: str) -> Dict[str, Any]:
        """
        Create a graceful error response.

        Args:
            error_message: The error message to include

        Returns:
            A response dict with error information
        """
        return {
            "content": f"I'm experiencing technical difficulties: {error_message}. Please try again in a moment.",
            "tool_calls": [],
            "metadata": {"error": True, "error_message": error_message},
        }

    def get_context_error_message(
        self,
        model_id: str,
        original_count: int,
        fallback_count: int,
        original_error: Exception,
    ) -> str:
        """
        Generate a helpful error message for context window issues.

        Args:
            model_id: The model that was being used
            original_count: Number of messages in original request
            fallback_count: Number of messages in fallback attempt
            original_error: The original exception

        Returns:
            A detailed error message with recommendations
        """
        error_str = str(original_error)

        if model_id.startswith("openai.gpt-oss"):
            if "length limit exceeded" in error_str or "Failed to buffer" in error_str:
                return (
                    f"GPT OSS model request body size exceeded Bedrock limits. "
                    f"Tried {original_count} messages (1st attempt), then "
                    f"{fallback_count} messages (fallback). "
                    f"The conversation with chunked messages is too large for a single request. "
                    f"Recommendations: (1) Much smaller BEDROCK_CHUNK_SIZE (10000-20000), "
                    f"(2) Very low BEDROCK_MAX_CONVERSATION_MESSAGES (5-10), "
                    f"(3) Start new conversation for large inputs, or (4) use Claude models. "
                    f"Original error: {error_str}"
                )
            elif "Unexpected token" in error_str or "expecting start token" in error_str:
                return (
                    f"GPT OSS model tokenization error. "
                    f"This often occurs with longer conversations or special characters. "
                    f"Tried {original_count} messages (1st attempt), then "
                    f"{fallback_count} messages (fallback). "
                    f"Recommendations: (1) Start a new conversation, "
                    f"(2) Use lower BEDROCK_MAX_CONVERSATION_MESSAGES (5-10), "
                    f"(3) Switch to Claude models for more robust tokenization. "
                    f"Original error: {error_str}"
                )
            else:
                return (
                    f"GPT OSS model context window exceeded even with aggressive trimming. "
                    f"Tried {original_count} messages (1st attempt), then "
                    f"{fallback_count} messages (fallback). "
                    f"For very large inputs, consider: (1) smaller BEDROCK_CHUNK_SIZE, "
                    f"(2) lower BEDROCK_MAX_CONVERSATION_MESSAGES, or "
                    f"(3) using Claude models which handle large contexts better. "
                    f"Original error: {error_str}"
                )
        else:
            return (
                f"Input exceeds model context window even with aggressive conversation trimming. "
                f"Tried {original_count} messages, then {fallback_count} messages. "
                f"Consider using smaller chunks or fewer messages. Original error: {error_str}"
            )
