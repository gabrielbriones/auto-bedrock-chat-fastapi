"""Unit tests for conversation persistence in the WebSocket handler.

Covers ``WebSocketChatHandler``:

  (a) all six ``conversation_*`` message types dispatch to their handlers
      (mock store + checkpointer);
  (b) ``_handle_conversation_load`` reads history via ``chat_graph.aget_state``
      only — it never queries the conversation store for messages;
  (c) auth gating: ``conversation_persistence_disabled`` when not configured,
      ``unauthorized_conversation`` when the session has no ``user_id``;
  (d) IDOR protection: loading/deleting/renaming another user's conversation
      returns ``conversation_not_found`` (not a distinguishing 403);
  (e) ``conversation_history_unavailable`` only when a conversation has
      recorded turns (``message_count > 0``) but ``aget_state`` yields no
      checkpoint values — a zero-message conversation loads with an empty
      history instead;
  (f) lazy conversation creation on the first chat message, reused on
      subsequent turns, with ``record_turn`` called after each turn;
  (g) anonymous (no ``user_id``) connections never get persisted
      conversations even when the feature is enabled;
  (h) auto-titling: ``conversation_titled`` is broadcast after the
      fire-and-forget background task completes, exactly once per
      conversation.
  (i) regression: ``_active_thread_id`` (used by feedback/history/clear)
      falls back to ``session.session_id`` — not ``None`` — whenever
      ``_handle_chat_message`` itself would fall back to it (persistence
      enabled but no store wired, or an anonymous session), so those
      handlers don't report "no active conversation" for a thread chat is
      still actively writing to.
  (j) ``conversation_list`` rejects a ``limit`` above the 200 upper bound
      (mirrors the REST endpoint's cap) rather than allowing an
      unbounded response, and projects each item down to the documented
      ``{id, title, updated_at, message_count}`` shape rather than
      forwarding the full store row.
  (k) regression: a ``conversation_store.create_conversation`` failure
      (e.g. transient DB error) degrades to the legacy
      ``session_id``-as-``thread_id`` behavior for that turn — chat
      delivery still succeeds — rather than surfacing an error
      ``ai_response``.
"""

import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from autolangchat import websocket_handler
from autolangchat.history import TOOL_LOOP_LEASE
from autolangchat.session_manager import ChatSessionManager
from autolangchat.websocket_handler import WebSocketChatHandler


def _make_config(**overrides):
    config = MagicMock()
    config.timeout = 30.0
    config.model_id = "us.anthropic.claude-sonnet-4-6"
    config.require_tool_auth = False
    config.include_auth_info_in_prompts = False
    config.feedback_allow_anonymous = False
    config.conversation_persistence_enabled = True
    config.conversation_title_model_id = None
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _make_session(user_id="alice"):
    return SimpleNamespace(
        session_id="ws-session-1",
        user_id=user_id,
        credentials=None,
        auth_handler=None,
        metadata={},
    )


class _FakeCheckpointState:
    def __init__(self, values):
        self.values = values


def _make_chat_graph():
    """A chat_graph whose ``ainvoke`` appends to an in-memory per-thread
    checkpoint dict, and whose ``aget_state``/``aupdate_state`` read/write
    that same dict — close enough to LangGraph's real contract for these
    handler-level tests."""
    checkpoints = {}

    async def fake_ainvoke(inputs, config):
        thread_id = config["configurable"]["thread_id"]
        msgs = checkpoints.setdefault(thread_id, [])
        msgs.append({"role": "user", "content": inputs["user_message"], "metadata": {}})
        assistant_msg = {
            "role": "assistant",
            "content": f"Reply to: {inputs['user_message']}",
            "tool_calls": [],
            "tool_results": [],
            "metadata": {"message_id": f"msg-{len(msgs)}", "model_id": "test-model"},
        }
        msgs.append(assistant_msg)
        return {"messages": msgs, "metadata": {}, "kb_results": []}

    async def fake_aget_state(cfg):
        thread_id = cfg["configurable"]["thread_id"]
        if thread_id not in checkpoints:
            return _FakeCheckpointState(None)
        return _FakeCheckpointState({"messages": checkpoints[thread_id]})

    async def fake_aupdate_state(cfg, values):
        checkpoints[cfg["configurable"]["thread_id"]] = values.get("messages", [])

    graph = MagicMock()
    graph.ainvoke = AsyncMock(side_effect=fake_ainvoke)
    graph.aget_state = AsyncMock(side_effect=fake_aget_state)
    graph.aupdate_state = AsyncMock(side_effect=fake_aupdate_state)
    graph._checkpoints = checkpoints  # exposed for test assertions/setup
    return graph


def _make_handler(session, config=None, conversation_store=None, chat_graph=None):
    session_manager = MagicMock()
    session_manager.get_session = AsyncMock(return_value=session)
    return WebSocketChatHandler(
        session_manager=session_manager,
        config=config or _make_config(),
        chat_graph=chat_graph or _make_chat_graph(),
        conversation_store=conversation_store,
    )


def _new_ws():
    ws = MagicMock()
    ws.send_json = AsyncMock()
    return ws


def _sent(ws):
    return [c.args[0] for c in ws.send_json.call_args_list]


async def _make_store():
    from autolangchat.db import SQLiteConversationStore

    store = SQLiteConversationStore(db_path=":memory:")
    await store.open()
    return store


# ---------------------------------------------------------------------------
# Dispatch: all six conversation_* message types
# ---------------------------------------------------------------------------


