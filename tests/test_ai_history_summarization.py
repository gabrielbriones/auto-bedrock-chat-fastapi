"""Tests for AI-Based History Truncation (Stage 2).

Covers:
- Per-message truncation in ``_truncate_history_total()`` (Stage 2.1)
- Stage 2.2 wipe conditioned on ``not ai_enabled``
- Fallback to plain-text truncation on AI failure
- Integration with ``preprocess_messages()``
"""

from unittest.mock import AsyncMock

from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.message_preprocessor import MessagePreprocessor, get_content_size

# ── Helpers ──────────────────────────────────────────────────────────────


def _cfg(
    history_total_length_threshold: int = 5_000,
    history_msg_length_threshold: int = 1_000,
    history_msg_truncation_target: int = 500,
    max_truncation_recursion: int = 3,
    single_msg_length_threshold: int = 500_000,
    single_msg_truncation_target: int = 425_000,
    ai_summarization: bool = True,
) -> ChatConfig:
    """Build a ChatConfig with sensible small-scale defaults for tests."""
    return ChatConfig(
        BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD=history_total_length_threshold,
        BEDROCK_HISTORY_MSG_LENGTH_THRESHOLD=history_msg_length_threshold,
        BEDROCK_HISTORY_MSG_TRUNCATION_TARGET=history_msg_truncation_target,
        BEDROCK_MAX_TRUNCATION_RECURSION=max_truncation_recursion,
        BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=single_msg_length_threshold,
        BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=single_msg_truncation_target,
        BEDROCK_ENABLE_AI_SUMMARIZATION=ai_summarization,
    )


def _pp(config: ChatConfig | None = None, llm_client=None, **kw) -> MessagePreprocessor:
    if config is None:
        config = _cfg(**kw)
    return MessagePreprocessor(config=config, llm_client=llm_client)


