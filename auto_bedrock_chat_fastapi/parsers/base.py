"""Base Parser class that all model parsers inherit from"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Parser(ABC):
    """Abstract base class for response parsers and message formatters"""

    def __init__(self, config: Any = None):
        """
        Initialize parser

        Args:
            config: ChatConfig instance with parser configuration
        """
        self.config = config

    @abstractmethod
    def parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse model response and extract content and tool calls

        Args:
            response: Raw response from the model

        Returns:
            Dict with keys: content, tool_calls, metadata
        """
        pass

    @abstractmethod
    def format_messages(
        self,
        messages: List[Dict[str, Any]],
        tools_desc: Optional[Dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Format messages for the model's API

        Args:
            messages: List of conversation messages
            tools_desc: Tool/function descriptions (optional)
            temperature: Sampling temperature (optional, uses config if not provided)
            max_tokens: Maximum tokens in response (optional, uses config if not provided)
            **kwargs: Additional model-specific parameters

        Returns:
            Request body dict formatted for the specific model
        """
        pass

    def format_bedrock_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Format conversation messages for Bedrock API format.
        Handles model-specific message structure (e.g., Claude's tool_use blocks).

        Can be overridden by subclasses for model-specific formatting.

        Args:
            messages: List of ChatMessage objects (typically from session manager)

        Returns:
            List of messages formatted for Bedrock API
        """
        # Default implementation: just return messages as-is
        # Subclasses should override for model-specific formatting
        return messages

    def truncate_tool_results(self, tool_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Truncate tool results to prevent context overflow.
        Uses config threshold and distributes budget proportionally across results.

        Can be overridden by subclasses for model-specific truncation logic.

        Args:
            tool_results: List of tool execution results

        Returns:
            Truncated tool results
        """
        if not self.config or not hasattr(self.config, "tool_result_new_response_threshold"):
            return tool_results

        # Calculate total size
        total_size = sum(len(str(r.get("result", ""))) for r in tool_results if "error" not in r)
        threshold = self.config.tool_result_new_response_threshold

        if total_size <= threshold:
            # Under threshold, return as-is
            return tool_results

        # Need to truncate - distribute budget proportionally
        num_results = len(tool_results)
        per_result_budget = threshold // num_results if num_results > 0 else threshold

        logger.info(
            f"Tool results group size ({total_size:,}) exceeds threshold ({threshold:,}). "
            f"Distributing {threshold:,} chars across {num_results} results (~{per_result_budget:,} each)"
        )

        truncated_results = []
        for tool_result in tool_results:
            if "error" in tool_result:
                # Keep errors as-is
                truncated_results.append(tool_result)
            else:
                result_value = tool_result.get("result", "No result")
                result_str = str(result_value)

                if len(result_str) > per_result_budget:
                    truncated = result_str[:per_result_budget]
                    truncated += f"\n\n[... truncated from {len(result_str):,} chars total ...]"
                    truncated_results.append({**tool_result, "result": truncated})
                else:
                    truncated_results.append(tool_result)

        return truncated_results

    def _sanitize_text(self, text: str) -> str:
        """
        Base sanitization method. Can be overridden by subclasses.
        By default, returns text as-is.

        Args:
            text: Text to sanitize

        Returns:
            Sanitized text
        """
        return text

    def _sanitize_message_content(self, content: Any) -> Any:
        """
        Sanitize message content (handles string, dict, and list formats)

        Args:
            content: Message content to sanitize

        Returns:
            Sanitized content
        """
        if isinstance(content, str):
            return self._sanitize_text(content)
        elif isinstance(content, dict):
            return {k: self._sanitize_message_content(v) for k, v in content.items()}
        elif isinstance(content, list):
            return [self._sanitize_message_content(item) for item in content]
        else:
            return content

    def _sanitize_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize a complete message object

        Args:
            msg: Message to sanitize

        Returns:
            Sanitized message
        """
        if not isinstance(msg, dict):
            return msg

        sanitized = msg.copy()

        # Sanitize content field
        if "content" in sanitized:
            sanitized["content"] = self._sanitize_message_content(sanitized["content"])

        # Sanitize other string fields
        if "tool_call_id" in sanitized:
            sanitized["tool_call_id"] = self._sanitize_text(str(sanitized["tool_call_id"]))

        if "name" in sanitized:
            sanitized["name"] = self._sanitize_text(str(sanitized["name"]))

        return sanitized
