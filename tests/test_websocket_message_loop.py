"""XMGPLAT-11999: the message loop keeps reading while a chat turn runs.

A turn used to run inline in ``_message_loop``, so nothing else on the socket
was read until it finished: pings went unanswered (the client's heartbeat
closed the socket mid-turn) and ``conversation_load`` waited for the turn.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.websockets import WebSocketDisconnect

from autolangchat.config import ChatConfig
from autolangchat.db import SQLiteConversationStore
from autolangchat.exceptions import WebSocketError
from autolangchat.session_manager import ChatSessionManager
from autolangchat.websocket_handler import WebSocketChatHandler


def _make_config():
    config = MagicMock()
    config.timeout = 30.0
    config.require_tool_auth = False
    config.include_auth_info_in_prompts = False
    config.feedback_allow_anonymous = False
    config.conversation_persistence_enabled = False
    config.enable_dynamic_overrides = False
    return config


class _GatedGraph:
    """A chat graph whose turn blocks until the test releases it."""

    def __init__(self):
        self.release = asyncio.Event()
        self.running = asyncio.Event()
        self.cancelled = False
        self.finished = False
        self.updates = []

    async def ainvoke(self, inputs, config):
        self.running.set()
        await config["configurable"]["on_progress"]({"type": "typing", "message": "Calling get_jobs... (1/1)"})
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.finished = True
        return {
            "messages": [{"role": "assistant", "content": "done", "metadata": {"message_id": "m-1"}}],
            "metadata": {},
            "kb_results": [],
        }

    async def aget_state(self, cfg):
        return SimpleNamespace(values={"messages": []})

    async def aupdate_state(self, cfg, values):
        self.updates.append(values)


class _QueueSocket:
    """A WebSocket fed frame by frame; ``None`` disconnects."""

    def __init__(self):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent = []
        self.send_json = AsyncMock(side_effect=self.sent.append)

    async def receive_text(self):
        frame = await self.inbox.get()
        if frame is None:
            raise WebSocketDisconnect()
        return json.dumps(frame)

    def types(self):
        return [m.get("type") for m in self.sent]


def _make_handler(graph, conversation_store=None):
    """``conversation_store`` switches on persistence for a signed-in user whose active conversation is ``c-1``."""
    persisted = conversation_store is not None
    session = SimpleNamespace(
        session_id="s-1",
        user_id="u-1" if persisted else None,
        credentials=None,
        auth_handler=None,
        metadata={"conversation_id": "c-1"} if persisted else {},
    )
    session_manager = MagicMock()
    session_manager.get_session = AsyncMock(return_value=session)
    config = _make_config()
    config.conversation_persistence_enabled = persisted
    return WebSocketChatHandler(
        session_manager=session_manager,
        config=config,
        chat_graph=graph,
        conversation_store=conversation_store,
    )


class _GatedConversationStore:
    """A conversation store whose ``record_turn`` blocks until the test releases it."""

    def __init__(self):
        self.release = asyncio.Event()
        self.recording = 0

    async def record_turn(self, conversation_id):
        self.recording += 1
        await self.release.wait()


async def _until(predicate, timeout=2.0):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


async def test_ping_is_answered_while_a_turn_runs():
    graph = _GatedGraph()
    ws = _QueueSocket()
    loop = asyncio.create_task(_make_handler(graph)._message_loop(ws))

    await ws.inbox.put({"type": "chat", "message": "analyse"})
    await asyncio.wait_for(graph.running.wait(), 2)
    await ws.inbox.put({"type": "ping"})
    await _until(lambda: "pong" in ws.types())

    assert "ai_response" not in ws.types()
    graph.release.set()
    await _until(lambda: "ai_response" in ws.types())
    await ws.inbox.put(None)
    await asyncio.wait_for(loop, 2)


async def test_second_chat_during_a_turn_is_refused():
    graph = _GatedGraph()
    ws = _QueueSocket()
    loop = asyncio.create_task(_make_handler(graph)._message_loop(ws))

    await ws.inbox.put({"type": "chat", "message": "first"})
    await asyncio.wait_for(graph.running.wait(), 2)
    await ws.inbox.put({"type": "chat", "message": "second"})
    await _until(lambda: "error" in ws.types())

    graph.release.set()
    await ws.inbox.put(None)
    await asyncio.wait_for(loop, 2)
    assert ws.types().count("ai_response") == 1


async def test_disconnect_mid_turn_lets_the_turn_finish():
    graph = _GatedGraph()
    ws = _QueueSocket()
    loop = asyncio.create_task(_make_handler(graph)._message_loop(ws))

    await ws.inbox.put({"type": "chat", "message": "analyse"})
    await asyncio.wait_for(graph.running.wait(), 2)
    await ws.inbox.put(None)
    await asyncio.sleep(0.05)

    assert not loop.done()
    graph.release.set()
    await asyncio.wait_for(loop, 2)
    assert graph.finished and not graph.cancelled


async def test_socket_closed_before_the_answer_still_records_the_turn():
    # The reported failure: the client's socket closed after the tool calls, so delivering the
    # answer raised. The turn must still be recorded so the reconnected client can reload it.
    graph = _GatedGraph()
    store = _GatedConversationStore()
    store.release.set()
    ws = _QueueSocket()
    handler = _make_handler(graph, conversation_store=store)
    loop = asyncio.create_task(handler._message_loop(ws))

    await ws.inbox.put({"type": "chat", "message": "analyse"})
    await asyncio.wait_for(graph.running.wait(), 2)
    await ws.inbox.put(None)
    ws.send_json.side_effect = RuntimeError('Cannot call "send" once a close message has been sent.')
    graph.release.set()
    await asyncio.wait_for(loop, 2)

    assert graph.finished
    assert store.recording == 1
    assert handler._total_errors == 0
    attempted = [call.args[0]["type"] for call in ws.send_json.await_args_list[len(ws.sent) :]]
    assert attempted == ["ai_response"]


async def test_cancelling_the_loop_cancels_the_turn():
    graph = _GatedGraph()
    ws = _QueueSocket()
    loop = asyncio.create_task(_make_handler(graph)._message_loop(ws))

    await ws.inbox.put({"type": "chat", "message": "analyse"})
    await asyncio.wait_for(graph.running.wait(), 2)
    loop.cancel()

    await _until(lambda: graph.cancelled)
    assert not graph.finished


async def test_clear_during_a_turn_is_refused_without_failing_the_turn():
    graph = _GatedGraph()
    ws = _QueueSocket()
    loop = asyncio.create_task(_make_handler(graph)._message_loop(ws))

    await ws.inbox.put({"type": "chat", "message": "analyse"})
    await asyncio.wait_for(graph.running.wait(), 2)
    await ws.inbox.put({"type": "clear"})
    await _until(lambda: "conversation_error" in ws.types())

    refusal = next(m for m in ws.sent if m["type"] == "conversation_error")
    assert refusal["code"] == "turn_in_progress"
    assert graph.updates == []
    assert "error" not in ws.types() and "history_cleared" not in ws.types()

    graph.release.set()
    await _until(lambda: "ai_response" in ws.types())
    await asyncio.sleep(0.05)
    await ws.inbox.put({"type": "clear"})
    await _until(lambda: "history_cleared" in ws.types())
    assert graph.updates == [{"messages": [], "metadata": {}}]

    await ws.inbox.put(None)
    await asyncio.wait_for(loop, 2)


async def test_follow_up_is_accepted_while_the_previous_turn_records_itself():
    graph = _GatedGraph()
    store = _GatedConversationStore()
    ws = _QueueSocket()
    loop = asyncio.create_task(_make_handler(graph, conversation_store=store)._message_loop(ws))

    graph.release.set()
    await ws.inbox.put({"type": "chat", "message": "first"})
    await _until(lambda: store.recording == 1)
    await ws.inbox.put({"type": "chat", "message": "second"})
    await _until(lambda: store.recording == 2)

    assert ws.types().count("ai_response") == 2
    assert "error" not in ws.types()

    store.release.set()
    await ws.inbox.put(None)
    await asyncio.wait_for(loop, 2)


async def test_progress_and_answer_frames_carry_the_turn_conversation():
    graph = _GatedGraph()
    store = _GatedConversationStore()
    store.release.set()
    graph.release.set()
    ws = _QueueSocket()
    loop = asyncio.create_task(_make_handler(graph, conversation_store=store)._message_loop(ws))

    await ws.inbox.put({"type": "chat", "message": "analyse"})
    await _until(lambda: "ai_response" in ws.types())
    await ws.inbox.put(None)
    await asyncio.wait_for(loop, 2)

    progress = [m for m in ws.sent if m["type"] == "typing" and m["message"].startswith("Calling")]
    assert [m["conversation_id"] for m in progress] == ["c-1"]
    answer = next(m for m in ws.sent if m["type"] == "ai_response")
    assert answer["conversation_id"] == "c-1"


async def test_a_failed_turn_reports_its_conversation():
    graph = _GatedGraph()
    graph.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    store = _GatedConversationStore()
    ws = _QueueSocket()
    loop = asyncio.create_task(_make_handler(graph, conversation_store=store)._message_loop(ws))

    await ws.inbox.put({"type": "chat", "message": "analyse"})
    await _until(lambda: "ai_response" in ws.types())
    await ws.inbox.put(None)
    await asyncio.wait_for(loop, 2)

    failure = next(m for m in ws.sent if m["type"] == "ai_response")
    assert failure["error"] is True
    assert failure["conversation_id"] == "c-1"


class _CheckpointingGatedGraph(_GatedGraph):
    """Checkpoints each turn on its own thread; with ``archive``, stores a tool result in the turn's artifact scope."""

    def __init__(self, archive=False):
        super().__init__()
        self.archive = archive
        self.checkpoints = {}

    async def ainvoke(self, inputs, config):
        configurable = config["configurable"]
        self.running.set()
        await self.release.wait()
        if self.archive:
            configurable["artifact_store"].put(configurable["artifact_scope"], "oversized tool result", {})
        thread_id = configurable["thread_id"]
        answer = {"role": "assistant", "content": "done", "metadata": {"message_id": f"{thread_id}-answer"}}
        messages = self.checkpoints.setdefault(thread_id, [])
        messages += [{"role": "user", "content": inputs["user_message"], "metadata": {}}, answer]
        return {"messages": list(messages), "metadata": {}, "kb_results": []}

    async def aget_state(self, cfg):
        return SimpleNamespace(values={"messages": self.checkpoints.get(cfg["configurable"]["thread_id"], [])})


