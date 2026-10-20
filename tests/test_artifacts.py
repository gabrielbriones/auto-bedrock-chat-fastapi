"""Artifact storage isolation, lifetime, and graph integration."""

import asyncio
import json
import logging
import re
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

import autolangchat.artifacts as artifacts_module
from autolangchat.artifacts import ArtifactError, ArtifactStore
from autolangchat.config import ChatConfig
from autolangchat.graph.graph import build_chat_graph
from autolangchat.graph.nodes.llm_call import llm_call_node
from autolangchat.graph.nodes.preprocess import preprocess_node
from autolangchat.session_manager import ChatSessionManager
from autolangchat.websocket_handler import WebSocketChatHandler


def _store(**limits):
    store = ArtifactStore(**limits)
    store.register_connection("connection", "alice")
    return store, store.activate("connection", "thread", "alice")


def test_isolation_switching_and_reconnection():
    store, scope = _store()
    handle = store.put(scope, "full\ntext\n", {"source": "user_message"})
    store.register_connection("second", "alice")
    other = store.activate("second", "thread", "alice")
    with pytest.raises(ArtifactError):
        store.read_region(other, handle)
    switched = store.activate("connection", "other-thread", "alice")
    with pytest.raises(ArtifactError):
        store.read_region(switched, handle)
    with pytest.raises(ArtifactError):
        store.put(scope, "late turn", {})
    restored = store.activate("connection", "thread", "alice")
    assert store.read_region(restored, handle)["text"] == "full\ntext\n"
    assert store.put(restored, "full\ntext\n", {"source": "user_message"}) == handle
    store.delete_session("connection")
    with pytest.raises(ArtifactError):
        store.put(restored, "late turn", {})
    store.register_connection("connection", "alice")
    new_scope = store.activate("connection", "thread", "alice")
    with pytest.raises(ArtifactError):
        store.read_region(new_scope, handle)


def test_expiry_and_bounded_reads(monkeypatch):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    store, scope = _store(max_age_seconds=10)
    handle = store.put(scope, "\u00e9" * 40_000, {})
    region = store.read_region(scope, handle)
    assert len(region["text"].encode("utf-8")) == 65536
    assert region["truncated"]
    clock[0] = 10
    assert not store.contains(scope, handle)
    with pytest.raises(ArtifactError):
        store.read_region(scope, handle)
    store.close()
    with pytest.raises(ArtifactError):
        store.put(scope, "closed", {})


@pytest.mark.parametrize("text", ["", "a", "a\n", "\n\n", "a\r\nb\rc\n", "a\v\f\x1c\x1d\x1e\x85\u2028\u2029b"])
@pytest.mark.parametrize("start_line,max_lines", [(1, 1), (2, 3), (20, 2)])
def test_region_reads_preserve_splitlines_semantics(text, start_line, max_lines):
    store, scope = _store()
    handle = store.put(scope, text, {})
    lines = text.splitlines(keepends=True)
    result = store.read_region(scope, handle, start_line=start_line, max_lines=max_lines)
    assert result["text"] == "".join(lines[start_line - 1 : start_line - 1 + max_lines])
    assert result["total_lines"] == len(lines)
    assert result["truncated"] is (start_line - 1 + max_lines < len(lines))


@pytest.mark.parametrize("text", ["a\n" * 500_000, "\u20ac" * 1_000_000])
def test_region_read_temporary_allocations_are_bounded(text):
    store, scope = _store()
    handle = store.put(scope, text, {})
    tracemalloc.start()
    try:
        result = store.read_region(scope, handle)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_048_576
    assert len(result["text"].encode("utf-8")) <= 65536
    assert result["truncated"] is True


def test_byte_cap_does_not_skip_partial_unicode_to_append_later_lines():
    store, scope = _store()
    text = "a" * 65534 + "\u20ac\nlater\n"
    handle = store.put(scope, text, {})
    result = store.read_region(scope, handle)
    assert result["text"] == text.encode("utf-8")[:65536].decode("utf-8", errors="ignore")
    assert result["truncated"] is True


