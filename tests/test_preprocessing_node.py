"""Preprocessing node tests.

Tests the preprocess_node (and indirectly MessagePreprocessor) through
the full graph so we exercise the preprocess → END shortcut when the LLM
is mocked.  All four truncation stages are verified without AWS calls.
"""

from __future__ import annotations

import ast
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.artifacts import ArtifactError, ArtifactStore
from autolangchat.context_budget import estimate_message_parts, provider_for_model
from autolangchat.graph.graph import build_chat_graph
from autolangchat.graph.nodes.preprocess import prepare_message_artifacts, preprocess_node
from autolangchat.message_preprocessor import (
    MessagePreprocessor,
    has_artifact_markers,
    is_artifact_marker_entry,
    is_artifact_marker_message,
)

# What an artifact marker says, whether it stands in for a tool result or for a user message.
_GENERIC_MARKER_MESSAGE = "Large content stored as a session artifact; only a preview is shown below."

# ---------------------------------------------------------------------------
# Minimal config stubs with tight thresholds for easy test control
# ---------------------------------------------------------------------------


class _TightConfig:
    """Config with very small thresholds so test messages trigger truncation."""

    model_id = "us.anthropic.claude-sonnet-5"
    fallback_model = None
    aws_region = "us-east-1"
    temperature = 0.7
    max_tokens = 1024
    top_p = 0.9
    checkpoint_postgres_url = None
    enable_ai_summarization = False
    system_prompt = None

    # Stage 1: single message > 50 chars → truncate to 25 chars
    single_msg_length_threshold = 50
    single_msg_truncation_target = 25

    # Stage 2: history total > 80 chars
    history_total_length_threshold = 80
    # Stage 2.3: per-message > 30 chars in history
    history_msg_length_threshold = 30
    history_msg_truncation_target = 20

    max_truncation_recursion = 3

    def get_system_prompt(self) -> str:
        return ""


class _DefaultConfig(_TightConfig):
    """Config using production-sized thresholds — no truncation for normal messages."""

    single_msg_length_threshold = 500_000
    single_msg_truncation_target = 425_000
    history_total_length_threshold = 650_000
    history_msg_length_threshold = 100_000
    history_msg_truncation_target = 85_000


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tight_config():
    return _TightConfig()


@pytest.fixture
def default_config():
    return _DefaultConfig()


def _ai_response(content: str = "ok"):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = []
    msg.response_metadata = {"model_id": "mock"}
    msg.usage_metadata = {"input_tokens": 1, "output_tokens": 1}
    return msg


# ---------------------------------------------------------------------------
# Direct node tests (no graph, no LLM)
# ---------------------------------------------------------------------------


class TestPreprocessNodeDirect:
    @pytest.mark.asyncio
    async def test_short_message_passes_through(self, default_config):
        """Short message is unchanged — preprocessing_applied is False."""
        messages = [{"role": "user", "content": "hi"}]
        state = {"messages": messages, "metadata": {}}
        cfg = {"configurable": {"chat_config": default_config}}

        result = await preprocess_node(state, cfg)

        assert result["messages"] == messages
        assert result["metadata"]["preprocessing_applied"] is False

    @pytest.mark.asyncio
    async def test_stage1_single_message_truncated(self, tight_config):
        """Single oversized message is truncated (Stage 1)."""
        long_content = "A" * 100  # > threshold of 50
        messages = [{"role": "user", "content": long_content}]
        state = {"messages": messages, "metadata": {}}
        cfg = {"configurable": {"chat_config": tight_config}}

        result = await preprocess_node(state, cfg)

        processed = result["messages"]
        assert len(processed) == 1
        assert len(processed[0]["content"]) <= tight_config.single_msg_length_threshold
        assert result["metadata"]["preprocessing_applied"] is True

    @pytest.mark.asyncio
    async def test_stage2_history_truncated(self, tight_config):
        """History that exceeds total threshold is reduced (Stage 2)."""
        # Build a 3-message history totalling well over 80 chars
        messages = [
            {"role": "user", "content": "X" * 40},
            {"role": "assistant", "content": "Y" * 40},
            {"role": "user", "content": "Z" * 40},
        ]
        total_before = sum(len(m["content"]) for m in messages)
        assert total_before > tight_config.history_total_length_threshold

        state = {"messages": messages, "metadata": {}}
        cfg = {"configurable": {"chat_config": tight_config}}

        result = await preprocess_node(state, cfg)

        processed = result["messages"]
        total_after = sum(len(m["content"]) for m in processed)
        assert total_after < total_before
        assert result["metadata"]["preprocessing_applied"] is True

    @pytest.mark.asyncio
    async def test_missing_chat_config_skips_preprocessing(self):
        """When chat_config is absent the node returns {} without crashing."""
        messages = [{"role": "user", "content": "hello"}]
        state = {"messages": messages, "metadata": {}}
        cfg = {"configurable": {}}  # no chat_config

        result = await preprocess_node(state, cfg)

        # Node must not raise; should return empty dict to leave state unchanged
        assert result == {}

    @pytest.mark.asyncio
    async def test_on_progress_not_called_for_short_messages(self, default_config):
        """No progress events are emitted when no truncation happens."""
        calls: List[Dict] = []

        async def on_progress(msg):
            calls.append(msg)

        messages = [{"role": "user", "content": "hello"}]
        state = {"messages": messages, "metadata": {}}
        cfg = {"configurable": {"chat_config": default_config, "on_progress": on_progress}}

        await preprocess_node(state, cfg)

        assert calls == []