# How a tab leaves conversation "a", and where that leaves it.
_SWITCHES = {
    "load": ({"type": "conversation_load", "conversation_id": "b"}, "b"),
    "new": ({"type": "conversation_new"}, None),
    "delete": ({"type": "conversation_delete", "conversation_id": "a"}, None),
}


async def _switch_mid_turn(graph, switch):
    """Run a turn on conversation "a" and leave it as ``switch`` says while the turn runs.

    Returns the frames sent, the tab's session and the conversation store once the loop has ended.
    """
    config = ChatConfig().model_copy(update={"conversation_persistence_enabled": True, "require_tool_auth": False})
    manager = ChatSessionManager(config)
    store = SQLiteConversationStore(db_path=":memory:")
    await store.open()
    for conversation_id in ("a", "b"):
        await store.create_conversation(conversation_id, "alice")
    handler = WebSocketChatHandler(session_manager=manager, config=config, chat_graph=graph, conversation_store=store)
    ws = _QueueSocket()
    await manager.create_session(ws, user_id="alice", is_authenticated=True)
    session = await manager.get_session(ws)
    session.mark_verified()
    loop = asyncio.create_task(handler._message_loop(ws))
    try:
        await ws.inbox.put({"type": "conversation_load", "conversation_id": "a"})
        await ws.inbox.put({"type": "chat", "message": "analyse"})
        await asyncio.wait_for(graph.running.wait(), 2)

        frame, after = _SWITCHES[switch]
        await ws.inbox.put(frame)
        handled = {
            "load": lambda: ws.types().count("conversation_loaded") == 2,
            "new": lambda: session.metadata["conversation_id"] is None,
            "delete": lambda: "conversation_deleted" in ws.types(),
        }[switch]
        await _until(handled)
        assert session.metadata["conversation_id"] == after

        graph.release.set()
        await _until(lambda: "ai_response" in ws.types())
        await ws.inbox.put(None)
        await asyncio.wait_for(loop, 2)
        return ws.sent, session, store
    finally:
        if not loop.done():
            loop.cancel()
        await handler.shutdown()


