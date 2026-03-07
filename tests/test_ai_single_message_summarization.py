"""Tests for Task 3.4: AI-Based Single-Message Summarization.

Covers:
- ``split_into_chunks()`` — module-level content-aware splitting
- ``_extract_text_content()`` — text extraction from any message format
- ``_summarize_with_llm()`` — atomic LLM summarization call
- ``_ai_summarize_message()`` — rolling map-reduce orchestrator
- ``_truncate_text()`` — AI-aware string truncation with fallback
- ``_truncate_oversized_messages()`` — AI path integration
"""

import logging
from unittest.mock import AsyncMock

import pytest

from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.message_preprocessor import MessagePreprocessor, get_content_size, split_into_chunks

# ── Helpers ──────────────────────────────────────────────────────────────


def _cfg(
    threshold: int = 1_000,
    target: int = 500,
    ai: bool = True,
) -> ChatConfig:
    """Build a ChatConfig with AI summarization enabled and small thresholds."""
    return ChatConfig(
        BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=threshold,
        BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=target,
        BEDROCK_ENABLE_AI_SUMMARIZATION=ai,
    )


def _pp(
    threshold: int = 1_000,
    target: int = 500,
    ai: bool = True,
    llm_client=None,
) -> MessagePreprocessor:
    return MessagePreprocessor(config=_cfg(threshold, target, ai), llm_client=llm_client)


def _big(n: int) -> str:
    """Return a repeating string of exactly *n* characters."""
    return ("x" * 100 + "\n") * (n // 101) + "x" * (n % 101)


def _mock_llm(summary_text: str = "short summary") -> AsyncMock:
    """Return a mock llm_client whose chat_completion returns a summary."""
    llm = AsyncMock()
    llm.chat_completion.return_value = {
        "content": summary_text,
        "tool_calls": [],
        "metadata": {},
    }
    return llm


# ============================================================================
# split_into_chunks
# ============================================================================


class TestSplitIntoChunks:
    """Tests for the module-level ``split_into_chunks()`` utility."""

    def test_empty_string(self):
        result = split_into_chunks("", 100)
        assert result == [""]

    def test_content_smaller_than_chunk_size(self):
        result = split_into_chunks("hello", 100, min_chunks=1)
        assert result == ["hello"]

    def test_exact_chunk_size(self):
        content = "a" * 300
        result = split_into_chunks(content, 100, min_chunks=1)
        assert len(result) == 3
        assert "".join(result) == content

    def test_min_chunks_enforced(self):
        """With 600 chars / 300 chunk_size = 2 chunks, but min 3 required."""
        content = "a" * 600
        result = split_into_chunks(content, 300, min_chunks=3)
        assert len(result) >= 3
        assert "".join(result) == content

    def test_natural_paragraph_boundary(self):
        """Splits on \\n\\n when available."""
        para1 = "a" * 80
        para2 = "b" * 80
        content = para1 + "\n\n" + para2
        result = split_into_chunks(content, 100, min_chunks=1)
        assert len(result) >= 2
        assert "".join(result) == content

    def test_natural_line_boundary(self):
        """Splits on \\n when no paragraph break in range."""
        lines = ["x" * 40 + "\n" for _ in range(10)]
        content = "".join(lines)
        result = split_into_chunks(content, 90, min_chunks=1)
        assert len(result) >= 2
        assert "".join(result) == content

    def test_hard_cut_when_no_boundaries(self):
        """Falls back to hard cut when no natural boundaries exist."""
        content = "a" * 500
        result = split_into_chunks(content, 200, min_chunks=1)
        assert len(result) >= 2
        assert "".join(result) == content
        for chunk in result[:-1]:
            assert len(chunk) <= 200

    def test_all_chunks_together_equal_original(self):
        """No gaps or overlaps — chunks fully reconstruct original."""
        content = "The quick brown fox. " * 50
        result = split_into_chunks(content, 100, min_chunks=1)
        assert "".join(result) == content

    def test_single_char_chunk_size(self):
        content = "abc"
        result = split_into_chunks(content, 1, min_chunks=1)
        assert len(result) == 3
        assert "".join(result) == content

    def test_large_min_chunks(self):
        """min_chunks is only enforced when content exceeds chunk_size."""
        # Content fits in a single chunk — min_chunks should NOT apply
        content = "abcde"
        result = split_into_chunks(content, 100, min_chunks=10)
        assert len(result) == 1
        assert result[0] == content

        # Content exceeds chunk_size — min_chunks forces finer splitting
        content_large = "a" * 500
        result_large = split_into_chunks(content_large, 400, min_chunks=5)
        assert len(result_large) >= 5
        assert "".join(result_large) == content_large


# ============================================================================
# _extract_text_content
# ============================================================================


class TestExtractTextContent:
    """Tests for ``_extract_text_content()``."""

    def test_string_content(self):
        msg = {"role": "user", "content": "hello world"}
        assert MessagePreprocessor._extract_text_content(msg) == "hello world"

    def test_empty_content(self):
        msg = {"role": "user", "content": ""}
        assert MessagePreprocessor._extract_text_content(msg) == ""

    def test_missing_content(self):
        msg = {"role": "user"}
        assert MessagePreprocessor._extract_text_content(msg) == ""

    def test_list_with_text_blocks(self):
        """Claude format — list of text blocks."""
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "part one"},
                {"type": "text", "text": "part two"},
            ],
        }
        result = MessagePreprocessor._extract_text_content(msg)
        assert "part one" in result
        assert "part two" in result

    def test_list_with_tool_result_blocks(self):
        """Claude format — tool_result with nested content."""
        msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "result data here",
                },
            ],
        }
        result = MessagePreprocessor._extract_text_content(msg)
        assert "result data here" in result

    def test_list_with_mixed_types(self):
        msg = {
            "role": "user",
            "content": [
                "plain string",
                {"type": "text", "text": "text block"},
                42,  # ignored gracefully
            ],
        }
        result = MessagePreprocessor._extract_text_content(msg)
        assert "plain string" in result
        assert "text block" in result

    def test_dict_content(self):
        msg = {"role": "tool", "content": {"content": "inner data"}}
        result = MessagePreprocessor._extract_text_content(msg)
        assert "inner data" in result

    def test_dict_content_with_text_key(self):
        msg = {"role": "tool", "content": {"text": "some text"}}
        result = MessagePreprocessor._extract_text_content(msg)
        assert "some text" in result