# ---------------------------------------------------------------------------
# Graph-level integration: preprocess ← → llm (LLM mocked)
# ---------------------------------------------------------------------------


class TestPreprocessThroughGraph:
    @pytest.mark.asyncio
    async def test_stage1_truncation_reaches_llm(self, tight_config):
        """Oversized message is truncated before the LLM node sees it."""
        long_content = "B" * 200
        received_messages: List[Any] = []

        async def capturing_invoke(lc_messages):
            received_messages.extend(lc_messages)
            return _ai_response("ok")

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.ainvoke = capturing_invoke

            graph = build_chat_graph(tight_config)
            await graph.ainvoke(
                {"user_message": long_content},
                config={"configurable": {"thread_id": "preprocess-graph-test"}},
            )

        # The HumanMessage content forwarded to the LLM should be shorter
        assert len(received_messages) == 1
        llm_content = received_messages[0].content
        assert len(llm_content) < len(long_content)

    @pytest.mark.asyncio
    async def test_preprocessing_applied_flag_in_final_metadata(self, tight_config):
        """Graph metadata reflects whether preprocessing was triggered."""
        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse") as MockLLM:
            instance = MockLLM.return_value
            instance.ainvoke = AsyncMock(return_value=_ai_response())

            graph = build_chat_graph(tight_config)

            # Short message → no preprocessing
            result_short = await graph.ainvoke(
                {"user_message": "hi"},
                config={"configurable": {"thread_id": "flag-test-short"}},
            )
            assert result_short["metadata"]["preprocessing_applied"] is False

            # Long message → preprocessing fires
            result_long = await graph.ainvoke(
                {"user_message": "C" * 200},
                config={"configurable": {"thread_id": "flag-test-long"}},
            )
            assert result_long["metadata"]["preprocessing_applied"] is True


# ---------------------------------------------------------------------------
# Artifact markers (XMGPLAT-11846): exempt from truncation, skipped by archiving
# ---------------------------------------------------------------------------


def _marker_entry(call_id: str = "m1", handle: str = "handle-1") -> Dict[str, Any]:
    return {
        "tool_call_id": call_id,
        "name": "get_report",
        "result": {
            "artifact_handle": handle,
            "source": "tool_response",
            "preview": "row " * 30,
            "size_bytes": 900_000,
        },
        "artifact_marker": True,
    }


def _tool_message(*entries: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "role": "tool",
        "content": "[]",
        "tool_results": list(entries),
        "metadata": {"artifact_markers": any(e.get("artifact_marker") for e in entries)},
    }


def _history(tool_msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "m1", "name": "get_report", "args": {}}]},
        tool_msg,
    ]


def _processor(config: Any) -> MessagePreprocessor:
    return MessagePreprocessor(config=config)