async def test_message_loop_dispatches_all_conversation_message_types():
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)

        for message_type, extra_data in [
            ("conversation_list", {}),
            ("conversation_new", {}),
            ("conversation_load", {"conversation_id": "nonexistent"}),
            ("conversation_delete", {"conversation_id": "nonexistent"}),
            ("conversation_delete_bulk", {"conversation_ids": ["nonexistent"]}),
            ("conversation_delete_all", {}),
            ("conversation_rename", {"conversation_id": "nonexistent", "title": "x"}),
        ]:
            ws = _new_ws()
            ws.receive_text = AsyncMock(side_effect=[__import__("json").dumps({"type": message_type, **extra_data})])
            # Drive dispatch directly via the handler method the message
            # loop would call, to avoid needing a real WebSocketDisconnect
            # after one message.
            handler_method = getattr(handler, f"_handle_{message_type}")
            await handler_method(ws, extra_data)
            if message_type == "conversation_new":
                # By design, conversation_new sends no acknowledgment — it
                # just clears session state for the next lazy-create.
                assert ws.send_json.await_count == 0, "conversation_new should not send anything"
            else:
                assert ws.send_json.await_count >= 1, f"{message_type} sent nothing"
    finally:
        await store.close()


async def test_conversation_list_rejects_limit_above_upper_bound():
    """A client can't force a huge single-response allocation by requesting
    an enormous limit — the WebSocket path mirrors the REST endpoint's
    le=200 cap."""
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)

        ws = _new_ws()
        await handler._handle_conversation_list(ws, {"limit": 100000})

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_error"
        assert sent[0]["code"] == "invalid_conversation_request"
    finally:
        await store.close()


async def test_conversation_list_accepts_limit_at_upper_bound():
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)

        ws = _new_ws()
        await handler._handle_conversation_list(ws, {"limit": 200})

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_list"
    finally:
        await store.close()


async def test_conversation_list_projects_to_documented_shape_only():
    """conversation_list must send only {id, title, updated_at,
    message_count} per item — not the full store row (user_id, created_at,
    metadata, is_archived), matching docs/wiki/websocket-client.md and
    avoiding an unnecessary payload/surface-area leak."""
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)
        await store.create_conversation("conv-1", "alice", title="Hello", metadata={"secret": "internal"})

        ws = _new_ws()
        await handler._handle_conversation_list(ws, {})

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_list"
        item = sent[0]["conversations"][0]
        assert set(item.keys()) == {"id", "title", "updated_at", "message_count"}
        assert item["id"] == "conv-1"
        assert item["title"] == "Hello"
    finally:
        await store.close()


async def test_conversation_list_sends_empty_string_for_untitled_conversation():
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)
        await store.create_conversation("conv-1", "alice")

        ws = _new_ws()
        await handler._handle_conversation_list(ws, {})

        assert _sent(ws)[0]["conversations"][0]["title"] == ""
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# conversation_load reads history via aget_state only
# ---------------------------------------------------------------------------


async def test_conversation_load_reads_history_via_aget_state_only():
    store = await _make_store()
    try:
        session = _make_session()
        chat_graph = _make_chat_graph()
        handler = _make_handler(session, conversation_store=store, chat_graph=chat_graph)

        await store.create_conversation("conv-1", "alice", title="Existing")
        chat_graph._checkpoints["conv-1"] = [
            {"role": "user", "content": "hi", "metadata": {"message_id": "m1"}},
            {"role": "assistant", "content": "hello", "metadata": {"message_id": "m2"}},
        ]

        # Spy on the store to prove it's never asked for message content.
        store.get_conversation = AsyncMock(wraps=store.get_conversation)

        ws = _new_ws()
        await handler._handle_conversation_load(ws, {"conversation_id": "conv-1"})

        chat_graph.aget_state.assert_awaited_once_with({"configurable": {"thread_id": "conv-1"}})
        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_loaded"
        assert [m["message_id"] for m in sent[0]["messages"]] == ["m1", "m2"]
        assert session.metadata["conversation_id"] == "conv-1"

        # get_conversation is legitimately called for the ownership check —
        # but nothing on the store is ever asked to return message content
        # (BaseConversationStore has no such method to call in the first
        # place; this assertion documents that aget_state is the sole
        # message source).
        store.get_conversation.assert_awaited_once_with("conv-1")
    finally:
        await store.close()


async def test_conversation_load_returns_empty_history_for_zero_message_conversation():
    """A brand-new conversation with no recorded turns (e.g. created via
    REST POST, never chatted in) has no checkpoint yet — that must load
    successfully with an empty message list, not conversation_history_unavailable."""
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)
        await store.create_conversation("conv-1", "alice")  # message_count defaults to 0

        ws = _new_ws()
        await handler._handle_conversation_load(ws, {"conversation_id": "conv-1"})

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_loaded"
        assert sent[0]["messages"] == []
        assert session.metadata.get("conversation_id") == "conv-1"
    finally:
        await store.close()


async def test_conversation_load_returns_history_unavailable_when_turns_recorded_but_checkpoint_missing():
    """A conversation with recorded turns (message_count > 0) whose
    checkpoint is missing really has lost history (e.g. MemorySaver after a
    restart) — that must return conversation_history_unavailable."""
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)
        await store.create_conversation("conv-1", "alice")
        await store.record_turn("conv-1")  # message_count becomes 1, no matching checkpoint exists

        ws = _new_ws()
        await handler._handle_conversation_load(ws, {"conversation_id": "conv-1"})

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_error"
        assert sent[0]["code"] == "conversation_history_unavailable"
        # Session must not be switched onto a conversation with lost history.
        assert session.metadata.get("conversation_id") is None
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# In-flight turns (XMGPLAT-11977): conversation_loaded.in_progress and
# closing messages for interrupted turns
# ---------------------------------------------------------------------------

_TOOL_CALL = {"id": "tc-1", "name": "get_job", "args": {"job_id": "j1"}}