# ============================================================================
# _summarize_with_llm
# ============================================================================


class TestSummarizeWithLlm:
    """Tests for ``_summarize_with_llm()`` — the atomic LLM call."""

    async def test_successful_summarization(self):
        pp = _pp()
        llm = _mock_llm("condensed output")
        pp.llm_client = llm
        pp._system_prompt = "You are a helper bot"
        result = await pp._summarize_with_llm(
            content="long content here",
            target_size=500,
            iteration_context="chunk 1 of 3",
        )
        assert result == "condensed output"
        llm.chat_completion.assert_awaited_once()

    async def test_prompt_includes_system_prompt(self):
        pp = _pp()
        llm = _mock_llm("ok")
        pp.llm_client = llm
        pp._system_prompt = "Main system prompt"
        await pp._summarize_with_llm(
            content="data",
            target_size=500,
        )
        call_args = llm.chat_completion.call_args
        messages = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else [])
        system_msg = messages[0]["content"]
        assert "Main system prompt" in system_msg

    async def test_prompt_includes_iteration_context(self):
        pp = _pp()
        llm = _mock_llm("ok")
        pp.llm_client = llm
        pp._system_prompt = None
        await pp._summarize_with_llm(
            content="data",
            target_size=500,
            iteration_context="chunk 2 of 5",
        )
        call_args = llm.chat_completion.call_args
        messages = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else [])
        system_msg = messages[0]["content"]
        assert "chunk 2 of 5" in system_msg

    async def test_temperature_is_low(self):
        pp = _pp()
        llm = _mock_llm("ok")
        pp.llm_client = llm
        pp._system_prompt = None
        await pp._summarize_with_llm(
            content="data",
            target_size=500,
        )
        call_args = llm.chat_completion.call_args
        assert call_args.kwargs.get("temperature") == 0.7

    async def test_max_tokens_derived_from_target(self):
        pp = _pp()
        llm = _mock_llm("ok")
        pp.llm_client = llm
        pp._system_prompt = None
        await pp._summarize_with_llm(
            content="data",
            target_size=8000,
        )
        call_args = llm.chat_completion.call_args
        # max_tokens = max(8000 // 4, 1024) = 2000
        assert call_args.kwargs.get("max_tokens") == 2000

    async def test_empty_response_raises(self):
        pp = _pp()
        llm = AsyncMock()
        llm.chat_completion.return_value = {"content": "", "tool_calls": [], "metadata": {}}
        pp.llm_client = llm
        pp._system_prompt = None
        with pytest.raises(RuntimeError, match="empty summary"):
            await pp._summarize_with_llm(
                content="data",
                target_size=500,
            )

    async def test_none_response_raises(self):
        pp = _pp()
        llm = AsyncMock()
        llm.chat_completion.return_value = None
        pp.llm_client = llm
        pp._system_prompt = None
        with pytest.raises(RuntimeError, match="empty summary"):
            await pp._summarize_with_llm(
                content="data",
                target_size=500,
            )

    async def test_no_system_prompt_still_works(self):
        pp = _pp()
        llm = _mock_llm("ok")
        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._summarize_with_llm(
            content="data",
            target_size=500,
        )
        assert result == "ok"
        # system message should not contain the context block
        call_args = llm.chat_completion.call_args
        messages = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else [])
        system_msg = messages[0]["content"]
        assert "main conversation uses this system context" not in system_msg


