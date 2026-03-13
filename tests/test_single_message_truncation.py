"""Tests for Task 3.2: Single-Message Truncation (Plain Text Path).

Covers:
- ``_truncate_oversized_messages()`` — the main pass
- ``_truncate_single_message()`` — dispatch by content type
- ``_truncate_list_content_items()`` — proportional list truncation
- ``_item_content_size()`` — helper for size calc
- ``_truncate_plain_text()`` label parameter (backward compat)
- Integration: pipeline order in ``preprocess_messages()``
"""

import logging
from unittest.mock import AsyncMock, MagicMock

from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.message_preprocessor import MessagePreprocessor, get_content_size

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_config(
    threshold: int = 1_000,
    target: int = 500,
    ai_summarization: bool = False,
) -> ChatConfig:
    """Build a ChatConfig with custom truncation thresholds for tests."""
    return ChatConfig(
        BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=threshold,
        BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=target,
        BEDROCK_ENABLE_AI_SUMMARIZATION=ai_summarization,
    )


def _make_preprocessor(
    threshold: int = 1_000,
    target: int = 500,
    ai_summarization: bool = False,
    config: ChatConfig | None = None,
) -> MessagePreprocessor:
    """Build a MessagePreprocessor with a config suitable for testing."""
    if config is None:
        config = _make_config(threshold, target, ai_summarization)
    return MessagePreprocessor(config=config)


def _big_string(n: int) -> str:
    """Return a repeating string of exactly *n* characters."""
    return ("x" * 100 + "\n") * (n // 101) + "x" * (n % 101)


# ── _truncate_oversized_messages ─────────────────────────────────────────


class TestTruncateOversizedMessages:
    """Tests for ``_truncate_oversized_messages()``."""

    async def test_no_config_uses_defaults(self):
        """No config → defaults are used; small messages pass through."""
        pp = MessagePreprocessor(config=None)
        msgs = [{"role": "user", "content": _big_string(5_000)}]
        result = await pp._truncate_oversized_messages(msgs)
        # 5 000 chars is well under the default 500K threshold
        assert result == msgs

    async def test_under_threshold_unchanged(self):
        """Messages under the threshold are passed through unmodified."""
        pp = _make_preprocessor(threshold=10_000, target=5_000)
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "short message"},
            {"role": "assistant", "content": "short reply"},
        ]
        result = await pp._truncate_oversized_messages(msgs)
        assert result == msgs

    async def test_string_content_truncated(self):
        """A user message with string content > threshold is truncated."""
        pp = _make_preprocessor(threshold=1_000, target=500)
        big = _big_string(2_000)
        msgs = [{"role": "user", "content": big}]
        result = await pp._truncate_oversized_messages(msgs)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        new_size = get_content_size(result[0])
        # The truncated result includes markers, so it should be roughly
        # around target (not exactly target, due to head/tail + markers).
        assert new_size < len(big)
        assert "TRUNCATED" in result[0]["content"]

    async def test_assistant_string_truncated(self):
        """Assistant messages are also subject to general truncation."""
        pp = _make_preprocessor(threshold=1_000, target=500)
        big = _big_string(2_000)
        msgs = [{"role": "assistant", "content": big}]
        result = await pp._truncate_oversized_messages(msgs)

        assert result[0]["role"] == "assistant"
        assert "TRUNCATED" in result[0]["content"]

    async def test_system_message_truncated_when_oversized(self):
        """Even system messages are truncated if they exceed threshold."""
        pp = _make_preprocessor(threshold=500, target=200)
        big = _big_string(1_000)
        msgs = [{"role": "system", "content": big}]
        result = await pp._truncate_oversized_messages(msgs)
        assert "TRUNCATED" in result[0]["content"]

    async def test_non_dict_items_preserved(self):
        """Non-dict items in the list are kept as-is."""
        pp = _make_preprocessor()
        msgs = ["not-a-dict", 42, None]
        result = await pp._truncate_oversized_messages(msgs)
        assert result == msgs

    async def test_metadata_preserved(self):
        """Extra fields on the message dict survive truncation."""
        pp = _make_preprocessor(threshold=500, target=200)
        big = _big_string(1_000)
        msgs = [
            {
                "role": "user",
                "content": big,
                "custom_field": "preserved",
                "is_tool_result": True,
                "tool_call_id": "tc_123",
            }
        ]
        result = await pp._truncate_oversized_messages(msgs)
        assert result[0]["custom_field"] == "preserved"
        assert result[0]["is_tool_result"] is True
        assert result[0]["tool_call_id"] == "tc_123"

    async def test_multiple_oversized_messages(self):
        """Multiple oversized messages are each truncated independently."""
        pp = _make_preprocessor(threshold=1_000, target=500)
        msgs = [
            {"role": "user", "content": _big_string(2_000)},
            {"role": "assistant", "content": "OK"},
            {"role": "user", "content": _big_string(3_000)},
        ]
        result = await pp._truncate_oversized_messages(msgs)
        assert len(result) == 3
        assert "TRUNCATED" in result[0]["content"]
        assert result[1]["content"] == "OK"
        assert "TRUNCATED" in result[2]["content"]

    async def test_ai_enabled_falls_back_to_plain_text(self):
        """When AI summarization is enabled and the LLM call raises,
        the plain-text fallback is used."""
        pp = _make_preprocessor(threshold=1_000, target=500, ai_summarization=True)
        # Mock LLM client whose chat_completion raises to trigger fallback
        llm_client = AsyncMock()
        llm_client.chat_completion.side_effect = RuntimeError("boom")
        msgs = [{"role": "user", "content": _big_string(2_000)}]
        pp.llm_client = llm_client
        result = await pp._truncate_oversized_messages(msgs)
        assert "TRUNCATED" in result[0]["content"]

    async def test_ai_not_enabled_without_llm_client(self):
        """AI summarization does not activate when llm_client is None."""
        pp = _make_preprocessor(threshold=1_000, target=500, ai_summarization=True)
        msgs = [{"role": "user", "content": _big_string(2_000)}]
        # Even though ai_summarization=True, llm_client=None → plain text
        result = await pp._truncate_oversized_messages(msgs)
        assert "TRUNCATED" in result[0]["content"]

    async def test_logging_on_truncation(self, caplog):
        """Info-level log emitted for each truncated message."""
        pp = _make_preprocessor(threshold=1_000, target=500)
        msgs = [{"role": "user", "content": _big_string(2_000)}]
        with caplog.at_level(logging.INFO):
            await pp._truncate_oversized_messages(msgs)

        log_text = caplog.text
        assert "Truncated oversized user message" in log_text
        assert "Oversized message truncation finished: 1 message(s) truncated" in log_text

    async def test_no_logging_when_no_truncation(self, caplog):
        """No 'Oversized message truncation' log if nothing was truncated."""
        pp = _make_preprocessor(threshold=10_000, target=5_000)
        msgs = [{"role": "user", "content": "small"}]
        with caplog.at_level(logging.INFO):
            await pp._truncate_oversized_messages(msgs)
        assert "Oversized message truncation finished" not in caplog.text