def _tool_loop_messages():
    return [
        {"role": "user", "content": "validate job", "metadata": {"message_id": "m1"}},
        {"role": "assistant", "content": "", "tool_calls": [_TOOL_CALL], "metadata": {"message_id": "m2"}},
        {
            "role": "tool",
            "content": "[]",
            "tool_results": [{"tool_call_id": "tc-1", "name": "get_job", "result": "{}"}],
            "metadata": {},
        },
    ]


async def test_conversation_load_reports_not_in_progress_for_tool_terminated_history():
    """History ending on a tool message with nothing running must not be
    reported as pending — that was the stuck-banner bug."""
    store = await _make_store()
    try:
        session = _make_session()
        chat_graph = _make_chat_graph()
        handler = _make_handler(session, conversation_store=store, chat_graph=chat_graph)
        await store.create_conversation("conv-1", "alice")
        chat_graph._checkpoints["conv-1"] = _tool_loop_messages()

        ws = _new_ws()
        await handler._handle_conversation_load(ws, {"conversation_id": "conv-1"})

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_loaded"
        assert sent[0]["in_progress"] is False
    finally:
        await store.close()


async def test_conversation_load_closes_and_persists_a_stuck_tool_loop():
    """A conversation left mid tool-loop by a turn that never closed itself
    (process killed, or stuck before this fix) is repaired on load."""
    store = await _make_store()
    try:
        session = _make_session()
        chat_graph = _make_chat_graph()
        handler = _make_handler(session, conversation_store=store, chat_graph=chat_graph)
        await store.create_conversation("conv-1", "alice")
        chat_graph._checkpoints["conv-1"] = _tool_loop_messages()

        ws = _new_ws()
        await handler._handle_conversation_load(ws, {"conversation_id": "conv-1"})

        closing = _sent(ws)[0]["messages"][-1]
        assert closing["role"] == "assistant"
        assert closing["content"] == websocket_handler._INTERRUPTED_TURN_MESSAGE
        assert closing["metadata"]["interrupted"] is True
        assert chat_graph._checkpoints["conv-1"][-1]["metadata"]["interrupted"] is True

        # Loading again does not stack a second closing message.
        await handler._handle_conversation_load(_new_ws(), {"conversation_id": "conv-1"})
        assert [m["role"] for m in chat_graph._checkpoints["conv-1"]] == ["user", "assistant", "tool", "assistant"]
    finally:
        await store.close()


async def test_conversation_load_reports_in_progress_while_turn_is_running():
    store = await _make_store()
    try:
        session = _make_session()
        chat_graph = _make_chat_graph()
        handler = _make_handler(session, conversation_store=store, chat_graph=chat_graph)
        await store.create_conversation("conv-1", "alice")
        session.metadata["conversation_id"] = "conv-1"

        started = asyncio.Event()
        release = asyncio.Event()

        async def blocking_ainvoke(inputs, config):
            chat_graph._checkpoints["conv-1"] = _tool_loop_messages()
            started.set()
            await release.wait()
            msgs = chat_graph._checkpoints["conv-1"]
            msgs.append({"role": "assistant", "content": "done", "metadata": {"message_id": "m4"}})
            return {"messages": msgs, "metadata": {}, "kb_results": []}

        chat_graph.ainvoke = AsyncMock(side_effect=blocking_ainvoke)

        turn = asyncio.create_task(handler._handle_chat_message(_new_ws(), {"message": "validate job"}))
        await started.wait()

        # A second connection (e.g. the user reopened the tab) loads the conversation mid-turn.
        ws_during = _new_ws()
        await handler._handle_conversation_load(ws_during, {"conversation_id": "conv-1"})
        assert _sent(ws_during)[0]["in_progress"] is True
        assert handler.in_progress("conv-1")
        # A running turn's tool loop is not mistaken for a stuck one.
        assert _sent(ws_during)[0]["messages"][-1]["role"] == "tool"
        chat_graph.aupdate_state.assert_not_awaited()

        release.set()
        await turn

        ws_after = _new_ws()
        await handler._handle_conversation_load(ws_after, {"conversation_id": "conv-1"})
        assert _sent(ws_after)[0]["in_progress"] is False
        assert not handler.in_progress("conv-1")
        # A completed turn is left untouched — no closing message.
        assert chat_graph._checkpoints["conv-1"][-1]["content"] == "done"
    finally:
        await store.close()


def _tool_loop_messages_at(ts: datetime):
    msgs = _tool_loop_messages()
    msgs[-1]["metadata"] = {"timestamp": ts.isoformat()}
    return msgs


async def test_conversation_load_leaves_recent_tool_loop_from_another_worker_alone():
    """Checkpoints are shared across workers: a recent tool-loop tail may be a
    turn running elsewhere, so it is reported in progress and not overwritten."""
    store = await _make_store()
    try:
        session = _make_session()
        chat_graph = _make_chat_graph()
        handler = _make_handler(session, conversation_store=store, chat_graph=chat_graph)
        await store.create_conversation("conv-1", "alice")
        chat_graph._checkpoints["conv-1"] = _tool_loop_messages_at(datetime.now())

        ws = _new_ws()
        await handler._handle_conversation_load(ws, {"conversation_id": "conv-1"})

        assert _sent(ws)[0]["in_progress"] is True
        assert _sent(ws)[0]["messages"][-1]["role"] == "tool"
        chat_graph.aupdate_state.assert_not_awaited()
    finally:
        await store.close()


async def test_conversation_load_repairs_tool_loop_once_its_lease_expires():
    store = await _make_store()
    try:
        session = _make_session()
        chat_graph = _make_chat_graph()
        handler = _make_handler(session, conversation_store=store, chat_graph=chat_graph)
        await store.create_conversation("conv-1", "alice")
        stale = datetime.now() - TOOL_LOOP_LEASE - TOOL_LOOP_LEASE / 10
        chat_graph._checkpoints["conv-1"] = _tool_loop_messages_at(stale)

        ws = _new_ws()
        await handler._handle_conversation_load(ws, {"conversation_id": "conv-1"})

        assert _sent(ws)[0]["in_progress"] is False
        assert chat_graph._checkpoints["conv-1"][-1]["metadata"]["interrupted"] is True
    finally:
        await store.close()