# ============================================================================
# _ai_summarize_message (rolling map-reduce)
# ============================================================================


class TestAiSummarizeMessage:
    """Tests for ``_ai_summarize_message()`` rolling map-reduce."""

    async def test_single_chunk_one_llm_call(self):
        """If message fits within chunk_size, only 1 LLM call is made."""
        pp = _pp()
        content = "a" * 500  # less than chunk_size → single chunk
        llm = _mock_llm("brief")
        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._ai_summarize_message(
            content=content,
            target_size=1_000,
            chunk_size=10_000,
        )
        # Content fits in one chunk, so only 1 LLM call
        assert llm.chat_completion.await_count == 1
        assert result == "brief"

    async def test_multiple_chunks_rolling(self):
        """Verify rolling summarization across multiple chunks."""
        pp = _pp()
        content = _big(600)
        call_count = 0

        async def mock_chat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "content": f"summary_{call_count}",
                "tool_calls": [],
                "metadata": {},
            }

        llm = AsyncMock()
        llm.chat_completion.side_effect = mock_chat

        pp.llm_client = llm
        pp._system_prompt = "system"
        result = await pp._ai_summarize_message(
            content=content,
            target_size=1_000,
            chunk_size=200,
        )
        assert call_count >= 3  # min_chunks=3
        # Final result is the last summary
        assert result == f"summary_{call_count}"

    async def test_rolling_combines_prev_summary_with_next_chunk(self):
        """Iteration 2+ receives prev_summary + NEXT SECTION + chunk."""
        pp = _pp()
        content = "a" * 100 + "\n\n" + "b" * 100 + "\n\n" + "c" * 100
        call_inputs = []

        async def capture_chat(*args, **kwargs):
            messages = kwargs.get("messages", args[0] if args else [])
            user_content = messages[1]["content"] if len(messages) > 1 else ""
            call_inputs.append(user_content)
            return {"content": "summary_so_far", "tool_calls": [], "metadata": {}}

        llm = AsyncMock()
        llm.chat_completion.side_effect = capture_chat

        pp.llm_client = llm
        pp._system_prompt = None
        await pp._ai_summarize_message(
            content=content,
            target_size=1_000,
            chunk_size=100,
        )

        # Second call should contain the separator
        assert len(call_inputs) >= 2
        assert "---NEXT SECTION---" in call_inputs[1]

    async def test_llm_error_propagates(self):
        """If any LLM call fails, the error propagates to the caller."""
        pp = _pp()
        content = _big(400)
        llm = AsyncMock()
        llm.chat_completion.side_effect = RuntimeError("LLM down")

        pp.llm_client = llm
        pp._system_prompt = None
        with pytest.raises(RuntimeError, match="LLM down"):
            await pp._ai_summarize_message(
                content=content,
                target_size=1_000,
                chunk_size=100,
            )

    async def test_min_three_chunks_enforced(self):
        """min_chunks only applies when content exceeds chunk_size."""
        pp = _pp()

        # Content smaller than chunk_size → 1 chunk, 1 call
        content_small = _big(600)
        llm = _mock_llm("ok")
        pp.llm_client = llm
        pp._system_prompt = None
        await pp._ai_summarize_message(
            content=content_small,
            target_size=1_000,
            chunk_size=10_000,
        )
        assert llm.chat_completion.await_count == 1

        # Content exceeds chunk_size → min_chunks forces ≥3 chunks
        content_large = _big(25_000)
        llm2 = _mock_llm("ok")
        pp.llm_client = llm2
        await pp._ai_summarize_message(
            content=content_large,
            target_size=1_000,
            chunk_size=10_000,
        )
        assert llm2.chat_completion.await_count >= 3

    async def test_logging_tracks_progress(self, caplog):
        pp = _pp()
        content = _big(600)
        llm = _mock_llm("short")

        pp.llm_client = llm
        pp._system_prompt = None
        with caplog.at_level(logging.DEBUG):
            await pp._ai_summarize_message(
                content=content,
                target_size=1_000,
                chunk_size=200,
            )

        assert "AI summarization: splitting" in caplog.text
        assert "AI summarization complete" in caplog.text