@pytest.mark.parametrize("invalidate", ["delete", "switch", "expire"])
def test_region_read_revalidates_after_scanning(monkeypatch, invalidate):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    store, scope = _store(max_age_seconds=10)
    handle = store.put(scope, "first\nsecond\n", {})
    finditer = re.finditer

    def scan(*args):
        for boundary in finditer(*args):
            yield boundary
        if invalidate == "delete":
            store.delete_session("connection")
        elif invalidate == "switch":
            store.activate("connection", "other", "alice")
        else:
            clock[0] = 10

    monkeypatch.setattr(re, "finditer", scan)
    with pytest.raises(ArtifactError):
        store.read_region(scope, handle)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_fallback", [False, True])
async def test_expiry_during_primary_request_preserves_replacement_handle(monkeypatch, use_fallback):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    store, scope = _store(max_age_seconds=10)
    chat_config = ChatConfig().model_copy(
        update={"context_budget_safety_fraction": 0, "fallback_model": "qwen.qwen3-32b-v1:0"}
    )
    original = "full original request"
    original_handle = store.put(scope, original, {"source": "user_message"})
    original_metadata = {"message_id": "u1", "timestamp": "t1", "artifact_handle": original_handle}
    messages = [{"role": "user", "content": original, "metadata": original_metadata}]
    calls = []

    async def invoke(lc_messages):
        calls.append(lc_messages)
        if len(calls) == 1:
            clock[0] = 10
            raise Exception("input is too long")
        if use_fallback and len(calls) == 2:
            raise Exception("input is too long")
        return AIMessage(content="answer")

    async def truncate(**kwargs):
        return [{**kwargs["messages"][0], "content": "shortened"}]

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=invoke)
    with (
        patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm),
        patch("autolangchat.graph.nodes.llm_call.MessagePreprocessor.preprocess_messages", side_effect=truncate),
    ):
        result = await llm_call_node(
            {"messages": messages},
            {"configurable": {"chat_config": chat_config, "artifact_store": store, "artifact_scope": scope}},
        )
    user = result["messages"][0]
    replacement = user["metadata"]["artifact_handle"]
    assert replacement != original_handle
    assert store.read_region(scope, replacement)["text"] == original
    assert user["content"] == original
    assert user["metadata"] == {**original_metadata, "artifact_handle": replacement}
    assert messages[0]["metadata"] == original_metadata
    assert calls[1][0].content == "shortened"
    if use_fallback:
        assert calls[2][0].content == original


def test_atomic_quota_and_deduplication():
    store, scope = _store(max_bytes_per_session=8, max_bytes=8, max_items_per_session=1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        handles = list(executor.map(lambda _: store.put(scope, "\u00e9" * 4, {}), range(8)))
    assert len(set(handles)) == 1
    with pytest.raises(ArtifactError, match="quota"):
        store.put(scope, "different", {})
    store.delete_thread("thread", "alice")
    with pytest.raises(ArtifactError):
        store.put(scope, "late write", {})
    fresh = store.activate("connection", "thread", "alice")
    assert store.put(fresh, "new", {})


def test_deleting_one_thread_preserves_others_and_user_purge_revokes_inflight_scopes():
    store, first = _store()
    first_handle = store.put(first, "first", {})
    second = store.activate("connection", "second", "alice")
    second_handle = store.put(second, "second", {})
    store.delete_thread("thread", "alice")
    assert store.read_region(second, second_handle)["text"] == "second"
    assert not store.contains(second, first_handle)
    store.delete_user("alice")
    with pytest.raises(ArtifactError):
        store.put(second, "late write", {})
    fresh = store.activate("connection", "second", "alice")
    assert not store.contains(fresh, second_handle)


@pytest.mark.asyncio
async def test_graph_archives_original_before_preprocessing_and_checkpoints_handle():
    store, scope = _store()
    config = ChatConfig().model_copy(update={"context_budget_safety_fraction": 0, "enable_ai_summarization": False})
    original = "Original request " * 100
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="response"))
    runnable = {
        "configurable": {
            "thread_id": "thread",
            "chat_config": config,
            "artifact_store": store,
            "artifact_scope": scope,
        }
    }
    with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
        graph = build_chat_graph(config)
        result = await graph.ainvoke({"user_message": original}, config=runnable)
        checkpoint = await graph.aget_state(runnable)
    user = next(message for message in result["messages"] if message["role"] == "user")
    handle = user["metadata"]["artifact_handle"]
    assert user["content"] == original
    assert store.read_region(scope, handle)["text"] == original
    assert any(
        message.get("metadata", {}).get("artifact_handle") == handle for message in checkpoint.values["messages"]
    )
    assert any(message.content == original for message in llm.ainvoke.call_args.args[0])