async def test_turn_start_waits_for_an_in_progress_load_repair():
    """A turn starting while a load is repairing the checkpoint must read the
    repaired history, not race the write."""
    store = await _make_store()
    try:
        session = _make_session()
        chat_graph = _make_chat_graph()
        handler = _make_handler(session, conversation_store=store, chat_graph=chat_graph)
        await store.create_conversation("conv-1", "alice")
        session.metadata["conversation_id"] = "conv-1"
        chat_graph._checkpoints["conv-1"] = _tool_loop_messages()

        writing = asyncio.Event()
        release = asyncio.Event()
        real_aupdate = chat_graph.aupdate_state.side_effect

        async def slow_aupdate(cfg, values):
            writing.set()
            await release.wait()
            await real_aupdate(cfg, values)

        chat_graph.aupdate_state.side_effect = slow_aupdate
        seen_at_start = []
        real_ainvoke = chat_graph.ainvoke.side_effect

        async def recording_ainvoke(inputs, config):
            seen_at_start.append([m["role"] for m in chat_graph._checkpoints["conv-1"]])
            return await real_ainvoke(inputs, config)

        chat_graph.ainvoke.side_effect = recording_ainvoke

        load = asyncio.create_task(handler._handle_conversation_load(_new_ws(), {"conversation_id": "conv-1"}))
        await writing.wait()
        turn = asyncio.create_task(handler._handle_chat_message(_new_ws(), {"message": "again"}))
        for _ in range(20):
            await asyncio.sleep(0)
        chat_graph.ainvoke.assert_not_awaited()

        release.set()
        await load
        await turn

        assert seen_at_start == [["user", "assistant", "tool", "assistant"]]
    finally:
        await store.close()