class TestArtifactMarkerHelpers:
    def test_entry_and_message_detection(self):
        marker = _marker_entry()
        plain = {"tool_call_id": "p", "name": "x", "result": "data"}
        assert is_artifact_marker_entry(marker)
        assert not is_artifact_marker_entry(plain)
        assert not is_artifact_marker_entry("nope")
        assert has_artifact_markers(_tool_message(plain, marker))
        assert not has_artifact_markers(_tool_message(plain))
        assert not has_artifact_markers({"role": "user", "content": "x"})

    def test_user_marker_detection(self):
        marker = {"role": "user", "content": "[Large message ...]", "artifact_marker": True}
        assert is_artifact_marker_message(marker) and has_artifact_markers(marker)
        for other in (
            {"role": "user", "content": "x"},
            {"role": "user", "content": "x", "artifact_marker": "yes"},
            {"role": "assistant", "content": "x", "artifact_marker": True},
            "nope",
        ):
            assert not is_artifact_marker_message(other) and not has_artifact_markers(other)


class TestMarkerExemptFromTruncation:
    @pytest.mark.asyncio
    async def test_stage1_keeps_marker_and_truncates_sibling(self, tight_config):
        marker = _marker_entry()
        big = {"tool_call_id": "b1", "name": "other", "result": "D" * 5000}
        messages = _history(_tool_message(marker, big))

        result = await _processor(tight_config).preprocess_messages(messages)

        entries = result[2]["tool_results"]
        assert entries[0] is marker or entries[0] == marker
        assert len(entries[1]["result"]) < 5000

    @pytest.mark.asyncio
    async def test_marker_only_message_is_unchanged_in_every_stage(self, tight_config):
        marker = _marker_entry()
        # Trailing zone (after the last user message): Stage 2.2 only wipes the middle zone.
        messages = _history(_tool_message(marker))

        result = await _processor(tight_config).preprocess_messages(messages)

        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_results"] == [marker]

    @pytest.mark.asyncio
    async def test_stage2_trailing_zone_keeps_marker_in_mixed_batch(self, tight_config):
        # The tool message sits after the last user message, so it is protected from the wipe
        # and reached only by Stage 2.3's per-message truncation.
        marker = _marker_entry(handle="keep-me")
        big = {"tool_call_id": "b1", "name": "other", "result": "E" * 3000}
        messages = [{"role": "user", "content": "old"}] + _history(_tool_message(marker, big))

        result = await _processor(tight_config).preprocess_messages(messages)

        entries = [m for m in result if m["role"] == "tool"][0]["tool_results"]
        assert entries[0] == marker
        assert len(entries[1]["result"]) < 3000

    @pytest.mark.asyncio
    async def test_marker_is_never_summarized_by_ai(self, tight_config):
        tight_config.enable_ai_summarization = True
        marker = _marker_entry()
        big = {"tool_call_id": "b1", "name": "other", "result": "F" * 3000}
        processor = _processor(tight_config)
        processor.llm_client = MagicMock()
        seen: List[str] = []

        async def fake_truncate_text(text, target, **kwargs):
            seen.append(text)
            return text[:target]

        with patch.object(processor, "_truncate_text", side_effect=fake_truncate_text):
            result = await processor.preprocess_messages(_history(_tool_message(marker, big)))

        assert seen and all(t.startswith("F") for t in seen)
        assert result[2]["tool_results"][0] == marker

    @pytest.mark.asyncio
    async def test_content_mirror_still_carries_marker_after_sibling_truncation(self, tight_config):
        marker = _marker_entry(handle="mirror-handle")
        big = {"tool_call_id": "b1", "name": "other", "result": "G" * 3000}

        result = await _processor(tight_config).preprocess_messages(_history(_tool_message(marker, big)))

        assert "mirror-handle" in result[2]["content"]

    @pytest.mark.asyncio
    async def test_user_marker_is_unchanged_in_every_stage(self, tight_config):
        note = str({"artifact_handle": "user-handle", "message": _GENERIC_MARKER_MESSAGE, "preview": "M" * 3000})
        marker = {"role": "user", "content": note, "artifact_marker": True}
        # The same text without the flag is cut to the tight target, so only the exemption keeps the handle.
        plain = {"role": "user", "content": note}
        history = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "answer"},
        ]

        kept = await _processor(tight_config).preprocess_messages(history + [marker])
        cut = await _processor(tight_config).preprocess_messages(history + [plain])

        assert kept[-1] == marker and "user-handle" in kept[-1]["content"]
        assert len(cut[-1]["content"]) < len(note)

    @pytest.mark.asyncio
    async def test_user_marker_is_never_summarized_by_ai(self, tight_config):
        tight_config.enable_ai_summarization = True
        note = str({"artifact_handle": "user-handle", "message": _GENERIC_MARKER_MESSAGE, "preview": "M" * 3000})
        marker = {"role": "user", "content": note, "artifact_marker": True}
        processor = _processor(tight_config)
        processor.llm_client = MagicMock()

        with patch.object(processor, "_truncate_text", side_effect=AssertionError("must not be summarized")):
            result = await processor.preprocess_messages([{"role": "assistant", "content": "a"}, marker])

        assert result[-1] == marker