# ── _truncate_single_message — format dispatch ──────────────────────────


class TestTruncateSingleMessageContent:
    """Tests for ``_truncate_single_message()`` dispatch logic."""

    async def test_string_content(self):
        pp = _make_preprocessor()
        msg = {"role": "user", "content": _big_string(2_000)}
        result = await pp._truncate_single_message(msg, target=500)
        assert isinstance(result["content"], str)
        assert "MESSAGE CONTENT TRUNCATED" in result["content"]

    async def test_list_content_delegates_to_list_handler(self):
        pp = _make_preprocessor()
        items = [
            {"type": "text", "text": _big_string(2_000)},
        ]
        msg = {"role": "user", "content": items}
        result = await pp._truncate_single_message(msg, target=500)
        assert isinstance(result["content"], list)
        # The text block should be truncated
        assert "TRUNCATED" in result["content"][0]["text"]

    async def test_dict_content_truncates_inner(self):
        pp = _make_preprocessor()
        msg = {
            "role": "user",
            "content": {"type": "tool_result", "content": _big_string(2_000)},
        }
        result = await pp._truncate_single_message(msg, target=500)
        assert isinstance(result["content"], dict)
        assert "TRUNCATED" in result["content"]["content"]

    async def test_dict_content_small_unchanged(self):
        pp = _make_preprocessor()
        msg = {
            "role": "user",
            "content": {"type": "tool_result", "content": "small"},
        }
        result = await pp._truncate_single_message(msg, target=500)
        assert result is msg  # identity — unchanged

    async def test_unknown_format_stringified(self):
        """An unusual content type is stringified and truncated."""
        pp = _make_preprocessor()
        msg = {"role": "user", "content": 12345}
        # 12345 as a string is 5 chars — under any reasonable target
        # Use a list-as-integer (edge case) that would be big when str()'d
        big_tuple = tuple(range(500))  # str representation is large
        msg = {"role": "user", "content": big_tuple}
        result = await pp._truncate_single_message(msg, target=100)
        assert isinstance(result["content"], str)


