"""Tests for conversation history management functionality"""

import pytest

from auto_bedrock_chat_fastapi import BedrockClient, ChatConfig
from auto_bedrock_chat_fastapi.config import load_config
from auto_bedrock_chat_fastapi.exceptions import ConfigurationError


class TestConversationManagement:
    """Test conversation history management strategies"""

    def setup_method(self):
        """Set up test data"""
        self.long_conversation = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "I'm doing well, thank you!"},
            {"role": "user", "content": "What's the weather like?"},
            {
                "role": "assistant",
                "content": "I don't have access to real-time weather data.",
            },
            {"role": "user", "content": "Can you help me with math?"},
            {
                "role": "assistant",
                "content": "Of course! What math problem would you like help with?",
            },
            {"role": "tool", "content": "Tool call result for calculator"},
            {"role": "user", "content": "What's 2+2?"},
            {"role": "assistant", "content": "2 + 2 = 4"},
            {"role": "tool", "content": "Another tool result"},
            {"role": "user", "content": "Thanks! What about 5+5?"},
            {"role": "assistant", "content": "5 + 5 = 10"},
            {"role": "user", "content": "And 10+10?"},
            {"role": "assistant", "content": "10 + 10 = 20"},
            {"role": "user", "content": "Perfect, thanks!"},
            {
                "role": "assistant",
                "content": "You're welcome! Happy to help with math.",
            },
            {"role": "user", "content": "What about 100+200?"},
            {"role": "assistant", "content": "100 + 200 = 300"},
        ]

    def test_sliding_window_strategy(self):
        """Test sliding window conversation management"""
        config = ChatConfig()
        # Manually set the values to bypass environment loading
        config.max_conversation_messages = 8
        config.conversation_strategy = "sliding_window"
        config.preserve_system_message = True
        client = BedrockClient(config)

        trimmed = client._conversation_manager.manage_conversation_history(self.long_conversation.copy())

        # Should have exactly 8 messages
        assert len(trimmed) == 8

        # First message should be system message
        assert trimmed[0]["role"] == "system"
        assert trimmed[0]["content"] == "You are a helpful assistant."

        # Should preserve the most recent messages
        assert trimmed[-1]["content"] == "100 + 200 = 300"
        assert trimmed[-2]["content"] == "What about 100+200?"

    def test_truncate_strategy(self):
        """Test truncate conversation management"""
        config = ChatConfig()
        # Manually set the values to bypass environment loading
        config.max_conversation_messages = 6
        config.conversation_strategy = "truncate"
        config.preserve_system_message = True
        client = BedrockClient(config)

        trimmed = client._conversation_manager.manage_conversation_history(self.long_conversation.copy())

        # Should have exactly 6 messages
        assert len(trimmed) == 6

        # First message should be system message
        assert trimmed[0]["role"] == "system"

        # Should have 5 most recent messages after system
        assert len(trimmed) == 6

    def test_smart_prune_strategy(self):
        """Test smart prune conversation management"""
        config = ChatConfig()
        # Manually set the values to bypass environment loading
        config.max_conversation_messages = 8
        config.conversation_strategy = "smart_prune"
        config.preserve_system_message = True
        client = BedrockClient(config)

        trimmed = client._conversation_manager.manage_conversation_history(self.long_conversation.copy())

        # Should have at most 8 messages
        assert len(trimmed) <= 8

        # First message should be system message
        assert trimmed[0]["role"] == "system"

        # Should prefer user/assistant messages over tool messages
        tool_count = sum(1 for msg in trimmed if msg.get("role") == "tool")
        user_assistant_count = sum(1 for msg in trimmed if msg.get("role") in ["user", "assistant"])

        # Should have fewer tool messages in the pruned result
        assert user_assistant_count > 0, "Should preserve user/assistant messages"
        original_tool_count = sum(1 for msg in self.long_conversation if msg.get("role") == "tool")
        assert tool_count <= original_tool_count

    def test_no_trimming_needed(self):
        """Test that short conversations are not trimmed"""
        config = ChatConfig(
            max_conversation_messages=25,  # More than our test conversation
            conversation_strategy="sliding_window",
        )
        client = BedrockClient(config)

        original_length = len(self.long_conversation)
        trimmed = client._conversation_manager.manage_conversation_history(self.long_conversation.copy())

        # Should be unchanged
        assert len(trimmed) == original_length
        assert trimmed == self.long_conversation

    def test_preserve_system_message_disabled(self):
        """Test conversation management without preserving system message"""
        config = ChatConfig()
        # Manually set the values to bypass environment loading
        config.max_conversation_messages = 5
        config.conversation_strategy = "truncate"
        config.preserve_system_message = False
        client = BedrockClient(config)

        trimmed = client._conversation_manager.manage_conversation_history(self.long_conversation.copy())

        # Should have exactly 5 messages
        assert len(trimmed) == 5

        # May or may not start with system message (depends on what's in the last 5)
        # Should be the last 5 messages from the original
        expected = self.long_conversation[-5:]
        assert trimmed == expected

    def test_conversation_without_system_message(self):
        """Test conversation management when no system message exists"""
        conversation_no_system = [msg for msg in self.long_conversation if msg.get("role") != "system"]

        config = ChatConfig()
        # Manually set the values to bypass environment loading
        config.max_conversation_messages = 6
        config.conversation_strategy = "sliding_window"
        config.preserve_system_message = True
        client = BedrockClient(config)

        trimmed = client._conversation_manager.manage_conversation_history(conversation_no_system.copy())

        # Should have exactly 6 messages (no system to preserve)
        assert len(trimmed) == 6

        # Should be the last 6 messages
        assert trimmed == conversation_no_system[-6:]