# ============================================================================
# _truncate_text — AI-aware string truncation
# ============================================================================


class TestTruncateText:
    """Tests for ``_truncate_text()`` — string-in / string-out truncation."""

    async def test_successful_ai_summarization(self):
        """AI summary under target → returned with marker."""
        pp = _pp(threshold=500, target=400)
        llm = _mock_llm("short summary")  # well under target

        pp.llm_client = llm
        pp._system_prompt = "ctx"
        result = await pp._truncate_text(_big(600), target=400)
        assert "AI SUMMARY" in result
        assert "short summary" in result

    async def test_ai_summary_too_long_falls_back(self):
        """AI summary exceeds target → plain-text fallback used."""
        pp = _pp(threshold=200, target=100)
        llm = _mock_llm("x" * 200)  # exceeds target of 100

        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._truncate_text(_big(400), target=100)
        # Should have used plain-text truncation as fallback
        assert "TRUNCATED" in result

    async def test_llm_error_falls_back_to_plain_text(self):
        """LLM call raises → plain-text fallback."""
        pp = _pp(threshold=200, target=100)
        llm = AsyncMock()
        llm.chat_completion.side_effect = Exception("timeout")

        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._truncate_text(_big(400), target=100)
        assert "TRUNCATED" in result

    async def test_metadata_preserved_via_single_message(self):
        """Original message metadata is preserved in _truncate_single_message."""
        pp = _pp(threshold=500, target=400)
        msg = {
            "role": "assistant",
            "content": _big(600),
            "tool_call_id": "tc_42",
        }
        llm = _mock_llm("short")

        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._truncate_single_message(msg, target=400)
        assert result["role"] == "assistant"
        assert result["tool_call_id"] == "tc_42"

    async def test_logging_on_success(self, caplog):
        pp = _pp(threshold=500, target=400)
        llm = _mock_llm("ok")

        pp.llm_client = llm
        pp._system_prompt = None
        with caplog.at_level(logging.DEBUG):
            await pp._truncate_text(_big(600), target=400)
        assert "AI summarization succeeded" in caplog.text

    async def test_logging_on_fallback_too_long(self, caplog):
        pp = _pp(threshold=200, target=100)
        llm = _mock_llm("x" * 200)

        pp.llm_client = llm
        pp._system_prompt = None
        with caplog.at_level(logging.WARNING):
            await pp._truncate_text(_big(400), target=100)
        assert "AI summary exceeded target" in caplog.text

    async def test_logging_on_llm_error(self, caplog):
        pp = _pp(threshold=200, target=100)
        llm = AsyncMock()
        llm.chat_completion.side_effect = RuntimeError("fail")

        pp.llm_client = llm
        pp._system_prompt = None
        with caplog.at_level(logging.WARNING):
            await pp._truncate_text(_big(400), target=100)
        assert "AI summarization failed" in caplog.text

    async def test_text_within_target_returned_unchanged(self):
        """Text that fits is returned as-is, no truncation."""
        pp = _pp(threshold=500, target=400)
        text = "This is short enough."
        result = await pp._truncate_text(text, target=400)
        assert result == text

    async def test_context_appears_in_marker(self):
        """The context kwarg appears in the AI summary marker."""
        pp = _pp(threshold=500, target=400)
        llm = _mock_llm("ok")
        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._truncate_text(
            _big(600),
            target=400,
            context="Tool tool_0",
        )
        assert "Tool tool_0" in result