@pytest.mark.parametrize("switch", sorted(_SWITCHES))
async def test_leaving_the_conversation_mid_turn_keeps_the_answer_on_the_turns_conversation(switch):
    graph = _CheckpointingGatedGraph()
    sent, session, store = await _switch_mid_turn(graph, switch)
    try:
        answer = next(m for m in sent if m["type"] == "ai_response")
        assert answer["conversation_id"] == "a"
        assert not answer.get("error")
        # The turn never moves the tab back, nor writes into the conversation it moved to.
        assert session.metadata["conversation_id"] == _SWITCHES[switch][1]
        assert list(graph.checkpoints) == ["a"]
        assert (await store.get_conversation("b"))["message_count"] == 0
        recorded = await store.get_conversation("a")
        assert recorded is None if switch == "delete" else recorded["message_count"] == 1
    finally:
        await store.close()


@pytest.mark.xfail(
    strict=True,
    reason="XMGPLAT-11971: leaving the conversation revokes the running turn's artifact scope, so the turn fails",
)
@pytest.mark.parametrize("switch", ["load", "new"])
async def test_leaving_the_conversation_mid_turn_does_not_fail_a_turn_that_archives_a_tool_result(switch):
    sent, _, store = await _switch_mid_turn(_CheckpointingGatedGraph(archive=True), switch)
    try:
        answer = next(m for m in sent if m["type"] == "ai_response")
        assert not answer.get("error"), answer["message"]
    finally:
        await store.close()


async def test_sends_to_one_socket_wait_for_each_other_and_a_timed_out_send_lets_the_next_one_go():
    handler = _make_handler(_GatedGraph())
    ws, other = _QueueSocket(), _QueueSocket()
    stall = asyncio.Event()
    entered = []

    async def send_json(message):
        entered.append(message["type"])
        if message["type"] == "stuck":
            await stall.wait()
        ws.sent.append(message)

    ws.send_json = AsyncMock(side_effect=send_json)

    stuck = asyncio.create_task(handler._send_bounded(ws, {"type": "stuck"}, 0.1))
    await _until(lambda: entered == ["stuck"])
    queued = asyncio.create_task(handler._send_message(ws, {"type": "queued"}))
    await handler._send_message(other, {"type": "elsewhere"})
    await asyncio.sleep(0.02)
    assert entered == ["stuck"]
    assert other.types() == ["elsewhere"]

    with pytest.raises(WebSocketError):
        await stuck
    await asyncio.wait_for(queued, 1)
    assert ws.types() == ["queued"]
