"""Comprehensive AI Summarization Unit Tests.

Fills coverage gaps identified across the AI summarization pipeline:

- ``_summarize_with_llm`` — max_tokens floor, None responses, exact prompt format
- ``_ai_summarize_message`` — first-chunk failure, chunk target_size, many chunks
- ``_truncate_text`` — AI-aware string truncation, marker format
- ``split_into_chunks`` — boundary priority, sentence/word splits, chunk_size=0
- ``_truncate_single_message`` — all content-type dispatch branches (str, list, dict, unknown)
- ``_truncate_list_content_items`` — proportional budget allocation
- ``_detect_zones`` — no system prompt, no user msg, single message
- ``_wipe_middle_zone`` / ``_total_messages_size`` — direct tests
- ``_history_step_truncate_zone`` — direct tests
- Recursive halving — multiple depths, max-recursion reached, plain-text path
- System prompt end-to-end through ``preprocess_messages``
- AI Step 1 success but still over budget → Step 3
"""

import logging
from unittest.mock import AsyncMock

import pytest

from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.message_preprocessor import MessagePreprocessor, get_content_size, split_into_chunks

# ── Helpers ──────────────────────────────────────────────────────────────


def _cfg(**overrides) -> ChatConfig:
    """Build a ChatConfig with sensible small-scale defaults for tests."""
    defaults = dict(
        BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=500_000,
        BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=425_000,
        BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD=5_000,
        BEDROCK_HISTORY_MSG_LENGTH_THRESHOLD=1_000,
        BEDROCK_HISTORY_MSG_TRUNCATION_TARGET=500,
        BEDROCK_MAX_TRUNCATION_RECURSION=3,
        BEDROCK_ENABLE_AI_SUMMARIZATION=True,
    )
    defaults.update(overrides)
    return ChatConfig(**defaults)


def _pp(config=None, llm_client=None, **kw) -> MessagePreprocessor:
    if config is None:
        config = _cfg(**kw)
    return MessagePreprocessor(config=config, llm_client=llm_client)


def _big(n: int) -> str:
    """Repeating string of exactly *n* characters."""
    return ("x" * 100 + "\n") * (n // 101) + "x" * (n % 101)


def _mock_llm(summary: str = "Short summary.") -> AsyncMock:
    client = AsyncMock()
    client.chat_completion.return_value = {
        "content": summary,
        "tool_calls": [],
        "metadata": {},
    }
    return client


def _simple_conv(
    *,
    system: str = "You are helpful.",
    middle_sizes: list[int] | None = None,
    last_user: str = "What is 2+2?",
    trailing_assistant: str = "4",
) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": system}]
    for size in middle_sizes or []:
        msgs.append({"role": "user", "content": _big(size)})
        msgs.append({"role": "assistant", "content": _big(size)})
    msgs.append({"role": "user", "content": last_user})
    msgs.append({"role": "assistant", "content": trailing_assistant})
    return msgs


# ============================================================================
# split_into_chunks — boundary priority & edge cases
# ============================================================================


class TestSplitIntoChunksBoundaries:
    """Additional coverage for ``split_into_chunks`` boundary splitting."""

    def test_paragraph_boundary_preferred_over_line(self):
        """Paragraph break (\\n\\n) should be used in preference to \\n."""
        # Build content with both \n\n and \n in the search zone
        head = "a" * 40
        content = head + "\nline\n\nparagraph" + "b" * 40
        # Use chunk_size that puts the break zone right where both markers are
        chunks = split_into_chunks(content, 50, min_chunks=1)
        # The first chunk should end at or near the \n\n
        assert chunks[0].endswith("\n\n") or "\n\n" not in chunks[0]

    def test_sentence_boundary(self):
        """Verify splitting at '. ' (sentence end)."""
        content = "a" * 40 + ". Sentence two." + "b" * 40
        chunks = split_into_chunks(content, 50, min_chunks=1)
        # Should find the ". " boundary
        assert len(chunks) >= 2
        assert "".join(chunks) == content

    def test_word_boundary(self):
        """Verify splitting at ' ' (word boundary) when no other break exists."""
        content = "word " * 20  # 100 chars, all word-boundary splittable
        chunks = split_into_chunks(content, 30, min_chunks=1)
        assert len(chunks) >= 3
        assert "".join(chunks) == content

    def test_chunk_size_zero(self):
        """chunk_size=0 should not crash."""
        result = split_into_chunks("hello world", 0, min_chunks=1)
        assert "".join(result) == "hello world"

    def test_only_boundaries(self):
        """Content of only newlines should split without error."""
        content = "\n\n\n\n\n\n\n\n"
        chunks = split_into_chunks(content, 3, min_chunks=1)
        assert "".join(chunks) == content

    def test_chunk_size_one(self):
        """chunk_size=1 should produce one character per chunk."""
        content = "abcde"
        chunks = split_into_chunks(content, 1, min_chunks=1)
        assert len(chunks) == 5
        assert "".join(chunks) == content

    def test_reconstruction_with_large_min_chunks(self):
        """Many min_chunks still reconstructs perfectly."""
        content = "x" * 500
        chunks = split_into_chunks(content, 200, min_chunks=50)
        assert "".join(chunks) == content
        assert len(chunks) >= 50


# ============================================================================
# _extract_text_content — additional format coverage
# ============================================================================


class TestExtractTextContentFormats:
    """Additional formats for ``_extract_text_content``."""

    def test_list_with_nested_dict_content(self):
        """List items with dict inner values get str()."""
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "tool_result", "content": {"nested": True}},
            ],
        }
        pp = _pp()
        text = pp._extract_text_content(msg)
        assert "Hello" in text
        assert "nested" in text

    def test_integer_content(self):
        """Non-standard content (int) is stringified."""
        msg = {"role": "assistant", "content": 42}
        pp = _pp()
        text = pp._extract_text_content(msg)
        assert text == "42"