class TestConversationConfiguration:
    """Test conversation management configuration"""

    def test_valid_conversation_strategies(self):
        """Test that valid conversation strategies are accepted"""
        valid_strategies = ["sliding_window", "truncate", "smart_prune"]

        for strategy in valid_strategies:
            config = load_config(conversation_strategy=strategy)
            assert config.conversation_strategy == strategy

    def test_invalid_conversation_strategy(self):
        """Test that invalid conversation strategies are rejected"""
        with pytest.raises(ConfigurationError):
            load_config(conversation_strategy="invalid_strategy")

    def test_conversation_config_defaults(self):
        """Test default values for conversation management"""
        config = ChatConfig()

        assert config.max_conversation_messages == 20
        assert config.conversation_strategy == "sliding_window"
        assert config.preserve_system_message is True

    def test_conversation_config_overrides(self):
        """Test that conversation management config can be overridden"""
        config = load_config(
            max_conversation_messages=15,
            conversation_strategy="smart_prune",
            preserve_system_message=False,
        )

        assert config.max_conversation_messages == 15
        assert config.conversation_strategy == "smart_prune"
        assert config.preserve_system_message is False

    def test_max_conversation_messages_validation(self):
        """Test validation of max_conversation_messages"""
        # Should accept positive integers
        config = load_config(max_conversation_messages=10)
        assert config.max_conversation_messages == 10

        # Should reject zero or negative values
        with pytest.raises(ConfigurationError):
            load_config(max_conversation_messages=0)

        with pytest.raises(ConfigurationError):
            load_config(max_conversation_messages=-1)


class TestConversationIntegration:
    """Test conversation management integration with BedrockClient"""

    @pytest.fixture
    def mock_client(self):
        """Create a BedrockClient with conversation management enabled"""
        config = ChatConfig()
        # Manually set the values to bypass environment loading
        config.max_conversation_messages = 5
        config.conversation_strategy = "sliding_window"
        config.preserve_system_message = True
        return BedrockClient(config)

    def test_conversation_management_integration(self, mock_client):
        """Test that conversation management is properly integrated"""
        # Create a long conversation
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Message 1"},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Message 2"},
            {"role": "assistant", "content": "Response 2"},
            {"role": "user", "content": "Message 3"},
            {"role": "assistant", "content": "Response 3"},
            {"role": "user", "content": "Message 4"},
        ]

        # Test the conversation management method directly
        trimmed = mock_client._conversation_manager.manage_conversation_history(messages)

        # Should be trimmed to 5 messages with system preserved
        assert len(trimmed) == 5
        assert trimmed[0]["role"] == "system"

        # Verify the trimming logic worked correctly
        expected_content = [
            "System prompt",
            "Response 2",
            "Message 3",
            "Response 3",
            "Message 4",
        ]
        actual_content = [msg["content"] for msg in trimmed]
        assert actual_content == expected_content


class TestMessageChunking:
    """Test message chunking functionality for large messages"""

    def setup_method(self):
        """Set up test data"""
        # Create a large message that exceeds default limits
        self.large_content = "This is a very long message. " * 4000  # ~120KB
        self.medium_content = "This is a medium message. " * 1000  # ~26KB
        self.small_content = "This is a small message."

    def test_small_message_not_chunked(self):
        """Test that small messages are not chunked"""
        config = ChatConfig()
        config.max_message_size = 100000
        config.enable_message_chunking = True
        client = BedrockClient(config)

        messages = [{"role": "user", "content": self.small_content}]
        result = client._message_chunker.check_and_chunk_messages(messages, client._tool_processor)

        # Should be unchanged
        assert len(result) == 1
        assert result[0]["content"] == self.small_content

    def test_large_message_chunked(self):
        """Test that large messages are chunked"""
        config = ChatConfig()
        config.max_message_size = 50000  # 50KB
        config.chunk_size = 40000  # 40KB
        config.chunking_strategy = "simple"
        config.enable_message_chunking = True
        client = BedrockClient(config)

        messages = [{"role": "user", "content": self.large_content}]
        result = client._message_chunker.check_and_chunk_messages(messages, client._tool_processor)

        # Should be chunked into multiple messages
        assert len(result) > 1

        # Each chunk should have chunk indicators in content
        for i, msg in enumerate(result):
            assert "[CHUNK" in msg["content"]
            # Check that chunk number is in content (e.g., [CHUNK 1/3])
            assert f"[CHUNK {i + 1}/{len(result)}]" in msg["content"]

    def test_chunking_disabled(self):
        """Test that chunking can be disabled"""
        config = ChatConfig()
        config.max_message_size = 1000  # Small limit
        config.enable_message_chunking = False
        client = BedrockClient(config)

        messages = [{"role": "user", "content": self.large_content}]
        result = client._message_chunker.check_and_chunk_messages(messages, client._tool_processor)

        # Should be unchanged even though message is large
        assert len(result) == 1
        assert result[0]["content"] == self.large_content

    def test_simple_chunking_strategy(self):
        """Test simple character-based chunking"""
        config = ChatConfig()
        config.chunking_strategy = "simple"
        config.chunk_size = 100
        config.chunk_overlap = 20
        client = BedrockClient(config)

        content = "A" * 250  # 250 characters
        chunks = client._message_chunker.simple_chunk(content)

        # Should create multiple chunks
        assert len(chunks) > 1

        # Verify chunk sizes and overlap
        for i, chunk in enumerate(chunks[:-1]):  # All except last
            assert len(chunk) <= 100
            if i < len(chunks) - 1:  # Check overlap with next chunk
                next_chunk = chunks[i + 1]
                # Some overlap should exist (last chars of current = first
                # chars of next)
                assert len(chunk) > 80  # Should be close to chunk_size
                assert len(next_chunk) > 0  # Next chunk should not be empty

    def test_context_aware_chunking(self):
        """Test context-aware chunking that preserves natural boundaries"""
        config = ChatConfig()
        config.chunking_strategy = "preserve_context"
        config.chunk_size = 100
        config.chunk_overlap = 10
        client = BedrockClient(config)

        content = "Sentence one. Sentence two. Sentence three.\n\nParagraph two starts here. More content follows."
        chunks = client._message_chunker.context_aware_chunk(content)

        # Should create chunks
        assert len(chunks) >= 1

        # Chunks should try to break on natural boundaries
        for chunk in chunks:
            # Should not be empty
            assert len(chunk.strip()) > 0

    def test_mixed_message_sizes(self):
        """Test handling of mixed message sizes - tool messages use truncation, user messages can chunk"""
        config = ChatConfig()
        config.max_message_size = 50000
        config.chunk_size = 40000
        config.enable_message_chunking = True
        # Set truncation threshold higher so truncation is used for tool messages
        config.tool_result_history_threshold = 200000
        client = BedrockClient(config)

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": self.small_content},
            {"role": "assistant", "content": self.medium_content},
            # Tool message: large but under history truncation threshold
            {"role": "tool", "content": self.large_content},
            {"role": "user", "content": self.small_content},
        ]

        result = client._message_chunker.check_and_chunk_messages(messages, client._tool_processor)

        # Tool messages are NOT chunked - they use truncation
        # So the large tool message is not chunked, just truncated if needed
        # Result should have same number of messages as input
        assert len(result) == len(messages)

        # Verify message order is preserved
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"
        assert result[3]["role"] == "tool"
        assert result[4]["role"] == "user"

        # Tool messages should not have chunk indicators
        # (only regular user/assistant messages can be chunked if enabled)
        tool_msg = result[3]
        assert "[CHUNK" not in tool_msg["content"]

    def test_chunk_message_structure(self):
        """Test that chunked messages have proper structure without metadata"""
        config = ChatConfig()
        config.max_message_size = 1000
        config.chunk_size = 800
        config.enable_message_chunking = True
        client = BedrockClient(config)

        large_message = {"role": "user", "content": "X" * 2000}
        chunked = client._message_chunker.chunk_large_message(large_message)

        assert len(chunked) > 1

        for i, chunk in enumerate(chunked):
            # Should have standard message fields only (no metadata to avoid
            # API errors)
            assert "role" in chunk
            assert "content" in chunk
            assert chunk["role"] == "user"

            # Should not have metadata field that causes ValidationException
            assert "metadata" not in chunk

            # Should have chunk information embedded in content
            if len(chunked) > 1:
                assert f"[CHUNK {i + 1}/{len(chunked)}]" in chunk["content"]
                if i == 0:  # First chunk should have explanation
                    assert "This message was too large and has been split into chunks" in chunk["content"]

    def test_tool_response_chunking_scenario(self):
        """Test that tool responses are truncated, not chunked"""
        config = ChatConfig()
        config.max_message_size = 5000
        config.chunk_size = 4000
        config.chunking_strategy = "preserve_context"
        config.chunk_overlap = 200
        config.enable_message_chunking = True
        # Force truncation by setting threshold lower than log content
        config.tool_result_history_threshold = 3000
        client = BedrockClient(config)

        # Simulate a large log file response
        log_content = ""
        for i in range(100):
            log_content += f"2024-11-07 10:{i:02d}:00 INFO - Processing item {i}\n"
            log_content += f"2024-11-07 10:{i:02d}:05 DEBUG - Item {i} validation successful\n"
            log_content += f"2024-11-07 10:{i:02d}:10 INFO - Item {i} completed successfully\n\n"

        messages = [
            {"role": "user", "content": "Show me the application logs"},
            {"role": "tool", "content": log_content},
        ]

        result = client._message_chunker.check_and_chunk_messages(messages, client._tool_processor)

        # Tool messages are NOT chunked - they use truncation
        # So result count should equal input count
        assert len(result) == 2

        # First message (user request) should be unchanged
        assert result[0]["content"] == "Show me the application logs"
        assert "[CHUNK" not in result[0]["content"]

        # Tool message should be truncated (with truncation marker), not chunked
        tool_msg = result[1]
        assert tool_msg["role"] == "tool"
        # Tool messages use truncation, not chunking markers
        assert "[CHUNK" not in tool_msg["content"]