class _QwenConfig(_DefaultConfig):
    # 32k-token cap: the per-message archive threshold is ~3.3k tokens
    model_id = "qwen.qwen3-32b-v1:0"


class TestArchivePassSkipsMarkers:
    @pytest.mark.asyncio
    async def test_marker_messages_are_not_archived_again(self):
        store = ArtifactStore()
        store.register_connection("conn", "user")
        scope = store.activate("conn", "thread", "user")

        oversized = "word " * 20_000
        marker_msg = _tool_message({**_marker_entry(), "result": {"artifact_handle": "h", "preview": oversized}})
        plain_msg = _tool_message({"tool_call_id": "p1", "name": "x", "result": oversized})
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "m1", "name": "get_report", "args": {}}]},
            marker_msg,
            plain_msg,
        ]
        cfg = {"configurable": {"chat_config": _QwenConfig(), "artifact_store": store, "artifact_scope": scope}}

        result = await preprocess_node({"messages": messages, "metadata": {}}, cfg)

        out = result["messages"]
        # The existing marker message is left as it is: no copy of it is archived and no handle is attached.
        assert out[2]["tool_results"] == marker_msg["tool_results"]
        assert "artifact_handle" not in (out[2].get("metadata") or {})
        # The plain oversized result is replaced by a marker; its original is the only thing stored.
        [replaced] = out[3]["tool_results"]
        assert is_artifact_marker_entry(replaced) and replaced["result"]["artifact_handle"]
        assert "artifact_handle" not in (out[3].get("metadata") or {})
        assert store.statistics()["puts"] == 1
        flags = {
            e["message_index"]: e["exceeds_threshold"] for e in result["metadata"]["message_context_budget"]["messages"]
        }
        assert flags[2] and flags[3]


def _download_history(tool_msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "download", "args": {}}]},
        tool_msg,
    ]


def _live_store():
    store = ArtifactStore()
    store.register_connection("conn", "user")
    return store, store.activate("conn", "thread", "user")


class TestPendingMessageEstimates:
    """The one estimate of a pending message also says what each of its parts costs, whatever the role."""

    def test_user_and_tool_messages_carry_the_estimate_of_each_of_their_parts(self):
        config = {"configurable": {"chat_config": _QwenConfig()}}
        user = {"role": "user", "content": "question " * 300}
        tool = _tool_message(
            {"tool_call_id": "t1", "name": "get_a", "result": {"rows": ["x"] * 400}},
            {"tool_call_id": "t2", "name": "get_b", "error": "boom"},
        )

        for messages, expected_parts in (([user], 1), (_download_history(tool), 2)):
            _, estimates = prepare_message_artifacts(messages, _QwenConfig(), config)

            [estimate] = estimates["messages"]
            assert len(estimate["parts"]) == expected_parts
            assert sum(part["estimated_tokens"] for part in estimate["parts"]) == estimate["estimated_tokens"]
            assert "tool_results" not in estimate

    def test_a_tool_message_without_results_has_no_parts_and_costs_nothing(self):
        config = {"configurable": {"chat_config": _QwenConfig()}}
        messages = [{"role": "user", "content": "q"}, {"role": "tool", "content": "[]", "tool_results": []}]

        _, estimates = prepare_message_artifacts(messages, _QwenConfig(), config)

        user, tool = estimates["messages"]
        assert user["estimated_tokens"] > 0 and len(user["parts"]) == 1
        assert tool["estimated_tokens"] == 0 and tool["parts"] == []