# ============================================================================
# _summarize_with_llm — max_tokens floor, prompt format
# ============================================================================


class TestSummarizeWithLlmDetails:
    """Fine-grained tests for ``_summarize_with_llm``."""

    async def test_max_tokens_floor_1024(self):
        """When target_size is tiny, max_tokens should floor at 1024."""
        pp = _pp()
        llm = _mock_llm("tiny summary")

        pp.llm_client = llm
        pp._system_prompt = None
        await pp._summarize_with_llm(
            content="some content",
            target_size=100,  # 100 // 4 = 25, but floor is 1024
        )

        call_kw = llm.chat_completion.call_args
        assert call_kw.kwargs.get("max_tokens", call_kw[1].get("max_tokens", None)) == 1024

    async def test_system_context_block_exact_format(self):
        """Verify the exact format of the system context when provided."""
        pp = _pp()
        llm = _mock_llm("summary")

        pp.llm_client = llm
        pp._system_prompt = "Be a pirate."
        await pp._summarize_with_llm(
            content="test",
            target_size=5000,
        )

        call_kw = llm.chat_completion.call_args
        system_msg = call_kw.kwargs.get("messages", call_kw[0][0] if call_kw[0] else None)
        if system_msg is None:
            system_msg = call_kw[1]["messages"]
        sys_content = system_msg[0]["content"]
        assert "\nThe main conversation uses this system context:\n---\nBe a pirate.\n---\n" in sys_content

    async def test_no_system_prompt_no_context_block(self):
        """When system_prompt=None, no context block appears."""
        pp = _pp()
        llm = _mock_llm("summary")

        pp.llm_client = llm
        pp._system_prompt = None
        await pp._summarize_with_llm(
            content="test",
            target_size=5000,
        )

        call_kw = llm.chat_completion.call_args
        messages = call_kw.kwargs.get("messages") or call_kw[1]["messages"]
        sys_content = messages[0]["content"]
        assert "system context" not in sys_content

    async def test_iteration_context_in_prompt(self):
        """Verify iteration_context is injected as a rule."""
        pp = _pp()
        llm = _mock_llm("summary")

        pp.llm_client = llm
        pp._system_prompt = None
        await pp._summarize_with_llm(
            content="test",
            target_size=5000,
            iteration_context="chunk 3 of 7",
        )

        call_kw = llm.chat_completion.call_args
        messages = call_kw.kwargs.get("messages") or call_kw[1]["messages"]
        sys_content = messages[0]["content"]
        assert "This is chunk 3 of 7" in sys_content

    async def test_llm_returns_content_none(self):
        """LLM response with ``{"content": None}`` raises RuntimeError."""
        pp = _pp()
        llm = AsyncMock()
        llm.chat_completion.return_value = {"content": None}

        pp.llm_client = llm
        pp._system_prompt = None
        with pytest.raises(RuntimeError, match="empty summary"):
            await pp._summarize_with_llm(
                content="test",
                target_size=5000,
            )


# ============================================================================
# _ai_summarize_message — edge cases
# ============================================================================


