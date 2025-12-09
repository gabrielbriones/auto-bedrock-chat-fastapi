"""Bedrock client for AI model interaction"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import boto3

from .config import ChatConfig
from .conversation_manager import ConversationManager
from .exceptions import BedrockClientError
from .message_chunker import MessageChunker
from .parsers import ClaudeParser, GPTParser, LlamaParser, Parser
from .retry_handler import RetryHandler
from .tool_message_processor import ToolMessageProcessor

logger = logging.getLogger(__name__)


# ============================================================================
# Logging Utilities (module-level functions for message preview/summary)
# ============================================================================


def generate_message_preview(content: Any, max_preview_len: int = 100) -> tuple:
    """
    Generate a preview string and content length for logging.

    This is a pure utility function used for debug logging of conversation messages.

    Args:
        content: Message content (string, list, or other)
        max_preview_len: Maximum length of preview string

    Returns:
        Tuple of (content_length, preview_string)
    """
    if isinstance(content, str):
        content_len = len(content)
        preview = content[:max_preview_len].replace("\n", " ")
        if len(content) > max_preview_len:
            preview += "..."

    elif isinstance(content, list):
        # Claude format with content blocks
        content_len = len(str(content))
        text_parts = [
            (
                item.get("text", "")
                if isinstance(item, dict) and item.get("type") == "text"
                else str(item)[:max_preview_len]
            )
            for item in content[:2]  # Show first 2 items
        ]
        preview = " | ".join(text_parts)[:max_preview_len]
        if len(content) > 2 or len(str(content)) > max_preview_len:
            preview += "..."

    else:
        # Other types (dict, etc.)
        content_len = len(str(content))
        preview = str(content)[:max_preview_len] + "..."

    return content_len, preview


def format_conversation_summary(messages: List[Dict[str, Any]]) -> str:
    """
    Format conversation messages for compact logging.

    Shows role, content length, and first 100 chars of content for each message.
    This is a pure utility function used for debug logging.

    Args:
        messages: List of conversation messages

    Returns:
        Formatted string summary of the conversation
    """
    summary_lines = []
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        # Generate preview and calculate size
        content_len, preview = generate_message_preview(content)

        summary_lines.append(f"  [{i+1}] {role} ({content_len:,} chars): {preview}")

    return "\n".join(summary_lines)


def _log_messages_state(messages: List[Dict[str, Any]], label: str) -> None:
    """
    Log the state of messages with tool call/result details.

    This is a utility function that logs message details at DEBUG level.
    Used to trace message transformations during chat_completion.

    Args:
        messages: List of conversation messages
        label: Description of the current state (e.g., "Before truncation")
    """
    logger.debug(f"{label}: {len(messages)} messages")
    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])
        tool_results = msg.get("tool_results", [])
        content_len = len(content) if isinstance(content, str) else len(str(content))
        logger.debug(
            f"  [{i}] {role}: content_len={content_len}, "
            f"tool_calls={len(tool_calls)}, tool_results={len(tool_results)}"
        )
        if tool_results:
            for j, tr in enumerate(tool_results):
                tool_call_id = tr.get("tool_call_id") or tr.get("tool_use_id")
                logger.debug(f"      - tool_result[{j}] keys={list(tr.keys())}, tool_call_id={tool_call_id}")
        if tool_calls:
            for j, tc in enumerate(tool_calls):
                tc_id = tc.get("id")
                logger.debug(f"      - tool_call[{j}] id={tc_id}")


def _truncate_for_logging(value: Any, max_len: int = 300) -> Any:
    """
    Recursively truncate large string values in a response for logging.

    Args:
        value: The value to truncate (string, dict, list, or other)
        max_len: Maximum length before truncation

    Returns:
        The value with large strings truncated
    """
    if isinstance(value, str) and len(value) > max_len:
        return value[:100] + f"... ({len(value):,} chars total)"
    elif isinstance(value, dict):
        return {k: _truncate_for_logging(v, max_len) for k, v in value.items()}
    elif isinstance(value, list):
        return [_truncate_for_logging(item, max_len) for item in value]
    else:
        return value


def _log_response_debug(response: Dict[str, Any], model_id: str) -> None:
    """
    Log response with truncated large values at DEBUG level.

    Args:
        response: The response dict from Bedrock API
        model_id: The model ID for context in log message
    """
    if logger.isEnabledFor(logging.DEBUG):
        truncated_response = _truncate_for_logging(response)
        logger.debug(f"Parsing response for model {model_id}: {truncated_response}")


class BedrockClient:
    """Amazon Bedrock client for AI model interactions"""

    def __init__(self, config: ChatConfig):
        self.config = config
        self._client = None
        self._session = None
        self._last_request_time = 0
        self._request_count = 0

        # Initialize the tool message processor with config values
        self._tool_processor = ToolMessageProcessor(
            tool_result_history_threshold=config.tool_result_history_threshold,
            tool_result_history_target=config.tool_result_history_target,
            tool_result_new_response_threshold=config.tool_result_new_response_threshold,
            tool_result_new_response_target=config.tool_result_new_response_target,
        )

        # Initialize the conversation manager with config values
        self._conversation_manager = ConversationManager(
            max_conversation_messages=config.max_conversation_messages,
            conversation_strategy=config.conversation_strategy,
            preserve_system_message=config.preserve_system_message,
        )

        # Initialize the message chunker with config values
        self._message_chunker = MessageChunker(
            enable_message_chunking=config.enable_message_chunking,
            max_message_size=config.max_message_size,
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
            chunking_strategy=config.chunking_strategy,
            tool_result_history_threshold=config.tool_result_history_threshold,
            tool_result_new_response_threshold=config.tool_result_new_response_threshold,
        )

        # Initialize the retry handler with config values
        self._retry_handler = RetryHandler(
            max_retries=config.max_retries,
            retry_delay=config.retry_delay,
            exponential_backoff=config.exponential_backoff,
            max_conversation_messages=config.max_conversation_messages,
            preserve_system_message=config.preserve_system_message,
        )

        # Initialize AWS session and client
        self._initialize_client()

    def _initialize_client(self):
        """Initialize boto3 client for Bedrock"""
        try:
            # Create AWS session
            self._session = boto3.Session(**self.config.get_aws_config())

            # Import botocore config for timeout settings
            from botocore.config import Config

            # Create client config with increased timeout for large models
            client_config = Config(
                read_timeout=max(120, self.config.timeout),  # At least 2 minutes
                connect_timeout=30,  # Increased connection timeout
                retries={"max_attempts": 3},
            )

            # Create Bedrock client
            self._client = self._session.client(
                "bedrock-runtime",
                region_name=self.config.aws_region,
                config=client_config,
            )

            logger.info(f"Bedrock client initialized for region: {self.config.aws_region}")

        except Exception as e:
            raise BedrockClientError(f"Failed to initialize Bedrock client: {str(e)}")

    def _get_parser(self, model_id: str) -> Parser:
        """
        Get the appropriate parser for the given model ID

        Args:
            model_id: Bedrock model ID

        Returns:
            Parser instance for the specified model
        """
        if model_id.startswith("anthropic.claude") or model_id.startswith("us.anthropic.claude"):
            return ClaudeParser(self.config)
        elif model_id.startswith("meta.llama") or model_id.startswith("us.meta.llama"):
            return LlamaParser(self.config)
        elif model_id.startswith("openai.gpt-oss"):
            return GPTParser(self.config)
        else:
            # Default to Claude parser for unknown models
            logger.warning(f"Unknown model ID: {model_id}, using ClaudeParser as default")
            return ClaudeParser(self.config)

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model_id: Optional[str] = None,
        tools_desc: Optional[Dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Main chat completion function called by the plugin

        Args:
            messages: List of conversation messages (system prompt should be first message if needed)
            model_id: Bedrock model ID to use
            tools_desc: Tools/functions available to the model
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            **kwargs: Additional model parameters

        Returns:
            Dict containing the model response, tool calls, and metadata
        """

        # Use config defaults if not provided
        model_id = model_id or self.config.model_id
        tools_desc = tools_desc or self.config.tools_desc
        temperature = temperature if temperature is not None else self.config.temperature
        max_tokens = max_tokens or self.config.max_tokens

        # Log request if enabled
        if self.config.log_api_calls:
            logger.info(f"Bedrock request: model={model_id}, messages={len(messages)}")

        try:
            # Rate limiting
            await self._handle_rate_limiting()

            # Manage conversation history to prevent context length issues
            original_count = len(messages)
            messages = self._conversation_manager.manage_conversation_history(messages)
            if len(messages) < original_count:
                logger.info(f"Conversation history trimmed from {original_count} to {len(messages)} messages")

            # Truncate tool messages in conversation history BEFORE sending to assistant
            # This prevents token limit overflow when assistant makes multiple sequential tool calls
            # _log_messages_state(messages, "Before truncation")

            messages = self._tool_processor.truncate_tool_messages_in_history(messages)
            # _log_messages_state(messages, "After truncation")

            # Final cleanup: Remove any orphaned tool_results that may have been left behind
            # after trimming and truncation
            messages = self._conversation_manager.remove_orphaned_tool_results(messages)
            # _log_messages_state(messages, "After orphan cleanup")

            # Check and chunk large messages to prevent individual message size
            # issues
            original_message_count = len(messages)
            messages = self._message_chunker.check_and_chunk_messages(messages, self._tool_processor)
            if len(messages) > original_message_count:
                logger.info(f"Large messages chunked: {original_message_count} -> {len(messages)} messages")

            # Try making the request with current messages
            response = await self._try_request_with_fallback(
                messages, model_id, tools_desc, temperature, max_tokens, **kwargs
            )
            # logger.debug(f"Bedrock response: {response}")

            # Parse and format the response
            formatted_response = self._parse_response(response, model_id)

            # Note: Tool calls are NOT executed here. The caller (typically WebSocketChatHandler)
            # is responsible for executing tool calls via _execute_tool_calls() method.
            # BedrockClient only handles Bedrock API communication.

            return formatted_response

        except Exception as e:
            logger.exception(f"Chat completion error: {str(e)}")

            # Try fallback model if configured
            if self.config.fallback_model and model_id != self.config.fallback_model:
                logger.info(f"Attempting fallback to model: {self.config.fallback_model}")
                try:
                    return await self.chat_completion(
                        messages=messages,
                        model_id=self.config.fallback_model,
                        tools_desc=tools_desc,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                except Exception as fallback_error:
                    logger.exception(f"Fallback model also failed: {str(fallback_error)}")

            # Handle graceful degradation
            if self.config.graceful_degradation:
                return self._create_error_response(str(e))

            raise BedrockClientError(f"Chat completion failed: {str(e)}")

    def _prepare_request_body(
        self,
        messages: List[Dict[str, Any]],
        model_id: str,
        tools_desc: Optional[Dict],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> Dict[str, Any]:
        """Prepare request body using the appropriate parser for the model"""

        # Get the appropriate parser for this model
        parser = self._get_parser(model_id)

        # Use parser to format messages with temperature and max_tokens parameters
        return parser.format_messages(
            messages, tools_desc=tools_desc, temperature=temperature, max_tokens=max_tokens, **kwargs
        )

    def format_messages_for_bedrock(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert ChatMessage-compatible dicts to Bedrock API format using model-specific parsers.

        This handles:
        1. Adding system prompt if not already present
        2. Converting ChatMessage dict format to model-specific Bedrock format
        3. Delegating to parser for model-specific message formatting

        Args:
            messages: List of message dicts with role, content, tool_calls, tool_results

        Returns:
            Messages formatted for Bedrock API in model-specific format
        """
        bedrock_messages = []

        # Check if system message is already present
        has_system_message = any(msg.get("role") == "system" for msg in messages)

        # Add system prompt as first message if not present
        if not has_system_message:
            bedrock_messages.append({"role": "system", "content": self.config.get_system_prompt()})

        # Get parser for current model
        parser = self._get_parser(self.config.model_id)

        # Use parser to format messages (handles model-specific formatting)
        formatted_messages = parser.format_bedrock_messages(messages)

        # Add formatted messages to the output
        bedrock_messages.extend(formatted_messages)

        return bedrock_messages

    def _log_conversation_history(self, model_id: str, messages: List[Dict[str, Any]]):
        """Log conversation history in compact format before sending to model"""
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Bedrock request for {model_id} with {len(messages)} messages:")
            logger.debug(f"\n{format_conversation_summary(messages)}")

    async def _make_request_with_retries(self, model_id: str, request_body: Dict[str, Any]) -> Dict[str, Any]:
        """Make Bedrock API request with retry logic"""

        def on_success():
            self._last_request_time = time.time()
            self._request_count += 1

        return await self._retry_handler.make_request_with_retries(
            client=self._client,
            model_id=model_id,
            request_body=request_body,
            on_success=on_success,
        )

    async def _try_request_with_fallback(self, messages, model_id, tools_desc, temperature, max_tokens, **kwargs):
        """
        Try making a request with automatic fallback for context window issues
        """
        # First attempt with current messages
        try:
            # Log conversation history before preparing request
            self._log_conversation_history(model_id, messages)

            request_body = self._prepare_request_body(
                messages=messages,
                model_id=model_id,
                tools_desc=tools_desc,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

            # Debug log for GPT models to track max_tokens issue
            if model_id.startswith("openai.gpt-oss"):
                logger.debug(
                    f"GPT request max_tokens: {request_body.get('max_tokens')} "
                    f"(original: {max_tokens}, messages: {len(messages)})"
                )
                # Log the actual messages being sent to GPT to verify sanitization
                for i, msg in enumerate(request_body.get("messages", [])):
                    msg_content = msg.get("content", "")
                    if isinstance(msg_content, str):
                        has_emoji = any(ord(c) >= 0x1F300 for c in msg_content)
                        logger.debug(
                            f"  Message {i}: role={msg.get('role')}, len={len(msg_content)}, has_emoji={has_emoji}"
                        )
                        if has_emoji:
                            logger.warning(f"  EMOJI DETECTED in message {i}: {repr(msg_content[:100])}")
                    else:
                        logger.debug(f"  Message {i}: role={msg.get('role')}, type={type(msg_content)}")

            return await self._make_request_with_retries(model_id, request_body)

        except BedrockClientError as e:
            # Check if this is a context window issue
            if self._retry_handler.is_context_window_error(e):
                logger.warning(f"Context/token issue detected: {str(e)[:100]}...")
                logger.warning("Trying aggressive fallback...")

                # Try with more aggressive conversation management
                fallback_messages = self._retry_handler.aggressive_conversation_fallback(messages)

                if len(fallback_messages) < len(messages):
                    logger.info(
                        f"Aggressive fallback: reduced from {len(messages)} to {len(fallback_messages)} messages"
                    )

                    try:
                        # Use more conservative max_tokens for aggressive fallback
                        fallback_max_tokens = (
                            min(max_tokens, 1000) if model_id.startswith("openai.gpt-oss") else max_tokens
                        )

                        # Log fallback conversation history before preparing request
                        self._log_conversation_history(model_id, fallback_messages)

                        request_body = self._prepare_request_body(
                            messages=fallback_messages,
                            model_id=model_id,
                            tools_desc=tools_desc,
                            temperature=temperature,
                            max_tokens=fallback_max_tokens,
                            **kwargs,
                        )

                        # Debug log for GPT fallback
                        if model_id.startswith("openai.gpt-oss"):
                            logger.debug(
                                f"GPT fallback max_tokens: {request_body.get('max_tokens')} "
                                f"(fallback: {fallback_max_tokens}, messages: {len(fallback_messages)})"
                            )

                        return await self._make_request_with_retries(model_id, request_body)
                    except BedrockClientError:
                        # If fallback also fails, provide helpful error message
                        logger.error("Aggressive fallback also failed")
                        error_msg = self._retry_handler.get_context_error_message(
                            model_id=model_id,
                            original_count=len(messages),
                            fallback_count=len(fallback_messages),
                            original_error=e,
                        )
                        raise BedrockClientError(error_msg)
                else:
                    # No further reduction possible
                    raise BedrockClientError(
                        f"Input exceeds model context window and cannot be reduced further. "
                        f"Current messages: {len(messages)}. Original error: {str(e)}"
                    )
            else:
                # Re-raise non-context-window errors
                raise

    def _parse_response(self, response: Dict[str, Any], model_id: str) -> Dict[str, Any]:
        """Parse and format model response using appropriate parser"""

        # Check if response is None or invalid
        if response is None:
            logger.error("Received None response from Bedrock API")
            return {
                "content": "I received an empty response from the AI service.",
                "tool_calls": [],
                "metadata": {"error": "None response"},
            }

        if not isinstance(response, dict):
            logger.error(f"Received invalid response type: {type(response)}")
            return {
                "content": "I received an invalid response format from the AI service.",
                "tool_calls": [],
                "metadata": {"error": f"Invalid response type: {type(response)}"},
            }

        try:
            # Log response with truncated large values
            _log_response_debug(response, model_id)

            # Get the appropriate parser for this model and parse the response
            parser = self._get_parser(model_id)
            return parser.parse_response(response)

        except Exception as e:
            logger.exception(f"Failed to parse response: {str(e)}")
            logger.error(f"Response content: {response}")
            return {
                "content": "I encountered an error processing the response.",
                "tool_calls": [],
                "metadata": {"error": str(e)},
            }

    def _create_error_response(self, error_message: str) -> Dict[str, Any]:
        """Create error response for graceful degradation"""
        return self._retry_handler.create_error_response(error_message)

    async def _handle_rate_limiting(self):
        """Simple rate limiting to avoid overwhelming the API"""

        current_time = time.time()
        time_since_last_request = current_time - self._last_request_time

        # Ensure minimum time between requests (basic rate limiting)
        min_interval = 0.1  # 100ms minimum between requests
        if time_since_last_request < min_interval:
            await asyncio.sleep(min_interval - time_since_last_request)

    async def health_check(self) -> Dict[str, Any]:
        """Check Bedrock service health"""

        try:
            # Simple test request
            test_messages = [{"role": "user", "content": "Hello"}]

            response = await self.chat_completion(messages=test_messages, max_tokens=10, temperature=0.1)

            return {
                "status": "healthy",
                "model": self.config.model_id,
                "region": self.config.aws_region,
                "response_received": bool(response.get("content")),
            }

        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "model": self.config.model_id,
                "region": self.config.aws_region,
            }
