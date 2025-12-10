"""Tool Message Processor for handling tool use/result pairs.

This module centralizes all tool message processing logic including:
- Detection of tool messages (tool_use, tool_result, GPT/Llama tool formats)
- Truncation of tool results in conversation history
- Processing of new tool result messages
- Intelligent content truncation while preserving meaning

Extracted from BedrockClient to improve maintainability.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Module-level utility functions for tool message detection
# ============================================================================


def is_tool_message(msg: dict) -> bool:
    """
    Check if a message is a tool-related message.

    Supports multiple formats:
    - Claude format: role="user" with content list containing tool_result
    - Dict format: role="user" with tool_result dict content
    - GPT format: role="tool" with string content
    - Llama format: role="user" with is_tool_result flag

    Args:
        msg: Message dictionary to check

    Returns:
        True if the message is a tool result message
    """
    if not isinstance(msg, dict):
        return False

    content = msg.get("content", "")

    # Claude format: role="user" with content list containing tool_result
    if msg.get("role") == "user" and isinstance(content, list):
        return any(isinstance(item, dict) and item.get("type") == "tool_result" for item in content)

    # Dict format: role="user" with tool_result dict
    if msg.get("role") == "user" and isinstance(content, dict):
        return content.get("type") == "tool_result"

    # Llama format: role="user" with is_tool_result flag
    if msg.get("role") == "user" and msg.get("is_tool_result"):
        return True

    # GPT format: role="tool" with string content
    if msg.get("role") == "tool" and isinstance(content, str):
        return True

    return False


def is_user_message(msg: dict) -> bool:
    """Check if a message is a user message (non-tool)."""
    if not isinstance(msg, dict):
        return False
    # User message but not a tool result
    return msg.get("role") == "user" and not is_tool_message(msg)


def get_content_size(msg: dict) -> int:
    """
    Get the size of message content for truncation decisions.

    Args:
        msg: Message dictionary

    Returns:
        Size of the content in characters
    """
    if not isinstance(msg, dict):
        return 0

    content = msg.get("content", "")

    if isinstance(content, str):
        return len(content)
    elif isinstance(content, list):
        # Claude format: sum all content items
        total = 0
        for item in content:
            if isinstance(item, dict):
                total += len(str(item.get("content", "")))
            else:
                total += len(str(item))
        return total
    elif isinstance(content, dict):
        return len(str(content.get("content", content)))
    else:
        return len(str(content))


def is_assistant_with_tool_use(msg: dict) -> bool:
    """
    Check if message is an assistant message that contains tool_use blocks.

    Args:
        msg: Message dictionary to check

    Returns:
        True if the message is an assistant message with tool use
    """
    if not isinstance(msg, dict):
        return False

    if msg.get("role") != "assistant":
        return False

    content = msg.get("content", [])
    if isinstance(content, list):
        return any(isinstance(item, dict) and item.get("type") == "tool_use" for item in content)
    return False


def is_tool_result_message(msg: dict) -> bool:
    """
    Check if message is a user message containing tool_result.

    Args:
        msg: Message dictionary to check

    Returns:
        True if the message contains tool results
    """
    if not isinstance(msg, dict):
        return False

    if msg.get("role") != "user":
        return False

    content = msg.get("content", [])
    if isinstance(content, list):
        return any(isinstance(item, dict) and item.get("type") == "tool_result" for item in content)
    return False


class ToolMessageProcessor:
    """
    Handles truncation and processing of tool messages.

    This class manages:
    - Truncating tool results in conversation history
    - Processing new tool result messages with intelligent truncation
    - Detecting and handling different tool message formats (GPT, Llama, Claude)
    """

    def __init__(
        self,
        tool_result_history_threshold: int = 50000,
        tool_result_history_target: int = 42500,
        tool_result_new_response_threshold: int = 500000,
        tool_result_new_response_target: int = 425000,
    ):
        """
        Initialize the tool message processor.

        Args:
            tool_result_history_threshold: Max chars for tool results in history
            tool_result_history_target: Target size when truncating history results
            tool_result_new_response_threshold: Max chars for new tool responses
            tool_result_new_response_target: Target size when truncating new responses
        """
        self.tool_result_history_threshold = tool_result_history_threshold
        self.tool_result_history_target = tool_result_history_target
        self.tool_result_new_response_threshold = tool_result_new_response_threshold
        self.tool_result_new_response_target = tool_result_new_response_target

    def truncate_tool_messages_in_history(
        self,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Truncate tool response content in message history, preserving recent responses.

        This method handles three distinct groups:
        1. Very old tool responses (before system message): aggressively truncated
        2. Recent tool responses (conversation history): moderately truncated
        3. Trailing tool responses (most recent): preserved for model processing

        IMPORTANT: We identify and preserve trailing tool messages at the END
        of the conversation. These are responses the model just generated or
        is about to process. If they exceed the new_response_threshold, they
        will be truncated proportionally as a group.

        Args:
            messages: List of conversation messages

        Returns:
            List of messages with truncated tool content
        """
        if not messages:
            return messages

        result = []
        trailing_tools_messages = []

        # First, identify the trailing tool message group at the END of the conversation
        trailing_tool_start_idx = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            if is_tool_message(messages[i]):
                trailing_tool_start_idx = i
            else:
                break

        num_trailing_tools = len(messages) - trailing_tool_start_idx

        # Calculate total size of trailing tools
        trailing_tools_total_size = 0
        if num_trailing_tools > 0:
            for i in range(trailing_tool_start_idx, len(messages)):
                trailing_tools_total_size += get_content_size(messages[i])
            logger.debug(
                f"Found {num_trailing_tools} trailing tool messages starting at index "
                f"{trailing_tool_start_idx}, total size: {trailing_tools_total_size:,} chars"
            )

        for i, msg in enumerate(messages):
            # Collect trailing tool messages separately for group processing
            is_trailing_tool = (num_trailing_tools > 0) and (i >= trailing_tool_start_idx)
            if is_trailing_tool:
                trailing_tools_messages.append((i, msg))
                continue

            if not isinstance(msg, dict):
                result.append(msg)
                continue

            content = msg.get("content", "")

            # Handle Claude format: role="user" with content list containing tool_result
            if msg.get("role") == "user" and isinstance(content, list):
                new_content = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        tool_content = item.get("content", "")
                        tool_use_id = item.get("tool_use_id", "unknown")
                        if isinstance(tool_content, str) and len(tool_content) > self.tool_result_history_threshold:
                            truncated = self._intelligently_truncate_tool_result(
                                tool_content,
                                tool_use_id,
                                self.tool_result_history_target,
                            )
                            new_item = dict(item)
                            new_item["content"] = truncated
                            new_content.append(new_item)
                            logger.debug(
                                f"Truncated tool_result {tool_use_id} in history from "
                                f"{len(tool_content)} to {len(truncated)} chars"
                            )
                        else:
                            new_content.append(item)
                    else:
                        new_content.append(item)

                new_msg = dict(msg)
                new_msg["content"] = new_content
                result.append(new_msg)

            # Handle GPT format: role="tool" with string content
            elif msg.get("role") == "tool" and isinstance(content, str):
                if len(content) > self.tool_result_history_threshold:
                    tool_call_id = msg.get("tool_call_id", "unknown")
                    truncated = self._intelligently_truncate_tool_result(
                        content,
                        tool_call_id,
                        self.tool_result_history_target,
                    )
                    new_msg = dict(msg)
                    new_msg["content"] = truncated
                    result.append(new_msg)
                    logger.debug(
                        f"Truncated GPT tool_result in history from " f"{len(content)} to {len(truncated)} chars"
                    )
                else:
                    result.append(msg)

            # Handle Llama format: role="user" with is_tool_result=True and string content
            elif msg.get("role") == "user" and msg.get("is_tool_result") and isinstance(content, str):
                if len(content) > self.tool_result_history_threshold:
                    tool_call_id = msg.get("tool_call_id", "unknown")
                    truncated = self._intelligently_truncate_tool_result(
                        content,
                        tool_call_id,
                        self.tool_result_history_target,
                    )
                    new_msg = dict(msg)
                    new_msg["content"] = truncated
                    result.append(new_msg)
                    logger.debug(
                        f"Truncated Llama tool_result {tool_call_id} in history from "
                        f"{len(content)} to {len(truncated)} chars"
                    )
                else:
                    result.append(msg)

            # Handle dict format: role="user" with content dict having type="tool_result"
            elif msg.get("role") == "user" and isinstance(content, dict) and content.get("type") == "tool_result":
                tool_content = content.get("content", "")
                tool_use_id = content.get("tool_use_id", "unknown")
                content_str = str(tool_content) if not isinstance(tool_content, str) else tool_content
                if len(content_str) > self.tool_result_history_threshold:
                    truncated = self._intelligently_truncate_tool_result(
                        content_str,
                        tool_use_id,
                        self.tool_result_history_target,
                    )
                    new_content = dict(content)
                    new_content["content"] = truncated
                    new_msg = dict(msg)
                    new_msg["content"] = new_content
                    result.append(new_msg)
                    logger.debug(
                        f"Truncated dict tool_result {tool_use_id} in history from "
                        f"{len(content_str)} to {len(truncated)} chars"
                    )
                else:
                    result.append(msg)

            else:
                result.append(msg)

        # Now handle trailing tool messages as a GROUP
        if trailing_tools_messages:
            if trailing_tools_total_size > self.tool_result_new_response_threshold:
                # Too large - need to truncate the group proportionally
                logger.warning(
                    f"Trailing tool group TOO LARGE ({trailing_tools_total_size:,} > "
                    f"{self.tool_result_new_response_threshold:,}). Truncating group proportionally."
                )

                num_trailing = len(trailing_tools_messages)
                per_tool_threshold = self.tool_result_new_response_threshold / num_trailing
                per_tool_target = int(self.tool_result_new_response_target * 0.8 / num_trailing)

                for idx, (_orig_idx, msg) in enumerate(trailing_tools_messages):
                    truncated_msg = self.process_tool_result_message(
                        msg,
                        is_conversation_history=True,
                        custom_threshold=per_tool_threshold,
                        custom_target=per_tool_target,
                    )

                    new_size = get_content_size(truncated_msg)
                    original_size = get_content_size(msg)
                    if new_size < original_size:
                        retention_pct = (new_size / original_size * 100) if original_size > 0 else 0
                        logger.info(
                            f"Trailing tool {idx+1}/{num_trailing} truncated: "
                            f"{original_size:,} → {new_size:,} chars ({retention_pct:.1f}% retained)"
                        )
                    result.append(truncated_msg)
            else:
                # Trailing tools fit within budget - keep as-is
                logger.debug(
                    f"Trailing tool group fits within budget ({trailing_tools_total_size:,} <= "
                    f"{self.tool_result_new_response_threshold:,}), preserving as-is"
                )
                for _, msg in trailing_tools_messages:
                    result.append(msg)

        return result

    def process_tool_result_message(
        self,
        message: Dict[str, Any],
        is_conversation_history: bool = False,
        custom_threshold: Optional[int] = None,
        custom_target: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Process tool result messages to handle oversized responses.

        Supports multiple formats:
        - Claude: role="user", content=[{type: "tool_result", tool_use_id: ..., content: ...}]
        - GPT: role="tool", tool_call_id: ..., content="string"
        - Llama: role="user", is_tool_result=True, content="string"
        - Mixed: role="user", content={type: "tool_result", ...}

        Two-tier truncation strategy:
        1. First tool response: 500K threshold → 425K target (maximize context)
        2. Conversation history: 50K threshold → 42.5K target (keep manageable)
        3. Custom budgets: For grouped tool messages with proportional budgets

        Args:
            message: Message with tool results in content
            is_conversation_history: True if processing existing conversation history,
                                     False if processing new/first tool response
            custom_threshold: Optional custom threshold (overrides default)
            custom_target: Optional custom target size (overrides default)

        Returns:
            Processed message with truncated/summarized tool results
        """
        # Determine thresholds based on context (configurable via settings)
        # Custom thresholds take precedence
        if custom_threshold is not None and custom_target is not None:
            large_threshold = custom_threshold
            target_size = custom_target
            context_label = "custom-budgeted"
        elif is_conversation_history:
            # For conversation history: aggressive truncation to keep context manageable
            large_threshold = self.tool_result_history_threshold
            target_size = self.tool_result_history_target
            context_label = "conversation history"
        else:
            # For first/new tool response: generous limit to maximize initial context
            large_threshold = self.tool_result_new_response_threshold
            target_size = self.tool_result_new_response_target
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

        # Llama format: role="user" with string content and is_tool_result=True marker
        elif message.get("role") == "user" and message.get("is_tool_result") and isinstance(content, str):
            content_size = len(content)
            tool_call_id = message.get("tool_call_id", "unknown")

            if content_size > large_threshold:
                logger.warning(
                    f"Llama tool result {tool_call_id} in {context_label} is very large ({content_size:,} chars), "
                    f"truncating to ~{target_size:,} chars (threshold: {large_threshold:,})..."
                )

                try:
                    # Apply intelligent truncation
                    truncated_content = self._intelligently_truncate_tool_result(
                        content, tool_call_id, max_size=target_size
                    )

                    # Return message with truncated content, preserving metadata
                    return {**message, "content": truncated_content}

                except Exception as e:
                    # Fallback: simple truncation if intelligent truncation fails
                    logger.error(f"Error truncating Llama tool result: {e}")
                    logger.error("Falling back to simple truncation")

                    simple_truncated = (
                        content[:target_size] + f"\n\n[TRUNCATED - Original size: {content_size:,} chars]"
                    )

                    return {**message, "content": simple_truncated}
            else:
                # Small result, return as-is
                return message

        # Mixed/Single dict format: role="user", content is a single tool_result dict
        elif message.get("role") == "user" and isinstance(content, dict) and content.get("type") == "tool_result":
            tool_result_content = content.get("content", "")
            tool_use_id = content.get("tool_use_id", "")

            # Calculate size
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

                    # Create new dict with truncated content
                    truncated_dict = content.copy()
                    truncated_dict["content"] = truncated_content

                    return {**message, "content": truncated_dict}

                except Exception as e:
                    # Fallback: simple truncation if intelligent truncation fails
                    logger.error(f"Error truncating tool result: {e}")
                    logger.error("Falling back to simple truncation")

                    simple_truncated = (
                        content_str[:target_size] + f"\n\n[TRUNCATED - Original size: {content_size:,} chars]"
                    )

                    truncated_dict = content.copy()
                    truncated_dict["content"] = simple_truncated

                    return {**message, "content": truncated_dict}
            else:
                # Small result, return as-is
                return message

        # Claude format: role="user" with content list containing tool_result items
        elif isinstance(content, list):
            processed_content = []

            # Count tool_result items to distribute target size
            tool_result_count = sum(
                1 for item in content if isinstance(item, dict) and item.get("type") == "tool_result"
            )

            # CRITICAL FIX: If there are multiple tool results in ONE message, divide BOTH
            # threshold and target proportionally across all results.
            # Previously, only the target was divided, causing threshold comparison to fail.
            # Example: 2 tool results @ 400K chars each = 800K chars total
            # - Old: threshold=750K chars, per_result_size=400K chars → 400K NOT > 750K → no truncation ❌
            # - New: threshold=375K chars, per_result_size=400K chars → 400K > 375K → truncate ✅
            if tool_result_count > 1:
                # Distribute both threshold and target size across all tool results
                per_item_threshold = large_threshold / tool_result_count
                per_item_target = int(target_size * 0.8 / tool_result_count)  # Leave 20% buffer
                logger.debug(
                    f"Multiple tool results ({tool_result_count}): dividing threshold "
                    f"{large_threshold:,} -> {per_item_threshold:,} and target "
                    f"{target_size:,} -> {per_item_target:,} per item"
                )
            else:
                per_item_threshold = large_threshold
                per_item_target = target_size

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

                logger.debug(
                    f"Processing tool result {tool_use_id}: {content_size:,} chars, "
                    f"large_threshold: {per_item_threshold:,}, target: {per_item_target:,}"
                )

                if content_size > per_item_threshold:
                    logger.warning(
                        f"Tool result {tool_use_id} in {context_label} is very large ({content_size:,} chars), "
                        f"truncating to ~{per_item_target:,} chars (threshold: {per_item_threshold:,})..."
                    )

                    try:
                        # Apply intelligent truncation with per-item target
                        truncated_content = self._intelligently_truncate_tool_result(
                            tool_result_content, tool_use_id, max_size=per_item_target
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
                            content_str[:per_item_target] + f"\n\n[TRUNCATED - Original size: {content_size:,} chars]"
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
        Truncate large tool results while preserving context.

        Strategy: Show beginning + end with summary for readability.

        Args:
            content: Tool result content (can be dict, list, or string)
            tool_id: Tool use ID for logging
            max_size: Maximum size in characters for truncated result

        Returns:
            Truncated string representation
        """
        content_str = str(content)
        original_size = len(content_str)

        logger.debug(
            f"_intelligently_truncate_tool_result called for {tool_id}: " f"{original_size:,} chars → max {max_size:,}"
        )

        # Use simple text truncation with context
        return self._truncate_plain_text(content_str, tool_id, max_size, original_size)

    def _truncate_plain_text(self, text: str, tool_id: str, max_size: int, original_size: int) -> str:
        """
        Truncate plain text with beginning + end preview.

        Strategy: Show first and last portions with summary.
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