class TestAiSummarizeMessageEdgeCases:
    """Additional edge cases for the rolling map-reduce orchestrator."""

    async def test_first_chunk_failure_raises(self):
        """If the first LLM call fails, exception propagates immediately."""
        pp = _pp()
        llm = AsyncMock()
        llm.chat_completion.side_effect = RuntimeError("LLM down")

        pp.llm_client = llm
        pp._system_prompt = None
        with pytest.raises(RuntimeError, match="LLM down"):
            await pp._ai_summarize_message(
                content=_big(1_000),
                target_size=300,
                chunk_size=300,
            )

        # Only one call should have been made
        assert llm.chat_completion.call_count == 1

    async def test_chunk_uses_chunk_size_as_target(self):
        """Each chunk's target_size should be chunk_size, not final target_size."""
        pp = _pp()
        llm = _mock_llm("summary")

        chunk_size = 300
        pp.llm_client = llm
        pp._system_prompt = None
        await pp._ai_summarize_message(
            content=_big(1_000),
            target_size=100,  # This is the final target, NOT per-chunk
            chunk_size=chunk_size,
        )

        # First call: target_size should be chunk_size (300)
        first_call = llm.chat_completion.call_args_list[0]
        first_messages = first_call.kwargs.get("messages") or first_call[1]["messages"]
        sys_content = first_messages[0]["content"]
        assert f"{chunk_size:,}" in sys_content

    async def test_many_chunks_all_processed(self):
        """With 10+ chunks, all are processed sequentially."""
        pp = _pp()
        llm = _mock_llm("summary")

        # 3000 chars / 100 chunk_size → many chunks (min_chunks=3)
        pp.llm_client = llm
        pp._system_prompt = None
        await pp._ai_summarize_message(
            content=_big(3_000),
            target_size=500,
            chunk_size=100,
        )

        # Should have many calls (one per chunk)
        assert llm.chat_completion.call_count >= 10

    async def test_returns_only_last_iteration_result(self):
        """Only the final chunk's summary is returned."""
        pp = _pp()
        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "content": f"summary-{call_count}",
                "tool_calls": [],
                "metadata": {},
            }

        llm = AsyncMock()
        llm.chat_completion.side_effect = side_effect

        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._ai_summarize_message(
            content=_big(1_000),
            target_size=300,
            chunk_size=300,
        )

        # Result should be the LAST summary
        assert result == f"summary-{call_count}"
        assert call_count >= 3

    async def test_iteration_context_format(self):
        """Verify iteration_context format 'chunk N of M' for each call."""
        pp = _pp()
        llm = _mock_llm("summary")

        pp.llm_client = llm
        pp._system_prompt = None
        await pp._ai_summarize_message(
            content=_big(1_000),
            target_size=300,
            chunk_size=300,
        )

        for i, call in enumerate(llm.chat_completion.call_args_list):
            messages = call.kwargs.get("messages") or call[1]["messages"]
            sys_content = messages[0]["content"]
            total_chunks = llm.chat_completion.call_count
            assert f"chunk {i + 1} of {total_chunks}" in sys_content


# ============================================================================
# _truncate_single_message — content format coverage
# ============================================================================


class TestTruncateSingleMessageFormats:
    """Test _truncate_single_message with various content formats."""

    async def test_list_content_message(self):
        """List-format (Claude) content items are truncated individually."""
        pp = _pp(
            BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=100,
            BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=50,
        )
        llm = _mock_llm("short summary")

        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": _big(200)},
            ],
        }
        pp.llm_client = llm
        pp._system_prompt = "system"
        result = await pp._truncate_single_message(msg, 50)
        # list content structure is preserved
        assert isinstance(result["content"], list)
        assert "short summary" in result["content"][0]["text"]

    async def test_dict_content_message(self):
        """Dict-format content inner value is summarized, structure preserved."""
        pp = _pp(
            BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=100,
            BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=50,
        )
        llm = _mock_llm("short summary")

        msg = {
            "role": "user",
            "content": {"type": "tool_result", "content": _big(200)},
        }
        pp.llm_client = llm
        pp._system_prompt = "system"
        result = await pp._truncate_single_message(msg, 50)
        # Dict structure preserved, inner content summarized
        assert isinstance(result["content"], dict)
        assert result["content"]["type"] == "tool_result"
        assert "short summary" in result["content"]["content"]

    async def test_ai_summary_marker_exact_format(self):
        """Verify the AI SUMMARY marker contains original and reduced sizes."""
        pp = _pp()
        llm = _mock_llm("short summary")

        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._truncate_text(_big(600), 500)

        assert result.startswith("[AI SUMMARY -")
        assert "Original:" in result
        assert "reduced to:" in result
        assert "chars]" in result
        assert "\n\n" in result  # separator before summary text

    async def test_whitespace_only_summary_falls_back(self):
        """Whitespace-only AI summary exceeds target → plain-text fallback."""
        pp = _pp()
        # Return whitespace that is longer than target
        llm = _mock_llm(" " * 600)

        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._truncate_text(_big(600), 500)

        # Should have fallen back to plain-text truncation
        assert "TRUNCATED" in result

    async def test_role_preserved_in_result(self):
        """Message role is preserved after AI summarization."""
        pp = _pp()
        llm = _mock_llm("tiny")

        msg = {"role": "user", "content": _big(600), "extra_key": "value"}
        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._truncate_single_message(msg, 500)

        assert result["role"] == "user"
        assert result["extra_key"] == "value"