# ============================================================================
# _truncate_oversized_messages — AI integration
# ============================================================================


class TestTruncateSingleMessageToolResults:
    """Regression: ChatManager tool messages route through _truncate_result_entries.

    Bug scenario (from production logs): a tool message with role="tool",
    content="Tool results (round 1)" (22 chars), and tool_results=[...]
    containing ~1.95M of data.  _truncate_single_message incorrectly
    routed this to AI summarization, which summarised the 22-char
    label instead of the actual payload, causing the message to GROW.
    """

    async def test_tool_results_are_truncated_not_label(self):
        """AI-enabled truncation shrinks ``tool_results`` entries, not the label."""
        pp = _pp(threshold=200, target=100)
        llm = _mock_llm("concise result")
        pp.llm_client = llm
        pp._system_prompt = "ctx"

        # Three oversized tool result entries (like 3 downloaded files)
        msg = {
            "role": "tool",
            "content": "Tool results (round 1)",
            "tool_calls": [
                {"id": "tool_0", "name": "download_file"},
                {"id": "tool_1", "name": "download_file"},
                {"id": "tool_2", "name": "download_file"},
            ],
            "tool_results": [
                {"tool_use_id": "tool_0", "result": _big(500)},
                {"tool_use_id": "tool_1", "result": _big(500)},
                {"tool_use_id": "tool_2", "result": _big(500)},
            ],
        }
        original_size = get_content_size(msg)
        assert original_size > 1400  # 3 × 500 + label

        result = await pp._truncate_single_message(msg, target=300)
        new_size = get_content_size(result)

        # The message must actually SHRINK (not grow)
        assert new_size < original_size, f"Tool message grew from {original_size} to {new_size}"
        # AI was used on individual entries
        assert llm.chat_completion.await_count >= 1
        # Label is preserved as-is
        assert result["content"] == "Tool results (round 1)"

    async def test_tool_results_plain_text_fallback(self):
        """Without AI, tool_results entries still get plain-text truncated."""
        pp = _pp(threshold=200, target=100, ai=False)

        msg = {
            "role": "tool",
            "content": "Tool results (round 1)",
            "tool_results": [
                {"tool_use_id": "tool_0", "result": _big(400)},
            ],
        }
        original_size = get_content_size(msg)
        result = await pp._truncate_single_message(msg, target=150)
        new_size = get_content_size(result)
        assert new_size < original_size

    async def test_non_tool_message_still_uses_ai_path(self):
        """Regular user messages still go through AI truncation path."""
        pp = _pp(threshold=200, target=100)
        llm = _mock_llm("summary")
        pp.llm_client = llm
        pp._system_prompt = "ctx"

        msg = {"role": "user", "content": _big(400)}
        result = await pp._truncate_single_message(msg, target=100)
        assert "summary" in result["content"]


