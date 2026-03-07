"""Bedrock client for AI model interaction — pure LLM transport layer.

This module handles only:
- Formatting messages for the Bedrock API (via model-specific parsers)
- Sending requests with transport-level retries
- Parsing responses
- Rate limiting
- Embedding generation

Orchestration (conversation management, message preprocessing, tool call loops,
fallback models, graceful degradation) is handled by ChatManager.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

import boto3

from .config import ChatConfig
from .exceptions import BedrockClientError, ContextWindowExceededError
from .parsers import ClaudeParser, GPTParser, LlamaParser, Parser
from .retry_handler import RetryHandler

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
    """Amazon Bedrock client — pure LLM transport layer.

    Handles only Bedrock API communication: formatting, sending, parsing.
    Does NOT manage conversation history, message preprocessing, tool call
    loops, fallback models, or graceful degradation (see ChatManager).
    """

    def __init__(self, config: ChatConfig):
        self.config = config
        self._client = None
        self._session = None
        self._last_request_time = 0
        self._request_count = 0

        # Initialize the retry handler for transport-level retries
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
        Send messages to Bedrock and return the parsed response.

        This is a pure transport method: format → send → parse.
        It does NOT manage conversation history, preprocess messages,
        handle tool call loops, or attempt fallback models.

        Args:
            messages: List of conversation messages (already preprocessed by ChatManager)
            model_id: Bedrock model ID to use
            tools_desc: Tools/functions available to the model
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            **kwargs: Additional model parameters

        Returns:
            Dict containing the model response, tool calls, and metadata

        Raises:
            ContextWindowExceededError: If input exceeds model context window
            BedrockClientError: For other API errors
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

            # Log conversation history before preparing request
            self._log_conversation_history(model_id, messages)

            # Prepare and send request
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

            response = await self._make_request_with_retries(model_id, request_body)

            # Parse and format the response
            formatted_response = self._parse_response(response, model_id)

            return formatted_response

        except BedrockClientError as e:
            # Detect context window errors and raise specific exception
            if self._retry_handler.is_context_window_error(e):
                raise ContextWindowExceededError(
                    f"Input exceeds model context window ({model_id}, " f"{len(messages)} messages): {str(e)}"
                ) from e
            raise

        except Exception as e:
            logger.exception(f"Chat completion error: {str(e)}")
            raise BedrockClientError(f"Chat completion failed: {str(e)}") from e

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

    def format_messages(
        self,
        messages: List[Dict[str, Any]],
        model_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Convert ChatMessage-compatible dicts to Bedrock API format using model-specific parsers.

        This handles:
        1. Adding system prompt if not already present
        2. Converting ChatMessage dict format to model-specific Bedrock format
        3. Delegating to parser for model-specific message formatting

        Args:
            messages: List of message dicts with role, content, tool_calls, tool_results
            model_id: Optional model ID to select the parser.  When ``None``
                (the default), ``self.config.model_id`` is used.  Pass an
                explicit model ID when formatting for a fallback model so
                the correct parser is selected.

        Returns:
            Messages formatted for Bedrock API in model-specific format
        """
        bedrock_messages = []

        # Check if system message is already present
        has_system_message = any(msg.get("role") == "system" for msg in messages)

        # Add system prompt as first message if not present
        if not has_system_message:
            bedrock_messages.append({"role": "system", "content": self.config.get_system_prompt()})

        # Get parser for the requested (or default) model
        parser = self._get_parser(model_id or self.config.model_id)

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

    async def generate_embedding(self, text: str, model_id: str = "amazon.titan-embed-text-v1") -> List[float]:
        """
        Generate embedding vector for a single text using AWS Bedrock.

        Args:
            text: Input text to embed
            model_id: Bedrock embedding model ID
                - amazon.titan-embed-text-v1 (1536 dimensions)
                - amazon.titan-embed-text-v2:0 (configurable dimensions)
                - cohere.embed-english-v3
                - cohere.embed-multilingual-v3

        Returns:
            Embedding vector as list of floats

        Raises:
            BedrockClientError: If embedding generation fails
        """
        try:
            # Prepare request body based on model
            if model_id.startswith("amazon.titan-embed"):
                body = json.dumps({"inputText": text})
            elif model_id.startswith("cohere.embed"):
                body = json.dumps({"texts": [text], "input_type": "search_document"})  # or "search_query" for queries
            else:
                raise BedrockClientError(f"Unsupported embedding model: {model_id}")

            # Rate limiting
            await self._handle_rate_limiting()

            # Invoke model
            response = self._client.invoke_model(
                modelId=model_id, body=body, contentType="application/json", accept="application/json"
            )

            # Parse response
            response_body = json.loads(response["body"].read())

            if model_id.startswith("amazon.titan-embed"):
                embedding = response_body.get("embedding")
            elif model_id.startswith("cohere.embed"):
                embedding = response_body.get("embeddings", [None])[0]
            else:
                raise BedrockClientError(f"Unknown response format for model: {model_id}")

            if not embedding:
                raise BedrockClientError("No embedding returned from model")

            logger.debug(f"Generated embedding: {len(embedding)} dimensions")
            return embedding

        except Exception as e:
            logger.error(f"Failed to generate embedding: {str(e)}")
            raise BedrockClientError(f"Embedding generation failed: {str(e)}")

    async def generate_embeddings_batch(
        self, texts: List[str], model_id: str = "amazon.titan-embed-text-v1", batch_size: int = 25
    ) -> List[List[float]]:
        """
        Generate embeddings for multiple texts in batches.

        Args:
            texts: List of input texts
            model_id: Bedrock embedding model ID
            batch_size: Number of texts to process concurrently (AWS limits apply)

        Returns:
            List of embedding vectors

        Note:
            AWS Bedrock has rate limits. Adjust batch_size based on your quota.
            Default is 25 concurrent requests which is conservative.
        """
        embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]

            logger.info(f"Processing embedding batch {i // batch_size + 1}/{(len(texts) - 1) // batch_size + 1}")

            # Process batch concurrently
            tasks = [self.generate_embedding(text, model_id) for text in batch]
            batch_embeddings = await asyncio.gather(*tasks, return_exceptions=True)

            # Handle any errors in the batch
            for j, result in enumerate(batch_embeddings):
                if isinstance(result, Exception):
                    logger.error(f"Failed to embed text {i + j}: {str(result)}")
                    # Return zero vector as fallback
                    embeddings.append([0.0] * 1536)  # Titan v1 default dimension
                else:
                    embeddings.append(result)

        logger.info(f"Generated {len(embeddings)} embeddings total")
        return embeddings