@pytest.mark.asyncio
async def test_websocket_preserves_feedback_and_title_inputs_and_passes_scope(caplog):
    caplog.set_level(logging.DEBUG, logger="autolangchat.websocket_handler")
    config = ChatConfig().model_copy(update={"conversation_persistence_enabled": True})
    manager = ChatSessionManager(config)
    ws = MagicMock()
    ws.send_json = AsyncMock()
    await manager.create_session(ws, user_id="alice", is_authenticated=True)
    session = await manager.get_session(ws)
    conversation_store = MagicMock()
    conversation_store.create_conversation = AsyncMock()
    conversation_store.record_turn = AsyncMock()
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={"messages": [{"role": "assistant", "content": "answer"}]})
    handler = WebSocketChatHandler(
        session_manager=manager, config=config, chat_graph=graph, conversation_store=conversation_store
    )
    handler._generate_and_apply_title = AsyncMock()
    original = "the full original input"
    try:
        await handler._handle_chat_message(ws, {"message": original})
        await asyncio.sleep(0)
        assert graph.ainvoke.call_args.args[0]["user_message"] == original
        configurable = graph.ainvoke.call_args.kwargs["config"]["configurable"]
        assert configurable["artifact_store"] is manager.artifact_store
        assert configurable["artifact_scope"].connection_id == session.connection_id
        assert next(iter(session.metadata["feedback_meta"].values()))["query"] == original
        assert handler._generate_and_apply_title.call_args.args[2][0]["content"] == original
        assert f"Received user message: {original}" in caplog.text
    finally:
        await handler.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("threshold,expected", [(0, True), (0.5, False)])
async def test_preprocessing_handles_only_oversized_messages(threshold, expected):
    store, scope = _store()
    config = ChatConfig().model_copy(update={"context_budget_safety_fraction": threshold})
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "request"}]
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="answer"))
    runnable = {"configurable": {"chat_config": config, "artifact_store": store, "artifact_scope": scope}}
    with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
        processed = await preprocess_node({"messages": messages}, runnable)
        result = await llm_call_node(processed, runnable)
    assert ("artifact_handle" in result["messages"][1].get("metadata", {})) is expected
    assert "metadata" not in messages[1]
    assert result["messages"][1]["content"] == "request"


@pytest.mark.asyncio
async def test_manager_removal_and_identity_change_revoke_artifacts():
    manager = ChatSessionManager(ChatConfig())
    ws = MagicMock()
    ws.close = AsyncMock()
    try:
        sid = await manager.create_session(ws, user_id="alice")
        session = await manager.get_session(ws)
        store = manager.artifact_store
        scope = store.activate(session.connection_id, "thread", "alice")
        handle = store.put(scope, "secret", {})
        await manager.update_session_user_id(sid, "bob")
        with pytest.raises(ArtifactError):
            store.put(scope, "late write", {})
        fresh = store.activate(session.connection_id, "thread", "bob")
        assert not store.contains(fresh, handle)
        await manager.remove_session(ws)
        with pytest.raises(ArtifactError):
            store.put(fresh, "late write", {})
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_original_is_archived_before_lossy_preprocessing():
    store, scope = _store()
    config = ChatConfig().model_copy(update={"context_budget_safety_fraction": 0})
    original = "entire original input"
    messages = [{"role": "user", "content": original}]

    async def truncate(*, messages, on_progress):
        return [{**messages[0], "content": "truncated"}]

    with patch("autolangchat.graph.nodes.preprocess.MessagePreprocessor.preprocess_messages", side_effect=truncate):
        result = await preprocess_node(
            {"messages": messages},
            {"configurable": {"chat_config": config, "artifact_store": store, "artifact_scope": scope}},
        )
    assert result["messages"][0]["content"] == "truncated"
    handle = result["messages"][0]["metadata"]["artifact_handle"]
    assert store.read_region(scope, handle)["text"] == original


