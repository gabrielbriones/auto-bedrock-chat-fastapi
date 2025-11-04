"""Bedrock client for AI model interaction"""

import boto3
import json
import asyncio
import logging
from typing import Dict, List, Any, Optional, Union
from botocore.exceptions import BotoCoreError, ClientError
from datetime import datetime
import time

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
            
            # Create Bedrock client
            self._client = self._session.client(
                'bedrock-runtime',
                region_name=self.config.aws_region
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
        **kwargs
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
            
            # Prepare the request based on model family
            request_body = self._prepare_request_body(
                messages=messages,
                model_id=model_id,
                tools_desc=tools_desc,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )
            
            # Make the API call with retries
            response = await self._make_request_with_retries(model_id, request_body)
            
            # Parse and format the response
            formatted_response = self._parse_response(response, model_id)
            
            # Process any tool calls
            if formatted_response.get("tool_calls"):
                tool_results = await self._execute_tool_calls(
                    formatted_response["tool_calls"],
                    tools_desc
                )
                formatted_response["tool_results"] = tool_results
            
            return formatted_response
            
        except Exception as e:
            logger.error(f"Chat completion error: {str(e)}")
            
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
                        **kwargs
                    )
                except Exception as fallback_error:
                    logger.error(f"Fallback model also failed: {str(fallback_error)}")
            
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
        **kwargs
    ) -> Dict[str, Any]:
        """Prepare request body based on model family"""
        
        if model_id.startswith("anthropic.claude") or model_id.startswith("us.anthropic.claude"):
            return self._prepare_claude_request(
                messages, tools_desc, temperature, max_tokens, **kwargs
            )
        elif model_id.startswith("amazon.titan"):
            return self._prepare_titan_request(
                messages, tools_desc, temperature, max_tokens, **kwargs
            )
        elif model_id.startswith("meta.llama"):
            return self._prepare_llama_request(
                messages, tools_desc, temperature, max_tokens, **kwargs
            )
        elif model_id.startswith("openai.gpt-oss"):
            return self._prepare_openai_gpt_request(
                messages, tools_desc, temperature, max_tokens, **kwargs
            )
        else:
            # Generic format
            return self._prepare_generic_request(
                messages, tools_desc, temperature, max_tokens, **kwargs
            )
    
    def _prepare_claude_request(
        self, messages, tools_desc, temperature, max_tokens, **kwargs
    ) -> Dict[str, Any]:
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
            "system": system_prompt
        }
        
        # Note: For newer Claude models, we only use temperature, not top_p
        # to avoid "temperature and top_p cannot both be specified" error
        # If you need top_p instead of temperature, modify the config accordingly
        
        # Add tools if available
        if tools_desc and tools_desc.get("functions"):
            request_body["tools"] = [
                {
                    "name": func["name"],
                    "description": func["description"],
                    "input_schema": func["parameters"]
                }
                for func in tools_desc["functions"]
            ]
        
        return request_body
    
    def _prepare_openai_gpt_request(
        self, messages, tools_desc, temperature, max_tokens, **kwargs
    ) -> Dict[str, Any]:
        """Prepare request for OpenAI GPT OSS models"""
        
        # For OpenAI format, messages can already include system message
        # If no system message is present, add the default one
        has_system_message = any(msg.get("role") == "system" for msg in messages)
        
        formatted_messages = []
        
        if not has_system_message:
            # Add default system message if none present
            formatted_messages.append({
                "role": "system",
                "content": self.config.get_system_prompt()
            })
        
        # Add all conversation messages
        formatted_messages.extend(messages)
        
        request_body = {
            "messages": formatted_messages,
            "max_tokens": max_tokens,
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
                        "parameters": func["parameters"]
                    }
                }
                for func in tools_desc["functions"]
            ]
        
        return request_body
    
    def _prepare_titan_request(
        self, messages, tools_desc, temperature, max_tokens, **kwargs
    ) -> Dict[str, Any]:
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
        
        # Combine messages into a single prompt for Titan
        prompt = system_prompt + "\n\n" if system_prompt else ""
        
        for msg in conversation_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt += f"{role.title()}: {content}\n"
        
        prompt += "Assistant:"
        
        return {
            "inputText": prompt,
            "textGenerationConfig": {
                "maxTokenCount": max_tokens,
                "temperature": temperature,
                "topP": kwargs.get("top_p", self.config.top_p),
                "stopSequences": ["User:", "Human:"]
            }
        }
    
    def _prepare_llama_request(
        self, messages, tools_desc, temperature, max_tokens, **kwargs
    ) -> Dict[str, Any]:
        """Prepare request for Llama models"""
        
        # For Llama, check if system message is already in messages
        has_system_message = any(msg.get("role") == "system" for msg in messages)
        
        formatted_messages = []
        
        if not has_system_message:
            # Add default system message if none present
            formatted_messages.append({
                "role": "system",
                "content": self.config.get_system_prompt()
            })
        
        formatted_messages.extend(messages)
        
        return {
            "messages": formatted_messages,
            "max_gen_len": max_tokens,
            "temperature": temperature,
            "top_p": kwargs.get("top_p", self.config.top_p),
        }
    
    def _prepare_generic_request(
        self, messages, tools_desc, temperature, max_tokens, **kwargs
    ) -> Dict[str, Any]:
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
            **kwargs
        }
    
    async def _make_request_with_retries(
        self, model_id: str, request_body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Make Bedrock API request with retry logic"""
        
        last_exception = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                # Convert to async
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._client.invoke_model(
                        modelId=model_id,
                        body=json.dumps(request_body),
                        contentType="application/json",
                        accept="application/json"
                    )
                )
                
                # Parse response
                response_body = json.loads(response['body'].read())
                
                # Update request tracking
                self._last_request_time = time.time()
                self._request_count += 1
                
                return response_body
                
            except (BotoCoreError, ClientError) as e:
                last_exception = e
                error_code = getattr(e, 'response', {}).get('Error', {}).get('Code', '')
                
                # Don't retry on certain errors
                if error_code in ['ValidationException', 'AccessDeniedException']:
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
                # Don't retry on unexpected errors
                break
        
        raise BedrockClientError(f"Request failed after {self.config.max_retries + 1} attempts: {str(last_exception)}")
    
    def _calculate_retry_delay(self, attempt: int) -> float:
        """Calculate delay for retry with exponential backoff"""
        
        base_delay = self.config.retry_delay
        
        if self.config.exponential_backoff:
            delay = base_delay * (2 ** attempt)
        else:
            delay = base_delay
        
        # Add jitter to prevent thundering herd
        import random
        jitter = random.uniform(0.1, 0.3) * delay
        
        return min(delay + jitter, 60.0)  # Cap at 60 seconds
    
    def _parse_response(self, response: Dict[str, Any], model_id: str) -> Dict[str, Any]:
        """Parse and format model response"""
        
        try:
            if model_id.startswith("anthropic.claude") or model_id.startswith("us.anthropic.claude"):
                return self._parse_claude_response(response)
            elif model_id.startswith("amazon.titan"):
                return self._parse_titan_response(response)
            elif model_id.startswith("meta.llama"):
                return self._parse_llama_response(response)
            elif model_id.startswith("openai.gpt-oss"):
                return self._parse_openai_gpt_response(response)
            else:
                return self._parse_generic_response(response)
                
        except Exception as e:
            logger.error(f"Failed to parse response: {str(e)}")
            return {
                "content": "I encountered an error processing the response.",
                "tool_calls": [],
                "metadata": {"error": str(e)}
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
                    tool_calls.append({
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "arguments": item.get("input", {})
                    })
        
        return {
            "content": content,
            "tool_calls": tool_calls,
            "metadata": {
                "model": response.get("model", "unknown"),
                "usage": response.get("usage", {}),
                "stop_reason": response.get("stop_reason")
            }
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
                    tool_calls.append({
                        "id": tool_call.get("id"),
                        "name": function.get("name"),
                        "arguments": json.loads(function.get("arguments", "{}"))
                    })
        
        return {
            "content": content,
            "tool_calls": tool_calls,
            "metadata": {
                "usage": response.get("usage", {}),
                "finish_reason": choices[0].get("finish_reason")
            }
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
                "outputTextTokenCount": results[0].get("tokenCount") if results else 0
            }
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
                "stop_reason": response.get("stop_reason")
            }
        }
    
    def _parse_generic_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Parse generic model response"""
        
        return {
            "content": response.get("content", response.get("text", "")),
            "tool_calls": response.get("tool_calls", []),
            "metadata": response.get("metadata", {})
        }
    
    async def _execute_tool_calls(
        self, tool_calls: List[Dict[str, Any]], tools_desc: Optional[Dict]
    ) -> List[Dict[str, Any]]:
        """Execute tool calls (API endpoints)"""
        
        # This will be implemented by the WebSocket handler
        # that has access to the FastAPI app and can make HTTP calls
        # For now, return placeholder
        
        results = []
        for tool_call in tool_calls:
            results.append({
                "tool_call_id": tool_call.get("id"),
                "name": tool_call.get("name"),
                "result": "Tool execution will be handled by WebSocket handler"
            })
        
        return results
    
    def _create_error_response(self, error_message: str) -> Dict[str, Any]:
        """Create error response for graceful degradation"""
        
        return {
            "content": f"I'm experiencing technical difficulties: {error_message}. Please try again in a moment.",
            "tool_calls": [],
            "metadata": {"error": True, "error_message": error_message}
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
            
            response = await self.chat_completion(
                messages=test_messages,
                max_tokens=10,
                temperature=0.1
            )
            
            return {
                "status": "healthy",
                "model": self.config.model_id,
                "region": self.config.aws_region,
                "response_received": bool(response.get("content"))
            }
            
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "model": self.config.model_id,
                "region": self.config.aws_region
            }