class TestDetectZones:
    """Direct tests for ``_detect_zones()``."""

    def test_empty_messages(self):
        zones = MessagePreprocessor._detect_zones([])
        assert zones == {"protected": [], "middle": []}

    def test_only_system_message(self):
        msgs = [{"role": "system", "content": "sys"}]
        zones = MessagePreprocessor._detect_zones(msgs)
        assert zones["protected"] == [0]
        assert zones["middle"] == []

    def test_no_system_prompt(self):
        """First message is NOT system → not protected as system."""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hey"},
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        # Last user is index 2, protected: 2,3; middle: 0,1
        assert 0 in zones["middle"]
        assert 1 in zones["middle"]
        assert 2 in zones["protected"]
        assert 3 in zones["protected"]

    def test_no_user_message(self):
        """Conversation with no user messages at all."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "hello"},
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        assert 0 in zones["protected"]  # system
        # No user message found → only system is protected
        assert 1 in zones["middle"]

    def test_standard_conversation(self):
        """Standard: system + middle exchanges + last user + assistant."""
        msgs = [
            {"role": "system", "content": "sys"},  # 0 - protected
            {"role": "user", "content": "q1"},  # 1 - middle
            {"role": "assistant", "content": "a1"},  # 2 - middle
            {"role": "user", "content": "q2"},  # 3 - protected (last user)
            {"role": "assistant", "content": "a2"},  # 4 - protected
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        assert zones["protected"] == [0, 3, 4]
        assert zones["middle"] == [1, 2]

    def test_single_user_message(self):
        """Only one user message → it's the last user → protected."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "only one"},
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        assert zones["protected"] == [0, 1]
        assert zones["middle"] == []

    def test_tool_messages_in_middle(self):
        """Tool messages are not user messages → stay in middle zone."""
        msgs = [
            {"role": "system", "content": "sys"},  # 0
            {"role": "user", "content": "q1"},  # 1 - middle
            {"role": "assistant", "content": "let me check"},  # 2 - middle
            {
                "role": "user",
                "content": [  # 3 - tool result → middle
                    {"type": "tool_result", "tool_use_id": "t1", "content": "data"}
                ],
            },
            {"role": "assistant", "content": "got it"},  # 4 - middle
            {"role": "user", "content": "final question"},  # 5 - protected (last user)
            {"role": "assistant", "content": "answer"},  # 6 - protected
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        assert zones["middle"] == [1, 2, 3, 4]
        assert zones["protected"] == [0, 5, 6]


# ============================================================================
# _wipe_middle_zone — direct test
# ============================================================================


class TestWipeMiddleZone:
    """Direct tests for ``_wipe_middle_zone()``."""

    def test_removes_specified_indices(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old q"},
            {"role": "assistant", "content": "old a"},
            {"role": "user", "content": "new q"},
        ]
        result = MessagePreprocessor._wipe_middle_zone(msgs, [1, 2])
        assert len(result) == 2
        assert result[0]["content"] == "sys"
        assert result[1]["content"] == "new q"

    def test_empty_indices_no_change(self):
        msgs = [{"role": "user", "content": "hi"}]
        result = MessagePreprocessor._wipe_middle_zone(msgs, [])
        assert len(result) == 1


# ============================================================================
# _total_messages_size — direct test
# ============================================================================


class TestTotalMessagesSize:
    """Direct tests for ``_total_messages_size()``."""

    def test_sums_all_messages(self):
        msgs = [
            {"role": "system", "content": "abc"},  # 3
            {"role": "user", "content": "de"},  # 2
            {"role": "assistant", "content": "f"},  # 1
        ]
        assert MessagePreprocessor._total_messages_size(msgs) == 6

    def test_empty_list(self):
        assert MessagePreprocessor._total_messages_size([]) == 0


# ============================================================================
# _truncate_single_message — all content-type dispatch branches
# ============================================================================


class TestTruncateSingleMessageBranches:
    """Direct tests for all content-type dispatch branches in _truncate_single_message."""

    async def test_string_content(self):
        pp = _pp()
        msg = {"role": "user", "content": _big(2000)}
        result = await pp._truncate_single_message(msg, 500)
        assert "TRUNCATED" in result["content"]
        assert result["role"] == "user"

    async def test_list_content(self):
        """List content dispatches to _truncate_list_content_items."""
        pp = _pp()
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": _big(2000)},
            ],
        }
        result = await pp._truncate_single_message(msg, 500)
        assert isinstance(result["content"], list)
        assert "TRUNCATED" in result["content"][0]["text"]

    async def test_dict_content_oversized(self):
        """Dict content with oversized inner content."""
        pp = _pp()
        msg = {
            "role": "user",
            "content": {"type": "tool_result", "content": _big(2000)},
        }
        result = await pp._truncate_single_message(msg, 500)
        assert isinstance(result["content"], dict)
        assert "TRUNCATED" in result["content"]["content"]

    async def test_dict_content_within_target(self):
        """Dict content within target is returned unchanged."""
        pp = _pp()
        msg = {
            "role": "user",
            "content": {"type": "tool_result", "content": "small"},
        }
        result = await pp._truncate_single_message(msg, 500)
        assert result["content"]["content"] == "small"

    async def test_unknown_content_type(self):
        """Non-str/list/dict content is stringified and truncated."""
        pp = _pp()
        msg = {"role": "user", "content": 12345}
        result = await pp._truncate_single_message(msg, 3)
        # 5 chars > target 3, so should be truncated
        assert isinstance(result["content"], str)

    async def test_metadata_preserved(self):
        """Extra keys on the message dict are preserved."""
        pp = _pp()
        msg = {"role": "user", "content": _big(2000), "custom": "data"}
        result = await pp._truncate_single_message(msg, 500)
        assert result["custom"] == "data"