class TestChunkingConfiguration:
    """Test chunking configuration validation"""

    def test_chunking_config_defaults(self):
        """Test default values for chunking configuration"""
        config = ChatConfig()

        assert config.max_message_size == 100000
        assert config.chunk_size == 80000
        assert config.chunking_strategy == "preserve_context"
        assert config.chunk_overlap == 1000
        assert config.enable_message_chunking is False

    def test_chunking_config_overrides(self):
        """Test that chunking configuration can be overridden"""
        config = load_config(
            max_message_size=50000,
            chunk_size=40000,
            chunking_strategy="simple",
            chunk_overlap=500,
            enable_message_chunking=False,
        )

        assert config.max_message_size == 50000
        assert config.chunk_size == 40000
        assert config.chunking_strategy == "simple"
        assert config.chunk_overlap == 500
        assert config.enable_message_chunking is False

    def test_invalid_chunking_strategy(self):
        """Test that invalid chunking strategies are rejected"""
        with pytest.raises(ConfigurationError):
            load_config(chunking_strategy="invalid_strategy")

    def test_invalid_chunk_sizes(self):
        """Test validation of chunk size parameters"""
        # Negative values should be rejected
        with pytest.raises(ConfigurationError):
            load_config(max_message_size=-1)

        with pytest.raises(ConfigurationError):
            load_config(chunk_size=0)

        with pytest.raises(ConfigurationError):
            load_config(chunk_overlap=-1)

    def test_chunk_size_validation_relationship(self):
        """Test that chunk_size must be smaller than max_message_size"""
        with pytest.raises(ConfigurationError):
            load_config(max_message_size=1000, chunk_size=1000)  # Equal should fail

        with pytest.raises(ConfigurationError):
            load_config(max_message_size=1000, chunk_size=1500)  # Larger should fail


