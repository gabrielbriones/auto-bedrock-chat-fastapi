"""Tests for multi-tool-call scenarios with conversation history truncation

This test module verifies that the intelligent truncation strategy handles
scenarios where the assistant makes multiple sequential tool calls, and the
cumulative conversation history grows beyond token limits.

Key scenarios tested:
1. Multiple tool calls accumulate to exceed history threshold
2. Tool messages in history are truncated intelligently
3. New tool responses are handled separately from history
4. Conversation context is preserved despite truncation
"""

from unittest.mock import patch

import pytest

from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
from auto_bedrock_chat_fastapi.config import ChatConfig


class TestMultiToolCallScenarios:
    """Test scenarios with multiple sequential tool calls"""

    @pytest.fixture
    def config_with_small_history_threshold(self):
        """Create config with small thresholds for testing"""
        config = ChatConfig(
            tool_result_history_threshold=100_000,  # 100K
            tool_result_history_target=85_000,  # 85K (85% of threshold)
            tool_result_new_response_threshold=500_000,  # 500K
            tool_result_new_response_target=425_000,  # 425K (85% of threshold)
        )
        return config

    @pytest.fixture
    def client(self, config_with_small_history_threshold):
        """Create BedrockClient with small thresholds"""
        with patch("boto3.client"):
            client = BedrockClient(config=config_with_small_history_threshold)
            return client

    def test_multiple_large_tool_responses_in_history(self, client):
        """Test that multiple large tool responses at END are grouped and truncated proportionally

        Behavior: Consecutive tool messages at END of history (trailing tools) are treated as a
        group and share the new_response budget (500K) proportionally.
        Earlier tool messages use the history budget (50K).
        """
        # Create messages with multiple tool calls
        large_content = "x" * 150_000  # 150KB each (exceeds individual thresholds)

        messages = [
            {"role": "user", "content": "Get data from tools"},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tool1", "content": large_content}],
            },
            {"role": "assistant", "content": "Processing first tool result"},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tool2", "content": large_content}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tool3", "content": large_content}],
            },
        ]

        # Apply truncation to history
        truncated = client._tool_processor.truncate_tool_messages_in_history(messages)

        # Verify all messages are present
        assert len(truncated) == len(messages)

        # Message 1 (earlier tool): Uses history threshold (50K), so 150K > 50K gets truncated
        msg1_content_list = truncated[1].get("content", [])
        if isinstance(msg1_content_list, list):
            for item in msg1_content_list:
                if item.get("type") == "tool_result":
                    # Should be truncated to history target (42.5K)
                    assert len(item.get("content", "")) <= client.config.tool_result_history_target

        # Messages 3 & 4 (trailing tools, grouped): Share 500K new_response budget
        # Per tool threshold = 500K / 2 = 250K
        # Since 150K < 250K, they should NOT be truncated
        for i in [3, 4]:
            msg_content_list = truncated[i].get("content", [])
            if isinstance(msg_content_list, list):
                for item in msg_content_list:
                    if item.get("type") == "tool_result":
                        # Trailing tools with 2 in group: per-tool threshold is 500K/2 = 250K
                        # 150K < 250K so should not be truncated
                        assert len(item.get("content", "")) == 150_000  # Unchanged

    def test_gpt_format_tool_messages_truncated(self, client):
        """Test that GPT-format (role=tool) messages are truncated in history"""
        large_content = "x" * 60_000  # 60KB

        messages = [
            {"role": "user", "content": "Execute tool"},
            {"role": "assistant", "content": "Executing tool A", "tool_calls": [{"id": "tool_a"}]},
            {"role": "tool", "tool_call_id": "tool_a", "content": large_content},
            {"role": "assistant", "content": "Executing tool B", "tool_calls": [{"id": "tool_b"}]},
            {"role": "tool", "tool_call_id": "tool_b", "content": large_content},
        ]

        truncated = client._tool_processor.truncate_tool_messages_in_history(messages)

        # Verify tool messages in history are truncated
        tool_message_count = sum(1 for msg in truncated if msg.get("role") == "tool")
        assert tool_message_count == 2

        # Check that tool messages (except last) are truncated
        for i, msg in enumerate(truncated):
            if msg.get("role") == "tool" and i < len(truncated) - 1:
                # Should be truncated to target or smaller
                assert (
                    len(msg.get("content", "")) <= client.config.tool_result_history_target + 1000
                )  # Allow small margin

    def test_cumulative_history_size_calculation(self, client):
        """Test that cumulative history size is calculated correctly"""
        large_content = "x" * 40_000  # 40KB each

        # 3 tool messages = 120KB total, exceeds 100K threshold
        messages = [
            {"role": "user", "content": "Start"},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": large_content}],
            },
            {"role": "assistant", "content": "Process 1"},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t2", "content": large_content}],
            },
            {"role": "assistant", "content": "Process 2"},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t3", "content": large_content}],
            },
        ]

        truncated = client._tool_processor.truncate_tool_messages_in_history(messages)

        # Calculate total tool message size in truncated result (excluding last message)
        total_size_with_history_truncation = 0
        for msg in truncated[:-1]:  # Exclude last message
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for item in msg["content"]:
                    if item.get("type") == "tool_result":
                        total_size_with_history_truncation += len(item.get("content", ""))

        # Total of first 2 tool messages should be significantly reduced from 80KB
        # (last message is not truncated, so we only look at first 2)
        assert total_size_with_history_truncation < 100_000

        # Verify last message is NOT truncated
        last_tool_msg = truncated[-1]
        if isinstance(last_tool_msg.get("content"), list):
            for item in last_tool_msg["content"]:
                if item.get("type") == "tool_result":
                    assert len(item.get("content", "")) == len(large_content)

    def test_non_tool_messages_preserved(self, client):
        """Test that non-tool messages are preserved in full"""
        large_content = "x" * 50_000

        messages = [
            {"role": "user", "content": "Please help" * 1000},  # Keep full
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": large_content}],
            },
            {"role": "assistant", "content": "I'll help" * 1000},  # Keep full
        ]

        truncated = client._tool_processor.truncate_tool_messages_in_history(messages)

        # Verify user and assistant messages are preserved
        user_msg = next((m for m in truncated if m.get("role") == "user" and isinstance(m.get("content"), str)), None)
        assert user_msg is not None
        assert "Please help" in user_msg["content"]

        assistant_msg = next((m for m in truncated if m.get("role") == "assistant"), None)
        assert assistant_msg is not None
        assert "I'll help" in assistant_msg["content"]

    def test_last_tool_message_not_truncated(self, client):
        """Test that the last tool message is not truncated"""
        large_content = "x" * 80_000  # Exceeds history threshold

        messages = [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "small"}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t2", "content": large_content}],
            },
        ]

        truncated = client._tool_processor.truncate_tool_messages_in_history(messages)

        # Last tool message should keep its full content
        last_msg = truncated[-1]
        assert last_msg.get("role") == "user"
        last_content_list = last_msg.get("content", [])
        if isinstance(last_content_list, list):
            for item in last_content_list:
                if item.get("type") == "tool_result":
                    # Last message should NOT be truncated to target
                    assert len(item.get("content", "")) == len(large_content)

    def test_empty_messages_list(self, client):
        """Test handling of empty messages list"""
        messages = []
        truncated = client._tool_processor.truncate_tool_messages_in_history(messages)
        assert truncated == []

    def test_messages_without_tool_results(self, client):
        """Test that messages without tool results pass through unchanged"""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm doing well"},
        ]

        truncated = client._tool_processor.truncate_tool_messages_in_history(messages)

        # All messages should be preserved exactly
        assert len(truncated) == len(messages)
        for orig, trunc in zip(messages, truncated):
            assert orig == trunc

    def test_dict_format_tool_messages_truncated(self, client):
        """Test that dict-format (single dict with type=tool_result) messages are truncated"""
        large_content = "x" * 60_000  # 60KB

        messages = [
            {"role": "user", "content": "Get data from tools"},
            {
                "role": "user",
                "content": {"type": "tool_result", "tool_use_id": "tool1", "content": large_content},
            },
            {"role": "assistant", "content": "Processing first tool result"},
            {
                "role": "user",
                "content": {"type": "tool_result", "tool_use_id": "tool2", "content": large_content},
            },
        ]

        # Apply truncation to history
        truncated = client._tool_processor.truncate_tool_messages_in_history(messages)

        # Verify all messages are present
        assert len(truncated) == len(messages)

        # Verify dict-format tool messages were truncated (except the last one)
        for i, msg in enumerate(truncated):
            if i < len(truncated) - 1 and msg.get("role") == "user":
                content = msg.get("content", {})
                if isinstance(content, dict) and content.get("type") == "tool_result":
                    # Truncated content should be smaller than original
                    truncated_content = content.get("content", "")
                    truncated_content_size = len(truncated_content)
                    assert truncated_content_size <= client.config.tool_result_history_target
                    # Should actually be truncated, not full 60KB
                    assert truncated_content_size < len(large_content)

    def test_production_scale_dict_format_1mb_response(self, client):
        """Test production scenario: 1.2MB dict-format tool response from logs

        This reproduces the exact issue from production logs where a tool response
        of 1,223,822 characters in dict format was not being truncated.
        """
        # Simulate 1.2MB response (from production logs)
        production_scale_response = "x" * 1_223_822

        messages = [
            {"role": "user", "content": "Initial request"},
            {
                "role": "assistant",
                "content": "Calling large tool",
                "tool_calls": [{"id": "toolu_bdrk_011"}],
            },
            {
                "role": "user",
                "content": {
                    "type": "tool_result",
                    "tool_use_id": "toolu_bdrk_011",
                    "content": production_scale_response,
                },
            },
        ]

        # Apply truncation
        truncated = client._tool_processor.truncate_tool_messages_in_history(messages)

        # Verify all messages present
        assert len(truncated) == 3

        # Get the truncated tool response (should NOT be the last message, so should be truncated)
        tool_msg = truncated[2]
        assert tool_msg.get("role") == "user"

        tool_content = tool_msg.get("content", {})
        assert isinstance(tool_content, dict)
        assert tool_content.get("type") == "tool_result"

        # Verify it was truncated
        # Since this is the last message and exceeds new_response_threshold (500K),
        # it uses new_response_target (425K), not history_target (42.5K)
        truncated_content = tool_content.get("content", "")
        truncated_size = len(truncated_content)

        # The truncation algorithm may end up smaller than the target
        # What matters is: (1) it's much smaller than original, (2) it's reasonable size
        # Should be MUCH smaller than original 1.2MB
        original_size = 1_223_822
        assert (
            truncated_size < original_size
        ), f"Expected truncation but got {truncated_size} chars from {original_size} original"

        # Should still have meaningful content (not just error message)
        assert truncated_size > 1000, f"Truncation too aggressive: only {truncated_size} chars remaining"

        # Verify truncation was actually applied (significant reduction)
        reduction_ratio = truncated_size / original_size
        assert (
            reduction_ratio < 0.50
        ), f"Expected significant truncation but only reduced by {100*(1-reduction_ratio):.1f}%"

    def test_mixed_format_tool_messages_all_truncated(self, client):
        """Test that all three message formats are truncated correctly in one conversation"""
        large_content = "x" * 60_000

        messages = [
            {"role": "user", "content": "Multiple tool formats"},
            # Format 1: Claude format (list with tool_result)
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tool1", "content": large_content}],
            },
            {"role": "assistant", "content": "Processing tool 1"},
            # Format 2: GPT format (role=tool with string content)
            {"role": "tool", "tool_call_id": "tool2", "content": large_content},
            {"role": "assistant", "content": "Processing tool 2"},
            # Format 3: Dict format (role=user with dict content)
            {
                "role": "user",
                "content": {"type": "tool_result", "tool_use_id": "tool3", "content": large_content},
            },
        ]

        truncated = client._tool_processor.truncate_tool_messages_in_history(messages)

        # Verify all messages are present
        assert len(truncated) == len(messages)

        # Verify each tool message was truncated appropriately
        for i, msg in enumerate(truncated):
            # Check Format 1: Claude list format
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for item in msg["content"]:
                    if item.get("type") == "tool_result":
                        # Should be truncated (except potentially last message)
                        if i < len(truncated) - 1:
                            assert len(item.get("content", "")) < len(large_content)

            # Check Format 2: GPT format
            elif msg.get("role") == "tool":
                # Should be truncated (except potentially last message)
                if i < len(truncated) - 1:
                    assert len(msg.get("content", "")) < len(large_content)

            # Check Format 3: Dict format
            elif msg.get("role") == "user" and isinstance(msg.get("content"), dict):
                if msg["content"].get("type") == "tool_result":
                    # Should be truncated (except potentially last message)
                    if i < len(truncated) - 1:
                        assert len(msg["content"].get("content", "")) < len(large_content)


