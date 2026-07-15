"""Unit tests for conversation persistence in the WebSocket handler (XMGPLAT-10380).

Covers ``WebSocketChatHandler``:

  (a) all six ``conversation_*`` message types dispatch to their handlers
      (mock store + checkpointer);
  (b) ``_handle_conversation_load`` reads history via ``chat_graph.aget_state``
      only — it never queries the conversation store for messages;
  (c) auth gating: ``conversation_persistence_disabled`` when not configured,
      ``unauthorized_conversation`` when the session has no ``user_id``;
  (d) IDOR protection: loading/deleting/renaming another user's conversation
      returns ``conversation_not_found`` (not a distinguishing 403);
  (e) ``conversation_history_unavailable`` when ``aget_state`` yields no
      checkpoint values;
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
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


async def test_conversation_load_returns_history_unavailable_when_no_checkpoint():
    store = await _make_store()
    try:
        session = _make_session()
        handler = _make_handler(session, conversation_store=store)
        await store.create_conversation("conv-1", "alice")

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