class TestTruncateOversizedMessagesAI:
    """Test the AI path wired into ``_truncate_oversized_messages()``."""

    async def test_ai_enabled_calls_summarization(self):
        """With AI ON + llm_client, AI summarization path is used."""
        pp = _pp(threshold=500, target=400)
        msg = {"role": "user", "content": _big(600)}
        llm = _mock_llm("brief summary")

        pp.llm_client = llm
        pp._system_prompt = "ctx"
        result = await pp._truncate_oversized_messages([msg])
        assert "AI SUMMARY" in result[0]["content"]
        assert llm.chat_completion.await_count >= 1

    async def test_ai_disabled_uses_plain_text(self):
        """With AI OFF, plain-text truncation is used even with llm_client."""
        pp = _pp(threshold=500, target=400, ai=False)
        msg = {"role": "user", "content": _big(600)}
        llm = _mock_llm("should not call")

        pp.llm_client = llm
        result = await pp._truncate_oversized_messages([msg])
        assert "TRUNCATED" in result[0]["content"]
        llm.chat_completion.assert_not_awaited()

    async def test_no_llm_client_uses_plain_text(self):
        """AI enabled but no llm_client → plain text."""
        pp = _pp(threshold=500, target=400, ai=True)
        msg = {"role": "user", "content": _big(600)}

        result = await pp._truncate_oversized_messages([msg])
        assert "TRUNCATED" in result[0]["content"]

    async def test_multiple_messages_mixed(self):
        """Multiple messages: only oversized ones get AI treatment."""
        pp = _pp(threshold=500, target=400)
        msgs = [
            {"role": "system", "content": "short"},
            {"role": "user", "content": _big(600)},
            {"role": "assistant", "content": "also short"},
        ]
        llm = _mock_llm("summarized")

        pp.llm_client = llm
        pp._system_prompt = "ctx"
        result = await pp._truncate_oversized_messages(msgs)
        assert len(result) == 3
        assert result[0]["content"] == "short"
        assert "AI SUMMARY" in result[1]["content"]
        assert result[2]["content"] == "also short"

    async def test_ai_error_on_one_message_still_processes_others(self):
        """If AI fails on one message, it falls back; other messages unaffected."""
        pp = _pp(threshold=200, target=150)

        first_message_done = False

        async def flaky_chat(*args, **kwargs):
            nonlocal first_message_done
            if not first_message_done:
                first_message_done = True
                raise RuntimeError("boom")
            return {"content": "ok", "tool_calls": [], "metadata": {}}

        llm = AsyncMock()
        llm.chat_completion.side_effect = flaky_chat

        msgs = [
            {"role": "user", "content": _big(300)},
            {"role": "user", "content": _big(300)},
        ]
        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._truncate_oversized_messages(msgs)
        # First message falls back to plain text (error on first chunk)
        assert "TRUNCATED" in result[0]["content"]
        # Second message gets AI summary (all calls succeed)
        assert "AI SUMMARY" in result[1]["content"]


# ============================================================================
# preprocess_messages — integration with AI
# ============================================================================


class TestPreprocessMessagesAIIntegration:
    """Verify AI summarization flows through the full pipeline."""

    async def test_oversized_message_summarized_in_pipeline(self):
        pp = _pp(threshold=500, target=400)
        llm = _mock_llm("pipeline summary")
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(600)},
        ]
        pp.llm_client = llm
        result = await pp.preprocess_messages(msgs)
        # The oversized user message should have been AI-summarized
        assert "AI SUMMARY" in result[1]["content"]

    async def test_pipeline_no_ai_when_disabled(self):
        pp = _pp(threshold=500, target=400, ai=False)
        llm = _mock_llm("should not call")
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(600)},
        ]
        pp.llm_client = llm
        result = await pp.preprocess_messages(msgs)
        assert "TRUNCATED" in result[1]["content"]
        llm.chat_completion.assert_not_awaited()


# ============================================================================
# on_progress callback — granular notifications from preprocessor
# ============================================================================


