"""Claude model parser"""

import json
import logging
from typing import Any, Dict, List, Optional

from .base import Parser

logger = logging.getLogger(__name__)


class ClaudeParser(Parser):
    """Parser for Anthropic Claude models"""

    def parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse Claude model response

        Args:
            response: Raw response from Claude API

        Returns:
            Dict with keys: content, tool_calls, metadata
        """
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

    def format_bedrock_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Format messages for Claude/Bedrock API.
        Converts ChatMessage objects to proper Bedrock format, handling tool_use blocks.

        Args:
            messages: List of message dicts (from session/context)

        Returns:
            List of messages formatted for Bedrock API
        """
        bedrock_messages = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls", [])
            tool_results = msg.get("tool_results", [])

            if role in ["user", "system"]:
                # Simple messages: just role + content
                bedrock_messages.append({"role": role, "content": content})

            elif role == "assistant":
                # Assistant messages may contain tool_use blocks
                if tool_calls:
                    # Build content array with text + tool_use blocks
                    content_array = []

                    # Add text content if present
                    if content and isinstance(content, str) and content.strip():
                        content_array.append({"type": "text", "text": content})

                    # Add tool_use blocks from tool_calls
                    for tool_call in tool_calls:
                        content_array.append({
                            "type": "tool_use",
                            "id": tool_call.get("id"),
                            "name": tool_call.get("name"),
                            "input": tool_call.get("arguments", {}),
                        })

                    bedrock_messages.append({"role": "assistant", "content": content_array})
                else:
                    # No tool calls, just add as-is
                    bedrock_messages.append({"role": "assistant", "content": content})

            elif role == "tool" and tool_results:
                # Tool result messages: add user message with tool_result blocks
                tool_result_content = []
                for i, tool_result in enumerate(tool_results):
                    tool_call_id = tool_calls[i].get("id") if i < len(tool_calls) else f"tool_call_{i}"

                    if "error" in tool_result:
                        tool_result_content.append({
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": f"Error: {tool_result['error']}",
                        })
                    else:
                        result_text = str(tool_result.get("result", "No result"))
                        tool_result_content.append({
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": result_text,
                        })

                if tool_result_content:
                    bedrock_messages.append({"role": "user", "content": tool_result_content})

        return bedrock_messages

    def format_messages(
        self,
        messages: List[Dict[str, Any]],
        tools_desc: Optional[Dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Format messages for Claude API

        Args:
            messages: List of conversation messages
            tools_desc: Tool/function descriptions (optional)
            temperature: Sampling temperature (optional)
            max_tokens: Maximum tokens in response (optional)
            **kwargs: Additional parameters

        Returns:
            Request body dict for Claude API
        """
        # Extract system prompt from messages if present
        system_prompt = None
        conversation_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                # Sanitize non-system messages
                conversation_messages.append(self._sanitize_message(msg))

        # Use default system prompt if none provided
        if not system_prompt and self.config:
            system_prompt = self.config.get_system_prompt()

        # Sanitize system prompt
        system_prompt = self._sanitize_message_content(system_prompt) if system_prompt else ""

        # Use provided parameters or fall back to config
        temp = temperature if temperature is not None else (self.config.temperature if self.config else 0.7)
        tokens = max_tokens if max_tokens is not None else (self.config.max_tokens if self.config else 4096)

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": tokens,
            "temperature": temp,
            "messages": conversation_messages,
            "system": system_prompt,
        }

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
