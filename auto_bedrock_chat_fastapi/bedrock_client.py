"""Bedrock client for AI model interaction"""

import asyncio
import json
import logging
import time
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

    def _prepare_openai_gpt_request(self, messages, tools_desc, temperature, max_tokens, **kwargs) -> Dict[str, Any]:
        """Prepare request for OpenAI GPT OSS models"""

        # For OpenAI format, messages can already include system message
        # If no system message is present, add the default one
        has_system_message = any(msg.get("role") == "system" for msg in messages)

        formatted_messages = []

        if not has_system_message:
            # Add default system message if none present
            formatted_messages.append({"role": "system", "content": self.config.get_system_prompt()})

        # Add all conversation messages
        formatted_messages.extend(messages)

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
            logger.info(f"Input approaching limit, adjusting max_tokens from " f"{max_tokens} to {adjusted_max_tokens}")
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

    async def _make_request_with_retries(self, model_id: str, request_body: Dict[str, Any]) -> Dict[str, Any]:
        """Make Bedrock API request with retry logic"""

        last_exception = None

        for attempt in range(self.config.max_retries + 1):
            try:
                # Convert to async
                # Debug logging for request
                logger.debug(f"Bedrock request for {model_id}: {json.dumps(request_body, indent=2)}")

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

        # Filter out tool messages completely for context window issues
        filtered_messages = []
        for msg in remaining_messages:
            role = msg.get("role", "")
            if role not in ["tool", "function"] and "tool_call" not in msg:
                filtered_messages.append(msg)

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

        # Add jitter to prevent thundering herd
        import random

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
            logger.debug(f"Parsing response for model {model_id}: {response}")

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

        content = response.get("generation", "")

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

        Args:
            messages: List of conversation messages

        Returns:
            List of messages with large messages chunked if needed
        """
        if not self.config.enable_message_chunking:
            return messages

        result = []

        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > self.config.max_message_size:
                logger.info(
                    f"Message size ({len(content)} chars) exceeds "
                    f"max_message_size ({self.config.max_message_size}), chunking..."
                )
                chunked_messages = self._chunk_large_message(msg)
                result.extend(chunked_messages)
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