# ── _truncate_list_content_items ─────────────────────────────────────────


class TestTruncateListContentItems:
    """Tests for ``_truncate_list_content_items()``."""

    async def test_under_target_unchanged(self):
        """If total size ≤ target, return original message."""
        pp = _make_preprocessor()
        items = [{"type": "text", "text": "hello"}]
        msg = {"role": "user", "content": items}
        result = await pp._truncate_list_content_items(msg, items, target=1_000)
        assert result is msg

    async def test_single_text_item_truncated(self):
        pp = _make_preprocessor()
        big = _big_string(2_000)
        items = [{"type": "text", "text": big}]
        msg = {"role": "user", "content": items}
        result = await pp._truncate_list_content_items(msg, items, target=500)

        assert len(result["content"]) == 1
        assert "TRUNCATED" in result["content"][0]["text"]

    async def test_proportional_distribution(self):
        """Two items: one large, one small — large gets more of the budget."""
        pp = _make_preprocessor()
        large = _big_string(4_000)
        small_text = "short"
        items = [
            {"type": "text", "text": large},
            {"type": "text", "text": small_text},
        ]
        msg = {"role": "user", "content": items}
        result = await pp._truncate_list_content_items(msg, items, target=1_000)

        # Large item should be truncated
        assert "TRUNCATED" in result["content"][0]["text"]
        # Small item should survive
        assert result["content"][1]["text"] == small_text

    async def test_tool_result_content_truncated(self):
        """tool_result items have their 'content' field truncated."""
        pp = _make_preprocessor()
        big = _big_string(3_000)
        items = [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": big},
        ]
        msg = {"role": "user", "content": items}
        result = await pp._truncate_list_content_items(msg, items, target=500)
        assert "TRUNCATED" in result["content"][0]["content"]
        # tool_use_id preserved
        assert result["content"][0]["tool_use_id"] == "toolu_1"

    async def test_zero_size_items_preserved(self):
        pp = _make_preprocessor()
        items = [
            {"type": "text", "text": ""},
            {"type": "text", "text": _big_string(2_000)},
        ]
        msg = {"role": "user", "content": items}
        result = await pp._truncate_list_content_items(msg, items, target=500)
        # Empty item kept as-is
        assert result["content"][0]["text"] == ""

    async def test_non_dict_items_preserved(self):
        pp = _make_preprocessor()
        items = [
            "plain-string",
            {"type": "text", "text": _big_string(2_000)},
        ]
        msg = {"role": "user", "content": items}
        result = await pp._truncate_list_content_items(msg, items, target=500)
        assert result["content"][0] == "plain-string"


# ── _item_content_size ───────────────────────────────────────────────────


class TestItemContentSize:
    """Tests for the static ``_item_content_size()`` helper."""

    def test_dict_with_text(self):
        assert MessagePreprocessor._item_content_size({"type": "text", "text": "hello"}) == 5

    def test_dict_with_str_content(self):
        assert MessagePreprocessor._item_content_size({"type": "tool_result", "content": "abc"}) == 3

    def test_dict_with_non_str_content(self):
        size = MessagePreprocessor._item_content_size({"type": "tool_result", "content": {"key": "value"}})
        assert size == len(str({"key": "value"}))

    def test_dict_with_neither(self):
        item = {"type": "tool_use", "name": "my_tool", "input": {}}
        assert MessagePreprocessor._item_content_size(item) == len(str(item))

    def test_non_dict(self):
        assert MessagePreprocessor._item_content_size("hello") == 5

    def test_non_dict_int(self):
        assert MessagePreprocessor._item_content_size(42) == 2  # "42"