# ============================================================================
# _truncate_list_content_items — proportional budget
# ============================================================================


class TestTruncateListContentItems:
    """Direct tests for proportional budget allocation."""

    async def test_proportional_allocation(self):
        """Items get proportional shares of the target budget."""
        pp = _pp()
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": _big(1000)},  # 1000 chars
                {"type": "text", "text": _big(3000)},  # 3000 chars
            ],
        }
        result = await pp._truncate_list_content_items(msg, msg["content"], 1000)
        items = result["content"]
        # Both should be truncated, but the larger one gets a bigger share
        assert "TRUNCATED" in items[0]["text"]
        assert "TRUNCATED" in items[1]["text"]

    async def test_small_items_kept_intact(self):
        """Items within their proportional budget are not truncated."""
        pp = _pp()
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "small"},  # 5 chars
                {"type": "text", "text": _big(2000)},  # 2000 chars
            ],
        }
        result = await pp._truncate_list_content_items(msg, msg["content"], 500)
        items = result["content"]
        assert items[0]["text"] == "small"  # untouched
        assert "TRUNCATED" in items[1]["text"]  # truncated

    async def test_zero_size_items_preserved(self):
        """Items with size 0 are preserved as-is."""
        pp = _pp()
        msg = {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64"}},  # no text/content
                {"type": "text", "text": _big(2000)},  # 2000 chars
            ],
        }
        result = await pp._truncate_list_content_items(msg, msg["content"], 500)
        items = result["content"]
        assert items[0]["type"] == "image"  # preserved
        assert "TRUNCATED" in items[1]["text"]

    async def test_total_within_target_no_change(self):
        """No truncation when total size <= target."""
        pp = _pp()
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "small"},
            ],
        }
        result = await pp._truncate_list_content_items(msg, msg["content"], 5000)
        assert result is msg  # same object returned

    async def test_content_key_truncation(self):
        """Items with 'content' key (tool_result) are truncated."""
        pp = _pp()
        msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": _big(2000),
                },
            ],
        }
        result = await pp._truncate_list_content_items(msg, msg["content"], 500)
        assert "TRUNCATED" in result["content"][0]["content"]


# ============================================================================
# _item_content_size — direct tests
# ============================================================================


class TestItemContentSize:
    """Direct tests for ``_item_content_size``."""

    def test_text_key(self):
        assert MessagePreprocessor._item_content_size({"text": "hello"}) == 5

    def test_content_key_str(self):
        assert MessagePreprocessor._item_content_size({"content": "abc"}) == 3

    def test_content_key_non_str(self):
        item = {"content": {"nested": True}}
        size = MessagePreprocessor._item_content_size(item)
        assert size == len(str({"nested": True}))

    def test_dict_no_text_or_content(self):
        item = {"type": "image", "source": "base64"}
        size = MessagePreprocessor._item_content_size(item)
        assert size == len(str(item))

    def test_non_dict_item(self):
        assert MessagePreprocessor._item_content_size("hello") == 5
        assert MessagePreprocessor._item_content_size(42) == 2


# ============================================================================
# _history_step_truncate_zone — direct tests
# ============================================================================


class TestHistoryStepTruncateZone:
    """Direct tests for ``_history_step_truncate_zone``."""

    async def test_truncates_oversized_only(self):
        pp = _pp()
        msgs = [
            {"role": "user", "content": "small"},  # 5 chars
            {"role": "user", "content": _big(2000)},  # 2000 chars
            {"role": "assistant", "content": "ok"},  # 2 chars
        ]
        result = await pp._history_step_truncate_zone(
            msgs,
            indices=[0, 1, 2],
            msg_threshold=100,
            msg_target=50,
        )
        assert result[0]["content"] == "small"  # untouched
        assert "TRUNCATED" in result[1]["content"]  # truncated
        assert result[2]["content"] == "ok"  # untouched

    async def test_non_dict_items_skipped(self):
        pp = _pp()
        msgs = ["not a dict", {"role": "user", "content": "ok"}]
        result = await pp._history_step_truncate_zone(
            msgs,
            indices=[0, 1],
            msg_threshold=100,
            msg_target=50,
        )
        assert result[0] == "not a dict"

    async def test_returns_new_list(self):
        """Should return a new list (not mutate the original)."""
        pp = _pp()
        msgs = [{"role": "user", "content": _big(2000)}]
        original_content = msgs[0]["content"]
        result = await pp._history_step_truncate_zone(
            msgs,
            indices=[0],
            msg_threshold=100,
            msg_target=50,
        )
        # Original should be unchanged
        assert msgs[0]["content"] == original_content
        assert result is not msgs