@pytest.mark.asyncio
async def test_quota_failure_is_explicit_and_prevents_model_invocation():
    store, scope = _store(max_bytes_per_session=1)
    config = ChatConfig().model_copy(update={"context_budget_safety_fraction": 0})
    llm = MagicMock()
    llm.ainvoke = AsyncMock()
    with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
        with pytest.raises(ArtifactError, match="quota"):
            await preprocess_node(
                {"messages": [{"role": "user", "content": "too large to archive"}]},
                {"configurable": {"chat_config": config, "artifact_store": store, "artifact_scope": scope}},
            )
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_artifacts_do_not_store_or_reject_input():
    store, scope = _store(max_bytes_per_session=1)
    config = ChatConfig().model_copy(update={"artifact_enabled": False, "context_budget_safety_fraction": 0})
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="answer"))
    with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
        result = await preprocess_node(
            {"messages": [{"role": "user", "content": "original"}]},
            {"configurable": {"chat_config": config, "artifact_store": store, "artifact_scope": scope}},
        )
    assert result["messages"][0] == {"role": "user", "content": "original"}
    assert store._artifacts == {}


@pytest.mark.asyncio
async def test_tool_results_are_archived_with_structure_and_retries_reuse_handles():
    store, scope = _store()
    config = ChatConfig().model_copy(
        update={"context_budget_safety_fraction": 0, "fallback_model": "qwen.qwen3-32b-v1:0"}
    )
    messages = [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "lookup", "args": {}}]},
        {
            "role": "tool",
            "content": "",
            "tool_results": [{"name": "lookup", "tool_call_id": "c1", "result": {"full": "result"}}],
        },
    ]
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        side_effect=[Exception("input is too long"), Exception("input is too long"), AIMessage(content="answer")]
    )
    with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
        runnable = {"configurable": {"chat_config": config, "artifact_store": store, "artifact_scope": scope}}
        processed = await preprocess_node({"messages": messages}, runnable)
        result = await llm_call_node(
            processed,
            runnable,
        )
    assert len(store._artifacts) == 1
    handle = result["messages"][2]["metadata"]["artifact_handle"]
    assert json.loads(store.read_region(scope, handle)["text"]) == messages[2]
    assert result["messages"][2]["tool_results"] == messages[2]["tool_results"]
    assert "artifact_handle" not in result["messages"][0].get("metadata", {})
    assert result["metadata"]["fallback_model_used"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["clear", "new", "load", "delete", "bulk", "all", "logout", "remove", "shutdown"])
async def test_lifecycle_handlers_revoke_inflight_artifact_scopes(operation):
    config = ChatConfig().model_copy(update={"conversation_persistence_enabled": True})
    manager = ChatSessionManager(config)
    ws = MagicMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    await manager.create_session(ws, user_id="alice", is_authenticated=True)
    session = await manager.get_session(ws)
    session.metadata["conversation_id"] = "thread"
    store = manager.artifact_store
    scope = store.activate(session.connection_id, "thread", "alice")
    handle = store.put(scope, "original", {})
    conversations = MagicMock()
    conversations.get_conversation = AsyncMock(return_value={"user_id": "alice", "message_count": 0})
    conversations.delete_conversation = AsyncMock()
    conversations.delete_conversations = AsyncMock(return_value=["thread"])
    conversations.delete_all_conversations = AsyncMock(return_value=1)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=None)
    graph.aupdate_state = AsyncMock()
    handler = WebSocketChatHandler(
        session_manager=manager, config=config, chat_graph=graph, conversation_store=conversations
    )
    operations = {
        "clear": (handler._handle_clear_history, {}),
        "new": (handler._handle_conversation_new, {}),
        "load": (handler._handle_conversation_load, {"conversation_id": "other"}),
        "delete": (handler._handle_conversation_delete, {"conversation_id": "thread"}),
        "bulk": (handler._handle_conversation_delete_bulk, {"conversation_ids": ["thread"]}),
        "all": (handler._handle_conversation_delete_all, {}),
        "logout": (handler._handle_logout, {}),
    }
    try:
        if operation == "remove":
            await manager.remove_session_by_id(session.session_id)
        elif operation == "shutdown":
            await manager.shutdown()
        else:
            method, data = operations[operation]
            await method(ws, data)
        with pytest.raises(ArtifactError):
            store.put(scope, "late write", {})
        if operation not in ("remove", "shutdown"):
            fresh = store.activate(session.connection_id, "thread", "alice")
            if operation in ("new", "load"):
                assert store.read_region(fresh, handle)["text"] == "original"
            else:
                assert not store.contains(fresh, handle)
    finally:
        await handler.shutdown()