class TestToolMessagesTruncationIntegration:
    """Integration tests for tool message truncation in conversation flow"""

    @pytest.fixture
    def config_aggressive_truncation(self):
        """Config with aggressive truncation for testing"""
        return ChatConfig(
            tool_result_history_threshold=50_000,
            tool_result_history_target=42_500,
            tool_result_new_response_threshold=300_000,
            tool_result_new_response_target=255_000,
        )

    @pytest.fixture
    def client_aggressive(self, config_aggressive_truncation):
        """Client with aggressive truncation"""
        with patch("boto3.client"):
            return BedrockClient(config=config_aggressive_truncation)

    def test_five_sequential_tool_calls(self, client_aggressive):
        """Simulate 5 sequential tool calls with large responses"""
        messages = [
            {"role": "user", "content": "Fetch data"},
        ]

        # Simulate 5 tool calls
        for i in range(5):
            large_response = "x" * 40_000  # Each tool returns 40KB

            # Tool response from assistant
            messages.append({"role": "assistant", "content": f"Calling tool {i}", "tool_calls": [{"id": f"tool_{i}"}]})

            # Tool result
            messages.append({"role": "tool", "tool_call_id": f"tool_{i}", "content": large_response})

        # Apply truncation before sending to assistant
        truncated = client_aggressive._tool_processor.truncate_tool_messages_in_history(messages)

        # Verify all messages are still present
        assert len(truncated) >= len(messages) - 1  # At least most messages

        # Verify tool messages are truncated appropriately (excluding last message)
        total_tool_size = sum(
            len(m.get("content", ""))
            for m in truncated[:-1]  # Exclude last message from truncation check
            if m.get("role") == "tool" and isinstance(m.get("content"), str)
        )

        # First 4 tool messages (160KB) should be unchanged or reduced
        # (they might not be reduced if not all combined messages exceed history threshold)
        # Last tool message should be untruncated (40KB)
        assert total_tool_size <= 160_000

        # Last message should not be truncated
        last_msg = truncated[-1]
        if last_msg.get("role") == "tool":
            assert len(last_msg.get("content", "")) == 40_000

    def test_mixed_tool_and_non_tool_messages(self, client_aggressive):
        """Test handling of mixed tool and non-tool messages"""
        messages = [
            {"role": "user", "content": "Start"},
            {"role": "assistant", "content": "I'll help with multiple steps"},
            {"role": "tool", "tool_call_id": "tool_1", "content": "x" * 35_000},
            {"role": "assistant", "content": "Got first result, fetching more"},
            {"role": "tool", "tool_call_id": "tool_2", "content": "x" * 35_000},
            {"role": "assistant", "content": "Summarizing", "tool_calls": [{"id": "tool_3"}]},
            {"role": "tool", "tool_call_id": "tool_3", "content": "x" * 35_000},
        ]

        truncated = client_aggressive._tool_processor.truncate_tool_messages_in_history(messages)

        # Verify structure is maintained
        assert len(truncated) == len(messages)

        # Verify assistant messages are preserved
        assistant_count = sum(1 for m in truncated if m.get("role") == "assistant")
        assert assistant_count == 3

        # Verify user message is preserved
        user_msg = next((m for m in truncated if m.get("role") == "user"), None)
        assert user_msg is not None
        assert user_msg["content"] == "Start"


