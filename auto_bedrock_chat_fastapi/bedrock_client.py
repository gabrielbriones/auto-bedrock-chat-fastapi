"""Bedrock client for AI model interaction"""

import asyncio
import json
import logging
import random
import time
import unicodedata
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .config import ChatConfig
from .exceptions import BedrockClientError

logger = logging.getLogger(__name__)


class BedrockClient:
    """Amazon Bedrock client for AI model interactions"""

    def __init__(self, config: ChatConfig):
        self.config = config
        self._client = None
        self._session = None
        self._last_request_time = 0
        self._request_count = 0

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
            messages = self._manage_conversation_history(messages)
            if len(messages) < original_count:
                logger.info(f"Conversation history trimmed from {original_count} to {len(messages)} messages")

            # Check and chunk large messages to prevent individual message size
            # issues
            original_message_count = len(messages)
            messages = self._check_and_chunk_messages(messages)
            if len(messages) > original_message_count:
                logger.info(f"Large messages chunked: {original_message_count} -> {len(messages)} messages")

            # Try making the request with current messages
            response = await self._try_request_with_fallback(
                messages, model_id, tools_desc, temperature, max_tokens, **kwargs
            )
            # logger.debug(f"Bedrock response: {response}")

            # Parse and format the response
            formatted_response = self._parse_response(response, model_id)

            # Process any tool calls
            if formatted_response.get("tool_calls"):
                tool_results = await self._execute_tool_calls(formatted_response["tool_calls"], tools_desc)
                formatted_response["tool_results"] = tool_results

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
        """Prepare request body based on model family"""

        if model_id.startswith("anthropic.claude") or model_id.startswith("us.anthropic.claude"):
            return self._prepare_claude_request(messages, tools_desc, temperature, max_tokens, **kwargs)
        elif model_id.startswith("amazon.titan"):
            return self._prepare_titan_request(messages, tools_desc, temperature, max_tokens, **kwargs)
        elif model_id.startswith("meta.llama") or model_id.startswith("us.meta.llama"):
            return self._prepare_llama_request(messages, tools_desc, temperature, max_tokens, **kwargs)
        elif model_id.startswith("openai.gpt-oss"):
            return self._prepare_openai_gpt_request(messages, tools_desc, temperature, max_tokens, **kwargs)
        else:
            # Generic format
            return self._prepare_generic_request(messages, tools_desc, temperature, max_tokens, **kwargs)

    def _prepare_claude_request(self, messages, tools_desc, temperature, max_tokens, **kwargs) -> Dict[str, Any]:
        """Prepare request for Claude models"""

        # Extract system prompt from messages if present
        system_prompt = None
        conversation_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                conversation_messages.append(msg)

        # Use default system prompt if none provided
        if not system_prompt:
            system_prompt = self.config.get_system_prompt()

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": conversation_messages,
            "system": system_prompt,
        }

        # Note: For newer Claude models, we only use temperature, not top_p
        # to avoid "temperature and top_p cannot both be specified" error
        # If you need top_p instead of temperature, modify the config
        # accordingly

        # Add tools if available
        if tools_desc and tools_desc.get("functions"):
            request_body["tools"] = [
                {
                    "name": func["name"],
                    "description": func["description"],
                    "input_schema": func["parameters"],
                }
                for func in tools_desc["functions"]
            ]

        return request_body

    def _sanitize_text_for_gpt(self, text: str) -> str:
        """
        Sanitize text for GPT models to avoid tokenization issues
        Removes problematic Unicode characters that may cause token errors
        """
        if not isinstance(text, str):
            text = str(text)

        # Replace problematic Unicode characters
        replacements = {
            "\u202f": " ",  # Narrow no-break space → regular space
            "\u00a0": " ",  # Non-breaking space → regular space
            "\u2009": " ",  # Thin space → regular space
            "\u200b": "",  # Zero-width space → remove
            "\u200c": "",  # Zero-width non-joiner → remove
            "\u200d": "",  # Zero-width joiner → remove
            "\ufeff": "",  # Zero-width no-break space (BOM) → remove
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        text = unicodedata.normalize("NFC", text)

        return text

    def _sanitize_message_content(self, content):
        """Sanitize message content (handles both string and dict/list formats)"""
        if isinstance(content, str):
            return self._sanitize_text_for_gpt(content)
        elif isinstance(content, dict):
            return {k: self._sanitize_message_content(v) for k, v in content.items()}
        elif isinstance(content, list):
            return [self._sanitize_message_content(item) for item in content]
        else:
            return content

    def _prepare_openai_gpt_request(self, messages, tools_desc, temperature, max_tokens, **kwargs) -> Dict[str, Any]:
        """Prepare request for OpenAI GPT OSS models"""

        # For OpenAI format, messages can already include system message
        # If no system message is present, add the default one
        has_system_message = any(msg.get("role") == "system" for msg in messages)

        formatted_messages = []

        if not has_system_message:
            # Add default system message if none present
            formatted_messages.append({"role": "system", "content": self.config.get_system_prompt()})

        # Add all conversation messages with sanitization for GPT models
        for msg in messages:
            sanitized_msg = msg.copy()
            if "content" in sanitized_msg:
                sanitized_msg["content"] = self._sanitize_message_content(sanitized_msg["content"])
            formatted_messages.append(sanitized_msg)

        # For GPT models with very large inputs, we need to ensure max_tokens stays positive
        # Estimate input size roughly and adjust max_tokens accordingly
        total_input_chars = sum(len(str(msg.get("content", ""))) for msg in formatted_messages)

        # Rough estimation: 1 token ≈ 4 characters (conservative estimate)
        estimated_input_tokens = total_input_chars // 4

        # GPT OSS models seem to have a lower context limit than expected
        # Use very conservative limits to prevent Bedrock service from
        # calculating negative tokens
        gpt_context_limit = 100000  # Conservative limit for GPT OSS
        min_response_tokens = 1  # Absolute minimum
        safe_response_tokens = 10  # Very conservative safe amount

        if estimated_input_tokens > gpt_context_limit * 0.9:  # Very large input (90% of context)
            # Use absolute minimum for very large inputs
            adjusted_max_tokens = min_response_tokens
            logger.warning(
                f"Very large input ({estimated_input_tokens} est. tokens), "
                f"using absolute minimal max_tokens: {adjusted_max_tokens}"
            )
        elif estimated_input_tokens > gpt_context_limit * 0.8:  # Large input (80% of context)
            # Use very safe small amount
            adjusted_max_tokens = safe_response_tokens
            logger.warning(
                f"Large input ({estimated_input_tokens} est. tokens), "
                f"using minimal max_tokens: {adjusted_max_tokens}"
            )
        elif estimated_input_tokens + max_tokens > gpt_context_limit:  # Approaching context limit
            # Calculate remaining safely
            remaining_tokens = gpt_context_limit - estimated_input_tokens
            adjusted_max_tokens = max(
                min_response_tokens, min(remaining_tokens - 1000, max_tokens)
            )  # Leave 1000 token buffer
            logger.info(f"Input approaching limit, adjusting max_tokens from {max_tokens} to {adjusted_max_tokens}")
        else:
            # Use original max_tokens for normal-sized inputs
            adjusted_max_tokens = max_tokens

        request_body = {
            "messages": formatted_messages,
            "max_tokens": adjusted_max_tokens,
            "temperature": temperature,
            "top_p": kwargs.get("top_p", self.config.top_p),
        }

        # Add tools if available
        if tools_desc and tools_desc.get("functions"):
            request_body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": func["name"],
                        "description": func["description"],
                        "parameters": func["parameters"],
                    },
                }
                for func in tools_desc["functions"]
            ]

        return request_body

    def _prepare_titan_request(self, messages, tools_desc, temperature, max_tokens, **kwargs) -> Dict[str, Any]:
        """Prepare request for Titan models"""

        # Extract system prompt from messages or use default
        system_prompt = None
        conversation_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                conversation_messages.append(msg)

        if not system_prompt:
            system_prompt = self.config.get_system_prompt()

        # Add tools information to system prompt if available (Titan doesn't
        # support tool calling)
        if tools_desc and tools_desc.get("functions"):
            tools_info = (
                "\n\nIMPORTANT: I am using Amazon Titan Text model which "
                "does NOT support tool calling or API execution.\n"
            )
            tools_info += (
                "I can only provide text responses and cannot access " "real-time data from your API endpoints.\n\n"
            )
            tools_info += "Your API has these available endpoints:\n"
            for func in tools_desc.get("functions", []):
                desc = func.get("description", "No description")
                tools_info += f"- {desc}\n"

            tools_info += (
                "\nTo get real-time data from your API, please use a model " "that supports tool calling such as:\n"
            )
            tools_info += "- Claude models (anthropic.claude-*)\n"
            tools_info += "- Llama models (meta.llama* or us.meta.llama*)\n"
            tools_info += "- OpenAI GPT OSS (openai.gpt-oss-*)\n\n"
            tools_info += "I can help explain how to use these endpoints or provide general guidance.\n"
            system_prompt += tools_info

        # Combine messages into a single prompt for Titan using the recommended
        # format
        prompt_parts = []

        # Add system prompt
        if system_prompt:
            prompt_parts.append(f"System: {system_prompt}")

        # Add conversation messages
        for msg in conversation_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "user":
                prompt_parts.append(f"User: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Bot: {content}")

        # End with Bot: to prompt for assistant response
        prompt_parts.append("Bot:")

        formatted_prompt = "\n".join(prompt_parts)

        # Prepare request following Titan Text Express format
        request_body = {
            "inputText": formatted_prompt,
            "textGenerationConfig": {
                "maxTokenCount": max_tokens,
                "temperature": temperature,
                "topP": kwargs.get("top_p", self.config.top_p),
            },
        }

        # Add empty stopSequences (Titan requires this field)
        request_body["textGenerationConfig"]["stopSequences"] = []

        return request_body

    def _prepare_llama_request(self, messages, tools_desc, temperature, max_tokens, **kwargs) -> Dict[str, Any]:
        """Prepare request for Llama models using proper prompt format"""

        # Convert messages to Llama's prompt format with special tokens
        prompt_parts = ["<|begin_of_text|>"]

        # Check if first message is system prompt
        start_idx = 0
        if messages and messages[0].get("role") == "system":
            system_content = messages[0]["content"]
            prompt_parts.extend(
                [
                    "<|start_header_id|>system<|end_header_id|>",
                    f"\n{system_content}<|eot_id|>",
                ]
            )
            start_idx = 1
        else:
            # Add default system message if none present
            system_prompt = self.config.get_system_prompt()
            if tools_desc:
                system_prompt += f"\n\nYou have access to these tools: {json.dumps(tools_desc, indent=2)}"
            prompt_parts.extend(
                [
                    "<|start_header_id|>system<|end_header_id|>",
                    f"\n{system_prompt}<|eot_id|>",
                ]
            )

        # Add conversation messages
        for msg in messages[start_idx:]:
            role = msg["role"]
            content = msg["content"]

            if role == "user":
                prompt_parts.extend(
                    [
                        "<|start_header_id|>user<|end_header_id|>",
                        f"\n{content}<|eot_id|>",
                    ]
                )
            elif role == "assistant":
                prompt_parts.extend(
                    [
                        "<|start_header_id|>assistant<|end_header_id|>",
                        f"\n{content}<|eot_id|>",
                    ]
                )

        # End with assistant header for completion
        prompt_parts.append("<|start_header_id|>assistant<|end_header_id|>")

        formatted_prompt = "".join(prompt_parts)

        return {
            "prompt": formatted_prompt,
            "max_gen_len": max_tokens,
            "temperature": temperature,
            "top_p": kwargs.get("top_p", self.config.top_p),
        }

    def _prepare_generic_request(self, messages, tools_desc, temperature, max_tokens, **kwargs) -> Dict[str, Any]:
        """Prepare generic request format"""

        # Extract system prompt from messages for generic format
        system_prompt = None
        conversation_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                conversation_messages.append(msg)

        if not system_prompt:
            system_prompt = self.config.get_system_prompt()

        return {
            "messages": conversation_messages,
            "system_prompt": system_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": tools_desc,
            **kwargs,
        }

    def _format_conversation_summary(self, messages: List[Dict[str, Any]]) -> str:
        """
        Format conversation messages for compact logging
        Shows role, content length, and first 100 chars of content for each message
        """
        summary_lines = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # Calculate content length
            if isinstance(content, str):
                content_len = len(content)
                preview = content[:100].replace("\n", " ")
                if len(content) > 100:
                    preview += "..."
            elif isinstance(content, list):
                # Claude format with content blocks
                content_len = len(str(content))
                text_parts = [
                    item.get("text", "") if isinstance(item, dict) and item.get("type") == "text" else str(item)[:100]
                    for item in content[:2]  # Show first 2 items
                ]
                preview = " | ".join(text_parts)[:100]
                if len(content) > 2 or len(str(content)) > 100:
                    preview += "..."
            else:
                content_len = len(str(content))
                preview = str(content)[:100] + "..."

            summary_lines.append(f"  [{i+1}] {role} ({content_len:,} chars): {preview}")

        return "\n".join(summary_lines)

    def _log_conversation_history(self, model_id: str, messages: List[Dict[str, Any]]):
        """Log conversation history in compact format before sending to model"""
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Bedrock request for {model_id} with {len(messages)} messages:")
            logger.debug(f"\n{self._format_conversation_summary(messages)}")

    async def _make_request_with_retries(self, model_id: str, request_body: Dict[str, Any]) -> Dict[str, Any]:
        """Make Bedrock API request with retry logic"""

        last_exception = None

        for attempt in range(self.config.max_retries + 1):
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._client.invoke_model(
                        modelId=model_id,
                        body=json.dumps(request_body),
                        contentType="application/json",
                        accept="application/json",
                    ),
                )

                # Parse response
                response_body = json.loads(response["body"].read())

                # Update request tracking
                self._last_request_time = time.time()
                self._request_count += 1

                return response_body

            except (BotoCoreError, ClientError) as e:
                last_exception = e
                # Safely extract error code, handling None response
                response = getattr(e, "response", None)
                error_code = ""
                error_message = str(e)
                if response and isinstance(response, dict):
                    error_code = response.get("Error", {}).get("Code", "")

                # Check for context length issues
                if error_code == "ValidationException" and "Input is too long" in error_message:
                    # This is a context length issue, enhance the error message
                    enhanced_message = (
                        f"Input is too long for the model's context window. "
                        f"Current conversation strategy: {self.config.conversation_strategy}, "
                        f"max messages: {self.config.max_conversation_messages}. "
                        f"Consider reducing max_conversation_messages or changing conversation_strategy. "
                        f"Original error: {error_message}"
                    )
                    last_exception = BedrockClientError(enhanced_message)

                # Don't retry on certain errors
                if error_code in ["ValidationException", "AccessDeniedException"]:
                    break

                # Don't retry on last attempt
                if attempt == self.config.max_retries:
                    break

                # Calculate delay
                delay = self._calculate_retry_delay(attempt)
                logger.warning(f"Request failed (attempt {attempt + 1}), retrying in {delay}s: {str(e)}")

                await asyncio.sleep(delay)

            except Exception as e:
                last_exception = e

                # Check if it's a timeout error that we can retry
                is_timeout = (
                    "ReadTimeoutError" in str(type(e)) or "timeout" in str(e).lower() or "timed out" in str(e).lower()
                )

                # Retry timeout errors, but not on last attempt
                if is_timeout and attempt < self.config.max_retries:
                    delay = self._calculate_retry_delay(attempt)
                    logger.warning(f"Timeout error (attempt {attempt + 1}), retrying in {delay}s: {str(e)}")
                    await asyncio.sleep(delay)
                    continue

                # Don't retry other unexpected errors
                break

        raise BedrockClientError(f"Request failed after {self.config.max_retries + 1} attempts: {str(last_exception)}")

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

            return await self._make_request_with_retries(model_id, request_body)

        except BedrockClientError as e:
            # Check if this is a context window issue, max_tokens error,
            # request body size issue, or token parsing error
            error_str = str(e)
            is_context_issue = (
                "Input is too long" in error_str
                or "max_tokens must be at least 1" in error_str
                or "got -" in error_str  # Negative max_tokens
                or "length limit exceeded" in error_str  # Request body too large
                or "Failed to buffer the request body" in error_str  # Bedrock HTTP limits
                or "Unexpected token" in error_str  # GPT tokenization issues
                or "expecting start token" in error_str
            )  # GPT token parsing errors

            if is_context_issue:
                logger.warning(f"Context/token issue detected: {error_str[:100]}...")
                logger.warning("Trying aggressive fallback...")

                # Try with more aggressive conversation management
                fallback_messages = self._aggressive_conversation_fallback(messages)

                if len(fallback_messages) < len(messages):
                    logger.info(
                        f"Aggressive fallback: reduced from {len(messages)} to {len(fallback_messages)} messages"
                    )

                    try:
                        # Use more conservative max_tokens for aggressive
                        # fallback
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

                        if model_id.startswith("openai.gpt-oss"):
                            if "length limit exceeded" in str(e) or "Failed to buffer" in str(e):
                                raise BedrockClientError(
                                    f"GPT OSS model request body size exceeded Bedrock limits. "
                                    f"Tried {len(messages)} messages (1st attempt), then "
                                    f"{len(fallback_messages)} messages (fallback). "
                                    f"The conversation with chunked messages is too large for a single request. "
                                    f"Recommendations: (1) Much smaller BEDROCK_CHUNK_SIZE (10000-20000), "
                                    f"(2) Very low BEDROCK_MAX_CONVERSATION_MESSAGES (5-10), "
                                    f"(3) Start new conversation for large inputs, or (4) use Claude models. "
                                    f"Original error: {str(e)}"
                                )
                            elif "Unexpected token" in str(e) or "expecting start token" in str(e):
                                raise BedrockClientError(
                                    f"GPT OSS model tokenization error. "
                                    f"This often occurs with longer conversations or special characters. "
                                    f"Tried {len(messages)} messages (1st attempt), then "
                                    f"{len(fallback_messages)} messages (fallback). "
                                    f"Recommendations: (1) Start a new conversation, "
                                    f"(2) Use lower BEDROCK_MAX_CONVERSATION_MESSAGES (5-10), "
                                    f"(3) Switch to Claude models for more robust tokenization. "
                                    f"Original error: {str(e)}"
                                )
                            else:
                                raise BedrockClientError(
                                    f"GPT OSS model context window exceeded even with aggressive trimming. "
                                    f"Tried {len(messages)} messages (1st attempt), then "
                                    f"{len(fallback_messages)} messages (fallback). "
                                    f"For very large inputs, consider: (1) smaller BEDROCK_CHUNK_SIZE, "
                                    f"(2) lower BEDROCK_MAX_CONVERSATION_MESSAGES, or "
                                    f"(3) using Claude models which handle large contexts better. "
                                    f"Original error: {str(e)}"
                                )
                        else:
                            raise BedrockClientError(
                                f"Input exceeds model context window even with aggressive conversation trimming. "
                                f"Tried {len(messages)} messages, then {len(fallback_messages)} messages. "
                                f"Consider using smaller chunks or fewer messages. Original error: {str(e)}"
                            )
                else:
                    # No further reduction possible
                    raise BedrockClientError(
                        f"Input exceeds model context window and cannot be reduced further. "
                        f"Current messages: {len(messages)}. Original error: {str(e)}"
                    )
            else:
                # Re-raise non-context-window errors
                raise

    def _aggressive_conversation_fallback(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Apply aggressive conversation management when context window is exceeded
        """
        # Check if we have an extremely large conversation (likely from
        # chunking)
        total_chars = sum(len(str(msg.get("content", ""))) for msg in messages)

        if len(messages) > 50 or total_chars > 500000:  # Very large conversation or content
            # Ultra-aggressive fallback for request body size issues
            logger.warning(f"Ultra-aggressive fallback triggered: {len(messages)} messages, {total_chars:,} chars")
            aggressive_limit = min(3, max(1, self.config.max_conversation_messages // 10))
        else:
            # Standard aggressive fallback
            aggressive_limit = max(5, self.config.max_conversation_messages // 3)

        result = []

        # Always preserve system message if present and configured
        if self.config.preserve_system_message and messages and messages[0].get("role") == "system":
            result.append(messages[0])
            remaining_messages = messages[1:]
            max_remaining = aggressive_limit - 1
        else:
            remaining_messages = messages
            max_remaining = aggressive_limit

        # Keep the most recent tool message if present (contains critical data)
        # but filter out older tool messages to reduce context size
        filtered_messages = []
        last_tool_message = None

        for msg in remaining_messages:
            role = msg.get("role", "")
            if role in ["tool", "function"]:
                # Keep track of the last tool message (most recent tool result)
                last_tool_message = msg
            elif "tool_call" not in msg:
                # Keep non-tool messages
                filtered_messages.append(msg)

        # Add the most recent tool message at the end if we had any
        if last_tool_message:
            filtered_messages.append(last_tool_message)

        # For ultra-aggressive mode, also filter out very long messages (likely
        # chunked)
        if len(messages) > 50:
            logger.info("Filtering out very large messages in ultra-aggressive mode")
            size_filtered = []
            for msg in filtered_messages:
                content_size = len(str(msg.get("content", "")))
                if content_size < 10000:  # Only keep small messages
                    size_filtered.append(msg)
                else:
                    # Keep a truncated version of large messages
                    truncated_msg = msg.copy()
                    truncated_msg["content"] = str(msg.get("content", ""))[:1000] + "...[truncated due to size]"
                    size_filtered.append(truncated_msg)
            filtered_messages = size_filtered

        # Take only the most recent messages
        if len(filtered_messages) > max_remaining:
            result.extend(filtered_messages[-max_remaining:])
        else:
            result.extend(filtered_messages)

        logger.info(f"Aggressive fallback: {len(messages)} -> {len(result)} messages")
        return result

    def _calculate_retry_delay(self, attempt: int) -> float:
        """Calculate delay for retry with exponential backoff"""

        base_delay = self.config.retry_delay

        if self.config.exponential_backoff:
            delay = base_delay * (2**attempt)
        else:
            delay = base_delay

        jitter = random.uniform(0.1, 0.3) * delay

        return min(delay + jitter, 60.0)  # Cap at 60 seconds

    def _parse_response(self, response: Dict[str, Any], model_id: str) -> Dict[str, Any]:
        """Parse and format model response"""

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
            if logger.isEnabledFor(logging.DEBUG):
                truncated_response = {}
                for key, val in response.items():
                    if isinstance(val, str) and len(val) > 300:
                        truncated_response[key] = val[:100] + f"... ({len(val):,} chars total)"
                    elif isinstance(val, dict):
                        # Handle nested dict - show structure with truncated values
                        nested_dict = {}
                        for nested_key, nested_val in val.items():
                            if isinstance(nested_val, str) and len(nested_val) > 300:
                                nested_dict[nested_key] = nested_val[:100] + f"... ({len(nested_val):,} chars total)"
                            else:
                                nested_dict[nested_key] = nested_val
                        truncated_response[key] = nested_dict
                    elif isinstance(val, list):
                        # Handle list - iterate each element
                        truncated_list = []
                        for item in val:
                            if isinstance(item, str) and len(item) > 300:
                                truncated_list.append(item[:100] + f"... ({len(item):,} chars total)")
                            elif isinstance(item, dict):
                                # Handle dict inside list
                                nested_dict = {}
                                for item_key, item_val in item.items():
                                    if isinstance(item_val, str) and len(item_val) > 300:
                                        nested_dict[item_key] = item_val[:100] + f"... ({len(item_val):,} chars total)"
                                    elif isinstance(item_val, dict):
                                        # Handle nested dict in list item (message.content case)
                                        inner_dict = {}
                                        for inner_key, inner_val in item_val.items():
                                            if isinstance(inner_val, str) and len(inner_val) > 300:
                                                inner_dict[inner_key] = (
                                                    inner_val[:100] + f"... ({len(inner_val):,} chars total)"
                                                )
                                            else:
                                                inner_dict[inner_key] = inner_val
                                        nested_dict[item_key] = inner_dict
                                    else:
                                        nested_dict[item_key] = item_val
                                truncated_list.append(nested_dict)
                            else:
                                truncated_list.append(item)
                        truncated_response[key] = truncated_list
                    else:
                        truncated_response[key] = val
                logger.debug(f"Parsing response for model {model_id}: {truncated_response}")

            if model_id.startswith("anthropic.claude") or model_id.startswith("us.anthropic.claude"):
                return self._parse_claude_response(response)
            elif model_id.startswith("amazon.titan"):
                return self._parse_titan_response(response)
            elif model_id.startswith("meta.llama") or model_id.startswith("us.meta.llama"):
                return self._parse_llama_response(response)
            elif model_id.startswith("openai.gpt-oss"):
                return self._parse_openai_gpt_response(response)
            else:
                return self._parse_generic_response(response)

        except Exception as e:
            logger.exception(f"Failed to parse response: {str(e)}")
            logger.error(f"Response content: {response}")
            return {
                "content": "I encountered an error processing the response.",
                "tool_calls": [],
                "metadata": {"error": str(e)},
            }

    def _parse_claude_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Parse Claude model response"""

        content = ""
        tool_calls = []

        # Extract content from response
        if "content" in response:
            for item in response["content"]:
                if item.get("type") == "text":
                    content += item.get("text", "")
                elif item.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "arguments": item.get("input", {}),
                        }
                    )

        return {
            "content": content,
            "tool_calls": tool_calls,
            "metadata": {
                "model": response.get("model", "unknown"),
                "usage": response.get("usage", {}),
                "stop_reason": response.get("stop_reason"),
            },
        }

    def _parse_openai_gpt_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Parse OpenAI GPT OSS model response"""

        choices = response.get("choices", [])
        if not choices:
            return {"content": "", "tool_calls": [], "metadata": {}}

        message = choices[0].get("message", {})
        content = message.get("content", "")

        # Extract tool calls
        tool_calls = []
        if "tool_calls" in message:
            for tool_call in message["tool_calls"]:
                if tool_call.get("type") == "function":
                    function = tool_call.get("function", {})
                    tool_calls.append(
                        {
                            "id": tool_call.get("id"),
                            "name": function.get("name"),
                            "arguments": json.loads(function.get("arguments", "{}")),
                        }
                    )

        return {
            "content": content,
            "tool_calls": tool_calls,
            "metadata": {
                "usage": response.get("usage", {}),
                "finish_reason": choices[0].get("finish_reason"),
            },
        }

    def _parse_titan_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Parse Titan model response"""

        results = response.get("results", [])
        content = results[0].get("outputText", "") if results else ""

        return {
            "content": content,
            "tool_calls": [],  # Titan doesn't support tool calling
            "metadata": {
                "inputTextTokenCount": response.get("inputTextTokenCount"),
                "outputTextTokenCount": results[0].get("tokenCount") if results else 0,
            },
        }

    def _parse_llama_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Parse Llama model response"""

        content = response.get("generation", "").lstrip()

        return {
            "content": content,
            "tool_calls": [],
            "metadata": {
                "generation_token_count": response.get("generation_token_count"),
                "prompt_token_count": response.get("prompt_token_count"),
                "stop_reason": response.get("stop_reason"),
            },
        }

    def _parse_generic_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Parse generic model response"""

        return {
            "content": response.get("content", response.get("text", "")),
            "tool_calls": response.get("tool_calls", []),
            "metadata": response.get("metadata", {}),
        }

    def _manage_conversation_history(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Manage conversation history to prevent context length issues

        Args:
            messages: Original conversation messages

        Returns:
            Trimmed messages that fit within context limits
        """
        if len(messages) <= self.config.max_conversation_messages:
            return messages

        logger.info(
            f"Conversation history has {len(messages)} messages, trimming to "
            f"{self.config.max_conversation_messages} using {self.config.conversation_strategy} strategy"
        )

        if self.config.conversation_strategy == "truncate":
            return self._truncate_messages(messages)
        elif self.config.conversation_strategy == "sliding_window":
            return self._sliding_window_messages(messages)
        elif self.config.conversation_strategy == "smart_prune":
            return self._smart_prune_messages(messages)
        else:
            # Default to sliding window
            return self._sliding_window_messages(messages)

    def _truncate_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Simple truncation - keep the most recent messages"""
        if self.config.preserve_system_message and messages and messages[0].get("role") == "system":
            # Keep system message + most recent messages
            system_msg = [messages[0]]
            recent_messages = messages[-(self.config.max_conversation_messages - 1) :]
            return system_msg + recent_messages
        else:
            return messages[-self.config.max_conversation_messages :]

    def _sliding_window_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sliding window - preserve system message and recent context"""
        result = []

        # Always preserve system message if present and configured
        if self.config.preserve_system_message and messages and messages[0].get("role") == "system":
            result.append(messages[0])
            remaining_messages = messages[1:]
            max_remaining = self.config.max_conversation_messages - 1
        else:
            remaining_messages = messages
            max_remaining = self.config.max_conversation_messages

        # Keep the most recent messages
        if len(remaining_messages) > max_remaining:
            result.extend(remaining_messages[-max_remaining:])
        else:
            result.extend(remaining_messages)

        return result

    def _smart_prune_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Smart pruning - remove tool messages first, then older messages"""
        result = []

        # Always preserve system message if present and configured
        if self.config.preserve_system_message and messages and messages[0].get("role") == "system":
            result.append(messages[0])
            remaining_messages = messages[1:]
            max_remaining = self.config.max_conversation_messages - 1
        else:
            remaining_messages = messages
            max_remaining = self.config.max_conversation_messages

        if len(remaining_messages) <= max_remaining:
            result.extend(remaining_messages)
            return result

        # First pass: filter out tool messages if we have too many
        non_tool_messages = []
        for msg in remaining_messages:
            role = msg.get("role", "")
            if role not in ["tool", "function"] and "tool_call" not in msg:
                non_tool_messages.append(msg)

        # If removing tool messages is enough, use that
        if len(non_tool_messages) <= max_remaining:
            result.extend(non_tool_messages)
            return result

        # Otherwise, take the most recent non-tool messages
        result.extend(non_tool_messages[-max_remaining:])
        return result

    def _check_and_chunk_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Check for large messages and chunk them if necessary
        Special handling for tool responses to prevent context overflow

        Three-tier system for tool messages:
        - Very large (>threshold): Apply intelligent truncation (keep single message)
        - Moderately large (>max_message_size): Apply chunking (split into multiple)
        - Small (<max_message_size): Keep as-is

        Args:
            messages: List of conversation messages

        Returns:
            List of messages with large messages chunked if needed
        """
        if not self.config.enable_message_chunking:
            return messages

        result = []
        total_messages = len(messages)

        for idx, msg in enumerate(messages):
            # Determine if this is the last message (most recent, likely new tool response)
            # vs. earlier messages (conversation history)
            is_last_message = idx == total_messages - 1

            # Get message content
            content = msg.get("content", "")

            # Calculate content size - handle both string and list formats
            if isinstance(content, str):
                content_size = len(content)
            elif isinstance(content, list):
                # For Claude format with list content, sum all content
                content_size = sum(len(str(item.get("content", ""))) for item in content)
            else:
                content_size = len(str(content))

            # Special handling for tool/function result messages
            # Handle both Claude format (role="user" with tool_result) and GPT format (role="tool")
            is_tool_message = False

            # Claude format: role="user" with content list containing tool_result items
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                has_tool_results = any(
                    isinstance(item, dict) and item.get("type") == "tool_result" for item in msg["content"]
                )
                if has_tool_results:
                    is_tool_message = True

            # GPT format: role="tool" with string content
            elif msg.get("role") == "tool" and isinstance(content, str):
                is_tool_message = True

            if content_size > 0:
                # For tool messages: check against intelligent truncation threshold first
                if is_tool_message:
                    is_conversation_history = not is_last_message
                    if is_conversation_history:
                        truncation_threshold = self.config.tool_result_history_threshold
                    else:
                        truncation_threshold = self.config.tool_result_new_response_threshold

                    # Check if needs intelligent truncation (very large messages)
                    if content_size > truncation_threshold:
                        processed_msg = self._process_tool_result_message(msg, is_conversation_history)
                        result.append(processed_msg)
                    # Check if needs chunking (moderately large messages)
                    elif content_size > self.config.max_message_size:
                        logger.info(
                            f"Tool message size ({content_size} chars) exceeds "
                            f"max_message_size ({self.config.max_message_size}), chunking..."
                        )
                        chunked_messages = self._chunk_large_message(msg)
                        result.extend(chunked_messages)
                    else:
                        # Under threshold: keep as-is
                        result.append(msg)
                else:
                    # For regular messages: use max_message_size threshold
                    if content_size > self.config.max_message_size:
                        logger.info(
                            f"Message size ({content_size} chars) exceeds "
                            f"max_message_size ({self.config.max_message_size}), chunking..."
                        )
                        chunked_messages = self._chunk_large_message(msg)
                        result.extend(chunked_messages)
                    else:
                        result.append(msg)
            else:
                result.append(msg)

        return result

    def _chunk_large_message(self, message: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Split a large message into smaller chunks

        Args:
            message: The message to chunk

        Returns:
            List of chunked messages with chunk information embedded in content
        """
        content = message.get("content", "")
        if not isinstance(content, str):
            return [message]  # Cannot chunk non-string content

        # Choose chunking strategy
        if self.config.chunking_strategy == "simple":
            chunks = self._simple_chunk(content)
        elif self.config.chunking_strategy == "preserve_context":
            chunks = self._context_aware_chunk(content)
        elif self.config.chunking_strategy == "semantic":
            chunks = self._semantic_chunk(content)
        else:
            chunks = self._context_aware_chunk(content)  # Default fallback

        # Create chunked messages
        chunked_messages = []
        total_chunks = len(chunks)

        for i, chunk in enumerate(chunks):
            chunk_number = i + 1

            # Add chunk context information to the content
            if total_chunks > 1:
                chunk_prefix = f"[CHUNK {chunk_number}/{total_chunks}] "
                if chunk_number == 1:
                    chunk_prefix += "This message was too large and has been split into chunks. "
                chunk_content = chunk_prefix + chunk
            else:
                chunk_content = chunk

            # Create new message with chunk (only keep standard message fields)
            chunked_msg = {
                "role": message.get("role", "user"),
                "content": chunk_content,
            }

            # Preserve any other standard message fields that might be needed
            if "name" in message:
                chunked_msg["name"] = message["name"]

            chunked_messages.append(chunked_msg)

        return chunked_messages

    def _simple_chunk(self, content: str) -> List[str]:
        """Simple character-based chunking"""
        chunks = []
        chunk_size = self.config.chunk_size
        overlap = self.config.chunk_overlap

        i = 0
        while i < len(content):
            # Determine chunk end position
            chunk_end = min(i + chunk_size, len(content))
            chunk = content[i:chunk_end]
            chunks.append(chunk)

            # Move to next chunk with overlap
            if chunk_end >= len(content):
                break
            i = chunk_end - overlap

        return chunks

    def _context_aware_chunk(self, content: str) -> List[str]:
        """Context-aware chunking that tries to break on natural boundaries"""
        chunks = []
        chunk_size = self.config.chunk_size
        overlap = self.config.chunk_overlap

        # Natural break points in order of preference
        break_patterns = ["\n\n", "\n", ". ", ", ", " "]

        i = 0
        while i < len(content):
            # Find the ideal chunk end
            ideal_end = min(i + chunk_size, len(content))

            if ideal_end >= len(content):
                # Last chunk, take everything remaining
                chunks.append(content[i:])
                break

            # Look for a good break point before the ideal end
            best_break = ideal_end
            for pattern in break_patterns:
                # Search backwards from ideal end for pattern
                search_start = max(i + chunk_size // 2, ideal_end - chunk_size // 4)
                last_occurrence = content.rfind(pattern, search_start, ideal_end)
                if last_occurrence > i:
                    best_break = last_occurrence + len(pattern)
                    break

            # Extract chunk
            chunk = content[i:best_break].strip()
            if chunk:  # Only add non-empty chunks
                chunks.append(chunk)

            # Move to next position with overlap
            i = max(best_break - overlap, i + 1)  # Ensure progress

        return chunks

    def _semantic_chunk(self, content: str) -> List[str]:
        """
        Semantic chunking that tries to preserve logical units
        Currently falls back to context-aware chunking, but could be enhanced
        with NLP libraries for more intelligent splitting
        """
        # For now, use context-aware chunking
        # In the future, this could use libraries like spacy or nltk
        # to split on sentence or paragraph boundaries more intelligently
        return self._context_aware_chunk(content)

    def _process_tool_result_message(
        self, message: Dict[str, Any], is_conversation_history: bool = False
    ) -> Dict[str, Any]:
        """
        Process tool result messages to handle oversized responses

        Supports both formats:
        - Claude: role="user", content=[{type: "tool_result", tool_use_id: ..., content: ...}]
        - GPT: role="tool", tool_call_id: ..., content="string"

        Two-tier truncation strategy:
        1. First tool response: 1M threshold → 850K target (maximize context)
        2. Conversation history: 100K threshold → 85K target (keep manageable)

        Args:
            message: Message with tool results in content
            is_conversation_history: True if processing existing conversation history,
                                     False if processing new/first tool response

        Returns:
            Processed message with truncated/summarized tool results
        """
        # Determine thresholds based on context (configurable via settings)
        if is_conversation_history:
            # For conversation history: aggressive truncation to keep context manageable
            large_threshold = self.config.tool_result_history_threshold
            target_size = self.config.tool_result_history_target
            context_label = "conversation history"
        else:
            # For first/new tool response: generous limit to maximize initial context
            large_threshold = self.config.tool_result_new_response_threshold
            target_size = self.config.tool_result_new_response_target
            context_label = "new tool response"

        content = message.get("content", "")

        # GPT format: role="tool" with string content
        if message.get("role") == "tool" and isinstance(content, str):
            content_size = len(content)

            if content_size > large_threshold:
                logger.warning(
                    f"Tool result in {context_label} is very large ({content_size:,} chars), "
                    f"truncating to ~{target_size:,} chars (threshold: {large_threshold:,})..."
                )

                try:
                    # Apply intelligent truncation
                    truncated_content = self._intelligently_truncate_tool_result(
                        content, message.get("tool_call_id", "unknown"), max_size=target_size
                    )

                    # Return message with truncated content
                    return {**message, "content": truncated_content}

                except Exception as e:
                    # Fallback: simple truncation if intelligent truncation fails
                    logger.error(f"Error truncating tool result: {e}")
                    logger.error("Falling back to simple truncation")

                    simple_truncated = (
                        content[:target_size] + f"\n\n[TRUNCATED - Original size: {content_size:,} chars]"
                    )

                    return {**message, "content": simple_truncated}
            else:
                # Small result, return as-is
                return message

        # Claude format: role="user" with content list containing tool_result items
        elif isinstance(content, list):
            processed_content = []

            for item in content:
                if not isinstance(item, dict) or item.get("type") != "tool_result":
                    # Keep non-tool-result items as-is
                    processed_content.append(item)
                    continue

                # Get tool result content and metadata
                tool_result_content = item.get("content", "")
                tool_use_id = item.get("tool_use_id", "")

                # Calculate size (only convert to string once)
                content_str = str(tool_result_content)
                content_size = len(content_str)

                if content_size > large_threshold:
                    logger.warning(
                        f"Tool result {tool_use_id} in {context_label} is very large ({content_size:,} chars), "
                        f"truncating to ~{target_size:,} chars (threshold: {large_threshold:,})..."
                    )

                    try:
                        # Apply intelligent truncation
                        truncated_content = self._intelligently_truncate_tool_result(
                            tool_result_content, tool_use_id, max_size=target_size
                        )

                        # Preserve all original fields, just update content
                        truncated_item = item.copy()
                        truncated_item["content"] = truncated_content
                        processed_content.append(truncated_item)

                    except Exception as e:
                        # Fallback: simple truncation if intelligent truncation fails
                        logger.error(f"Error truncating tool result {tool_use_id}: {e}")
                        logger.error("Falling back to simple truncation")

                        simple_truncated = (
                            content_str[:target_size] + f"\n\n[TRUNCATED - Original size: {content_size:,} chars]"
                        )

                        fallback_item = item.copy()
                        fallback_item["content"] = simple_truncated
                        processed_content.append(fallback_item)
                else:
                    # Keep small results as-is
                    processed_content.append(item)

            # Return message with processed content
            return {"role": message.get("role"), "content": processed_content}

        # Unknown format, return as-is
        return message

    def _intelligently_truncate_tool_result(self, content: Any, tool_id: str, max_size: int = 50_000) -> str:
        """
        Intelligently truncate large tool results while preserving context

        Strategies:
        1. If JSON object: Try to truncate long strings first
        2. If still too long: Try to reduce the number of items in arrays with summary
        3. If text: Show beginning + end with summary

        Args:
            content: Tool result content (can be dict, list, or string)
            tool_id: Tool use ID for logging
            max_size: Maximum size in characters for truncated result

        Returns:
            Intelligently truncated string representation
        """
        content_str = str(content)
        original_size = len(content_str)

        # Try to parse as JSON for smart truncation
        try:
            if isinstance(content, str):
                parsed = json.loads(content)
            else:
                parsed = content

            # Handle JSON array (most common for list endpoints)
            if isinstance(parsed, list):
                return self._truncate_json_array(parsed, tool_id, max_size, original_size)

            # Handle JSON object
            elif isinstance(parsed, dict):
                return self._truncate_json_object(parsed, tool_id, max_size, original_size)

        except (json.JSONDecodeError, TypeError):
            # Not JSON, treat as plain text
            pass

        # Fallback: Simple text truncation with context
        return self._truncate_plain_text(content_str, tool_id, max_size, original_size)

    def _calculate_max_items_to_show(self, total_items: int, max_size: int, item_sample_size: int = 100) -> int:
        """
        Calculate how many items to show based on size constraints

        Args:
            total_items: Total number of items in collection
            max_size: Maximum size in characters for output
            item_sample_size: Estimated size per item in characters

        Returns:
            Number of items to show (minimum 1, maximum 20)
        """
        # Start with a reasonable sample size
        base_items = min(10, total_items)

        # Estimate if we can show more based on max_size
        # Leave 20% buffer for formatting, summaries, etc.
        available_size = int(max_size * 0.8)
        estimated_items = max(1, available_size // item_sample_size)

        # Cap at 20 items to keep output manageable
        return min(base_items, estimated_items, 20)

    def _truncate_json_array(self, data: List[Any], tool_id: str, max_size: int, original_size: int) -> str:
        """
        Truncate JSON array intelligently

        Strategy: Start with all items and iteratively remove until target size is reached
        """
        total_items = len(data)

        if total_items == 0:
            return json.dumps(data)

        # Start with all items and work backwards
        items_to_show = total_items
        preview_items = data[:items_to_show]
        preview_str = json.dumps(preview_items)

        # Target size with some buffer for summary text (75% of max_size)
        target_size = int(max_size * 0.75)

        # If we're already under target, great!
        if len(preview_str) <= target_size:
            items_to_show = total_items
            preview_items = data
            preview_str = json.dumps(preview_items)
        else:
            # Binary search for optimal item count
            left = 1
            right = total_items
            best_count = 1
            best_preview = json.dumps(data[:1])

            while left <= right:
                mid = (left + right) // 2
                preview_items = data[:mid]
                preview_str = json.dumps(preview_items)

                if len(preview_str) <= target_size:
                    # This fits, try to get more
                    best_count = mid
                    best_preview = preview_str
                    left = mid + 1
                else:
                    # Too large, try fewer items
                    right = mid - 1

            items_to_show = best_count
            preview_str = best_preview

        # Build summary
        summary = self._build_array_summary(data, items_to_show, total_items)

        # Combine preview + summary
        result = (
            f"[TOOL RESULT TRUNCATED - Original size: {original_size:,} chars]\n"
            f"{summary}\n\n"
            f"SHOWING FIRST {items_to_show} OF {total_items} ITEMS:\n"
            f"{preview_str}\n\n"
            f"... ({total_items - items_to_show} more items not shown)\n\n"
            f"RECOMMENDATION: Use pagination or filtering in your API call to get specific items.\n"
            f"Example: Add 'limit' and 'offset' parameters, or filter by specific criteria."
        )

        return result

    def _build_array_summary(self, data: List[Any], shown: int, total: int) -> str:
        """Build summary statistics for JSON array"""
        try:
            # Analyze array structure
            if not data:
                return "Empty array"

            first_item = data[0]

            if isinstance(first_item, dict):
                # Extract field names
                fields = list(first_item.keys())
                return (
                    f"SUMMARY: Array of {total} objects\n"
                    f"Fields per object: {', '.join(fields[:10])}"
                    f"{' ...' if len(fields) > 10 else ''}"
                )
            else:
                item_type = type(first_item).__name__
                return f"SUMMARY: Array of {total} {item_type} values"

        except Exception:
            return f"SUMMARY: Array of {total} items"

    def _truncate_json_object_recursively(self, data: Dict[str, Any], max_size: int) -> Dict[str, Any]:
        """
        Recursively truncate JSON object values

        Args:
            data: JSON object to truncate
            max_size: Maximum size per element

        Returns:
            Truncated JSON object (new copy, doesn't mutate input)
        """
        if not data:
            return data

        truncated = {}
        # Prevent division by zero
        max_size_per_element = max_size / max(1, len(data))

        # Calculate how many keys to show using common function
        total_keys = len(data)
        max_items = self._calculate_max_items_to_show(total_keys, max_size)

        # Limit number of keys shown
        keys_to_show = list(data.keys())[:max_items]

        for key in keys_to_show:
            value = data[key]

            if isinstance(value, dict):
                truncated[key] = self._truncate_json_object_recursively(value, max_size_per_element)
            elif isinstance(value, list):
                # Calculate max items for this list
                list_max_items = self._calculate_max_items_to_show(len(value), max_size_per_element)

                # Truncate arrays to max_items
                truncated_list = []
                for item in value[:list_max_items]:
                    if isinstance(item, dict):
                        truncated_list.append(self._truncate_json_object_recursively(item, max_size_per_element))
                    else:
                        item_str = str(item)
                        if len(item_str) > max_size_per_element:
                            truncated_list.append(item_str[: int(max_size_per_element)] + "...")
                        else:
                            truncated_list.append(item)

                # Add indicator if array was truncated
                if len(value) > list_max_items:
                    truncated[key] = truncated_list + [f"... ({len(value) - list_max_items} more items)"]
                else:
                    truncated[key] = truncated_list
            else:
                value_str = str(value)
                if len(value_str) > max_size_per_element:
                    truncated[key] = value_str[: int(max_size_per_element)] + "..."
                else:
                    truncated[key] = value

        # Add indicator if dict was truncated
        if len(data) > max_items:
            truncated["..."] = f"({len(data) - max_items} more fields)"

        return truncated

    def _truncate_json_object(self, data: Dict[str, Any], tool_id: str, max_size: int, original_size: int) -> str:
        """
        Truncate JSON object intelligently

        Strategy: Detect if object is a wrapper around array data and handle accordingly
        """
        # Check if this is a wrapper object with a large array field
        # Common patterns: {"items": [...], "count": N}, {"results": [...], "total": N}, etc.
        array_field = None
        array_data = None
        max_array_size = 0

        for key, value in data.items():
            if isinstance(value, list):
                value_str = json.dumps(value)
                value_size = len(value_str)
                if value_size > max_array_size:
                    max_array_size = value_size
                    array_field = key
                    array_data = value

        # If we found a large array that dominates the object (>80% of content),
        # treat it specially and give it most of the budget
        if array_field and max_array_size > original_size * 0.8:
            # Give 90% of budget to the array, 10% for metadata
            array_budget = int(max_size * 0.9)

            # Truncate the array
            array_result = self._truncate_json_array(array_data, tool_id, array_budget, max_array_size)

            # Build metadata summary (other fields)
            metadata = {k: v for k, v in data.items() if k != array_field}
            metadata_str = json.dumps(metadata) if metadata else "{}"

            result = (
                f"[TOOL RESULT TRUNCATED - Original size: {original_size:,} chars]\n\n"
                f"SUMMARY: Object with main array field '{array_field}' ({len(array_data)} items) plus {len(metadata)} metadata fields\n\n"
                f"METADATA FIELDS:\n"
                f"{metadata_str}\n\n"
                f"ARRAY DATA:\n"
                f"{array_result}"
            )
        else:
            # Standard object truncation for balanced objects
            content_max_size = int(max_size * 0.8)
            preview = self._truncate_json_object_recursively(data, content_max_size)

            preview_str = json.dumps(preview)

            result = (
                f"[TOOL RESULT TRUNCATED - Original size: {original_size:,} chars]\n\n"
                f"SUMMARY: Object with {len(data)} top-level fields\n\n"
                f"STRUCTURE PREVIEW:\n"
                f"{preview_str}\n\n"
                f"RECOMMENDATION: Request specific fields or use a more targeted API endpoint."
            )

        return result

    def _truncate_plain_text(self, text: str, tool_id: str, max_size: int, original_size: int) -> str:
        """
        Truncate plain text with beginning + end preview

        Strategy: Show first and last portions with summary
        """
        if len(text) <= max_size:
            return text

        # Show first 40% and last 10%
        head_size = int(max_size * 0.4)
        tail_size = int(max_size * 0.1)

        head = text[:head_size]
        tail = text[-tail_size:] if tail_size > 0 else ""

        # Count lines for summary
        total_lines = text.count("\n") + 1

        result = (
            f"[TOOL RESULT TRUNCATED - Original size: {original_size:,} chars, {total_lines:,} lines]\n\n"
            f"BEGINNING:\n"
            f"{head}\n\n"
            f"... ({original_size - head_size - tail_size:,} chars omitted) ...\n\n"
            f"ENDING:\n"
            f"{tail}\n\n"
            f"RECOMMENDATION: Use filtering or pagination to get specific data."
        )

        return result

    async def _execute_tool_calls(
        self, tool_calls: List[Dict[str, Any]], tools_desc: Optional[Dict]
    ) -> List[Dict[str, Any]]:
        """Execute tool calls (API endpoints)"""

        # This will be implemented by the WebSocket handler
        # that has access to the FastAPI app and can make HTTP calls
        # For now, return placeholder

        results = []
        for tool_call in tool_calls:
            results.append(
                {
                    "tool_call_id": tool_call.get("id"),
                    "name": tool_call.get("name"),
                    "result": "Tool execution will be handled by WebSocket handler",
                }
            )

        return results

    def _create_error_response(self, error_message: str) -> Dict[str, Any]:
        """Create error response for graceful degradation"""

        return {
            "content": f"I'm experiencing technical difficulties: {error_message}. Please try again in a moment.",
            "tool_calls": [],
            "metadata": {"error": True, "error_message": error_message},
        }

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
