"""
Message Chunker for BedrockClient

This module provides centralized message chunking functionality for splitting
large messages into smaller chunks that fit within model context limits.

Classes:
    MessageChunker: Handles message chunking with multiple strategies

Functions:
    simple_chunk: Basic character-based chunking
    context_aware_chunk: Chunking that preserves natural boundaries
    semantic_chunk: Chunking that preserves logical units (currently falls back to context-aware)
"""

import logging
from typing import Any, Dict, List

from .tool_message_processor import is_tool_message

logger = logging.getLogger(__name__)


# ============================================================================
# Module-level Chunking Functions
# ============================================================================


def simple_chunk(content: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    """
    Simple character-based chunking.
    
    Args:
        content: The content to chunk
        chunk_size: Maximum size of each chunk
        chunk_overlap: Number of characters to overlap between chunks
        
    Returns:
        List of content chunks
    """
    chunks = []
    i = 0
    
    while i < len(content):
        # Determine chunk end position
        chunk_end = min(i + chunk_size, len(content))
        chunk = content[i:chunk_end]
        chunks.append(chunk)

        # Move to next chunk with overlap
        if chunk_end >= len(content):
            break
        i = chunk_end - chunk_overlap

    return chunks


def context_aware_chunk(content: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    """
    Context-aware chunking that tries to break on natural boundaries.
    
    Looks for natural break points like paragraph breaks, newlines, 
    sentence endings, and word boundaries.
    
    Args:
        content: The content to chunk
        chunk_size: Maximum size of each chunk
        chunk_overlap: Number of characters to overlap between chunks
        
    Returns:
        List of content chunks
    """
    chunks = []
    
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
        i = max(best_break - chunk_overlap, i + 1)  # Ensure progress

    return chunks


def semantic_chunk(content: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    """
    Semantic chunking that tries to preserve logical units.
    
    Currently falls back to context-aware chunking, but could be enhanced
    with NLP libraries for more intelligent splitting.
    
    Args:
        content: The content to chunk
        chunk_size: Maximum size of each chunk
        chunk_overlap: Number of characters to overlap between chunks
        
    Returns:
        List of content chunks
    """
    # For now, use context-aware chunking
    # In the future, this could use libraries like spacy or nltk
    # to split on sentence or paragraph boundaries more intelligently
    return context_aware_chunk(content, chunk_size, chunk_overlap)


# ============================================================================
# MessageChunker Class
# ============================================================================


class MessageChunker:
    """
    Handles message chunking with multiple strategies.
    
    This class provides functionality to split large messages into smaller
    chunks that fit within model context limits while preserving message
    semantics as much as possible.
    
    Supports three chunking strategies:
    - simple: Basic character-based chunking
    - preserve_context: Context-aware chunking on natural boundaries
    - semantic: Semantic-aware chunking (currently same as preserve_context)
    
    Args:
        enable_message_chunking: Whether chunking is enabled
        max_message_size: Maximum size before chunking is applied
        chunk_size: Size of each chunk
        chunk_overlap: Overlap between chunks
        chunking_strategy: Strategy to use ("simple", "preserve_context", "semantic")
        tool_result_history_threshold: Threshold for tool results in history
        tool_result_new_response_threshold: Threshold for new tool responses
    """

    def __init__(
        self,
        enable_message_chunking: bool = True,
        max_message_size: int = 100000,
        chunk_size: int = 50000,
        chunk_overlap: int = 200,
        chunking_strategy: str = "preserve_context",
        tool_result_history_threshold: int = 50000,
        tool_result_new_response_threshold: int = 100000,
    ):
        self.enable_message_chunking = enable_message_chunking
        self.max_message_size = max_message_size
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.chunking_strategy = chunking_strategy
        self.tool_result_history_threshold = tool_result_history_threshold
        self.tool_result_new_response_threshold = tool_result_new_response_threshold

    def check_and_chunk_messages(
        self,
        messages: List[Dict[str, Any]],
        tool_processor: Any = None,
    ) -> List[Dict[str, Any]]:
        """
        Check for large messages and chunk them if necessary.
        
        Special handling for tool responses to prevent context overflow.

        Three-tier system for tool messages:
        - Very large (>threshold): Apply intelligent truncation (keep single message)
        - Moderately large (>max_message_size): Apply chunking (split into multiple)
        - Small (<max_message_size): Keep as-is

        CRITICAL: Trailing tool message groups (consecutive tools at end) are already
        handled by the tool processor. Skip re-truncation here.

        Args:
            messages: List of conversation messages
            tool_processor: Optional ToolMessageProcessor for tool result truncation

        Returns:
            List of messages with large messages chunked if needed
        """
        if not self.enable_message_chunking:
            return messages

        # Identify trailing tool message group (consecutive tools at end)
        # These have already been group-truncated and should NOT be re-truncated
        trailing_tool_start_idx = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            if is_tool_message(messages[i]):
                trailing_tool_start_idx = i
            else:
                break
        
        num_trailing_tools = len(messages) - trailing_tool_start_idx

        result = []
        total_messages = len(messages)

        for idx, msg in enumerate(messages):
            # Check if this message is part of the trailing tool group
            # If so, skip all truncation/chunking - it's already been handled
            is_trailing_tool = (num_trailing_tools > 0) and (idx >= trailing_tool_start_idx)
            
            if is_trailing_tool:
                # Preserve trailing tool messages as-is (already group-truncated)
                result.append(msg)
                continue

            # Determine if this is the last message (most recent, likely new tool response)
            # vs. earlier messages (conversation history)
            is_last_message = idx == total_messages - 1

            # Get message content
            content = msg.get("content", "")

            # Calculate content size - handle both string and list formats
            content_size = self._get_content_size(content)

            # Special handling for tool/function result messages
            # Handle three formats: Claude list format, dict format, and GPT format
            msg_is_tool = is_tool_message(msg)
            # Also check for metadata flag indicating tool result message (from websocket_handler)
            has_tool_result_flag = (
                msg.get('metadata', {}).get('is_tool_result', False) 
                if isinstance(msg, dict) else False
            )
            msg_is_tool = msg_is_tool or has_tool_result_flag

            if content_size > 0:
                # For tool messages: use intelligent truncation only (no chunking)
                # Chunking tool responses is counterproductive - they are atomic semantic units
                # that should not be fragmented across multiple messages
                if msg_is_tool:
                    is_conversation_history = not is_last_message
                    if is_conversation_history:
                        truncation_threshold = self.tool_result_history_threshold
                    else:
                        truncation_threshold = self.tool_result_new_response_threshold

                    # Use intelligent truncation for large messages (no chunking)
                    if content_size > truncation_threshold and tool_processor is not None:
                        processed_msg = tool_processor.process_tool_result_message(
                            msg, is_conversation_history
                        )
                        result.append(processed_msg)
                    else:
                        # Under threshold: keep as-is (never chunk tool messages)
                        result.append(msg)
                else:
                    # For regular messages: use max_message_size threshold with optional chunking
                    if content_size > self.max_message_size and self.enable_message_chunking:
                        logger.info(
                            f"Message size ({content_size} chars) exceeds "
                            f"max_message_size ({self.max_message_size}), chunking..."
                        )
                        chunked_messages = self.chunk_large_message(msg)
                        result.extend(chunked_messages)
                    else:
                        result.append(msg)
            else:
                result.append(msg)

        return result

    def chunk_large_message(self, message: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Split a large message into smaller chunks.

        Args:
            message: The message to chunk

        Returns:
            List of chunked messages with chunk information embedded in content
        """
        content = message.get("content", "")
        if not isinstance(content, str):
            return [message]  # Cannot chunk non-string content

        # Choose chunking strategy
        if self.chunking_strategy == "simple":
            chunks = simple_chunk(content, self.chunk_size, self.chunk_overlap)
        elif self.chunking_strategy == "preserve_context":
            chunks = context_aware_chunk(content, self.chunk_size, self.chunk_overlap)
        elif self.chunking_strategy == "semantic":
            chunks = semantic_chunk(content, self.chunk_size, self.chunk_overlap)
        else:
            # Default fallback
            chunks = context_aware_chunk(content, self.chunk_size, self.chunk_overlap)

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
            
            # CRITICAL: Preserve tool_call_id for GPT format tool messages
            # GPT format requires: role="tool", tool_call_id="...", content="string"
            if message.get("role") == "tool" and "tool_call_id" in message:
                chunked_msg["tool_call_id"] = message["tool_call_id"]

            chunked_messages.append(chunked_msg)

        return chunked_messages

    def _get_content_size(self, content: Any) -> int:
        """
        Calculate content size handling different formats.
        
        Args:
            content: Message content (string, list, or other)
            
        Returns:
            Size of the content in characters
        """
        if isinstance(content, str):
            return len(content)
        elif isinstance(content, list):
            # For Claude format with list content, sum all content
            return sum(len(str(item.get("content", ""))) for item in content)
        else:
            return len(str(content))

    def simple_chunk(self, content: str) -> List[str]:
        """Simple character-based chunking."""
        return simple_chunk(content, self.chunk_size, self.chunk_overlap)

    def context_aware_chunk(self, content: str) -> List[str]:
        """Context-aware chunking that tries to break on natural boundaries."""
        return context_aware_chunk(content, self.chunk_size, self.chunk_overlap)

    def semantic_chunk(self, content: str) -> List[str]:
        """Semantic chunking that tries to preserve logical units."""
        return semantic_chunk(content, self.chunk_size, self.chunk_overlap)