class TestOnProgressCallback:
    """Verify that ``preprocess_messages`` emits progress notifications
    via the ``on_progress`` callback during AI summarization."""

    async def test_single_oversized_message_emits_summarizing(self):
        """One oversized message → 'Summarizing conversation...' notification."""
        on_progress = AsyncMock()
        pp = _pp(threshold=500, target=400)
        llm = _mock_llm("summarized")
        pp.llm_client = llm
        msgs = [
            {"role": "user", "content": _big(600)},
        ]
        await pp.preprocess_messages(msgs, on_progress=on_progress)

        messages = [c[0][0]["message"] for c in on_progress.call_args_list]
        assert "Summarizing conversation..." in messages

    async def test_multiple_oversized_messages_emit_per_message_progress(self):
        """Multiple oversized messages → numbered progress per message."""
        on_progress = AsyncMock()
        pp = _pp(threshold=200, target=100)
        llm = _mock_llm("ok")
        pp.llm_client = llm
        msgs = [
            {"role": "user", "content": _big(300)},
            {"role": "user", "content": _big(300)},
            {"role": "user", "content": _big(300)},
        ]
        await pp.preprocess_messages(msgs, on_progress=on_progress)

        messages = [c[0][0]["message"] for c in on_progress.call_args_list]
        assert "Summarizing conversation..." in messages
        assert "Summarizing message 1/3..." in messages
        assert "Summarizing message 2/3..." in messages
        assert "Summarizing message 3/3..." in messages

    async def test_tool_result_entries_emit_per_result_progress(self):
        """Oversized tool results → 'Summarizing result N/M...' per entry."""
        on_progress = AsyncMock()
        pp = _pp(threshold=200, target=100)
        llm = _mock_llm("ok")
        pp.llm_client = llm

        tool_msg = {
            "role": "tool",
            "tool_calls": [
                {"id": "tc_1", "function": {"name": "fn1"}},
                {"id": "tc_2", "function": {"name": "fn2"}},
            ],
            "tool_results": [
                {"call_id": "tc_1", "content": _big(300)},
                {"call_id": "tc_2", "content": _big(300)},
            ],
        }
        msgs = [tool_msg]
        await pp.preprocess_messages(msgs, on_progress=on_progress)

        messages = [c[0][0]["message"] for c in on_progress.call_args_list]
        assert "Summarizing result 1/2..." in messages
        assert "Summarizing result 2/2..." in messages

    async def test_no_progress_without_ai(self):
        """Without AI summarization, no progress notifications emitted."""
        on_progress = AsyncMock()
        pp = _pp(threshold=500, target=400, ai=False)
        msgs = [
            {"role": "user", "content": _big(600)},
        ]
        await pp.preprocess_messages(msgs, on_progress=on_progress)

        on_progress.assert_not_awaited()

    async def test_no_progress_when_nothing_oversized(self):
        """Small messages → no progress notifications."""
        on_progress = AsyncMock()
        pp = _pp(threshold=5_000, target=4_000)
        llm = _mock_llm("ok")
        pp.llm_client = llm
        msgs = [{"role": "user", "content": "short"}]
        await pp.preprocess_messages(msgs, on_progress=on_progress)

        on_progress.assert_not_awaited()

    async def test_on_progress_none_is_safe(self):
        """on_progress=None doesn't cause errors during AI summarization."""
        pp = _pp(threshold=500, target=400)
        llm = _mock_llm("ok")
        pp.llm_client = llm
        msgs = [{"role": "user", "content": _big(600)}]

        # Should not raise
        result = await pp.preprocess_messages(msgs, on_progress=None)
        assert len(result) == 1

    async def test_progress_callback_receives_typing_type(self):
        """All progress notifications have type='typing'."""
        on_progress = AsyncMock()
        pp = _pp(threshold=500, target=400)
        llm = _mock_llm("ok")
        pp.llm_client = llm
        msgs = [{"role": "user", "content": _big(600)}]
        await pp.preprocess_messages(msgs, on_progress=on_progress)

        for call in on_progress.call_args_list:
            assert call[0][0]["type"] == "typing"