async def test_failed_turn_mid_tool_loop_persists_closing_assistant_message():
    session = _make_session()
    session.metadata["conversation_id"] = "conv-1"
    chat_graph = _make_chat_graph()
    store = MagicMock()
    store.record_turn = AsyncMock()
    handler = _make_handler(session, chat_graph=chat_graph, conversation_store=store)

    async def failing_ainvoke(inputs, config):
        chat_graph._checkpoints["conv-1"] = _tool_loop_messages()
        raise RuntimeError("bedrock exploded")

    chat_graph.ainvoke = AsyncMock(side_effect=failing_ainvoke)

    ws = _new_ws()
    await handler._handle_chat_message(ws, {"message": "validate job"})

    assert any(m["type"] == "ai_response" and m.get("error") for m in _sent(ws))
    msgs = chat_graph._checkpoints["conv-1"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    closing = msgs[-1]
    assert closing["content"] == websocket_handler._INTERRUPTED_TURN_MESSAGE
    assert closing["tool_calls"] == []
    assert closing["metadata"]["interrupted"] is True
    assert closing["metadata"]["message_id"]
    assert not handler.in_progress("conv-1")


async def test_cancelled_turn_mid_tool_loop_persists_closing_message_and_reraises():
    session = _make_session()
    session.metadata["conversation_id"] = "conv-1"
    chat_graph = _make_chat_graph()
    handler = _make_handler(session, chat_graph=chat_graph, conversation_store=MagicMock())

    async def cancelled_ainvoke(inputs, config):
        chat_graph._checkpoints["conv-1"] = _tool_loop_messages()
        raise asyncio.CancelledError()

    chat_graph.ainvoke = AsyncMock(side_effect=cancelled_ainvoke)

    with pytest.raises(asyncio.CancelledError):
        await handler._handle_chat_message(_new_ws(), {"message": "validate job"})

    msgs = chat_graph._checkpoints["conv-1"]
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["metadata"]["interrupted"] is True
    assert not handler.in_progress("conv-1")


async def test_interrupted_turn_answers_dangling_tool_calls_before_closing():
    """A turn cut off after the model requested tools but before any result
    was saved gets a synthetic tool result, so the next Bedrock call isn't
    rejected for a toolUse without a toolResult."""
    session = _make_session()
    session.metadata["conversation_id"] = "conv-1"
    chat_graph = _make_chat_graph()
    handler = _make_handler(session, chat_graph=chat_graph, conversation_store=MagicMock())

    async def failing_ainvoke(inputs, config):
        chat_graph._checkpoints["conv-1"] = _tool_loop_messages()[:2]
        raise RuntimeError("tool crashed")

    chat_graph.ainvoke = AsyncMock(side_effect=failing_ainvoke)

    await handler._handle_chat_message(_new_ws(), {"message": "validate job"})

    msgs = chat_graph._checkpoints["conv-1"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[2]["tool_results"] == [
        {"tool_call_id": "tc-1", "name": "get_job", "error": websocket_handler._INTERRUPTED_TURN_MESSAGE}
    ]
    assert msgs[-1]["metadata"]["interrupted"] is True


async def test_failed_turn_not_ending_mid_tool_loop_leaves_history_untouched():
    session = _make_session()
    session.metadata["conversation_id"] = "conv-1"
    chat_graph = _make_chat_graph()
    handler = _make_handler(session, chat_graph=chat_graph, conversation_store=MagicMock())

    async def failing_ainvoke(inputs, config):
        chat_graph._checkpoints["conv-1"] = [{"role": "user", "content": "hi", "metadata": {}}]
        raise RuntimeError("model rejected request")

    chat_graph.ainvoke = AsyncMock(side_effect=failing_ainvoke)

    await handler._handle_chat_message(_new_ws(), {"message": "hi"})

    chat_graph.aupdate_state.assert_not_awaited()
    assert chat_graph._checkpoints["conv-1"] == [{"role": "user", "content": "hi", "metadata": {}}]


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


async def test_conversation_persistence_disabled_error_when_not_configured():
    session = _make_session()
    config = _make_config(conversation_persistence_enabled=False)
    handler = _make_handler(session, config=config, conversation_store=None)

    ws = _new_ws()
    await handler._handle_conversation_list(ws, {})

    sent = _sent(ws)
    assert sent[0]["type"] == "conversation_error"
    assert sent[0]["code"] == "conversation_persistence_disabled"


async def test_conversation_persistence_disabled_error_when_store_missing():
    session = _make_session()
    handler = _make_handler(session, conversation_store=None)  # config enabled, store not wired

    ws = _new_ws()
    await handler._handle_conversation_list(ws, {})

    sent = _sent(ws)
    assert sent[0]["code"] == "conversation_persistence_disabled"


async def test_unauthorized_conversation_error_for_anonymous_session():
    store = await _make_store()
    try:
        session = _make_session(user_id=None)
        handler = _make_handler(session, conversation_store=store)

        ws = _new_ws()
        await handler._handle_conversation_list(ws, {})

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_error"
        assert sent[0]["code"] == "unauthorized_conversation"
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# IDOR protection
# ---------------------------------------------------------------------------


async def test_conversation_load_of_other_users_conversation_is_not_found():
    store = await _make_store()
    try:
        await store.create_conversation("bobs-conv", "bob")
        session = _make_session(user_id="alice")
        handler = _make_handler(session, conversation_store=store)

        ws = _new_ws()
        await handler._handle_conversation_load(ws, {"conversation_id": "bobs-conv"})

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_error"
        assert sent[0]["code"] == "conversation_not_found"
        assert session.metadata.get("conversation_id") is None
    finally:
        await store.close()


async def test_conversation_delete_of_other_users_conversation_is_not_found():
    store = await _make_store()
    try:
        await store.create_conversation("bobs-conv", "bob")
        session = _make_session(user_id="alice")
        handler = _make_handler(session, conversation_store=store)

        ws = _new_ws()
        await handler._handle_conversation_delete(ws, {"conversation_id": "bobs-conv"})

        sent = _sent(ws)
        assert sent[0]["code"] == "conversation_not_found"
        # Bob's conversation must still exist.
        assert await store.get_conversation("bobs-conv") is not None
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# conversation_delete_bulk
# ---------------------------------------------------------------------------


async def test_conversation_delete_bulk_removes_only_owned_ids():
    store = await _make_store()
    try:
        await store.create_conversation("c1", "alice")
        await store.create_conversation("c2", "alice")
        await store.create_conversation("bobs-conv", "bob")
        session = _make_session(user_id="alice")
        handler = _make_handler(session, conversation_store=store)

        ws = _new_ws()
        await handler._handle_conversation_delete_bulk(
            ws, {"conversation_ids": ["c1", "c2", "bobs-conv", "never-existed"]}
        )

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_bulk_deleted"
        assert sorted(sent[0]["deleted_ids"]) == ["c1", "c2"]
        assert sent[0]["active_conversation_deleted"] is False
        assert await store.get_conversation("c1") is None
        assert await store.get_conversation("c2") is None
        # Bob's conversation must still exist.
        assert await store.get_conversation("bobs-conv") is not None
    finally:
        await store.close()


async def test_conversation_delete_bulk_clears_active_conversation_when_included():
    store = await _make_store()
    try:
        await store.create_conversation("c1", "alice")
        session = _make_session(user_id="alice")
        session.metadata["conversation_id"] = "c1"
        session.metadata["feedback_meta"] = {"some": "state"}
        handler = _make_handler(session, conversation_store=store)

        ws = _new_ws()
        await handler._handle_conversation_delete_bulk(ws, {"conversation_ids": ["c1"]})

        sent = _sent(ws)
        assert sent[0]["deleted_ids"] == ["c1"]
        assert sent[0]["active_conversation_deleted"] is True
        assert session.metadata.get("conversation_id") is None
        assert "feedback_meta" not in session.metadata
    finally:
        await store.close()


async def test_conversation_delete_bulk_rejects_empty_or_malformed_ids():
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)

        for bad_payload in [{"conversation_ids": []}, {"conversation_ids": "c1"}, {"conversation_ids": [1, 2]}, {}]:
            ws = _new_ws()
            await handler._handle_conversation_delete_bulk(ws, bad_payload)
            sent = _sent(ws)
            assert sent[0]["type"] == "conversation_error"
            assert sent[0]["code"] == "invalid_conversation_request"
    finally:
        await store.close()


async def test_conversation_delete_bulk_rejects_oversized_id_list():
    """Every id becomes a bound parameter in one statement — an unbounded
    list would blow past SQLite's variable limit."""
    store = await _make_store()
    try:
        await store.create_conversation("c1", "alice")
        session = _make_session(user_id="alice")
        handler = _make_handler(session, conversation_store=store)

        too_many = [f"conv-{i}" for i in range(websocket_handler._CONVERSATION_DELETE_BULK_MAX_IDS + 1)]
        ws = _new_ws()
        await handler._handle_conversation_delete_bulk(ws, {"conversation_ids": too_many})

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_error"
        assert sent[0]["code"] == "invalid_conversation_request"
        # Nothing may be deleted when the request is rejected.
        assert await store.get_conversation("c1") is not None
    finally:
        await store.close()


async def test_conversation_delete_bulk_deduplicates_ids():
    """Duplicates must collapse (they'd otherwise count double against the
    id cap) and be reported once in ``deleted_ids``."""
    store = await _make_store()
    try:
        await store.create_conversation("c1", "alice")
        session = _make_session(user_id="alice")
        handler = _make_handler(session, conversation_store=store)

        ws = _new_ws()
        await handler._handle_conversation_delete_bulk(ws, {"conversation_ids": ["c1", "c1", "c1"]})

        sent = _sent(ws)
        assert sent[0]["deleted_ids"] == ["c1"]
        assert await store.get_conversation("c1") is None
    finally:
        await store.close()


async def test_conversation_rename_of_other_users_conversation_is_not_found():
    store = await _make_store()
    try:
        await store.create_conversation("bobs-conv", "bob", title="Original")
        session = _make_session(user_id="alice")
        handler = _make_handler(session, conversation_store=store)

        ws = _new_ws()
        await handler._handle_conversation_rename(ws, {"conversation_id": "bobs-conv", "title": "Hijacked"})

        sent = _sent(ws)
        assert sent[0]["code"] == "conversation_not_found"
        row = await store.get_conversation("bobs-conv")
        assert row["title"] == "Original"
    finally:
        await store.close()


@pytest.mark.parametrize("payload", [{"title": None}, {"title": ""}, {"title": "   "}, {"title": 42}, {}])
async def test_conversation_rename_rejects_blank_or_null_title_and_keeps_previous(payload):
    store = await _make_store()
    try:
        await store.create_conversation("conv-1", "alice", title="Original")
        handler = _make_handler(_make_session(), conversation_store=store)

        ws = _new_ws()
        await handler._handle_conversation_rename(ws, {"conversation_id": "conv-1", **payload})

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_error"
        assert sent[0]["code"] == "invalid_conversation_request"
        assert (await store.get_conversation("conv-1"))["title"] == "Original"
    finally:
        await store.close()


async def test_conversation_rename_trims_and_persists_title():
    store = await _make_store()
    try:
        await store.create_conversation("conv-1", "alice", title="Original")
        handler = _make_handler(_make_session(), conversation_store=store)

        ws = _new_ws()
        await handler._handle_conversation_rename(ws, {"conversation_id": "conv-1", "title": "  Job 42  "})

        sent = _sent(ws)
        assert sent[0]["type"] == "conversation_renamed"
        assert sent[0]["title"] == "Job 42"
        assert (await store.get_conversation("conv-1"))["title"] == "Job 42"
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Lazy conversation creation + record_turn
# ---------------------------------------------------------------------------


async def test_chat_message_lazily_creates_conversation_once():
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)
        handler._build_title_llm_client = lambda: None  # keep titling on the fallback path

        ws1 = _new_ws()
        await handler._handle_chat_message(ws1, {"message": "hello"})
        sent1 = _sent(ws1)
        types1 = [m["type"] for m in sent1]
        assert "conversation_created" in types1
        conv_id = next(m["conversation_id"] for m in sent1 if m["type"] == "conversation_created")

        row = await store.get_conversation(conv_id)
        assert row["message_count"] == 1

        # Let the fire-and-forget titling task settle before the second turn.
        for t in list(handler._background_tasks):
            await t

        ws2 = _new_ws()
        await handler._handle_chat_message(ws2, {"message": "second message"})
        sent2 = _sent(ws2)
        types2 = [m["type"] for m in sent2]
        assert "conversation_created" not in types2
        ai_response = next(m for m in sent2 if m["type"] == "ai_response")
        assert ai_response["conversation_id"] == conv_id

        row = await store.get_conversation(conv_id)
        assert row["message_count"] == 2
    finally:
        await store.close()