class TestArchivePassToolMessages:
    @pytest.mark.asyncio
    async def test_oversized_result_is_replaced_and_stored_once_as_the_file_text(self):
        """The artifact is the downloaded text itself: neither the JSON mirror nor a copy of the results."""
        store, scope = _live_store()
        text = "line of text\n" * 40_000
        entry = {"tool_call_id": "t1", "name": "download", "result": {"file_content": text}}
        tool_msg = {"role": "tool", "content": json.dumps([entry["result"]]), "tool_results": [entry]}
        cfg = {"configurable": {"chat_config": _QwenConfig(), "artifact_store": store, "artifact_scope": scope}}

        out = (await preprocess_node({"messages": _download_history(tool_msg), "metadata": {}}, cfg))["messages"]

        [replaced] = out[2]["tool_results"]
        marker = replaced["result"]
        assert is_artifact_marker_entry(replaced) and marker["content_field"] == "file_content"
        meta = store.get_meta(scope, marker["artifact_handle"])
        assert meta["size_bytes"] == len(text.encode()) == store.statistics()["bytes"]  # one copy, not two
        assert meta["source"] == "tool_response"
        assert "artifact_handle" not in (out[2].get("metadata") or {})
        assert len(out[2]["content"]) < 5_000  # the mirror no longer carries the original

    @pytest.mark.asyncio
    async def test_the_original_is_estimated_once_and_the_estimate_precedes_the_store(self, caplog):
        store, scope = _live_store()
        text = "line of text\n" * 40_000
        entry = {"tool_call_id": "t1", "name": "download", "result": {"file_content": text}}
        tool_msg = {"role": "tool", "content": "[]", "tool_results": [entry]}
        cfg = {"configurable": {"chat_config": _QwenConfig(), "artifact_store": store, "artifact_scope": scope}}

        from autolangchat.graph.nodes import preprocess as preprocess_module
        from autolangchat.graph.tools import spill as spill_module

        sizes: Dict[str, List[int]] = {"preprocess": [], "spill": []}

        def spy(where):
            def estimate(message, provider=None):
                sizes[where].append(len(str(message)))
                return estimate_message_parts(message, provider)

            return estimate

        with (
            patch.object(preprocess_module, "estimate_message_parts", spy("preprocess")),
            patch.object(spill_module, "estimate_message_parts", spy("spill")),
            caplog.at_level("DEBUG", logger="autolangchat"),
        ):
            await preprocess_node({"messages": _download_history(tool_msg), "metadata": {}}, cfg)

        # The big original is estimated once, by the preprocessing node; the spill only sizes the small marker.
        assert len(sizes["preprocess"]) == 1 and sizes["preprocess"][0] > len(text)
        assert sizes["spill"] and max(sizes["spill"]) < 5_000

        records = [(r.name, r.getMessage()) for r in caplog.records]
        estimates = [i for i, (_, m) in enumerate(records) if m.startswith("Message context estimate")]
        stored = [i for i, (n, m) in enumerate(records) if n == "autolangchat.artifacts" and "Stored artifact" in m]
        spilled = [i for i, (_, m) in enumerate(records) if m.startswith("Spilled tool result")]
        assert len(estimates) == len(stored) == len(spilled) == 1
        assert estimates[0] < stored[0] < spilled[0]
        line = records[estimates[0]][1]
        assert "message_index=2" in line and "exceeds_threshold=True" in line
        # How the number arises: per part, its tokens, characters, content kind and chars/token ratio.
        assert "parts=[download: " in line and "chars, kind=" in line and "chars/token)" in line
        assert not any("Tool result context estimate" in m or "Tool result batch estimate" in m for _, m in records)

    @pytest.mark.asyncio
    async def test_message_with_nothing_to_replace_is_archived_as_a_copy(self, caplog):
        """Errors are never replaced: the message is archived whole, once, and keeps its content."""
        store, scope = _live_store()
        entry = {"tool_call_id": "t1", "name": "download", "error": "boom " * 40_000}
        tool_msg = {"role": "tool", "content": json.dumps([entry["error"]]), "tool_results": [entry]}
        cfg = {"configurable": {"chat_config": _QwenConfig(), "artifact_store": store, "artifact_scope": scope}}

        with caplog.at_level("DEBUG", logger="autolangchat"):
            out = (await preprocess_node({"messages": _download_history(tool_msg), "metadata": {}}, cfg))["messages"]

        handle = out[2]["metadata"]["artifact_handle"]
        expected = json.dumps([entry], ensure_ascii=False)
        meta = store.get_meta(scope, handle)
        assert meta["size_bytes"] == len(expected.encode()) == store.statistics()["bytes"]  # one copy, not two
        assert meta["source"] == "tool_message"
        assert store.read_region(scope, handle, max_lines=1)["text"].startswith('[{"tool_call_id": "t1"')
        assert out[2]["tool_results"][0]["error"] == entry["error"]
        copies = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Stored a copy of message")]
        assert len(copies) == 1 and copies[0].startswith("Stored a copy of message 2 (role=tool")
        assert copies[0].endswith(f"as artifact {handle}; the message keeps its content")
        assert not any("Archived message" in r.getMessage() for r in caplog.records)