# ── _truncate_plain_text label parameter ─────────────────────────────────


class TestTruncatePlainTextLabel:
    """Verify the label kwarg on ``_truncate_plain_text``."""

    def test_default_label_is_tool_result(self):
        pp = _make_preprocessor()
        text = _big_string(2_000)
        result = pp._truncate_plain_text(text, 500, 2_000)
        assert "[TOOL RESULT TRUNCATED" in result

    def test_custom_label(self):
        pp = _make_preprocessor()
        text = _big_string(2_000)
        result = pp._truncate_plain_text(text, 500, 2_000, label="MESSAGE CONTENT")
        assert "[MESSAGE CONTENT TRUNCATED" in result
        assert "TOOL RESULT" not in result

    def test_text_under_max_returned_unchanged(self):
        pp = _make_preprocessor()
        result = pp._truncate_plain_text("short", 500, 5)
        assert result == "short"


# ── Integration: preprocess_messages pipeline ────────────────────────────


class TestPreprocessMessagesPipeline:
    """Verify ``preprocess_messages()`` runs the single-message truncation step."""

    async def test_pipeline_truncates_oversized_message(self):
        """An oversized user message is truncated through the full pipeline."""
        pp = _make_preprocessor(threshold=1_000, target=500)
        big = _big_string(2_000)
        msgs = [
            {"role": "system", "content": "hello"},
            {"role": "user", "content": big},
        ]
        result = await pp.preprocess_messages(msgs)
        assert "TRUNCATED" in result[1]["content"]

    async def test_pipeline_preserves_small_messages(self):
        pp = _make_preprocessor(threshold=10_000, target=5_000)
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = await pp.preprocess_messages(msgs)
        assert result[0]["content"] == "hi"
        assert result[1]["content"] == "hello"

    async def test_pipeline_no_config_skips_general_truncation(self):
        """Without config, only the tool-result pass runs."""
        pp = MessagePreprocessor(config=None)
        big = _big_string(2_000)  # Would be over any reasonable threshold
        msgs = [{"role": "user", "content": big}]
        result = await pp.preprocess_messages(msgs)
        # Not tool-result, so tool-result pass is a no-op.
        # No config → general pass is also a no-op.
        assert result[0]["content"] == big

    async def test_pipeline_tool_result_then_general(self):
        """Stage 2 history truncation truncates oversized tool results in middle zone."""
        # history_total_length_threshold is low enough to trigger Stage 2,
        # and history_msg thresholds ensure the tool result gets truncated.
        config = ChatConfig(
            BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=100_000,
            BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=50_000,
            BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD=500,
            BEDROCK_HISTORY_MSG_LENGTH_THRESHOLD=500,
            BEDROCK_HISTORY_MSG_TRUNCATION_TARGET=200,
        )
        pp = MessagePreprocessor(config=config)
        # A tool result that exceeds history-msg threshold but NOT single-msg threshold
        tool_msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": _big_string(1_000),
                }
            ],
        }
        msgs = [
            {"role": "system", "content": "hello"},
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "fn", "input": {}}],
            },
            # Must NOT be trailing (they'd get special treatment),
            # so place a normal message after.
            tool_msg,
            {"role": "assistant", "content": "reply"},
        ]
        result = await pp.preprocess_messages(msgs)

        # Tool result should be truncated by Stage 2 history truncation
        tool_content = result[3]["content"][0]["content"]
        assert "TRUNCATED" in tool_content


# ── Integration: chat_manager._preprocess_messages ──────────────────────


class TestChatManagerPreprocessWiring:
    """Verify chat_manager._preprocess_messages calls the new truncation step."""

    async def test_oversized_message_truncated_via_chat_manager(self):
        """An oversized message goes through _truncate_oversized_messages
        when processed via ChatManager._preprocess_messages."""
        from auto_bedrock_chat_fastapi.chat_manager import ChatManager

        config = _make_config(threshold=1_000, target=500)

        cm = ChatManager(
            llm_client=MagicMock(),
            config=config,
        )

        big = _big_string(2_000)
        msgs = [{"role": "user", "content": big}]
        metadata: dict = {}
        result = await cm._preprocess_messages(msgs, metadata)

        assert len(result) >= 1
        # The big message should have been truncated
        assert "TRUNCATED" in result[0]["content"]