async def test_chat_message_degrades_to_session_id_when_create_conversation_fails():
    """A transient conversation-store failure (DB down, connection dropped,
    etc.) must not block chat delivery — conversation persistence is
    optional, so the turn should fall back to session_id as thread_id
    rather than surfacing an error ai_response."""
    session = _make_session()
    chat_graph = _make_chat_graph()

    failing_store = MagicMock()
    failing_store.create_conversation = AsyncMock(side_effect=RuntimeError("db unavailable"))

    handler = _make_handler(session, chat_graph=chat_graph, conversation_store=failing_store)

    ws = _new_ws()
    await handler._handle_chat_message(ws, {"message": "hello"})

    sent = _sent(ws)
    types = [m["type"] for m in sent]
    assert "conversation_created" not in types
    ai_response = next(m for m in sent if m["type"] == "ai_response")
    assert ai_response["conversation_id"] is None
    assert "message" in ai_response and ai_response["message"]  # real assistant reply, not an error envelope
    assert not ai_response.get("error")

    # The graph turn actually ran against session_id, not a dropped/never-created conversation_id.
    chat_graph.ainvoke.assert_awaited_once()
    _, kwargs = chat_graph.ainvoke.await_args
    assert kwargs["config"]["configurable"]["thread_id"] == session.session_id

    # No stale conversation_id was recorded, so the next turn retries lazy-creation.
    assert session.metadata.get("conversation_id") is None
    failing_store.record_turn.assert_not_called()


async def test_anonymous_session_never_gets_persisted_conversation():
    store = await _make_store()
    try:
        session = _make_session(user_id=None)
        handler = _make_handler(session, conversation_store=store)

        ws = _new_ws()
        await handler._handle_chat_message(ws, {"message": "hello"})

        sent = _sent(ws)
        assert "conversation_created" not in [m["type"] for m in sent]
        ai_response = next(m for m in sent if m["type"] == "ai_response")
        assert ai_response["conversation_id"] is None
        assert await store.get_conversation_count("anonymous") == 0
        assert session.metadata.get("conversation_id") is None
    finally:
        await store.close()