def _big(n: int) -> str:
    """Repeating string of exactly *n* characters."""
    return ("x" * 100 + "\n") * (n // 101) + "x" * (n % 101)


def _mock_llm(summary: str = "Summary of conversation.") -> AsyncMock:
    """Create an AsyncMock LLM client that returns *summary*."""
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
    """Build a conversation with controllable middle-zone sizes."""
    msgs: list[dict] = [{"role": "system", "content": system}]
    for size in middle_sizes or []:
        msgs.append({"role": "user", "content": _big(size)})
        msgs.append({"role": "assistant", "content": _big(size)})
    msgs.append({"role": "user", "content": last_user})
    msgs.append({"role": "assistant", "content": trailing_assistant})
    return msgs


# ============================================================================
# ============================================================================
# AI Step 1 in _run_history_truncation
# ============================================================================


class TestAIStep1InRunHistoryTruncation:
    """Tests for the AI-enabled Step 1 in the history truncation loop."""

    async def test_ai_step1_truncates_middle_zone_in_place(self):
        """Full pipeline: Stage 2.1 truncates each middle-zone message in-place."""
        pp = _pp(
            history_total_length_threshold=500,
            history_msg_length_threshold=200,
            history_msg_truncation_target=100,
            ai_summarization=True,
        )
        llm = _mock_llm("Brief summary of old conversation.")
        msgs = _simple_conv(middle_sizes=[400])
        total_before = pp._total_messages_size(msgs)
        assert total_before > 500

        pp.llm_client = llm
        pp._system_prompt = "Be helpful"
        result = await pp._truncate_history_total(msgs)
        # Message count preserved (truncated in-place, not merged)
        assert len(result) == len(msgs)
        # No whole-zone summary marker
        has_summary = any("[CONVERSATION CONTEXT]" in m.get("content", "") for m in result)
        assert not has_summary
        # Middle-zone messages were AI-summarized
        assert "AI SUMMARY" in result[1]["content"]
        # Total size reduced
        total_after = pp._total_messages_size(result)
        assert total_after < total_before

    async def test_ai_off_uses_plain_text_step1(self):
        """When AI is off, plain-text Step 1 runs instead."""
        pp = _pp(
            history_total_length_threshold=3_000,
            history_msg_length_threshold=500,
            history_msg_truncation_target=200,
            ai_summarization=False,
        )
        msgs = _simple_conv(middle_sizes=[2_000])
        result = await pp._truncate_history_total(msgs)
        # No summary message
        has_summary = any("[CONVERSATION CONTEXT]" in m.get("content", "") for m in result)
        assert not has_summary
        # Same number of messages (plain-text only truncates, doesn't remove)
        assert len(result) == len(msgs)
        assert "TRUNCATED" in result[1]["content"]

    async def test_ai_enabled_but_no_llm_client(self):
        """If enable_ai_summarization=True but llm_client=None, AI is off."""
        pp = _pp(
            history_total_length_threshold=3_000,
            history_msg_length_threshold=500,
            history_msg_truncation_target=200,
            ai_summarization=True,
        )
        msgs = _simple_conv(middle_sizes=[2_000])
        # No llm_client → AI path disabled
        result = await pp._truncate_history_total(msgs)
        has_summary = any("[CONVERSATION CONTEXT]" in m.get("content", "") for m in result)
        assert not has_summary

    async def test_step2_skipped_when_ai_enabled(self):
        """Stage 2.2 (middle wipe) is skipped when ai_enabled=True."""
        pp = _pp(
            history_total_length_threshold=100,
            history_msg_length_threshold=50,
            history_msg_truncation_target=30,
            ai_summarization=True,
        )
        llm = _mock_llm("Brief summary.")
        msgs = _simple_conv(
            system="sys",
            middle_sizes=[200],
            last_user="q",
            trailing_assistant="a",
        )
        n_before = len(msgs)

        pp.llm_client = llm
        pp._system_prompt = "sys"
        result = await pp._truncate_history_total(msgs)
        # Message count preserved — per-message truncation, no wipe
        assert len(result) == n_before
        # No whole-zone summary marker
        has_summary = any("[CONVERSATION CONTEXT]" in m.get("content", "") for m in result)
        assert not has_summary
        # The system and trailing messages are preserved
        assert result[0]["content"] == "sys"
        assert result[-2]["content"] == "q"
        assert result[-1]["content"] == "a"

    async def test_ai_failure_plain_text_fallback(self):
        """When AI is ON + LLM fails, messages fall back to plain-text truncation.

        Stage 2.2 (wipe) is conditioned on ``not ai_enabled``, so it
        never runs when AI is configured ON — even if the LLM is broken.
        The AI path is attempted first, fails, then plain-text
        truncation kicks in as a fallback.
        """
        pp = _pp(
            history_total_length_threshold=50,
            history_msg_length_threshold=50,
            history_msg_truncation_target=30,
            ai_summarization=True,
        )
        llm = AsyncMock()
        llm.chat_completion.side_effect = RuntimeError("LLM down")
        msgs = _simple_conv(
            system="sys",
            middle_sizes=[200],
            last_user="q",
            trailing_assistant="a",
        )
        n_before = len(msgs)
        pp.llm_client = llm
        pp._system_prompt = "sys"
        result = await pp._truncate_history_total(msgs)
        # Message count preserved — Stage 2.2 wipe never runs when ai_enabled
        assert len(result) == n_before
        # System and trailing exchange survive
        assert result[0]["content"] == "sys"
        assert result[-2]["content"] == "q"
        assert result[-1]["content"] == "a"
        # No summary message
        has_summary = any("[CONVERSATION CONTEXT]" in m.get("content", "") for m in result)
        assert not has_summary
        # LLM was called but failed → plain-text fallback used
        assert llm.chat_completion.called

    async def test_no_middle_zone_ai_noop(self):
        """When there's no middle zone, Stage 2.3 still AI-summarizes protected user messages."""
        pp = _pp(
            history_total_length_threshold=100,
            history_msg_length_threshold=50,
            history_msg_truncation_target=30,
            ai_summarization=True,
        )
        llm = _mock_llm("short summary")
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(300)},
            {"role": "assistant", "content": "ok"},
        ]
        pp.llm_client = llm
        result = await pp._truncate_history_total(msgs)
        # No middle zone → Stage 2.3 truncates all user/tool messages
        # AI is enabled so LLM is called for summarization
        assert llm.chat_completion.called
        # Marker may be dropped when marker+summary exceeds target
        assert "short summary" in result[1]["content"]

    async def test_recursive_halving_with_ai(self):
        """Recursive halving works with AI enabled."""
        pp = _pp(
            history_total_length_threshold=200,
            history_msg_length_threshold=150,
            history_msg_truncation_target=100,
            max_truncation_recursion=2,
            ai_summarization=True,
        )
        llm = _mock_llm("Short summary.")
        msgs = [
            {"role": "system", "content": _big(300)},
            {"role": "user", "content": _big(300)},
            {"role": "assistant", "content": _big(300)},
        ]
        pp.llm_client = llm
        result = await pp._truncate_history_total(msgs)
        total = pp._total_messages_size(result)
        assert total < pp._total_messages_size(msgs)

    async def test_string_messages_truncated_with_ai(self):
        """String-content middle-zone messages are AI-summarized when AI is enabled."""
        pp = _pp(
            history_total_length_threshold=100,
            history_msg_length_threshold=200,
            history_msg_truncation_target=100,
            ai_summarization=True,
        )
        llm = _mock_llm("summary")
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(200)},
            {"role": "assistant", "content": _big(200)},
            {"role": "user", "content": "Tell me more"},
            {"role": "assistant", "content": "done"},
        ]
        pp.llm_client = llm
        pp._system_prompt = "Be helpful"
        result = await pp._truncate_history_total(msgs)
        # AI is enabled → LLM called for summarization
        assert llm.chat_completion.called
        # Messages are truncated/summarized
        total_after = pp._total_messages_size(result)
        assert total_after < pp._total_messages_size(msgs)