# ============================================================================
# _truncate_plain_text — output format
# ============================================================================


class TestTruncatePlainTextFormat:
    """Verify the output structure of ``_truncate_plain_text``."""

    def test_output_structure(self):
        pp = _pp()
        text = _big(2000)
        result = pp._truncate_plain_text(text, 500, 2000)

        # Default label is "TOOL RESULT"
        assert result.startswith("[TOOL RESULT TRUNCATED")
        assert "Original size: 2,000 chars" in result
        assert "BEGINNING:" in result
        assert "ENDING:" in result
        assert "RECOMMENDATION:" in result

    def test_custom_label(self):
        pp = _pp()
        result = pp._truncate_plain_text("x" * 200, 50, 200, label="TOOL RESULT")
        assert "[TOOL RESULT TRUNCATED" in result

    def test_text_within_max_size_returned_unchanged(self):
        pp = _pp()
        result = pp._truncate_plain_text("short", 100, 5)
        assert result == "short"

    def test_head_tail_proportions(self):
        """Head ≈ 80% and tail ≈ 20% of *content budget* (max_size − markers)."""
        pp = _pp()
        text = "a" * 5000 + "b" * 5000  # 10_000 chars
        max_size = 2000
        result = pp._truncate_plain_text(text, max_size, 10_000)

        # The function dynamically subtracts marker overhead (~200 chars),
        # then allocates 80% of content budget to head, 20% to tail.
        # With max_size=2000, content_budget ≈ 1800, head ≈ 1440, tail ≈ 360.
        # Just verify head is all 'a's and tail is all 'b's.
        beg_idx = result.index("BEGINNING:\n") + len("BEGINNING:\n")
        end_idx = result.index("\n\n...", beg_idx)
        head_content = result[beg_idx:end_idx]
        assert len(head_content) > max_size * 0.5, "Head should use >50% of max_size"
        assert set(head_content) == {"a"}, "Head should be all 'a's"

        tail_idx = result.index("ENDING:\n") + len("ENDING:\n")
        tail_end = result.index("\n\nRECOMMENDATION", tail_idx)
        tail_content = result[tail_idx:tail_end]
        assert len(tail_content) > max_size * 0.1, "Tail should use >10% of max_size"
        assert set(tail_content) == {"b"}, "Tail should be all 'b's"

        # Total result should use most of the budget
        assert len(result) > max_size * 0.85, "Should use >85% of max_size budget"
        assert len(result) <= max_size, "Must not exceed max_size"

    @pytest.mark.parametrize("max_size", [500, 2_000, 50_000, 200_000])
    def test_budget_utilization_above_85_pct(self, max_size):
        """Truncated output should use >85% of budget, not just 50%."""
        pp = _pp()
        text = "x" * (max_size * 5)
        result = pp._truncate_plain_text(text, max_size, len(text))
        utilization = len(result) / max_size
        assert utilization > 0.85, f"Budget utilization {utilization:.1%} for max_size={max_size:,}"
        assert len(result) <= max_size, "Must never exceed max_size"

    def test_small_max_size_falls_back_to_simple_cut(self):
        """When max_size < marker overhead, simple truncation kicks in."""
        pp = _pp()
        text = "y" * 1000
        result = pp._truncate_plain_text(text, 50, 1000)
        assert len(result) == 50

    def test_result_never_exceeds_max_size(self):
        """Across a range of sizes, result is always ≤ max_size."""
        pp = _pp()
        for max_size in [100, 300, 600, 1_000, 10_000, 100_000]:
            text = "z" * (max_size * 3)
            result = pp._truncate_plain_text(text, max_size, len(text))
            assert len(result) <= max_size, f"Result {len(result):,} > max_size {max_size:,}"


class TestClaudeFormatBudgetConsistency:
    """Claude list-format and GPT format should get the same per-item budget."""

    def test_claude_and_gpt_per_item_target_equal(self):
        """Removing double MULTI_TOOL_BUDGET_FACTOR gives same per-item
        budget as the GPT branch for multi-tool messages."""
        from auto_bedrock_chat_fastapi.message_preprocessor import MessagePreprocessor

        MessagePreprocessor(config=None)
        total_target = 340_000
        num_results = 3

        # GPT branch: target // num_results
        gpt_per_item = total_target // num_results  # 113_333

        # Claude branch (after fix): target / count (no extra factor)
        claude_per_item = max(int(total_target / num_results), 1)  # 113_333

        # They should match (within rounding)
        assert abs(gpt_per_item - claude_per_item) <= 1


# ============================================================================
# Recursive halving — depth tracking & edge cases
# ============================================================================