async def test_chat_message_legacy_behavior_when_persistence_disabled():
    config = _make_config(conversation_persistence_enabled=False)
    session = _make_session()
    chat_graph = _make_chat_graph()
    handler = _make_handler(session, config=config, chat_graph=chat_graph, conversation_store=None)

    ws = _new_ws()
    await handler._handle_chat_message(ws, {"message": "hello"})

    # thread_id falls back to session_id exactly as before this feature existed.
    chat_graph.ainvoke.assert_awaited_once()
    _, kwargs = chat_graph.ainvoke.await_args
    assert kwargs["config"]["configurable"]["thread_id"] == session.session_id

    sent = _sent(ws)
    ai_response = next(m for m in sent if m["type"] == "ai_response")
    assert ai_response["conversation_id"] is None


# ---------------------------------------------------------------------------
# Auto-titling broadcast
# ---------------------------------------------------------------------------


async def test_conversation_titled_broadcast_after_background_task_completes():
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)

        class _FakeAI:
            content = "A Generated Title"

        class _FakeLLM:
            async def ainvoke(self, messages):
                return _FakeAI()

        handler._build_title_llm_client = lambda: _FakeLLM()

        ws = _new_ws()
        await handler._handle_chat_message(ws, {"message": "hello"})

        # Titling hasn't necessarily run yet — it's fire-and-forget.
        for t in list(handler._background_tasks):
            await t

        sent = _sent(ws)
        titled = [m for m in sent if m["type"] == "conversation_titled"]
        assert len(titled) == 1
        conv_id = next(m["conversation_id"] for m in sent if m["type"] == "conversation_created")
        assert titled[0]["conversation_id"] == conv_id
        assert titled[0]["title"] == "A Generated Title"

        row = await store.get_conversation(conv_id)
        assert row["title"] == "A Generated Title"
        assert len(handler._background_tasks) == 0  # done-callback cleaned up
    finally:
        await store.close()


async def test_conversation_titled_not_broadcast_on_second_turn():
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)
        handler._build_title_llm_client = lambda: None

        ws1 = _new_ws()
        await handler._handle_chat_message(ws1, {"message": "hello"})
        for t in list(handler._background_tasks):
            await t

        ws2 = _new_ws()
        await handler._handle_chat_message(ws2, {"message": "again"})
        for t in list(handler._background_tasks):
            await t

        sent2 = _sent(ws2)
        assert "conversation_titled" not in [m["type"] for m in sent2]
    finally:
        await store.close()


async def test_titling_failure_is_swallowed_and_does_not_affect_chat_delivery():
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)

        class _FailingLLM:
            async def ainvoke(self, messages):
                raise RuntimeError("boom")

        handler._build_title_llm_client = lambda: _FailingLLM()

        ws = _new_ws()
        await handler._handle_chat_message(ws, {"message": "hello"})
        for t in list(handler._background_tasks):
            await t

        sent = _sent(ws)
        # ai_response still delivered even though titling failed internally
        # (the titler's own fallback still succeeds, so a title IS applied —
        # only a raise from generate_conversation_title itself, or from the
        # store/send afterward, would be swallowed here).
        assert any(m["type"] == "ai_response" for m in sent)
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Regression: _active_thread_id must agree with _handle_chat_message's
# use_conversation_persistence gate, not just the raw config flag.
# ---------------------------------------------------------------------------


async def test_history_falls_back_to_session_id_when_persistence_enabled_but_store_not_wired():
    """conversation_persistence_enabled=True with no store configured must
    not break legacy history for the session's own chat turns — chat itself
    falls back to session_id as thread_id in this case, so history/clear/
    feedback must follow the same thread_id, not report "no active
    conversation"."""
    session = _make_session()
    chat_graph = _make_chat_graph()
    handler = _make_handler(session, chat_graph=chat_graph, conversation_store=None)

    ws_chat = _new_ws()
    await handler._handle_chat_message(ws_chat, {"message": "hello"})
    sent_chat = _sent(ws_chat)
    assert "conversation_created" not in [m["type"] for m in sent_chat]

    ws_history = _new_ws()
    await handler._handle_history_request(ws_history, {})
    sent_history = _sent(ws_history)
    assert sent_history[0]["type"] == "history"
    assert len(sent_history[0]["messages"]) == 2  # user + assistant


async def test_history_falls_back_to_session_id_for_anonymous_session_when_persistence_enabled():
    store = await _make_store()
    try:
        session = _make_session(user_id=None)
        chat_graph = _make_chat_graph()
        handler = _make_handler(session, chat_graph=chat_graph, conversation_store=store)

        ws_chat = _new_ws()
        await handler._handle_chat_message(ws_chat, {"message": "hello"})

        ws_history = _new_ws()
        await handler._handle_history_request(ws_history, {})
        sent_history = _sent(ws_history)
        assert sent_history[0]["type"] == "history"
        assert len(sent_history[0]["messages"]) == 2
    finally:
        await store.close()


async def test_clear_history_falls_back_to_session_id_when_store_not_wired():
    session = _make_session()
    chat_graph = _make_chat_graph()
    handler = _make_handler(session, chat_graph=chat_graph, conversation_store=None)

    ws_chat = _new_ws()
    await handler._handle_chat_message(ws_chat, {"message": "hello"})

    ws_clear = _new_ws()
    await handler._handle_clear_history(ws_clear, {})
    sent_clear = _sent(ws_clear)
    assert sent_clear[0]["type"] == "history_cleared"
    # The checkpoint under session_id (not "no active conversation") was cleared.
    assert chat_graph._checkpoints[session.session_id] == []