# ============================================================================
# Integration with preprocess_messages
# ============================================================================


class TestPreprocessMessagesAIHistoryIntegration:
    """Verify preprocess_messages() runs the AI history-total step."""

    async def test_ai_history_truncation_in_pipeline(self):
        pp = _pp(
            history_total_length_threshold=500,
            history_msg_length_threshold=200,
            history_msg_truncation_target=100,
            single_msg_length_threshold=500_000,
            single_msg_truncation_target=425_000,
            ai_summarization=True,
        )
        llm = _mock_llm("Pipeline summary.")
        msgs = _simple_conv(middle_sizes=[400])
        total_before = pp._total_messages_size(msgs)
        assert total_before > 500

        pp.llm_client = llm
        result = await pp.preprocess_messages(msgs)
        # No whole-zone summary marker
        has_summary = any("[CONVERSATION CONTEXT]" in m.get("content", "") for m in result)
        assert not has_summary
        # Messages AI-summarized in-place — count preserved
        assert len(result) == len(msgs)
        assert "AI SUMMARY" in result[1]["content"]
        total_after = pp._total_messages_size(result)
        assert total_after < total_before

    async def test_pipeline_ai_off_no_summary(self):
        pp = _pp(
            history_total_length_threshold=3_000,
            history_msg_length_threshold=500,
            history_msg_truncation_target=200,
            single_msg_length_threshold=500_000,
            single_msg_truncation_target=425_000,
            ai_summarization=False,
        )
        msgs = _simple_conv(middle_sizes=[2_000])
        result = await pp.preprocess_messages(msgs)
        has_summary = any("[CONVERSATION CONTEXT]" in m.get("content", "") for m in result)
        assert not has_summary


# ============================================================================
# Edge cases
# ============================================================================