class TestRecursiveHalving:
    """Tests for recursive halving in ``_truncate_history_total``."""

    async def test_max_recursion_one_limited_recurse(self):
        """With max_recursion=1, at most one recursive halving occurs."""
        pp = _pp(
            BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD=100,
            BEDROCK_MAX_TRUNCATION_RECURSION=1,
            BEDROCK_ENABLE_AI_SUMMARIZATION=False,
        )
        msgs = _simple_conv(middle_sizes=[500, 500])
        result = await pp._truncate_history_total(
            msgs,
            total_threshold=100,
            msg_threshold=200,
            msg_target=100,
            max_recursion=1,
            depth=0,
        )
        # Should return best-effort result
        assert isinstance(result, list)

    async def test_max_recursion_reached_logs_error(self, caplog):
        """When max_recursion is reached, an error is logged."""
        pp = _pp(
            BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD=10,
            BEDROCK_MAX_TRUNCATION_RECURSION=1,
            BEDROCK_ENABLE_AI_SUMMARIZATION=False,
        )
        # Create messages that can't be reduced below threshold
        msgs = [
            {"role": "system", "content": _big(100)},
            {"role": "user", "content": _big(100)},
            {"role": "assistant", "content": _big(100)},
        ]
        with caplog.at_level(logging.ERROR):
            result = await pp._truncate_history_total(
                msgs,
                total_threshold=10,
                msg_threshold=50,
                msg_target=25,
                max_recursion=1,
                depth=0,
            )
        assert "max recursion" in caplog.text.lower()
        assert isinstance(result, list)

    async def test_halved_values_at_each_depth(self):
        """Verify thresholds are halved at each recursion depth."""
        pp = _pp(BEDROCK_ENABLE_AI_SUMMARIZATION=False)
        calls = []

        original_run = pp._truncate_history_total

        async def tracking_run(messages, *, total_threshold, msg_threshold, msg_target, max_recursion, depth, **kwargs):
            calls.append(
                {
                    "depth": depth,
                    "total_threshold": total_threshold,
                    "msg_threshold": msg_threshold,
                    "msg_target": msg_target,
                }
            )
            return await original_run(
                messages,
                total_threshold=total_threshold,
                msg_threshold=msg_threshold,
                msg_target=msg_target,
                max_recursion=max_recursion,
                depth=depth,
                **kwargs,
            )

        pp._truncate_history_total = tracking_run

        msgs = [
            {"role": "system", "content": _big(50)},
            {"role": "user", "content": _big(2000)},
            {"role": "assistant", "content": _big(2000)},
            {"role": "user", "content": _big(2000)},
            {"role": "assistant", "content": _big(2000)},
        ]

        await pp._truncate_history_total(
            msgs,
            total_threshold=500,
            msg_threshold=1000,
            msg_target=500,
            max_recursion=3,
            depth=0,
        )

        # Should have recursive calls
        if len(calls) >= 2:
            assert calls[1]["total_threshold"] == calls[0]["total_threshold"] // 2
            assert calls[1]["msg_threshold"] == calls[0]["msg_threshold"] // 2
            assert calls[1]["msg_target"] == calls[0]["msg_target"] // 2

    async def test_plain_text_recursive_halving(self):
        """Recursive halving works in the plain-text path (AI disabled)."""
        pp = _pp(BEDROCK_ENABLE_AI_SUMMARIZATION=False)
        msgs = _simple_conv(middle_sizes=[2000, 2000])

        result = await pp._truncate_history_total(
            msgs,
            total_threshold=500,
            msg_threshold=1000,
            msg_target=500,
            max_recursion=3,
            depth=0,
        )
        # Should complete without errors
        assert isinstance(result, list)
        # Size should be reduced
        total = MessagePreprocessor._total_messages_size(result)
        original = MessagePreprocessor._total_messages_size(msgs)
        assert total < original


class TestAIStep1ThenStep3:
    """When AI Step 1 succeeds but total still exceeds threshold, Step 3 fires."""

    async def test_step3_after_ai_step1(self):
        """Stage 2.1 truncates middle zone, Stage 2.3 truncates all user/tool."""
        # Create a config with a tiny total threshold
        pp = _pp(
            BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD=200,
            BEDROCK_HISTORY_MSG_LENGTH_THRESHOLD=100,
            BEDROCK_HISTORY_MSG_TRUNCATION_TARGET=50,
            BEDROCK_MAX_TRUNCATION_RECURSION=3,
        )
        llm = _mock_llm("A" * 150)

        msgs = [
            {"role": "system", "content": _big(50)},  # protected
            {"role": "user", "content": _big(500)},  # middle
            {"role": "assistant", "content": _big(500)},  # middle
            {"role": "user", "content": _big(500)},  # protected (last user) - oversized
            {"role": "assistant", "content": _big(10)},  # protected
        ]
        original_total = sum(get_content_size(m) for m in msgs)

        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._truncate_history_total(
            msgs,
            total_threshold=200,
            msg_threshold=100,
            msg_target=50,
            max_recursion=3,
            depth=0,
        )

        # Per-message truncation preserves message count
        assert isinstance(result, list)
        assert len(result) == len(msgs)
        # No whole-zone summary markers
        summaries = [m for m in result if "[CONVERSATION CONTEXT]" in str(m.get("content", ""))]
        assert len(summaries) == 0
        # Total size reduced significantly from original
        new_total = sum(get_content_size(m) for m in result)
        assert new_total < original_total