async def test_active_thread_id_matches_chat_message_gate_directly():
    """Direct unit check that _active_thread_id and the chat-message gate
    never diverge, across all four (persistence, store, user_id) combinations."""
    chat_graph = _make_chat_graph()

    for persistence_enabled, has_store, user_id in [
        (False, True, "alice"),
        (True, False, "alice"),
        (True, True, None),
        (True, True, "alice"),
    ]:
        session = _make_session(user_id=user_id)
        config = _make_config(conversation_persistence_enabled=persistence_enabled)
        store = MagicMock() if has_store else None
        handler = _make_handler(session, config=config, chat_graph=chat_graph, conversation_store=store)

        expect_conversation_thread_id = persistence_enabled and has_store and bool(user_id)
        if expect_conversation_thread_id:
            session.metadata["conversation_id"] = "some-conv-id"
            assert handler._active_thread_id(session) == "some-conv-id"
        else:
            assert handler._active_thread_id(session) == session.session_id


# ---------------------------------------------------------------------------
# XMGPLAT-11842: reconnect with an in-use preferred_session_id through the
# real handle_connection entry point (not just ChatSessionManager directly)
# ---------------------------------------------------------------------------


def _live_ws():
    """A WebSocket double that stays "open": receive_text() blocks forever
    (an unset asyncio.Event), matching a connection whose message loop is
    actually running, rather than one that disconnects immediately.

    Sets ``reached_message_loop`` the moment ``receive_text()`` is first
    called, so callers can deterministically wait for ``handle_connection``
    to have finished session setup instead of a fixed sleep (which can be
    too short on a loaded CI worker and race the following assertions).
    """
    reached_message_loop = asyncio.Event()

    async def _never_resolves():
        reached_message_loop.set()
        await asyncio.Event().wait()

    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.headers = {}
    ws.cookies = {}
    ws.receive_text = AsyncMock(side_effect=_never_resolves)
    ws.reached_message_loop = reached_message_loop
    return ws


def _sso_user_info(user_id):
    """A verified-SSO ``_validate_sso_token_and_extract_user`` return value."""
    from autolangchat.auth_handler import AuthType, Credentials

    return {
        "user_id": user_id,
        "credentials": Credentials(auth_type=AuthType.SSO),
        "auth_handler": MagicMock(),
        "metadata": {},
        "display_name": user_id,
    }


async def test_handle_connection_reconnect_while_open_evicts_stale_connection():
    """A second connection presenting the same session_id as an already-live
    connection must evict the stale one (closing its socket) instead of
    leaving the old connection's mapping dangling to later pop the
    replacement session (XMGPLAT-11842)."""
    cfg = _make_config(max_sessions=100, session_timeout=3600, sso_session_secret=None)
    session_manager = ChatSessionManager(cfg)
    handler = WebSocketChatHandler(session_manager=session_manager, config=cfg, chat_graph=_make_chat_graph())

    preferred = str(uuid.uuid4())
    old_ws = _live_ws()
    new_ws = _live_ws()

    old_task = asyncio.create_task(handler.handle_connection(old_ws, user_id="alice", preferred_session_id=preferred))
    await asyncio.wait_for(old_ws.reached_message_loop.wait(), timeout=2)

    session = await session_manager.get_session_by_id(preferred)
    assert session is not None and session.websocket is old_ws

    new_task = asyncio.create_task(handler.handle_connection(new_ws, user_id="alice", preferred_session_id=preferred))
    await asyncio.wait_for(new_ws.reached_message_loop.wait(), timeout=2)

    try:
        session = await session_manager.get_session_by_id(preferred)
        assert session is not None and session.websocket is new_ws

        old_ws.close.assert_awaited_once()

        new_established = [m for m in _sent(new_ws) if m["type"] == "connection_established"]
        assert new_established and new_established[0]["session_id"] == preferred
    finally:
        for task in (old_task, new_task):
            task.cancel()
        for task in (old_task, new_task):
            try:
                await task
            except asyncio.CancelledError:
                pass


async def test_handle_connection_reconnect_unauthenticated_claim_cannot_take_over_sso_session():
    """XMGPLAT-11842 security regression: the ownership check must compare
    *verified* identities, not raw client-supplied ones. The ``user_id``
    query parameter is fully client-controlled (see
    ``autolangchat/plugin.py``'s ``websocket_chat`` route), so an attacker
    who merely knows (or guesses) a victim's user_id and active session_id
    must not be able to evict that live, SSO-authenticated connection by
    claiming the same ``user_id`` without presenting a valid SSO session."""
    cfg = _make_config(max_sessions=100, session_timeout=3600, sso_session_secret="s3cr3t-session-secret")
    session_manager = ChatSessionManager(cfg)
    handler = WebSocketChatHandler(
        session_manager=session_manager,
        config=cfg,
        chat_graph=_make_chat_graph(),
        sso_session_store=MagicMock(),
    )
    handler._validate_sso_token_and_extract_user = AsyncMock(return_value=_sso_user_info("alice"))

    preferred = str(uuid.uuid4())
    old_ws = _live_ws()
    old_ws.cookies = {"sso_session_token": "valid-token"}
    new_ws = _live_ws()
    # No SSO cookie on the new connection: an unauthenticated claim.
    new_ws.cookies = {}

    old_task = asyncio.create_task(handler.handle_connection(old_ws, preferred_session_id=preferred))
    await asyncio.wait_for(old_ws.reached_message_loop.wait(), timeout=2)

    try:
        await asyncio.wait_for(
            handler.handle_connection(new_ws, user_id="alice", preferred_session_id=preferred), timeout=2
        )

        session = await session_manager.get_session_by_id(preferred)
        assert session is not None and session.websocket is old_ws and session.user_id == "alice"

        errors = [m for m in _sent(new_ws) if m["type"] == "error"]
        assert errors and "different user" in errors[0]["message"]
        new_ws.close.assert_awaited_once()
        assert new_ws.close.await_args.kwargs.get("code") == 1008

        old_ws.close.assert_not_called()
    finally:
        old_task.cancel()
        try:
            await old_task
        except asyncio.CancelledError:
            pass