class TestAIHistoryEdgeCases:

    async def test_single_middle_message_truncated(self):
        """Even a single middle message gets truncated in-place."""
        pp = _pp(
            history_total_length_threshold=100,
            history_msg_length_threshold=200,
            history_msg_truncation_target=100,
            ai_summarization=True,
        )
        llm = _mock_llm("One message summary.")
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(300)},  # single middle msg
            {"role": "assistant", "content": "reply"},  # middle
            {"role": "user", "content": "latest"},
            {"role": "assistant", "content": "done"},
        ]
        pp.llm_client = llm
        pp._system_prompt = None
        result = await pp._truncate_history_total(msgs)
        # Message count preserved
        assert len(result) == len(msgs)
        # No whole-zone summary
        has_summary = any("[CONVERSATION CONTEXT]" in m.get("content", "") for m in result)
        assert not has_summary
        # The oversized middle message was truncated
        assert get_content_size(result[1]) < get_content_size(msgs[1])

    async def test_truncated_content_format(self):
        """Truncated messages use [MESSAGE CONTENT TRUNCATED] markers, not [CONVERSATION CONTEXT]."""
        pp = _pp(
            history_total_length_threshold=100,
            history_msg_length_threshold=200,
            history_msg_truncation_target=100,
            ai_summarization=True,
        )
        llm = _mock_llm("The conversation covered weather topics.")
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(300)},
            {"role": "assistant", "content": _big(300)},
            {"role": "user", "content": "latest"},
            {"role": "assistant", "content": "done"},
        ]
        pp.llm_client = llm
        pp._system_prompt = "test"
        result = await pp._truncate_history_total(msgs)
        # No whole-zone summary markers
        summary_msgs = [m for m in result if "[CONVERSATION CONTEXT]" in m.get("content", "")]
        assert len(summary_msgs) == 0
        # Message count preserved
        assert len(result) == len(msgs)
        # Truncated messages have TRUNCATED markers
        truncated_msgs = [
            m for m in result if isinstance(m.get("content", ""), str) and "TRUNCATED" in m.get("content", "")
        ]
        assert len(truncated_msgs) >= 1
        # Total size reduced
        assert pp._total_messages_size(result) < pp._total_messages_size(msgs)

    async def test_tool_messages_in_middle_zone_truncated(self):
        """Tool call + result messages in the middle zone are truncated."""
        pp = _pp(
            history_total_length_threshold=100,
            ai_summarization=True,
        )
        llm = _mock_llm("Used weather tool, got 42°F.")
        tool_result = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "42°F " + "x" * 200,
                },
            ],
        }
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(200)},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "get_weather"}}],
            },
            tool_result,
            {"role": "assistant", "content": _big(200)},
            {"role": "user", "content": "Thanks"},
            {"role": "assistant", "content": "Welcome"},
        ]
        pp.llm_client = llm
        pp._system_prompt = "test"
        result = await pp._truncate_history_total(msgs)
        # Message count preserved
        assert len(result) == len(msgs)
        # Total size reduced
        assert pp._total_messages_size(result) < pp._total_messages_size(msgs)

    async def test_no_config_skips_ai_history(self):
        """No config → no history truncation at all."""
        pp = MessagePreprocessor(config=None)
        msgs = [
            {"role": "user", "content": _big(5_000)},
            {"role": "assistant", "content": _big(5_000)},
        ]
        result = await pp._truncate_history_total(msgs)
        assert result is msgs

    async def test_whitespace_only_llm_response_still_truncates(self):
        """Even with a whitespace-only LLM response, per-message truncation works."""
        pp = _pp(
            history_total_length_threshold=100,
            history_msg_length_threshold=200,
            history_msg_truncation_target=100,
            ai_summarization=True,
        )
        llm = _mock_llm("   \n  ")  # whitespace only
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(300)},
            {"role": "assistant", "content": _big(300)},
            {"role": "user", "content": "latest"},
            {"role": "assistant", "content": "done"},
        ]
        pp.llm_client = llm
        pp._system_prompt = "test"
        result = await pp._truncate_history_total(msgs)
        # No whole-zone summary marker
        has_summary = any("[CONVERSATION CONTEXT]" in m.get("content", "") for m in result)
        assert not has_summary
        # Messages still truncated via plain-text path
        assert len(result) == len(msgs)
        assert pp._total_messages_size(result) < pp._total_messages_size(msgs)
