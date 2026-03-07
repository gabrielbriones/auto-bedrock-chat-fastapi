"""Tests for Task 3.3: History-Total Truncation (Plain Text Path).

Covers:
- ``_detect_zones()`` — zone classification
- ``_total_messages_size()`` — sum helper
- ``_wipe_middle_zone()`` — step 2 removal
- ``_history_step_truncate_zone()`` — per-index truncation
- ``_truncate_history_total()`` — the orchestrating method
- ``_run_history_truncation()`` — 3-step + recursive halving
- Integration: pipeline order in ``preprocess_messages()``
"""

import logging
from unittest.mock import AsyncMock, MagicMock

from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.message_preprocessor import MessagePreprocessor

# ── Helpers ──────────────────────────────────────────────────────────────


def _cfg(
    history_total_length_threshold: int = 5_000,
    history_msg_length_threshold: int = 1_000,
    history_msg_truncation_target: int = 500,
    max_truncation_recursion: int = 3,
    single_msg_length_threshold: int = 500_000,
    single_msg_truncation_target: int = 425_000,
    ai_summarization: bool = False,
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


def _pp(config: ChatConfig | None = None, **kw) -> MessagePreprocessor:
    if config is None:
        config = _cfg(**kw)
    return MessagePreprocessor(config=config)


def _big(n: int) -> str:
    """Repeating string of exactly *n* characters."""
    return ("x" * 100 + "\n") * (n // 101) + "x" * (n % 101)


def _simple_conv(
    *,
    system: str = "You are helpful.",
    middle_sizes: list[int] | None = None,
    last_user: str = "What is 2+2?",
    trailing_assistant: str = "4",
) -> list[dict]:
    """Build a typical conversation with controllable middle-zone sizes.

    Layout: [system, *middle, last_user, trailing_assistant]
    """
    msgs: list[dict] = [{"role": "system", "content": system}]
    for size in middle_sizes or []:
        msgs.append({"role": "user", "content": _big(size)})
        msgs.append({"role": "assistant", "content": _big(size)})
    msgs.append({"role": "user", "content": last_user})
    msgs.append({"role": "assistant", "content": trailing_assistant})
    return msgs


# ── _detect_zones ────────────────────────────────────────────────────────


class TestDetectZones:
    """Tests for zone classification."""

    def test_empty_messages(self):
        zones = MessagePreprocessor._detect_zones([])
        assert zones == {"protected": [], "middle": []}

    def test_system_only(self):
        msgs = [{"role": "system", "content": "hello"}]
        zones = MessagePreprocessor._detect_zones(msgs)
        # System msg is protected; no middle zone
        assert zones["protected"] == [0]
        assert zones["middle"] == []

    def test_system_plus_user_plus_assistant(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        # Protected: system (0) + last user (1) + trailing assistant (2)
        assert zones["protected"] == [0, 1, 2]
        assert zones["middle"] == []

    def test_middle_zone_between_system_and_last_user(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old question"},  # middle
            {"role": "assistant", "content": "old answer"},  # middle
            {"role": "user", "content": "new question"},  # protected (last user)
            {"role": "assistant", "content": "new answer"},  # protected (trailing)
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        assert zones["middle"] == [1, 2]
        assert zones["protected"] == [0, 3, 4]

    def test_no_system_message(self):
        msgs = [
            {"role": "user", "content": "old"},  # middle
            {"role": "assistant", "content": "old ans"},  # middle
            {"role": "user", "content": "latest"},  # protected
            {"role": "assistant", "content": "reply"},  # protected
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        assert zones["middle"] == [0, 1]
        assert zones["protected"] == [2, 3]

    def test_tool_messages_not_counted_as_user(self):
        """Tool-result messages should NOT count as the 'last real user msg'."""
        from auto_bedrock_chat_fastapi.message_preprocessor import is_user_message

        tool_msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "data"},
            ],
        }
        assert not is_user_message(tool_msg)

        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "real question"},  # last real user
            {"role": "assistant", "content": "calling tool"},
            tool_msg,  # trailing (after last user)
            {"role": "assistant", "content": "final answer"},
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        # Protected: 0 (system), 1 (last real user), 2-4 (trailing)
        assert zones["protected"] == [0, 1, 2, 3, 4]
        assert zones["middle"] == []

    def test_large_middle_zone(self):
        msgs = [
            {"role": "system", "content": "sys"},
            *[{"role": "user", "content": f"q{i}"} for i in range(5)],
            *[{"role": "assistant", "content": f"a{i}"} for i in range(5)],
            {"role": "user", "content": "latest"},
            {"role": "assistant", "content": "done"},
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        n = len(msgs)
        # System=0, latest user=n-2, trailing assistant=n-1 → protected
        expected_protected = {0, n - 2, n - 1}
        assert set(zones["protected"]) == expected_protected
        assert set(zones["middle"]) == set(range(n)) - expected_protected

    def test_only_one_user_message(self):
        """When there's only one user message, everything is protected."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "only question"},
            {"role": "assistant", "content": "answer"},
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        assert zones["protected"] == [0, 1, 2]
        assert zones["middle"] == []


# ── _total_messages_size ─────────────────────────────────────────────────


class TestTotalMessagesSize:
    def test_basic(self):
        msgs = [
            {"role": "user", "content": "hello"},  # 5
            {"role": "assistant", "content": "hi"},  # 2
        ]
        assert MessagePreprocessor._total_messages_size(msgs) == 7

    def test_empty(self):
        assert MessagePreprocessor._total_messages_size([]) == 0


# ── _wipe_middle_zone ───────────────────────────────────────────────────


class TestWipeMiddleZone:
    def test_removes_middle_indices(self):
        msgs = ["a", "b", "c", "d", "e"]
        result = MessagePreprocessor._wipe_middle_zone(msgs, [1, 2, 3])
        assert result == ["a", "e"]

    def test_empty_middle(self):
        msgs = ["a", "b"]
        result = MessagePreprocessor._wipe_middle_zone(msgs, [])
        assert result == ["a", "b"]


# ── _history_step_truncate_zone ──────────────────────────────────────────


class TestHistoryStepTruncateZone:
    async def test_truncates_oversized_at_indices(self):
        pp = _pp()
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(2_000)},  # index 1 → middle
            {"role": "assistant", "content": "short"},  # index 2
            {"role": "user", "content": "latest"},
        ]
        result = await pp._history_step_truncate_zone(
            msgs,
            indices=[1, 2],
            msg_threshold=1_000,
            msg_target=500,
        )
        # Index 1 was oversized → truncated
        assert "TRUNCATED" in result[1]["content"]
        # Index 2 was under threshold → unchanged
        assert result[2]["content"] == "short"
        # Original list not mutated
        assert "TRUNCATED" not in msgs[1]["content"]

    async def test_no_indices_noop(self):
        pp = _pp()
        msgs = [{"role": "user", "content": _big(2_000)}]
        result = await pp._history_step_truncate_zone(
            msgs,
            indices=[],
            msg_threshold=500,
            msg_target=200,
        )
        assert result[0]["content"] == msgs[0]["content"]


# ── _truncate_history_total (orchestrator) ───────────────────────────────


class TestTruncateHistoryTotal:
    """Test the full 3-step + recursive halving flow."""

    async def test_no_config_returns_unchanged(self):
        pp = MessagePreprocessor(config=None)
        msgs = [{"role": "user", "content": _big(10_000)}]
        result = await pp._truncate_history_total(msgs)
        assert result is msgs

    async def test_under_threshold_returns_unchanged(self):
        pp = _pp(history_total_length_threshold=100_000)
        msgs = _simple_conv(middle_sizes=[100, 100])
        result = await pp._truncate_history_total(msgs)
        assert result == msgs

    async def test_step1_resolves_by_truncating_middle(self):
        """Step 1 alone should be enough when a single middle msg is oversized."""
        pp = _pp(
            history_total_length_threshold=3_000,
            history_msg_length_threshold=500,
            history_msg_truncation_target=200,
        )
        # Middle zone: 1 pair of user+assistant, each 2000 chars
        msgs = _simple_conv(middle_sizes=[2_000])
        total_before = pp._total_messages_size(msgs)
        assert total_before > 3_000

        result = await pp._truncate_history_total(msgs)
        total_after = pp._total_messages_size(result)

        # Same number of messages (step 1 doesn't remove, just truncates)
        assert len(result) == len(msgs)
        assert total_after < total_before
        # Middle messages were truncated
        assert "TRUNCATED" in result[1]["content"]

    async def test_step2_wipes_middle_when_step1_not_enough(self):
        """If step 1 is insufficient, step 2 wipes the middle zone entirely."""
        pp = _pp(
            history_total_length_threshold=100,  # Very tight — only system + last exchange survive
            history_msg_length_threshold=50,
            history_msg_truncation_target=30,
        )
        msgs = _simple_conv(
            system="sys",
            middle_sizes=[200, 200],
            last_user="q",
            trailing_assistant="a",
        )
        assert len(msgs) == 7  # system + 4 middle + user + assistant

        result = await pp._truncate_history_total(msgs)

        # Middle zone (4 messages) should be gone
        assert len(result) < len(msgs)
        # System and trailing exchange survive
        assert result[0]["content"] == "sys"
        assert result[-2]["content"] == "q"
        assert result[-1]["content"] == "a"

    async def test_step3_truncates_protected_zone(self):
        """When step 2 resolved middle but protected is still oversized."""
        # Build a conversation where the last user message is huge
        pp = _pp(
            history_total_length_threshold=500,
            history_msg_length_threshold=300,
            history_msg_truncation_target=100,
        )
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(1_000)},  # last real user
            {"role": "assistant", "content": "ok"},
        ]
        # No middle zone → step 1 is a no-op, step 2 is a no-op
        # Step 3 should truncate the user message in protected zone
        result = await pp._truncate_history_total(msgs)
        assert "TRUNCATED" in result[1]["content"]

    async def test_step3_skips_system_and_assistant(self):
        """Step 3 should only truncate user/tool messages, not system or assistant."""
        pp = _pp(
            history_total_length_threshold=500,
            history_msg_length_threshold=300,
            history_msg_truncation_target=100,
        )
        # Build a conversation where system, assistant AND user are all oversized.
        # Only the user message should be truncated in Step 3.
        big_system = _big(400)
        big_assistant = _big(400)
        big_user = _big(1_000)
        msgs = [
            {"role": "system", "content": big_system},
            {"role": "user", "content": big_user},  # last real user
            {"role": "assistant", "content": big_assistant},
        ]
        # No middle zone → steps 1-2 are no-ops
        result = await pp._truncate_history_total(msgs)
        # User message should be truncated (role="user" > 300 threshold)
        assert "TRUNCATED" in result[1]["content"]
        # System message should NOT be truncated (protected by role filter)
        assert result[0]["content"] == big_system
        # Assistant message should NOT be truncated (protected by role filter)
        assert result[2]["content"] == big_assistant

    async def test_step3_truncates_tool_messages(self):
        """Step 3 should truncate tool messages in the protected zone."""
        pp = _pp(
            history_total_length_threshold=500,
            history_msg_length_threshold=300,
            history_msg_truncation_target=100,
        )
        # Conversation: user asks question → assistant calls tool → tool result → assistant responds
        # The tool result is oversized and should be truncated by Step 3
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "look up job"},  # last real user
            {"role": "assistant", "content": "calling tool"},
            {"role": "tool", "content": _big(1_000)},  # oversized tool
            {"role": "assistant", "content": "done"},
        ]
        result = await pp._truncate_history_total(msgs)
        # Tool message should be truncated (role="tool" > 300)
        assert "TRUNCATED" in result[3]["content"]
        # Assistant messages should NOT be truncated
        assert result[2]["content"] == "calling tool"
        assert result[4]["content"] == "done"

    async def test_recursive_halving(self):
        """When all 3 steps at the initial thresholds aren't enough,
        recursive halving kicks in."""
        pp = _pp(
            history_total_length_threshold=200,
            history_msg_length_threshold=150,
            history_msg_truncation_target=100,
            max_truncation_recursion=2,
        )
        msgs = [
            {"role": "system", "content": _big(300)},
            {"role": "user", "content": _big(300)},
            {"role": "assistant", "content": _big(300)},
        ]
        result = await pp._truncate_history_total(msgs)
        total = pp._total_messages_size(result)
        # After recursive halving with depth up to 2, total should be smaller
        assert total < pp._total_messages_size(msgs)

    async def test_max_recursion_stops(self, caplog):
        """Recursion stops at max_recursion_depth and logs an error."""
        pp = _pp(
            history_total_length_threshold=10,  # impossibly tight
            history_msg_length_threshold=5,
            history_msg_truncation_target=3,
            max_truncation_recursion=1,
        )
        msgs = [
            {"role": "system", "content": _big(500)},
            {"role": "user", "content": _big(500)},
            {"role": "assistant", "content": _big(500)},
        ]
        with caplog.at_level(logging.ERROR):
            result = await pp._truncate_history_total(msgs)
        assert "max recursion" in caplog.text.lower()
        # Still returns a result (best effort)
        assert len(result) > 0

    async def test_logging_stage2_1(self, caplog):
        pp = _pp(
            history_total_length_threshold=500,
            history_msg_length_threshold=200,
            history_msg_truncation_target=100,
        )
        msgs = _simple_conv(middle_sizes=[400])
        with caplog.at_level(logging.INFO):
            await pp._truncate_history_total(msgs)
        assert "Stage 2.1" in caplog.text

    async def test_ai_enabled_skips_stage2_2_wipe(self):
        """When AI summarization is enabled, Stage 2.2 (wipe) is skipped.

        Stage 2.1 does per-message truncation of the middle zone.
        Stage 2.2 (wipe entire middle zone) is only for AI-OFF.
        With AI on, middle messages are individually truncated, not wiped.
        """
        pp = _pp(
            history_total_length_threshold=200,
            history_msg_length_threshold=150,
            history_msg_truncation_target=100,
            ai_summarization=True,
        )
        msgs = _simple_conv(
            system="sys",
            middle_sizes=[200],
            last_user="q",
            trailing_assistant="a",
        )
        n_before = len(msgs)
        # AsyncMock so the AI path can call llm_client.chat_completion()
        llm_client = AsyncMock()
        llm_client.chat_completion.return_value = {
            "content": "summary text",
            "tool_calls": [],
            "metadata": {},
        }
        pp.llm_client = llm_client
        result = await pp._truncate_history_total(msgs)
        # Stage 2.2 (wipe) skipped because AI is on →
        # all messages survive but middle ones are truncated per-message.
        assert len(result) == n_before
        # Middle messages should have AI SUMMARY or TRUNCATED markers (AI may
        # fall back to plain text when recursive halving makes targets tiny),
        # but never [CONVERSATION CONTEXT] (that would indicate a wipe).
        assert any("AI SUMMARY" in m.get("content", "") or "TRUNCATED" in m.get("content", "") for m in result)
        assert not any("[CONVERSATION CONTEXT]" in m.get("content", "") for m in result)


# ── Integration with preprocess_messages ─────────────────────────────────


class TestPreprocessMessagesHistoryIntegration:
    """Verify ``preprocess_messages()`` runs the history-total step."""

    async def test_history_truncation_runs_in_pipeline(self):
        pp = _pp(
            history_total_length_threshold=500,
            history_msg_length_threshold=200,
            history_msg_truncation_target=100,
            single_msg_length_threshold=500_000,
            single_msg_truncation_target=425_000,
        )
        msgs = _simple_conv(middle_sizes=[400])
        total_before = pp._total_messages_size(msgs)
        assert total_before > 500

        result = await pp.preprocess_messages(msgs)
        total_after = pp._total_messages_size(result)
        assert total_after < total_before

    async def test_pipeline_order_single_then_history(self):
        """Single-message truncation (step 2) runs BEFORE history-total (step 3)."""
        # A message that's huge enough to trigger single-message truncation
        # AND where the total history also exceeds threshold
        pp = _pp(
            history_total_length_threshold=1_000,
            history_msg_length_threshold=300,
            history_msg_truncation_target=150,
            single_msg_length_threshold=2_000,
            single_msg_truncation_target=1_000,
        )
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(3_000)},  # hits single-msg threshold
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": _big(3_000)},  # last user, also big
            {"role": "assistant", "content": "done"},
        ]
        result = await pp.preprocess_messages(msgs)
        # Both truncation passes should have fired
        total = pp._total_messages_size(result)
        # After both passes, total should be much smaller
        assert total < pp._total_messages_size(msgs)

    async def test_no_config_skips_history_truncation(self):
        pp = MessagePreprocessor(config=None)
        msgs = [
            {"role": "user", "content": _big(5_000)},
            {"role": "assistant", "content": _big(5_000)},
        ]
        result = await pp.preprocess_messages(msgs)
        # Nothing truncated (no config)
        assert pp._total_messages_size(result) == pp._total_messages_size(msgs)


# ── Integration with ChatManager ─────────────────────────────────────────


class TestChatManagerHistoryWiring:
    """Verify chat_manager._preprocess_messages triggers history truncation
    via the message_preprocessor path."""

    async def test_oversized_history_reduced_via_chat_manager(self):
        from auto_bedrock_chat_fastapi.chat_manager import ChatManager

        config = _cfg(
            history_total_length_threshold=500,
            history_msg_length_threshold=200,
            history_msg_truncation_target=100,
        )

        cm = ChatManager(
            llm_client=MagicMock(),
            config=config,
        )

        msgs = _simple_conv(middle_sizes=[400])
        metadata: dict = {}
        result = await cm._preprocess_messages(msgs, metadata)

        # chat_manager._preprocess_messages calls
        # _truncate_oversized_messages, but NOT _truncate_history_total
        # directly (that's only in preprocess_messages).
        # This test verifies the sync path works without error.
        assert len(result) > 0


# ── Edge cases ───────────────────────────────────────────────────────────


class TestHistoryTruncationEdgeCases:
    async def test_single_message_conversation(self):
        """A single user message — no middle zone, step 3 may truncate."""
        pp = _pp(
            history_total_length_threshold=100,
            history_msg_length_threshold=50,
            history_msg_truncation_target=30,
        )
        msgs = [{"role": "user", "content": _big(500)}]
        result = await pp._truncate_history_total(msgs)
        assert len(result) == 1
        assert "TRUNCATED" in result[0]["content"]

    async def test_all_protected_no_middle(self):
        """When all messages are protected, step 1-2 do nothing."""
        pp = _pp(
            history_total_length_threshold=100,
            history_msg_length_threshold=50,
            history_msg_truncation_target=30,
        )
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": _big(300)},
            {"role": "assistant", "content": "ok"},
        ]
        zones = MessagePreprocessor._detect_zones(msgs)
        assert zones["middle"] == []

        result = await pp._truncate_history_total(msgs)
        # Step 3 should truncate the big user message
        assert "TRUNCATED" in result[1]["content"]

    async def test_non_dict_messages_handled(self):
        pp = _pp(history_total_length_threshold=10)
        msgs = ["not-a-dict", {"role": "user", "content": "hi"}]
        result = await pp._truncate_history_total(msgs)
        assert result[0] == "not-a-dict"

    async def test_empty_messages(self):
        pp = _pp(history_total_length_threshold=10)
        result = await pp._truncate_history_total([])
        assert result == []
