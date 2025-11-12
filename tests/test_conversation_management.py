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

        trimmed = client._manage_conversation_history(self.long_conversation.copy())

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

        trimmed = client._manage_conversation_history(self.long_conversation.copy())

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

        trimmed = client._manage_conversation_history(self.long_conversation.copy())

        # Should have at most 8 messages
        assert len(trimmed) <= 8

        # First message should be system message
        assert trimmed[0]["role"] == "system"

        # Should prefer user/assistant messages over tool messages
        tool_count = sum(1 for msg in trimmed if msg.get("role") == "tool")
        user_assistant_count = sum(
            1 for msg in trimmed if msg.get("role") in ["user", "assistant"]
        )

        # Should have fewer tool messages in the pruned result
        assert user_assistant_count > 0, "Should preserve user/assistant messages"
        original_tool_count = sum(
            1 for msg in self.long_conversation if msg.get("role") == "tool"
        )
        assert tool_count <= original_tool_count

    def test_no_trimming_needed(self):
        """Test that short conversations are not trimmed"""
        config = ChatConfig(
            max_conversation_messages=25,  # More than our test conversation
            conversation_strategy="sliding_window",
        )
        client = BedrockClient(config)

        original_length = len(self.long_conversation)
        trimmed = client._manage_conversation_history(self.long_conversation.copy())

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

        trimmed = client._manage_conversation_history(self.long_conversation.copy())

        # Should have exactly 5 messages
        assert len(trimmed) == 5

        # May or may not start with system message (depends on what's in the last 5)
        # Should be the last 5 messages from the original
        expected = self.long_conversation[-5:]
        assert trimmed == expected

    def test_conversation_without_system_message(self):
        """Test conversation management when no system message exists"""
        conversation_no_system = [
            msg for msg in self.long_conversation if msg.get("role") != "system"
        ]

        config = ChatConfig()
        # Manually set the values to bypass environment loading
        config.max_conversation_messages = 6
        config.conversation_strategy = "sliding_window"
        config.preserve_system_message = True
        client = BedrockClient(config)

        trimmed = client._manage_conversation_history(conversation_no_system.copy())

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
        trimmed = mock_client._manage_conversation_history(messages)

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
        result = client._check_and_chunk_messages(messages)

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
        result = client._check_and_chunk_messages(messages)

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
        result = client._check_and_chunk_messages(messages)

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
        chunks = client._simple_chunk(content)

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
        chunks = client._context_aware_chunk(content)

        # Should create chunks
        assert len(chunks) >= 1

        # Chunks should try to break on natural boundaries
        for chunk in chunks:
            # Should not be empty
            assert len(chunk.strip()) > 0

    def test_mixed_message_sizes(self):
        """Test handling of mixed message sizes in conversation"""
        config = ChatConfig()
        config.max_message_size = 50000
        config.chunk_size = 40000
        config.enable_message_chunking = True
        client = BedrockClient(config)

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": self.small_content},
            {"role": "assistant", "content": self.medium_content},
            # This should be chunked
            {"role": "tool", "content": self.large_content},
            {"role": "user", "content": self.small_content},
        ]

        result = client._check_and_chunk_messages(messages)

        # Should have more messages due to chunking the large tool response
        assert len(result) > len(messages)

        # Find the chunked messages (tool messages with chunk indicators)
        chunked_messages = [msg for msg in result if "[CHUNK" in msg.get("content", "")]
        assert len(chunked_messages) > 1

        # Verify all chunked messages are from the tool role
        for msg in chunked_messages:
            assert msg["role"] == "tool"

    def test_chunk_message_structure(self):
        """Test that chunked messages have proper structure without metadata"""
        config = ChatConfig()
        config.max_message_size = 1000
        config.chunk_size = 800
        config.enable_message_chunking = True
        client = BedrockClient(config)

        large_message = {"role": "user", "content": "X" * 2000}
        chunked = client._chunk_large_message(large_message)

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
                    assert (
                        "This message was too large and has been split into chunks"
                        in chunk["content"]
                    )

    def test_tool_response_chunking_scenario(self):
        """Test realistic scenario of chunking a large tool response (like a log file)"""
        config = ChatConfig()
        config.max_message_size = 5000
        config.chunk_size = 4000
        config.chunking_strategy = "preserve_context"
        config.chunk_overlap = 200
        config.enable_message_chunking = True
        client = BedrockClient(config)

        # Simulate a large log file response
        log_content = ""
        for i in range(100):
            log_content += f"2024-11-07 10:{i:02d}:00 INFO - Processing item {i}\n"
            log_content += (
                f"2024-11-07 10:{i:02d}:05 DEBUG - Item {i} validation successful\n"
            )
            log_content += (
                f"2024-11-07 10:{i:02d}:10 INFO - Item {i} completed successfully\n\n"
            )

        messages = [
            {"role": "user", "content": "Show me the application logs"},
            {"role": "tool", "content": log_content},
        ]

        result = client._check_and_chunk_messages(messages)

        # Should have chunked the tool response
        assert len(result) > 2

        # First message (user request) should be unchanged
        assert result[0]["content"] == "Show me the application logs"
        assert "[CHUNK" not in result[0]["content"]

        # Tool messages should be chunked
        tool_messages = [msg for msg in result[1:] if msg["role"] == "tool"]
        assert len(tool_messages) > 1

        # Each tool chunk should indicate it's part of a series
        for msg in tool_messages:
            assert "[CHUNK" in msg["content"]


class TestChunkingConfiguration:
    """Test chunking configuration validation"""

    def test_chunking_config_defaults(self):
        """Test default values for chunking configuration"""
        config = ChatConfig()

        assert config.max_message_size == 100000
        assert config.chunk_size == 80000
        assert config.chunking_strategy == "preserve_context"
        assert config.chunk_overlap == 1000
        assert config.enable_message_chunking is True

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