class _QwenNoArtifactsConfig(_QwenConfig):
    artifact_enabled = False


class _QwenTinyThresholdConfig(_QwenConfig):
    # A threshold below any marker, so a marker itself is "oversized" on a later pass.
    context_budget_safety_fraction = 0.001


def _paste() -> str:
    """About 520 KB of text: over the legacy truncation threshold (500,000 chars) as well as the artifact one."""
    return "analyze this:\n" + "line of text\n" * 40_000


def _user_history(text: str) -> List[Dict[str, Any]]:
    return [{"role": "user", "content": text, "metadata": {"message_id": "u1", "timestamp": "t"}}]


def _artifact_config(store: ArtifactStore, scope, chat_config: Any = None) -> Dict[str, Any]:
    return {
        "configurable": {
            "chat_config": chat_config or _QwenConfig(),
            "artifact_store": store,
            "artifact_scope": scope,
        }
    }


class TestOversizedUserMessage:
    @pytest.mark.asyncio
    async def test_replaced_by_a_marker_instead_of_being_truncated(self, caplog):
        store, scope = _live_store()
        text = _paste()

        with caplog.at_level("DEBUG", logger="autolangchat"):
            result = await preprocess_node(
                {"messages": _user_history(text), "metadata": {}}, _artifact_config(store, scope)
            )

        [user] = result["messages"]
        marker = ast.literal_eval(user["content"])
        handle = marker["artifact_handle"]
        assert user["artifact_marker"] is True and len(user["content"]) < 3_000
        assert marker["source"] == "user_message" and marker["message"] == _GENERIC_MARKER_MESSAGE
        assert marker["preview"] == text[:1000] and marker["size_bytes"] == len(text.encode())
        assert user["metadata"] == {"message_id": "u1", "timestamp": "t"}  # the handle is in the marker only
        # The artifact is the message, stored once; the original is neither truncated nor stored twice.
        assert store.read_region(scope, handle, max_lines=1)["text"] == "analyze this:\n"
        assert store.statistics()["puts"] == 1 and store.get_meta(scope, handle)["size_bytes"] == len(text.encode())
        messages = [r.getMessage() for r in caplog.records]
        assert not any(m.startswith(("Truncating message", "Truncated oversized")) for m in messages)
        assert any("no messages exceeded threshold" in m for m in messages)

    @pytest.mark.asyncio
    async def test_the_estimate_precedes_the_store_and_the_spill_lines(self, caplog):
        store, scope = _live_store()

        with caplog.at_level("DEBUG", logger="autolangchat"):
            await preprocess_node({"messages": _user_history(_paste()), "metadata": {}}, _artifact_config(store, scope))

        records = [(r.name, r.getMessage()) for r in caplog.records]
        estimates = [i for i, (_, m) in enumerate(records) if m.startswith("Message context estimate")]
        stored = [i for i, (n, m) in enumerate(records) if n == "autolangchat.artifacts" and "Stored artifact" in m]
        spilled = [i for i, (_, m) in enumerate(records) if m.startswith("Spilled user message")]
        assert len(estimates) == len(stored) == len(spilled) == 1
        assert estimates[0] < stored[0] < spilled[0]
        assert "message_index=0" in records[estimates[0]][1] and "exceeds_threshold=True" in records[estimates[0]][1]
        assert records[spilled[0]][1].startswith("Spilled user message 0: 520,")
        # No vague "archived" line for a message that was replaced.
        assert not any("Archived message" in m or "Stored a copy" in m for _, m in records)

    @pytest.mark.asyncio
    async def test_the_estimate_line_describes_the_text_like_it_does_for_a_tool_result(self, caplog):
        store, scope = _live_store()
        text = _paste()

        with caplog.at_level("DEBUG", logger="autolangchat"):
            await preprocess_node({"messages": _user_history(text), "metadata": {}}, _artifact_config(store, scope))

        [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Message context estimate")]
        [part] = estimate_message_parts(_user_history(text)[0], provider_for_model(_QwenConfig.model_id))
        assert f"message_index=0 estimated_tokens={part.tokens} " in line
        # How the number arises: the message text's tokens, characters, content kind and chars/token ratio.
        assert line.endswith(
            f"parts=[content: {part.tokens} tokens ({len(text):,} chars, kind={part.kind}, "
            f"{part.chars_per_token:.2f} chars/token)]"
        )

    @pytest.mark.asyncio
    async def test_the_marker_is_what_a_later_pass_and_the_checkpoint_hold(self):
        store, scope = _live_store()
        config = _artifact_config(store, scope, _QwenTinyThresholdConfig())

        first = await preprocess_node({"messages": _user_history(_paste()), "metadata": {}}, config)
        again, estimates = prepare_message_artifacts(first["messages"], config["configurable"]["chat_config"], config)

        # The marker is over this tiny threshold, but it is never replaced again nor copied into the store.
        assert estimates["messages"][-1]["exceeds_threshold"] is True
        assert again == first["messages"] and "artifact_handle" not in again[0]["metadata"]
        assert store.statistics()["puts"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", ["no_scope", "artifacts_disabled"])
    async def test_without_a_usable_store_the_message_is_left_to_truncation(self, case):
        store, scope = _live_store()
        chat_config = _QwenNoArtifactsConfig() if case == "artifacts_disabled" else _QwenConfig()
        config = _artifact_config(store, scope, chat_config)
        if case == "no_scope":
            config = {"configurable": {"chat_config": chat_config}}

        [user] = (await preprocess_node({"messages": _user_history(_paste()), "metadata": {}}, config))["messages"]

        assert "artifact_marker" not in user and "MESSAGE CONTENT TRUNCATED" in user["content"]
        assert store.statistics()["puts"] == 0

    @pytest.mark.asyncio
    async def test_a_message_that_cannot_be_stored_stops_the_turn(self, caplog):
        store = ArtifactStore(max_bytes_per_session=1024)
        store.register_connection("conn", "user")
        scope = store.activate("conn", "thread", "user")

        with caplog.at_level("ERROR", logger="autolangchat"):
            with pytest.raises(ArtifactError, match="quota"):
                await preprocess_node(
                    {"messages": _user_history(_paste()), "metadata": {}}, _artifact_config(store, scope)
                )

        assert any(r.getMessage() == "Failed to store message 0 as a session artifact" for r in caplog.records)
        assert store.statistics()["puts"] == 0

    @pytest.mark.asyncio
    async def test_a_message_the_marker_would_not_shrink_is_still_copied_with_a_handle(self):
        """Over a tiny threshold but short: a marker would be longer, so the message keeps its content."""
        store, scope = _live_store()
        config = _artifact_config(store, scope, _QwenTinyThresholdConfig())
        text = "short question " * 14

        [user] = (await preprocess_node({"messages": _user_history(text), "metadata": {}}, config))["messages"]

        assert user["content"] == text and "artifact_marker" not in user
        assert store.read_region(scope, user["metadata"]["artifact_handle"])["text"] == text