class TestCumulativeHistoryLimits:
    """Integration tests to verify cumulative conversation history stays within limits"""

    @pytest.fixture
    def config_realistic(self):
        """Config with realistic thresholds simulating production"""
        return ChatConfig(
            tool_result_history_threshold=50_000,
            tool_result_history_target=42_500,
            tool_result_new_response_threshold=500_000,
            tool_result_new_response_target=425_000,
        )

    @pytest.fixture
    def client_realistic(self, config_realistic):
        """Client with realistic configuration"""
        with patch("boto3.client"):
            return BedrockClient(config=config_realistic)

    def _calculate_total_size(self, messages):
        """Helper to calculate total message size in bytes (approximation)"""
        total = 0
        for msg in messages:
            if isinstance(msg.get("content"), str):
                total += len(msg["content"].encode("utf-8"))
            elif isinstance(msg.get("content"), list):
                for item in msg["content"]:
                    if isinstance(item, dict):
                        total += len(str(item.get("content", "")).encode("utf-8"))
        return total

    def test_cumulative_history_under_limit_after_truncation(self, client_realistic):
        """Verify cumulative history stays under safe limit after truncation"""
        # Simulate realistic conversation with 10 sequential tool calls
        messages = [
            {"role": "user", "content": "Please perform multiple analysis steps"},
        ]

        # Each tool returns 30KB (well under individual 50K threshold)
        # But 10 calls = 300KB cumulative (exceeds 50K threshold)
        for i in range(10):
            messages.append({"role": "assistant", "content": f"Analyzing with tool {i}"})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"tool_{i}",
                    "content": "x" * 30_000,  # 30KB each
                }
            )

        # Apply truncation
        truncated = client_realistic._tool_processor.truncate_tool_messages_in_history(messages)

        # Calculate size
        total_size = self._calculate_total_size(truncated)

        # Verify truncation brought down cumulative size
        # Original would be ~320KB (10 x 30KB + messages)
        # After truncation should be significantly reduced
        assert total_size < 320_000

        # All messages should still be present (no removal, just truncation)
        assert len(truncated) == len(messages)

    def test_conversation_grows_but_stays_manageable(self, client_realistic):
        """Test that even with growing conversation, truncation is applied when thresholds exceeded"""
        messages = [
            {"role": "user", "content": "Start conversation"},
            {"role": "assistant", "content": "Ready to help"},
        ]

        # Simulate 5 sequential tool calls with LARGE responses that EXCEED threshold
        for round_num in range(5):
            tool_content = "x" * 60_000  # 60KB per call (exceeds 50K threshold)

            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"call_{round_num}",
                            "content": tool_content,
                        }
                    ],
                }
            )
            messages.append({"role": "assistant", "content": f"Processing result {round_num}"})

        # Before truncation: 5 x 60KB = 300KB tool responses alone
        before_size = self._calculate_total_size(messages)
        assert before_size > 290_000

        # Apply truncation
        truncated = client_realistic._tool_processor.truncate_tool_messages_in_history(messages)

        # After truncation: should be reduced (all but last tool message truncated to 42.5K)
        after_size = self._calculate_total_size(truncated)

        # Should be reduced: 4 truncated (4 x 42.5K = 170K) + 1 last (60K) = 230K tool content
        # Plus assistant messages and initial user message
        assert after_size < before_size

    def test_non_tool_messages_preserved_in_large_conversation(self, client_realistic):
        """Verify that non-tool messages (dialogue context) are preserved despite truncation"""
        user_dialogue = [
            "I need to analyze this dataset",
            "That looks good, continue",
            "Now let's verify the results",
            "Perfect, what's the summary?",
            "Excellent work",
        ]

        assistant_dialogue = [
            "I'll analyze the data in stages",
            "Got first results, processing next step",
            "Verification complete, analyzing consistency",
            "Summary: All steps successful",
            "Analysis complete with high confidence",
        ]

        messages = []
        for i in range(5):
            messages.append({"role": "user", "content": user_dialogue[i]})
            messages.append({"role": "assistant", "content": assistant_dialogue[i]})

            # Large tool response between exchanges
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"analysis_{i}",
                    "content": "x" * 35_000,
                }
            )

        # Apply truncation
        truncated = client_realistic._tool_processor.truncate_tool_messages_in_history(messages)

        # Verify all user dialogue preserved
        for dialogue in user_dialogue:
            found = any(dialogue in msg.get("content", "") for msg in truncated if msg.get("role") == "user")
            assert found, f"User dialogue '{dialogue}' not found after truncation"

        # Verify all assistant dialogue preserved
        for dialogue in assistant_dialogue:
            found = any(dialogue in str(msg.get("content", "")) for msg in truncated if msg.get("role") == "assistant")
            assert found, f"Assistant dialogue '{dialogue}' not found after truncation"

    def test_last_tool_message_never_truncated(self, client_realistic):
        """Verify that the last tool message (current response) is never truncated"""
        large_response = "x" * 60_000  # Exceeds history threshold

        messages = [
            {"role": "user", "content": "Initial query"},
            {"role": "assistant", "content": "Processing"},
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 30_000},
            {"role": "assistant", "content": "Got first result"},
            {"role": "tool", "tool_call_id": "t2", "content": "x" * 30_000},
            {"role": "assistant", "content": "Got second result"},
            {"role": "tool", "tool_call_id": "t3", "content": large_response},
        ]

        truncated = client_realistic._tool_processor.truncate_tool_messages_in_history(messages)

        # Verify last message is NOT truncated
        last_msg = truncated[-1]
        assert last_msg.get("role") == "tool"
        assert len(last_msg.get("content", "")) == len(large_response)

    def test_mixed_claude_and_gpt_format_messages(self, client_realistic):
        """Test truncation works correctly with mixed message formats"""
        messages = [
            {"role": "user", "content": "Query"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "claude_tool_1",
                        "content": "x" * 35_000,
                    }
                ],
            },
            {"role": "assistant", "content": "Processing"},
            {"role": "tool", "tool_call_id": "gpt_tool_1", "content": "x" * 35_000},
            {"role": "assistant", "content": "Got result"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "claude_tool_2",
                        "content": "x" * 35_000,
                    }
                ],
            },
        ]

        truncated = client_realistic._tool_processor.truncate_tool_messages_in_history(messages)

        # Verify all messages are present
        assert len(truncated) == len(messages)

        # Verify structure is maintained
        claude_messages = [m for m in truncated if m.get("role") == "user" and isinstance(m.get("content"), list)]
        gpt_messages = [m for m in truncated if m.get("role") == "tool"]

        assert len(claude_messages) > 0
        assert len(gpt_messages) > 0

    def test_zero_size_messages_handled(self, client_realistic):
        """Test that empty or zero-size messages don't cause issues"""
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": ""},
            {"role": "tool", "tool_call_id": "t1", "content": ""},
            {"role": "user", "content": "Real query"},
            {"role": "assistant", "content": "Real response"},
        ]

        truncated = client_realistic._tool_processor.truncate_tool_messages_in_history(messages)

        # Should handle without error
        assert len(truncated) == len(messages)

    def test_maximum_growth_scenario(self, client_realistic):
        """Test extreme scenario: many large tool responses that exceed threshold"""
        # Simulate worst case: 10 tool calls, each returning 60KB (exceeds 50KB threshold)
        messages = [
            {"role": "user", "content": "Analyze everything"},
        ]

        for i in range(10):
            messages.append({"role": "assistant", "content": f"Step {i}"})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"task_{i}",
                    "content": "x" * 60_000,  # 60KB each (exceeds threshold)
                }
            )

        # Before truncation: ~600KB of tool content
        before = self._calculate_total_size(messages)
        assert before > 590_000

        # Apply truncation
        truncated = client_realistic._tool_processor.truncate_tool_messages_in_history(messages)

        # After truncation should be significantly smaller
        after = self._calculate_total_size(truncated)

        # Should reduce from 600KB to approximately:
        # - 9 truncated messages @ 42.5KB each = 382.5KB
        # - 1 last message = 60KB
        # - Assistant messages and initial query ~ 200 bytes
        # Total ~ 450KB (25% reduction)
        assert after < before
        reduction_ratio = (before - after) / before
        assert reduction_ratio > 0.15
