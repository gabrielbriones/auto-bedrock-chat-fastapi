"""GPT OSS model parser with emoji sanitization"""

import json
import logging
import unicodedata
from typing import Any, Dict, List, Optional

from .base import Parser

logger = logging.getLogger(__name__)


class GPTParser(Parser):
    """Parser for OpenAI GPT OSS models with emoji sanitization"""

    def parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse OpenAI GPT OSS model response

        Args:
            response: Raw response from GPT OSS API

        Returns:
            Dict with keys: content, tool_calls, metadata
        """
        choices = response.get("choices", [])
        if not choices:
            return {"content": "", "tool_calls": [], "metadata": {}}

        message = choices[0].get("message", {})
        content = message.get("content") or ""  # Handle None content gracefully

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

    def format_bedrock_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Format messages for GPT/OpenAI API format.
        Handles tool_calls as separate tool messages instead of tool_use blocks.

        Args:
            messages: List of message dicts (from session/context)

        Returns:
            List of messages formatted for OpenAI/GPT API
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
                # Assistant messages in OpenAI format
                assistant_msg = {"role": "assistant", "content": content}

                # If there are tool calls, add them in OpenAI format
                if tool_calls:
                    gpt_tool_calls = []
                    for tool_call in tool_calls:
                        gpt_tool_calls.append(
                            {
                                "id": tool_call.get("id"),
                                "type": "function",
                                "function": {
                                    "name": tool_call.get("name"),
                                    "arguments": json.dumps(tool_call.get("arguments", {})),
                                },
                            }
                        )

                    if gpt_tool_calls:
                        assistant_msg["tool_calls"] = gpt_tool_calls

                bedrock_messages.append(assistant_msg)

            elif role == "tool" and tool_results:
                # Add individual tool result messages (OpenAI format)
                for i, tool_result in enumerate(tool_results):
                    tool_call_id = tool_calls[i].get("id") if i < len(tool_calls) else f"tool_call_{i}"

                    if "error" in tool_result:
                        result_content = f"Error: {tool_result['error']}"
                    else:
                        result_content = str(tool_result.get("result", "No result"))

                    bedrock_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": result_content,
                        }
                    )

        return bedrock_messages

    def format_messages(
        self,
        messages: List[Dict[str, Any]],
        tools_desc: Optional[Dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Format messages for GPT OSS API with emoji sanitization

        Args:
            messages: List of conversation messages
            tools_desc: Tool/function descriptions (optional)
            temperature: Sampling temperature (optional)
            max_tokens: Maximum tokens in response (optional)
            **kwargs: Additional parameters

        Returns:
            Request body dict for GPT OSS API
        """
        # For OpenAI format, messages can already include system message
        # If no system message is present, add the default one
        has_system_message = any(msg.get("role") == "system" for msg in messages)

        formatted_messages = []

        if not has_system_message:
            # Add default system message if none present
            system_prompt = self.config.get_system_prompt() if self.config else "You are a helpful assistant."
            sanitized_system = self._sanitize_message_content(system_prompt)
            formatted_messages.append({"role": "system", "content": sanitized_system})

        # Add all conversation messages with sanitization
        for msg in messages:
            sanitized_msg = self._sanitize_message(msg)
            formatted_messages.append(sanitized_msg)

        # Use provided max_tokens or fall back to config
        adjusted_max_tokens = (
            max_tokens if max_tokens is not None else (self.config.max_tokens if self.config else 4096)
        )

        request_body = {
            "messages": formatted_messages,
            "max_tokens": adjusted_max_tokens,
            "temperature": (
                temperature if temperature is not None else (self.config.temperature if self.config else 0.7)
            ),
            "top_p": kwargs.get("top_p") or (getattr(self.config, "top_p", 0.9) if self.config else 0.9),
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

    def _sanitize_text(self, text: str) -> str:
        """
        Sanitize text for GPT models to avoid tokenization issues.
        Removes problematic Unicode characters including emojis.

        Args:
            text: Text to sanitize

        Returns:
            Sanitized text
        """
        if not isinstance(text, str):
            text = str(text)

        # First normalize to NFC to combine characters consistently
        text = unicodedata.normalize("NFC", text)

        # Replace problematic Unicode characters
        replacements = {
            "\u202f": " ",  # Narrow no-break space → regular space
            "\u00a0": " ",  # Non-breaking space → regular space
            "\u2009": " ",  # Thin space → regular space
            "\u200b": "",  # Zero-width space → remove
            "\u200c": "",  # Zero-width non-joiner → remove
            "\u200d": "",  # Zero-width joiner → remove
            "\ufeff": "",  # Zero-width no-break space (BOM) → remove
            "\u2060": "",  # Word joiner → remove
            "\u2061": "",  # Function application → remove
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        # Remove control characters except common ones (newline, tab, carriage return)
        text = "".join(char for char in text if unicodedata.category(char)[0] != "C" or char in "\n\t\r")

        # Remove emojis and other symbols that cause GPT tokenization issues
        text = "".join(
            char
            for char in text
            if not (
                # Emoji ranges (common emoji zones)
                ("\U0001F300" <= char <= "\U0001F9FF")  # Emoticons, symbols, pictographs
                or ("\U0001F600" <= char <= "\U0001F64F")  # Emoticons
                or ("\U0001F900" <= char <= "\U0001F9FF")  # Supplemental Symbols and Pictographs
                or ("\U0001F1E6" <= char <= "\U0001F1FF")  # Flags
                or ord(char) >= 0x1F300  # All high Unicode ranges with emoji/symbols
            )
        )

        return text