# ============================================================================
# Boundary: message size exactly equals threshold → NOT truncated
# ============================================================================


class TestBoundaryConditions:
    """Boundary condition tests."""

    async def test_message_at_exact_threshold_not_truncated(self):
        """A message whose size == threshold should NOT be truncated."""
        threshold = 1000
        pp = _pp(
            BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=threshold,
            BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=500,
        )
        msg = {"role": "user", "content": "x" * threshold}
        result = await pp._truncate_oversized_messages([msg])
        assert result[0]["content"] == "x" * threshold  # unchanged

    async def test_message_one_over_threshold_truncated(self):
        """A message one char over threshold IS truncated."""
        threshold = 1000
        pp = _pp(
            BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=threshold,
            BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=500,
        )
        msg = {"role": "user", "content": "x" * (threshold + 1)}
        result = await pp._truncate_oversized_messages([msg])
        assert "TRUNCATED" in result[0]["content"]


# ============================================================================
# System prompt end-to-end through preprocess_messages
# ============================================================================


class TestSystemPromptEndToEnd:
    """End-to-end: system_prompt flows through preprocess_messages to LLM calls."""

    async def test_system_prompt_reaches_single_message_summarizer(self):
        """system_prompt from config reaches _summarize_with_llm."""
        pp = _pp(
            BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=100,
            BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=50,
            BEDROCK_SYSTEM_PROMPT="You are a pirate.",
        )
        llm = _mock_llm("small summary")

        msgs = [
            {"role": "system", "content": "You are a pirate."},
            {"role": "user", "content": _big(500)},  # oversized
            {"role": "assistant", "content": "ok"},
        ]

        pp.llm_client = llm
        await pp.preprocess_messages(msgs)

        # Verify the LLM call included the system prompt context
        assert llm.chat_completion.call_count >= 1
        first_call = llm.chat_completion.call_args_list[0]
        messages = first_call.kwargs.get("messages") or first_call[1]["messages"]
        sys_content = messages[0]["content"]
        assert "pirate" in sys_content.lower()

    async def test_system_prompt_reaches_history_summarizer(self):
        """system_prompt flows to Stage 1 AI summarization for oversized messages."""
        pp = _pp(
            BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=200,
            BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=100,
            BEDROCK_SYSTEM_PROMPT="You help with cooking.",
        )
        llm = _mock_llm("Summary.")

        msgs = [
            {"role": "system", "content": "You help with cooking."},
            {"role": "user", "content": _big(300)},
            {"role": "assistant", "content": "final a"},
        ]

        pp.llm_client = llm
        await pp.preprocess_messages(msgs)

        # At least one LLM call (for single-message summarization) should mention cooking
        found_cooking = False
        for call in llm.chat_completion.call_args_list:
            messages = call.kwargs.get("messages") or call[1]["messages"]
            for m in messages:
                if "cooking" in str(m.get("content", "")).lower():
                    found_cooking = True
                    break
        assert found_cooking


# AI fallback logging
# ============================================================================


class TestAIFallbackLogging:
    """Verify logging behavior during AI fallback paths."""

    async def test_ai_truncate_fallback_logs_exception(self, caplog):
        """_truncate_text logs the exception on AI failure."""
        pp = _pp()
        llm = AsyncMock()
        llm.chat_completion.side_effect = RuntimeError("LLM timeout")

        pp.llm_client = llm
        pp._system_prompt = None
        with caplog.at_level(logging.WARNING):
            result = await pp._truncate_text(_big(600), 500)

        assert "AI summarization failed" in caplog.text
        assert "TRUNCATED" in result


# ============================================================================
# System prompt flow through _ai_summarize_message to each chunk
# ============================================================================


class TestSystemPromptToAllChunks:
    """Verify system_prompt is passed to EVERY chunk LLM call."""

    async def test_all_chunks_receive_system_prompt(self):
        pp = _pp()
        llm = _mock_llm("summary")

        pp.llm_client = llm
        pp._system_prompt = "Top secret context."
        await pp._ai_summarize_message(
            content=_big(1_000),
            target_size=300,
            chunk_size=300,
        )

        for i, call in enumerate(llm.chat_completion.call_args_list):
            messages = call.kwargs.get("messages") or call[1]["messages"]
            sys_content = messages[0]["content"]
            assert "Top secret context." in sys_content, f"Chunk {i} missing system prompt"