class TestToolUseToolResultPairManagement:
    """Test that tool_use/tool_result pairs stay together during trimming"""

    @pytest.fixture
    def bedrock_client(self):
        """Create a BedrockClient for testing"""
        config = ChatConfig()
        config.max_conversation_messages = 6
        config.preserve_system_message = True
        config.conversation_strategy = "sliding_window"
        return BedrockClient(config)

    def test_sliding_window_keeps_tool_pairs_together(self, bedrock_client):
        """
        Test that sliding_window removes tool_result if its tool_use is not selected.

        This tests the fix for the issue where old tool_result messages would be kept
        while their matching tool_use assistant messages were trimmed away.
        """
        # Create a scenario with multiple tool_use/tool_result pairs
        messages = [
            {"role": "system", "content": "System"},
            # Old tool pair (should be removed when trimming)
            {"role": "assistant", "tool_calls": [{"id": "old_call_1", "name": "func", "input": {}}]},
            {"role": "user", "tool_results": [{"tool_call_id": "old_call_1", "content": "result1"}]},
            # Normal message
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            # Recent tool pair (should be kept)
            {"role": "assistant", "tool_calls": [{"id": "recent_call", "name": "func", "input": {}}]},
            {"role": "user", "tool_results": [{"tool_call_id": "recent_call", "content": "recent_result"}]},
            # Add more messages to trigger trimming (max is 6)
            {"role": "user", "content": "msg8"},
            {"role": "assistant", "content": "response8"},
            {"role": "user", "content": "msg9"},
        ]

        assert len(messages) > 6
        trimmed = bedrock_client._conversation_manager.sliding_window_messages(messages)

        # Verify system message is preserved
        assert trimmed[0]["role"] == "system"

        # Verify NO orphaned tool_results (all should have matching tool_use in previous message)
        for i, msg in enumerate(trimmed):
            if msg.get("role") == "user" and msg.get("tool_results"):
                # Must have previous assistant with matching tool_use
                assert i > 0, "tool_result at start of trimmed messages"
                prev_msg = trimmed[i - 1]
                assert (
                    prev_msg.get("role") == "assistant"
                ), f"tool_result message but previous is {prev_msg.get('role')}, not assistant"

                result_ids = {r.get("tool_call_id") for r in msg.get("tool_results", [])}
                tool_use_ids = {c.get("id") for c in prev_msg.get("tool_calls", [])}
                assert result_ids.issubset(
                    tool_use_ids
                ), f"tool_result IDs {result_ids} not in previous tool_use IDs {tool_use_ids}"

    def test_truncate_messages_removes_orphaned_tool_results(self, bedrock_client):
        """Test that _truncate_messages removes tool_results without matching tool_use"""
        bedrock_client.config.conversation_strategy = "truncate"

        messages = [
            {"role": "system", "content": "System"},
            # Old orphaned pair
            {"role": "assistant", "tool_calls": [{"id": "orphaned_id", "name": "func", "input": {}}]},
            {"role": "user", "tool_results": [{"tool_call_id": "orphaned_id", "content": "data"}]},
            # Recent messages
            {"role": "user", "content": "msg"},
            {"role": "assistant", "content": "response"},
            *[{"role": "user", "content": f"msg{i}"} for i in range(6, 20)],
        ]

        trimmed = bedrock_client._conversation_manager.truncate_messages(messages)

        # Verify no orphaned tool_results
        for msg in trimmed:
            if msg.get("role") == "user" and msg.get("tool_results"):
                # If we kept a tool_result, its tool_use must be in the history
                for result in msg.get("tool_results", []):
                    tool_use_id = result.get("tool_call_id")
                    found = False
                    for prev_msg in trimmed:
                        if prev_msg.get("role") == "assistant" and prev_msg.get("tool_calls"):
                            for call in prev_msg.get("tool_calls", []):
                                if call.get("id") == tool_use_id:
                                    found = True
                                    break
                    assert found, f"tool_result {tool_use_id} has no matching tool_use in trimmed messages"

    def test_smart_prune_maintains_tool_pairs(self, bedrock_client):
        """Test that _smart_prune_messages maintains tool_use/tool_result pairs"""
        bedrock_client.config.conversation_strategy = "smart_prune"

        messages = [
            {"role": "system", "content": "System"},
            {"role": "assistant", "tool_calls": [{"id": "call1", "name": "func", "input": {}}]},
            {"role": "user", "tool_results": [{"tool_call_id": "call1", "content": "result"}]},
            {"role": "user", "content": "msg"},
            {"role": "assistant", "content": "response"},
            *[{"role": "user", "content": f"msg{i}"} for i in range(6, 20)],
        ]

        trimmed = bedrock_client._conversation_manager.smart_prune_messages(messages)

        # Verify structure is valid
        for i, msg in enumerate(trimmed):
            if msg.get("role") == "user" and msg.get("tool_results"):
                # Check previous is assistant with matching tool_use
                if i > 0:
                    prev_msg = trimmed[i - 1]
                    if prev_msg.get("role") == "assistant":
                        result_ids = {r.get("tool_call_id") for r in msg.get("tool_results", [])}
                        tool_use_ids = {c.get("id") for c in prev_msg.get("tool_calls", [])}
                        assert result_ids.issubset(tool_use_ids)

    def test_multiple_tool_pairs_in_history(self, bedrock_client):
        """Test trimming with multiple tool_use/tool_result pairs"""
        messages = [
            {"role": "system", "content": "System"},
            # Pair 1 (old, should be removed)
            {"role": "assistant", "tool_calls": [{"id": "id1", "name": "func", "input": {}}]},
            {"role": "user", "tool_results": [{"tool_call_id": "id1", "content": "result1"}]},
            # Pair 2 (old, should be removed)
            {"role": "assistant", "tool_calls": [{"id": "id2", "name": "func", "input": {}}]},
            {"role": "user", "tool_results": [{"tool_call_id": "id2", "content": "result2"}]},
            {"role": "user", "content": "msg"},
            # Pair 3 (recent, should be kept with dependencies)
            {"role": "assistant", "tool_calls": [{"id": "id3", "name": "func", "input": {}}]},
            {"role": "user", "tool_results": [{"tool_call_id": "id3", "content": "result3"}]},
            # Add more to trigger trimming
            *[{"role": "user", "content": f"msg{i}"} for i in range(9, 25)],
        ]

        trimmed = bedrock_client._conversation_manager.sliding_window_messages(messages)

        # Verify all tool_results have matching tool_uses
        for i, msg in enumerate(trimmed):
            if msg.get("role") == "user" and msg.get("tool_results"):
                # Find the most recent preceding assistant with tool_use
                found_match = False
                for j in range(i - 1, -1, -1):
                    prev = trimmed[j]
                    if prev.get("role") == "assistant" and prev.get("tool_calls"):
                        for result in msg.get("tool_results", []):
                            for call in prev.get("tool_calls", []):
                                if call.get("id") == result.get("tool_call_id"):
                                    found_match = True
                                    break
                    if found_match:
                        break
                assert found_match, "tool_result not matched with any preceding tool_use"

    def test_orphaned_tool_result_from_earlier_call_is_removed(self, bedrock_client):
        """
        Test the real-world scenario: Old tool_result should be removed when its tool_use is trimmed.

        This tests the case from production logs where:
        1. First tool call creates [assistant with tool_use] + [user with tool_result]
        2. Some conversation happens
        3. Another tool call
        4. When trimming happens, the old tool_result gets orphaned if tool_use assistant is removed
        """
        bedrock_client.config.max_conversation_messages = 6

        messages = [
            {"role": "system", "content": "You are helpful"},
            # First tool call pair (OLD - should be removed during trim)
            {
                "role": "assistant",
                "tool_calls": [{"id": "old_tool_id", "name": "func1", "input": {}}],
                "content": "Calling first tool",
            },
            {
                "role": "tool",
                "content": "Tool 1 results",
                "tool_calls": [{"id": "old_tool_id", "name": "func1", "input": {}}],
                "tool_results": [{"tool_call_id": "old_tool_id", "content": "LARGE DATA " * 10000}],
            },
            # Conversation in between
            {"role": "user", "content": "How are the results?"},
            {"role": "assistant", "content": "Results look good"},
            # Second tool call pair (NEW - should be kept)
            {
                "role": "assistant",
                "tool_calls": [{"id": "new_tool_id", "name": "func2", "input": {}}],
                "content": "Calling second tool",
            },
            {
                "role": "tool",
                "content": "Tool 2 results",
                "tool_calls": [{"id": "new_tool_id", "name": "func2", "input": {}}],
                "tool_results": [{"tool_call_id": "new_tool_id", "content": "More data"}],
            },
            # Add more conversation to trigger trimming
            {"role": "user", "content": "list all jobs"},
        ]

        # Before calling _manage_conversation_history directly, use the public method
        # that applies both trimming AND orphan cleanup
        # Call the internal method to get trimmed messages
        trimmed = bedrock_client._conversation_manager.manage_conversation_history(messages)

        # Verify the old tool_result is removed
        tool_result_ids = set()
        tool_use_ids = set()

        for msg in trimmed:
            # Collect tool_use IDs
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for call in msg.get("tool_calls", []):
                    tool_use_ids.add(call.get("id"))

            # Collect tool_result IDs
            if msg.get("role") == "tool" and msg.get("tool_results"):
                for result in msg.get("tool_results", []):
                    tool_result_ids.add(result.get("tool_call_id"))

        # The old_tool_id should NOT be in results since it was removed
        assert "old_tool_id" not in tool_result_ids, "Old orphaned tool_result should have been removed"

        # The new_tool_id should be in both
        assert "new_tool_id" in tool_use_ids, "New tool_use should be present"
        assert "new_tool_id" in tool_result_ids, "New tool_result should be present"

        # Verify no orphaned tool_results exist
        for tool_result_id in tool_result_ids:
            assert tool_result_id in tool_use_ids, f"tool_result {tool_result_id} has no matching tool_use"

    def test_orphan_removal_after_truncation_pipeline(self, bedrock_client):
        """
        Integration test: Replicate the exact production scenario.

        This tests the full pipeline that includes:
        1. Message trimming (sliding_window)
        2. Tool message truncation (truncate_tool_messages_in_history)
        3. Final orphan removal

        Scenario from production logs:
        - First tool call: download logs for 2 job IDs (large data)
        - Conversation continues
        - Second tool call: list all jobs
        - After trimming to 6 messages, the old tool_result becomes orphaned
        """
        bedrock_client.config.max_conversation_messages = 6

        # Build conversation history that matches production scenario
        messages = [
            {"role": "system", "content": "You are helpful"},
            # First user request
            {
                "role": "user",
                "content": "download logs for 38ac64d0-208f-4eda-a45d-5f75be15b2a6 and 337b267d-445a-4fe5-b248-99b756d2f47a",
            },
            # First tool call pair (large data - will be truncated)
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "toolu_old_1", "name": "log_get", "input": {}},
                    {"id": "toolu_old_2", "name": "log_get", "input": {}},
                ],
                "content": "Retrieving logs",
            },
            {
                "role": "tool",
                "content": "Tool results",
                "tool_calls": [
                    {"id": "toolu_old_1", "name": "log_get", "input": {}},
                    {"id": "toolu_old_2", "name": "log_get", "input": {}},
                ],
                "tool_results": [
                    {"tool_call_id": "toolu_old_1", "content": "LOG DATA " * 50000},
                    {"tool_call_id": "toolu_old_2", "content": "LOG DATA " * 50000},
                ],
            },
            # AI response to logs
            {"role": "assistant", "content": "Logs retrieved successfully"},
            # Second user request (this is where trimming kicks in)
            {"role": "user", "content": "list all jobs"},
            # Second tool call pair (new)
            {
                "role": "assistant",
                "tool_calls": [{"id": "toolu_new", "name": "jobs_get", "input": {}}],
                "content": "Listing jobs",
            },
            {
                "role": "tool",
                "content": "Tool results",
                "tool_calls": [{"id": "toolu_new", "name": "jobs_get", "input": {}}],
                "tool_results": [{"tool_call_id": "toolu_new", "content": "JOB DATA " * 100000}],
            },
        ]

        # Now simulate the full pipeline from chat_completion:
        # 1. Manage conversation history (trim)
        messages_after_trim = bedrock_client._conversation_manager.manage_conversation_history(messages)

        # 2. Truncate tool messages (using the tool processor)
        messages_after_truncate = bedrock_client._tool_processor.truncate_tool_messages_in_history(messages_after_trim)

        # 3. Final orphan removal
        messages_final = bedrock_client._conversation_manager.remove_orphaned_tool_results(messages_after_truncate)

        # Verify final messages don't have orphaned tool_results
        available_tool_use_ids = set()
        tool_result_ids = set()

        for msg in messages_final:
            # Collect all tool_use IDs
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for call in msg.get("tool_calls", []):
                    available_tool_use_ids.add(call.get("id"))

            # Collect all tool_result IDs
            if msg.get("role") == "tool" and msg.get("tool_results"):
                for result in msg.get("tool_results", []):
                    tool_result_ids.add(result.get("tool_call_id"))

        # Critical assertion: every tool_result must have a corresponding tool_use
        for tool_result_id in tool_result_ids:
            assert tool_result_id in available_tool_use_ids, (
                f"ORPHANED tool_result {tool_result_id} has no matching tool_use. "
                f"Available tool_use IDs: {available_tool_use_ids}"
            )

        # The old tool IDs should NOT be in results (they were trimmed away)
        assert "toolu_old_1" not in tool_result_ids, "Old tool ID should have been removed"
        assert "toolu_old_2" not in tool_result_ids, "Old tool ID should have been removed"

    def test_production_scenario_with_debug_logging(self):
        """
        Replicates the exact production error scenario with explicit logging.

        This is the scenario from 2025-12-09 13:26:16 logs where:
        - User requests logs for two jobs (creates 2 large tool results)
        - Conversation grows to 8 messages
        - New request triggers trimming to 6 messages
        - One of the trimmed messages contained the assistant's tool_calls
        - But tool_results still referenced the now-missing tool_uses
        - Orphan cleanup should catch this and remove the orphaned results
        """
        # Build conversation matching production logs exactly
        # Just need meaningful tool_use IDs and structure; don't need massive data
        messages = [
            {"role": "system", "content": "You are an expert analyst."},
            {"role": "user", "content": "download logs for job1 and job2"},
            {
                "role": "assistant",
                "content": "I'll retrieve the logs.",
                "tool_calls": [
                    {"id": "toolu_bdrk_015Da6g5srFjm72ic7ZDHAM2", "name": "log_get", "arguments": {"job_id": "job1"}},
                    {"id": "toolu_bdrk_01ATXyXfAd98KeVQQSwqzfia", "name": "log_get", "arguments": {"job_id": "job2"}},
                ],
            },
            {
                "role": "tool",
                "content": None,
                "tool_results": [
                    {"tool_call_id": "toolu_bdrk_015Da6g5srFjm72ic7ZDHAM2", "result": "Large log data"},
                    {"tool_call_id": "toolu_bdrk_01ATXyXfAd98KeVQQSwqzfia", "result": "Large log data"},
                ],
            },
            {"role": "assistant", "content": "Here's the summary..."},
            {"role": "user", "content": "list all jobs"},
            {
                "role": "assistant",
                "content": "Getting jobs...",
                "tool_calls": [
                    {"id": "toolu_bdrk_015WTyZhYH116pGLdH8HE6Y7", "name": "jobs_get", "arguments": {}},
                ],
            },
            {
                "role": "tool",
                "content": None,
                "tool_results": [
                    {"tool_call_id": "toolu_bdrk_015WTyZhYH116pGLdH8HE6Y7", "result": "Job list data"},
                ],
            },
        ]

        # Simulate trimming with config that forces conversation to 6 messages
        config = ChatConfig(
            model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            max_conversation_messages=6,
            conversation_strategy="sliding_window",
        )

        client = BedrockClient(config)

        # Step 1: Trim conversation
        trimmed = client._conversation_manager.manage_conversation_history(messages)
        print(f"\nAfter trim: {len(trimmed)} messages (expected 6)")
        # Note: actual trim may not reach exactly 6 due to how sliding_window works
        # The important thing is we'll have orphaned tool_results after this

        # For this test, let's manually simulate the production scenario where
        # trimming removed an assistant message but tool_result messages remain
        # This is the core issue that orphan cleanup should fix
        messages_with_orphans = [
            {"role": "system", "content": "System prompt"},
            # Message 2 with first tool_calls is intentionally removed/trimmed away
            # But message 4 (the tool message) still references them!
            {"role": "assistant", "content": "Here's the summary...", "tool_calls": []},
            {"role": "user", "content": "list all jobs"},
            {
                "role": "assistant",
                "content": "Getting jobs...",
                "tool_calls": [
                    {"id": "toolu_bdrk_015WTyZhYH116pGLdH8HE6Y7", "name": "jobs_get", "arguments": {}},
                ],
            },
            {
                "role": "tool",
                "content": None,
                "tool_results": [
                    # These are ORPHANED - the assistant that created them is gone
                    {"tool_call_id": "toolu_bdrk_015Da6g5srFjm72ic7ZDHAM2", "result": "Large log data"},
                    {"tool_call_id": "toolu_bdrk_01ATXyXfAd98KeVQQSwqzfia", "result": "Large log data"},
                    # This one is valid
                    {"tool_call_id": "toolu_bdrk_015WTyZhYH116pGLdH8HE6Y7", "result": "Job list data"},
                ],
            },
        ]

        # Step 2: Remove orphans (THE KEY FIX)
        cleaned = client._conversation_manager.remove_orphaned_tool_results(messages_with_orphans)

        # CRITICAL ASSERTIONS
        # After cleanup, verify no orphans remain
        available_tool_use_ids = set()
        for msg in cleaned:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    available_tool_use_ids.add(tc.get("id"))

        tool_result_ids = set()
        for msg in cleaned:
            if msg.get("role") == "tool" and msg.get("tool_results"):
                for result in msg.get("tool_results", []):
                    tool_result_ids.add(result.get("tool_call_id"))

        print("\nFinal message set:")
        print(f"  Available tool_use IDs: {available_tool_use_ids}")
        print(f"  Tool_result IDs: {tool_result_ids}")
        print(f"  Cleaned messages: {len(cleaned)}")

        # The critical assertion from production: no orphans
        for tool_result_id in tool_result_ids:
            assert tool_result_id in available_tool_use_ids, (
                f"ORPHANED tool_result {tool_result_id} still present! " f"Available: {available_tool_use_ids}"
            )

        # Verify we removed the orphaned tool results message (since it had orphans)
        # OR it was cleaned up by removing the orphaned tool_results
        orphan_tool_ids = {"toolu_bdrk_015Da6g5srFjm72ic7ZDHAM2", "toolu_bdrk_01ATXyXfAd98KeVQQSwqzfia"}
        remaining_in_results = orphan_tool_ids & tool_result_ids
        assert (
            len(remaining_in_results) == 0
        ), f"Orphaned tool IDs should have been removed, but found: {remaining_in_results}"

        # Verify we kept the valid tool result
        assert "toolu_bdrk_015WTyZhYH116pGLdH8HE6Y7" in tool_result_ids, "Current valid tool ID should be preserved"

        print("\n✓ Production scenario test PASSED")
        print("  - Removed orphaned tool results")
        print("  - Preserved valid tool results")
        print("  - No orphans remain for Bedrock API")

    def test_sliding_window_prevents_orphans_during_trim(self):
        """
        CRITICAL TEST: Verify sliding window doesn't create orphaned tool_results.

        This replicates the exact production scenario from 2025-12-09 13:42:31:
        - 8 messages total
        - Messages contain tool_call/tool_result pairs that need to stay together
        - Trimming to 6 messages should NOT split tool pairs

        The bug was that sliding_window would keep message [3] (tool_results)
        but remove message [2] (assistant with tool_calls), creating an orphan.
        """
        config = ChatConfig()
        config.max_conversation_messages = 6
        config.conversation_strategy = "sliding_window"

        client = BedrockClient(config=config)

        # EXACT scenario from production logs
        messages = [
            # [0] System message
            {"role": "system", "content": "You are a helpful assistant."},
            # [1] User request
            {"role": "user", "content": "download logs"},
            # [2] Assistant with tool calls - THIS WILL BE TRIMMED AWAY
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "toolu_bdrk_01YD2xnFxRh7JfLJ7j2vUvC7", "name": "download_logs", "input": {}},
                    {"id": "toolu_bdrk_014aBs8gMk3eu12YJDn6N6eu", "name": "get_status", "input": {}},
                ],
            },
            # [3] User with tool results for [2]'s calls - THIS WILL REMAIN (more recent)
            {
                "role": "user",
                "content": None,
                "tool_results": [
                    {"tool_call_id": "toolu_bdrk_01YD2xnFxRh7JfLJ7j2vUvC7", "result": "Large log data"},
                    {"tool_call_id": "toolu_bdrk_014aBs8gMk3eu12YJDn6N6eu", "result": "Status data"},
                ],
            },
            # [4] Assistant response
            {"role": "assistant", "content": "I've retrieved the logs and status."},
            # [5] User next request
            {"role": "user", "content": "list all jobs"},
            # [6] Assistant with tool calls - THIS WILL REMAIN
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "toolu_bdrk_01Q9gzEJZ3Fv1RY51m26jz2C", "name": "list_jobs", "input": {}}],
            },
            # [7] User with tool results for [6]'s calls - THIS WILL REMAIN
            {
                "role": "user",
                "content": None,
                "tool_results": [{"tool_call_id": "toolu_bdrk_01Q9gzEJZ3Fv1RY51m26jz2C", "result": "Job list data"}],
            },
        ]

        print("\n=== PRODUCTION SCENARIO TEST ===")
        print("Before sliding_window trim:")
        print(f"  Total messages: {len(messages)}")
        for i, msg in enumerate(messages):
            tool_calls = len(msg.get("tool_calls", []))
            tool_results = len(msg.get("tool_results", []))
            print(f"  [{i}] {msg['role']:10s} tool_calls={tool_calls} tool_results={tool_results}")

        # Apply sliding window trim
        trimmed = client._conversation_manager.sliding_window_messages(messages)

        print("\nAfter sliding_window trim:")
        print(f"  Total messages: {len(trimmed)}")
        for i, msg in enumerate(trimmed):
            tool_calls = len(msg.get("tool_calls", []))
            tool_results = len(msg.get("tool_results", []))
            print(f"  [{i}] {msg['role']:10s} tool_calls={tool_calls} tool_results={tool_results}")

        # KEY VALIDATION: No orphaned tool_results
        available_tool_use_ids = set()
        for msg in trimmed:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    tool_id = tc.get("id")
                    available_tool_use_ids.add(tool_id)
                    print(f"  Found tool_use: {tool_id}")

        orphaned_tool_results = []
        for msg_idx, msg in enumerate(trimmed):
            if msg.get("role") == "user" and msg.get("tool_results"):
                for result in msg.get("tool_results", []):
                    tool_id = result.get("tool_call_id")
                    if tool_id not in available_tool_use_ids:
                        orphaned_tool_results.append((msg_idx, tool_id))
                        print(f"  ✗ ORPHANED tool_result: {tool_id} at message {msg_idx}")
                    else:
                        print(f"  ✓ Valid tool_result: {tool_id}")

        # CRITICAL ASSERTION
        assert len(orphaned_tool_results) == 0, (
            f"FAILED: Found {len(orphaned_tool_results)} orphaned tool_results! "
            f"This replicates the production bug. Orphans: {orphaned_tool_results}. "
            f"Available tool_use IDs: {available_tool_use_ids}"
        )

        # Additional check: ensure we kept tool pairs together
        # If [7] (tool_results for latest tool_use) is in the result, [6] (assistant with that tool_use) must be too
        for i, msg in enumerate(trimmed):
            if msg.get("role") == "user" and msg.get("tool_results"):
                for result in msg.get("tool_results", []):
                    tool_id = result.get("tool_call_id")
                    # Verify this tool_use is in the trimmed messages
                    found_tool_use = False
                    for _, check_msg in enumerate(trimmed):
                        if check_msg.get("role") == "assistant" and check_msg.get("tool_calls"):
                            for tc in check_msg.get("tool_calls", []):
                                if tc.get("id") == tool_id:
                                    found_tool_use = True
                                    break
                    assert (
                        found_tool_use
                    ), f"Tool pair broken: tool_result {tool_id} at msg {i} has no matching tool_use in trimmed messages"

        print("\n✓ PRODUCTION SCENARIO TEST PASSED")
        print("  - Sliding window preserved tool pairs")
        print("  - No orphaned tool_results")
        print("  - Ready for Bedrock API")

    def test_remove_orphaned_tool_results_bedrock_format(self):
        """
        CRITICAL TEST: Verify orphan removal works with Bedrock/Claude format.

        This replicates the exact production bug from 2025-12-09 14:11:04 where
        messages were already formatted for Bedrock API:
        - tool_use blocks are embedded in assistant message content array
        - tool_result blocks are embedded in user message content array

        The original _remove_orphaned_tool_results only checked for tool_calls
        field on assistant messages, missing the embedded format.

        Error: "messages.0.content.0: unexpected `tool_use_id` found in `tool_result`
        blocks: toolu_bdrk_015QuXs2tUAWcqgw6Hi3Be6Z"
        """
        config = ChatConfig()
        client = BedrockClient(config=config)

        # Messages in Bedrock/Claude API format (after format_bedrock_messages)
        # This is what gets sent to the API
        messages_bedrock_format = [
            # [0] System message
            {"role": "system", "content": "You are a helpful assistant."},
            # [1] User message with orphaned tool_result blocks
            # The assistant message that had the corresponding tool_use was trimmed away!
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_bdrk_015QuXs2tUAWcqgw6Hi3Be6Z",  # ORPHAN!
                        "content": "[TOOL RESULT TRUNCATED] Large log data...",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_bdrk_01V2ZKnqQTmZ19PbbU7iecXi",  # ORPHAN!
                        "content": "[TOOL RESULT TRUNCATED] More large log data...",
                    },
                ],
            },
            # [2] Assistant response
            {"role": "assistant", "content": "## Log Download Complete\n\nI've successfully retrieved the logs..."},
            # [3] User request
            {"role": "user", "content": "list all jobs"},
            # [4] Assistant with tool_use embedded in content (Bedrock format)
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_bdrk_01PMzbERhrarKtwFe3DxGMSx",
                        "name": "jobs_jobs_get",
                        "input": {},
                    }
                ],
            },
            # [5] User with tool_result embedded in content (Bedrock format)
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_bdrk_01PMzbERhrarKtwFe3DxGMSx",  # VALID - matches [4]
                        "content": "[TOOL RESULT TRUNCATED] Job list data...",
                    }
                ],
            },
        ]

        print("\n=== BEDROCK FORMAT ORPHAN TEST ===")
        print("Before orphan cleanup:")
        for i, msg in enumerate(messages_bedrock_format):
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, list):
                types = [item.get("type") for item in content if isinstance(item, dict)]
                print(f"  [{i}] {role}: content array with {types}")
            else:
                preview = str(content)[:50] if content else "(empty)"
                print(f"  [{i}] {role}: {preview}...")

        # Apply orphan cleanup
        cleaned = client._conversation_manager.remove_orphaned_tool_results(messages_bedrock_format)

        print("\nAfter orphan cleanup:")
        for i, msg in enumerate(cleaned):
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, list):
                types = [item.get("type") for item in content if isinstance(item, dict)]
                ids = [item.get("tool_use_id") or item.get("id") for item in content if isinstance(item, dict)]
                print(f"  [{i}] {role}: content array with {types}, ids={ids}")
            else:
                preview = str(content)[:50] if content else "(empty)"
                print(f"  [{i}] {role}: {preview}...")

        # Collect all tool_use IDs from assistant messages (Bedrock format)
        available_tool_use_ids = set()
        for msg in cleaned:
            if msg.get("role") == "assistant":
                content = msg.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_use":
                            available_tool_use_ids.add(item.get("id"))

        print(f"\nAvailable tool_use IDs: {available_tool_use_ids}")

        # Collect all tool_result IDs from user messages (Bedrock format)
        tool_result_ids = set()
        for msg in cleaned:
            if msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_result":
                            tool_result_ids.add(item.get("tool_use_id"))

        print(f"Remaining tool_result IDs: {tool_result_ids}")

        # KEY VALIDATION: No orphaned tool_results
        orphaned_ids = tool_result_ids - available_tool_use_ids
        assert len(orphaned_ids) == 0, (
            f"FAILED: Found {len(orphaned_ids)} orphaned tool_results in Bedrock format! "
            f"Orphans: {orphaned_ids}. Available: {available_tool_use_ids}. "
            f"This replicates the production bug that caused ValidationException"
        )

        # Verify the orphaned tool_results were removed
        orphan_ids_original = {"toolu_bdrk_015QuXs2tUAWcqgw6Hi3Be6Z", "toolu_bdrk_01V2ZKnqQTmZ19PbbU7iecXi"}
        remaining_orphans = orphan_ids_original & tool_result_ids
        assert (
            len(remaining_orphans) == 0
        ), f"Original orphan IDs should have been removed, but found: {remaining_orphans}"

        # Verify the valid tool_result was kept
        assert (
            "toolu_bdrk_01PMzbERhrarKtwFe3DxGMSx" in tool_result_ids
        ), "Valid tool_result ID should have been preserved"

        print("\n✓ BEDROCK FORMAT ORPHAN TEST PASSED")
        print("  - Detected orphaned tool_results in content array format")
        print(f"  - Removed {len(orphan_ids_original)} orphaned tool_results")
        print("  - Preserved valid tool_result")
        print("  - Ready for Bedrock API (no ValidationException)")

    def test_llama_format_tool_truncation(self):
        """
        CRITICAL TEST: Verify tool truncation works with Llama format.

        This replicates the production bug from 2025-12-09 15:08:39 where
        Llama tool results weren't truncated because they didn't have
        the metadata needed to identify them as tool messages.

        Llama format: role="user" with string content and is_tool_result=True marker

        Error: "This model's maximum context length is 131072 tokens"
        """
        config = ChatConfig()
        client = BedrockClient(config=config)

        # Messages in Llama format (after format_bedrock_messages in Llama parser)
        # Note: Llama tool results are plain user messages marked with is_tool_result
        large_content = "X" * 700_000  # 700K chars - way over threshold

        messages_llama_format = [
            # [0] System message
            {"role": "system", "content": "You are a helpful assistant."},
            # [1] User request
            {"role": "user", "content": "download logs for job-1 and job-2"},
            # [2] Assistant (empty content, tool calls tracked elsewhere in Llama)
            {"role": "assistant", "content": ""},
            # [3] Llama format tool result - LARGE, should be truncated
            {
                "role": "user",
                "content": large_content,
                "is_tool_result": True,
                "tool_call_id": "llama-tool-0",
            },
            # [4] Llama format tool result - LARGE, should be truncated
            {
                "role": "user",
                "content": large_content,
                "is_tool_result": True,
                "tool_call_id": "llama-tool-1",
            },
        ]

        print("\n=== LLAMA FORMAT TOOL TRUNCATION TEST ===")
        print("Before truncation:")
        for i, msg in enumerate(messages_llama_format):
            role = msg.get("role")
            content = msg.get("content", "")
            is_tool = msg.get("is_tool_result", False)
            size = len(content) if isinstance(content, str) else 0
            print(f"  [{i}] {role}: {size:,} chars, is_tool_result={is_tool}")

        # Apply tool truncation
        truncated = client._tool_processor.truncate_tool_messages_in_history(messages_llama_format)

        print("\nAfter truncation:")
        for i, msg in enumerate(truncated):
            role = msg.get("role")
            content = msg.get("content", "")
            is_tool = msg.get("is_tool_result", False)
            size = len(content) if isinstance(content, str) else 0
            print(f"  [{i}] {role}: {size:,} chars, is_tool_result={is_tool}")

        # KEY VALIDATION: Large Llama tool results should be truncated
        for i, msg in enumerate(truncated):
            if msg.get("is_tool_result"):
                content = msg.get("content", "")
                size = len(content) if isinstance(content, str) else 0
                # Should be truncated to under the threshold
                assert size < 700_000, (
                    f"FAILED: Llama tool result at [{i}] was NOT truncated! "
                    f"Size: {size:,} chars. Expected < 700,000 chars."
                )
                print(f"  ✓ Tool result [{i}] truncated: {size:,} chars")

        print("\n✓ LLAMA FORMAT TOOL TRUNCATION TEST PASSED")
        print("  - Recognized Llama format tool results (is_tool_result=True)")
        print("  - Truncated large tool results")
        print("  - Ready for Llama context window")
