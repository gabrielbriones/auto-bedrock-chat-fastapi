"""
Conversation Manager for BedrockClient

This module provides centralized conversation history management functionality,
including trimming, sliding window, and smart pruning strategies.

Classes:
    ConversationManager: Manages conversation history trimming and tool pair integrity

Functions:
    build_tool_use_location_map: Build mapping of tool_use_id to assistant message index
    get_selected_tool_use_ids: Get all tool_use IDs from selected message indices
    is_tool_result_message: Check if a message is a tool result message
"""

import logging
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)


# ============================================================================
# Module-level Helper Functions
# ============================================================================


def build_tool_use_location_map(messages: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Build a mapping of tool_use_id to the index of the assistant message that called it.

    Args:
        messages: List of conversation messages

    Returns:
        Dict mapping tool_use_id -> index of assistant message
    """
    tool_use_locations = {}
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tool_call in msg.get("tool_calls", []):
                tool_use_id = tool_call.get("id")
                if tool_use_id:
                    tool_use_locations[tool_use_id] = i
    return tool_use_locations


def get_selected_tool_use_ids(messages: List[Dict[str, Any]], selected_indices: Set[int]) -> Set[str]:
    """
    Get all tool_use IDs from the selected message indices.

    Args:
        messages: Full list of messages
        selected_indices: Set of indices currently selected

    Returns:
        Set of tool_use_ids that are in the selection
    """
    selected_tool_use_ids = set()
    for i in selected_indices:
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tool_call in msg.get("tool_calls", []):
                tool_use_id = tool_call.get("id")
                if tool_use_id:
                    selected_tool_use_ids.add(tool_use_id)
    return selected_tool_use_ids


def is_tool_result_message(msg: Dict[str, Any]) -> bool:
    """
    Check if a message is a tool result message.

    Tool result messages can be:
    - user message with tool_results field
    - tool role message with tool_results field
    - user message with content list containing tool_result blocks

    Args:
        msg: Message to check

    Returns:
        True if message contains tool results
    """
    role = msg.get("role")

    # Check pre-formatted tool_results field
    if role in ("user", "tool") and msg.get("tool_results"):
        return True

    # Check Bedrock/Claude format
    if role == "user":
        content = msg.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    return True

    return False


# ============================================================================
# ConversationManager Class
# ============================================================================


class ConversationManager:
    """
    Manages conversation history trimming and tool pair integrity.

    This class handles:
    - Message trimming strategies (truncate, sliding_window, smart_prune)
    - Tool use/result pair preservation
    - Orphaned tool result removal

    The manager ensures that Claude's requirement of matching tool_use/tool_result
    pairs is maintained during conversation trimming operations.

    Args:
        max_conversation_messages: Maximum number of messages to keep
        conversation_strategy: Strategy for trimming ("truncate", "sliding_window", "smart_prune")
        preserve_system_message: Whether to preserve the system message during trimming
    """

    def __init__(
        self,
        max_conversation_messages: int = 100,
        conversation_strategy: str = "sliding_window",
        preserve_system_message: bool = True,
    ):
        self.max_conversation_messages = max_conversation_messages
        self.conversation_strategy = conversation_strategy
        self.preserve_system_message = preserve_system_message

    def manage_conversation_history(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Manage conversation history to prevent context length issues.

        Args:
            messages: Original conversation messages

        Returns:
            Trimmed messages that fit within context limits
        """
        if len(messages) <= self.max_conversation_messages:
            return messages

        logger.info(
            f"Conversation history has {len(messages)} messages, trimming to "
            f"{self.max_conversation_messages} using {self.conversation_strategy} strategy"
        )

        if self.conversation_strategy == "truncate":
            trimmed = self.truncate_messages(messages)
        elif self.conversation_strategy == "sliding_window":
            trimmed = self.sliding_window_messages(messages)
        elif self.conversation_strategy == "smart_prune":
            trimmed = self.smart_prune_messages(messages)
        else:
            # Default to sliding window
            trimmed = self.sliding_window_messages(messages)

        # Final cleanup: remove any orphaned tool_results from the trimmed output
        trimmed = self.remove_orphaned_tool_results(trimmed)

        logger.info(f"Conversation history trimmed from {len(messages)} to {len(trimmed)} messages")
        return trimmed

    def truncate_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Simple truncation - keep the most recent messages.

        IMPORTANT: Claude requires that each tool_result block has a corresponding
        tool_use block in the immediately previous message. This method ensures
        tool_use/tool_result pairs stay together.

        Args:
            messages: List of conversation messages

        Returns:
            Truncated list of messages
        """
        if self.preserve_system_message and messages and messages[0].get("role") == "system":
            # Keep system message + most recent messages
            system_msg = [messages[0]]
            remaining_messages = messages[1:]
            max_remaining = self.max_conversation_messages - 1
        else:
            remaining_messages = messages
            max_remaining = self.max_conversation_messages

        # Identify which messages to keep by index
        if len(remaining_messages) > max_remaining:
            selected_indices = set(range(len(remaining_messages) - max_remaining, len(remaining_messages)))
        else:
            selected_indices = set(range(len(remaining_messages)))

        # Map tool_use_id to the index of the assistant message that called it
        tool_use_locations = build_tool_use_location_map(remaining_messages)

        # Finalize selection: ensure tool pairs stay together and remove orphans
        selected_messages = self._finalize_message_selection(
            remaining_messages, selected_indices, tool_use_locations, "Truncate"
        )

        if self.preserve_system_message and messages and messages[0].get("role") == "system":
            return system_msg + selected_messages
        else:
            return selected_messages

    def sliding_window_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sliding window - preserve system message and recent context.

        IMPORTANT: Claude requires that each tool_result block has a corresponding
        tool_use block in the immediately previous message. This method ensures
        tool_use/tool_result pairs stay together.

        Args:
            messages: List of conversation messages

        Returns:
            Windowed list of messages
        """
        result = []

        # Always preserve system message if present and configured
        if self.preserve_system_message and messages and messages[0].get("role") == "system":
            result.append(messages[0])
            remaining_messages = messages[1:]
            max_remaining = self.max_conversation_messages - 1
        else:
            remaining_messages = messages
            max_remaining = self.max_conversation_messages

        # DEBUG: Log initial parameters
        logger.debug(
            f"Sliding window: total messages={len(messages)}, "
            f"remaining_messages={len(remaining_messages)}, max_remaining={max_remaining}"
        )

        # Keep the most recent messages
        if len(remaining_messages) > max_remaining:
            selected_indices = set(range(len(remaining_messages) - max_remaining, len(remaining_messages)))
        else:
            selected_indices = set(range(len(remaining_messages)))

        # Map tool_use_id to the index of the assistant message that called it
        tool_use_locations = build_tool_use_location_map(remaining_messages)

        # IMPORTANT: Initial validation - if our initial selection includes a tool_result message
        # without its corresponding assistant message, we have an orphan situation.
        # We need to remove such tool_result messages from the selection BEFORE pairing.
        initial_orphaned = set()
        for i in list(selected_indices):
            msg = remaining_messages[i]
            if msg.get("role") == "user" and msg.get("tool_results"):
                for tool_result in msg.get("tool_results", []):
                    tool_use_id = tool_result.get("tool_call_id")
                    if tool_use_id and tool_use_id in tool_use_locations:
                        tool_use_idx = tool_use_locations[tool_use_id]
                        if tool_use_idx not in selected_indices:
                            # This tool_result's assistant is NOT in our selection - it's orphaned
                            logger.debug(
                                f"Sliding window: Initial selection has orphaned tool_result "
                                f"{tool_use_id} at index {i}, removing it"
                            )
                            initial_orphaned.add(i)

        selected_indices -= initial_orphaned

        # Finalize selection: ensure tool pairs stay together and remove orphans
        selected_messages = self._finalize_message_selection(
            remaining_messages, selected_indices, tool_use_locations, "Sliding window"
        )

        result.extend(selected_messages)
        return result

    def smart_prune_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Smart pruning - remove tool messages first, then older messages.

        IMPORTANT: Claude requires that each tool_result block has a corresponding
        tool_use block in the immediately previous message. This method ensures
        tool_use/tool_result pairs stay together.

        Args:
            messages: List of conversation messages

        Returns:
            Pruned list of messages
        """
        result = []

        # Always preserve system message if present and configured
        if self.preserve_system_message and messages and messages[0].get("role") == "system":
            result.append(messages[0])
            remaining_messages = messages[1:]
            max_remaining = self.max_conversation_messages - 1
        else:
            remaining_messages = messages
            max_remaining = self.max_conversation_messages

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

        # Otherwise, take the most recent non-tool messages and ensure tool pairs stay together
        if len(non_tool_messages) > max_remaining:
            selected_indices = set(range(len(non_tool_messages) - max_remaining, len(non_tool_messages)))
        else:
            selected_indices = set(range(len(non_tool_messages)))

        # Map tool_use_id to assistant message index in non_tool_messages
        tool_use_locations = build_tool_use_location_map(non_tool_messages)

        # Finalize selection: ensure tool pairs stay together and remove orphans
        selected_messages = self._finalize_message_selection(
            non_tool_messages, selected_indices, tool_use_locations, "Smart prune"
        )

        result.extend(selected_messages)
        return result

    def remove_orphaned_tool_results(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Remove tool_result messages that don't have a matching tool_use in the messages.

        This is a final cleanup pass after trimming to ensure no orphaned tool_results exist.
        An orphaned tool_result is one where the corresponding tool_use (in a previous assistant
        message) is not present in the messages list.

        Handles multiple message formats:
        1. Pre-formatted: tool_calls field on assistant messages, tool_results field on user/tool messages
        2. Bedrock/Claude format: content is a list with {"type": "tool_use", ...} or {"type": "tool_result", ...}

        Args:
            messages: Messages after trimming

        Returns:
            Messages with orphaned tool_results removed
        """
        if not messages:
            return messages

        # Build a set of all tool_use IDs from assistant messages
        # Check both pre-formatted (tool_calls field) and Bedrock format (content list with tool_use type)
        available_tool_use_ids = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                # Check pre-formatted tool_calls field
                if msg.get("tool_calls"):
                    for tool_call in msg.get("tool_calls", []):
                        tool_use_id = tool_call.get("id")
                        if tool_use_id:
                            available_tool_use_ids.add(tool_use_id)
                            logger.debug(f"Found available tool_use_id (from tool_calls): {tool_use_id}")

                # Check Bedrock/Claude format: content is a list with tool_use blocks
                content = msg.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_use":
                            tool_use_id = item.get("id")
                            if tool_use_id:
                                available_tool_use_ids.add(tool_use_id)
                                logger.debug(f"Found available tool_use_id (from content): {tool_use_id}")

        logger.debug(f"Available tool_use IDs: {available_tool_use_ids}")

        # If there are no tool_use IDs, we may have orphaned tool_results
        # Check if there are any tool_results - if so, they're orphaned and should be removed
        has_tool_results = False
        for msg in messages:
            role = msg.get("role")
            if role in ("user", "tool"):
                # Check pre-formatted tool_results field
                if msg.get("tool_results"):
                    has_tool_results = True
                    break
                # Check Bedrock/Claude format
                content = msg.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_result":
                            has_tool_results = True
                            break

        if not available_tool_use_ids and not has_tool_results:
            logger.debug("No tool_use IDs or tool_results found in messages, returning as-is")
            return messages

        # Filter out messages that are orphaned tool_results
        cleaned_messages = []
        for i, msg in enumerate(messages):
            is_completely_orphaned = False
            role = msg.get("role")

            # Handle pre-formatted tool_results field
            if role in ("user", "tool") and msg.get("tool_results"):
                # Filter tool_results to keep only non-orphaned ones
                non_orphaned_results = []
                for tool_result in msg.get("tool_results", []):
                    # Check both possible keys for tool_use_id
                    tool_use_id = tool_result.get("tool_call_id") or tool_result.get("tool_use_id")
                    logger.debug(
                        f"  Checking message[{i}] tool_result: tool_use_id={tool_use_id}, "
                        f"available={tool_use_id in available_tool_use_ids if tool_use_id else False}"
                    )

                    if tool_use_id and tool_use_id not in available_tool_use_ids:
                        # This tool_result is orphaned, skip it
                        logger.warning(f"    - Removing orphaned tool_result for {tool_use_id} from message[{i}]")
                    else:
                        # Keep this tool_result
                        non_orphaned_results.append(tool_result)

                # If all tool_results were removed, mark the message as orphaned
                if len(non_orphaned_results) == 0 and len(msg.get("tool_results", [])) > 0:
                    logger.warning(f"Removing message[{i}] - all tool_results were orphaned")
                    is_completely_orphaned = True
                elif len(non_orphaned_results) < len(msg.get("tool_results", [])):
                    # Some tool_results were removed, update the message
                    msg = msg.copy()  # Don't modify original
                    msg["tool_results"] = non_orphaned_results
                    logger.debug(f"Updated message[{i}]: kept {len(non_orphaned_results)} tool_results")

            # Handle Bedrock/Claude format: content is a list with tool_result blocks
            elif role == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    # Check if content contains tool_result blocks
                    has_tool_result_blocks = any(
                        isinstance(item, dict) and item.get("type") == "tool_result" for item in content
                    )

                    if has_tool_result_blocks:
                        # Filter content to keep only non-orphaned tool_results and other content
                        non_orphaned_content = []
                        orphaned_count = 0

                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "tool_result":
                                tool_use_id = item.get("tool_use_id")
                                logger.debug(
                                    f"  Checking message[{i}] tool_result block: tool_use_id={tool_use_id}, "
                                    f"available={tool_use_id in available_tool_use_ids if tool_use_id else False}"
                                )

                                if tool_use_id and tool_use_id not in available_tool_use_ids:
                                    # This tool_result is orphaned, skip it
                                    logger.warning(
                                        f"    - Removing orphaned tool_result block for {tool_use_id} "
                                        f"from message[{i}]"
                                    )
                                    orphaned_count += 1
                                else:
                                    non_orphaned_content.append(item)
                            else:
                                # Keep non-tool_result content
                                non_orphaned_content.append(item)

                        # If all content was tool_results and all were orphaned, remove the message
                        if len(non_orphaned_content) == 0 and orphaned_count > 0:
                            logger.warning(f"Removing message[{i}] - all tool_result blocks were orphaned")
                            is_completely_orphaned = True
                        elif orphaned_count > 0:
                            # Some tool_results were removed, update the message
                            msg = msg.copy()  # Don't modify original
                            msg["content"] = non_orphaned_content
                            logger.debug(f"Updated message[{i}]: removed {orphaned_count} orphaned tool_result blocks")

            if not is_completely_orphaned:
                cleaned_messages.append(msg)

        if len(cleaned_messages) < len(messages):
            logger.debug(
                f"Removed {len(messages) - len(cleaned_messages)} completely orphaned message(s) "
                f"during final cleanup"
            )

        return cleaned_messages

    def _ensure_tool_pairs_stay_together(
        self,
        messages: List[Dict[str, Any]],
        selected_indices: Set[int],
        tool_use_locations: Dict[str, int],
        strategy_name: str = "strategy",
    ) -> Set[int]:
        """
        Ensure tool_use/tool_result pairs stay together in the selected indices.

        Claude requires that each tool_result block has a corresponding tool_use block.
        This method iteratively adds missing pairs to the selection.

        Args:
            messages: Full list of messages being processed
            selected_indices: Set of indices currently selected (will be modified)
            tool_use_locations: Map of tool_use_id -> assistant message index
            strategy_name: Name for logging purposes

        Returns:
            Updated set of selected indices with complete tool pairs
        """
        needs_iteration = True
        iterations = 0
        max_iterations = 10  # Prevent infinite loops

        while needs_iteration and iterations < max_iterations:
            iterations += 1
            needs_iteration = False

            for i in list(selected_indices):
                msg = messages[i]

                # If this is a tool_result, ensure its tool_use assistant is included
                if msg.get("role") == "user" and msg.get("tool_results"):
                    for tool_result in msg.get("tool_results", []):
                        tool_use_id = tool_result.get("tool_call_id")
                        if tool_use_id and tool_use_id in tool_use_locations:
                            tool_use_idx = tool_use_locations[tool_use_id]
                            if tool_use_idx not in selected_indices:
                                selected_indices.add(tool_use_idx)
                                needs_iteration = True

                # If this is an assistant with tool_use, ensure its result message is included
                elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                    for tool_call in msg.get("tool_calls", []):
                        tool_use_id = tool_call.get("id")
                        # Find the corresponding tool_result message
                        for j in range(i + 1, len(messages)):
                            next_msg = messages[j]
                            if next_msg.get("role") == "user" and next_msg.get("tool_results"):
                                for tool_result in next_msg.get("tool_results", []):
                                    if tool_result.get("tool_call_id") == tool_use_id:
                                        if j not in selected_indices:
                                            selected_indices.add(j)
                                            needs_iteration = True
                                        break
                            # Stop searching if we hit another assistant message
                            elif next_msg.get("role") == "assistant":
                                break

        return selected_indices

    def _remove_orphaned_tool_results_from_selection(
        self, messages: List[Dict[str, Any]], selected_indices: Set[int], strategy_name: str = "strategy"
    ) -> Set[int]:
        """
        Remove tool_result messages whose tool_use_id is not in the selected set.

        After trimming, some tool_results may have lost their corresponding
        assistant message with tool_use. This removes those orphans.

        Args:
            messages: Full list of messages being processed
            selected_indices: Set of indices currently selected (will be modified)
            strategy_name: Name for logging purposes

        Returns:
            Updated set of selected indices with orphans removed
        """
        # Build set of tool_use_ids that are actually selected
        selected_tool_use_ids = get_selected_tool_use_ids(messages, selected_indices)

        if selected_tool_use_ids:
            logger.debug(f"{strategy_name}: Selected tool_use IDs: {selected_tool_use_ids}")

        # Remove tool_result messages whose tool_use_id is not in the selected set
        indices_to_remove = set()
        for i in list(selected_indices):
            msg = messages[i]
            # Check user messages with tool_results field
            if msg.get("role") == "user" and msg.get("tool_results"):
                for tool_result in msg.get("tool_results", []):
                    tool_use_id = tool_result.get("tool_call_id")
                    if tool_use_id and tool_use_id not in selected_tool_use_ids:
                        logger.debug(f"{strategy_name}: Removing orphaned tool_result {tool_use_id} at index {i}")
                        indices_to_remove.add(i)
                        break
            # Check tool role messages with tool_results field
            elif msg.get("role") == "tool" and msg.get("tool_results"):
                for tool_result in msg.get("tool_results", []):
                    tool_use_id = tool_result.get("tool_call_id")
                    if tool_use_id and tool_use_id not in selected_tool_use_ids:
                        logger.debug(
                            f"{strategy_name}: Removing orphaned tool_result {tool_use_id} "
                            f"from tool message at index {i}"
                        )
                        indices_to_remove.add(i)
                        break

        selected_indices -= indices_to_remove
        if indices_to_remove:
            logger.debug(f"{strategy_name}: Removed {len(indices_to_remove)} orphaned tool_result message(s)")

        return selected_indices

    def _finalize_message_selection(
        self,
        messages: List[Dict[str, Any]],
        selected_indices: Set[int],
        tool_use_locations: Dict[str, int],
        strategy_name: str = "strategy",
    ) -> List[Dict[str, Any]]:
        """
        Finalize message selection by ensuring tool pairs and removing orphans.

        This is a convenience method that combines:
        1. _ensure_tool_pairs_stay_together
        2. _remove_orphaned_tool_results_from_selection
        3. Converting indices back to sorted message list

        Args:
            messages: Full list of messages being processed
            selected_indices: Set of indices currently selected
            tool_use_locations: Map of tool_use_id -> assistant message index
            strategy_name: Name for logging purposes

        Returns:
            List of selected messages in original order
        """
        # Ensure tool pairs stay together
        selected_indices = self._ensure_tool_pairs_stay_together(
            messages, selected_indices, tool_use_locations, strategy_name
        )

        # Remove orphaned tool_results
        selected_indices = self._remove_orphaned_tool_results_from_selection(messages, selected_indices, strategy_name)

        # Convert indices back to messages, maintaining order
        return [messages[i] for i in sorted(selected_indices)]
