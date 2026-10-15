"""XMGPLAT-11999: the message loop keeps reading while a chat turn runs.

A turn used to run inline in ``_message_loop``, so nothing else on the socket
was read until it finished: pings went unanswered (the client's heartbeat
closed the socket mid-turn) and ``conversation_load`` waited for the turn.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from starlette.websockets import WebSocketDisconnect

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

    async def ainvoke(self, inputs, config):
        self.running.set()
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
        return None


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


def _make_handler(graph):
    session = SimpleNamespace(session_id="s-1", user_id=None, credentials=None, auth_handler=None, metadata={})
    session_manager = MagicMock()
    session_manager.get_session = AsyncMock(return_value=session)
    return WebSocketChatHandler(session_manager=session_manager, config=_make_config(), chat_graph=graph)


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


async def test_cancelling_the_loop_cancels_the_turn():
    graph = _GatedGraph()
    ws = _QueueSocket()
    loop = asyncio.create_task(_make_handler(graph)._message_loop(ws))

    await ws.inbox.put({"type": "chat", "message": "analyse"})
    await asyncio.wait_for(graph.running.wait(), 2)
    loop.cancel()

    await _until(lambda: graph.cancelled)
    assert not graph.finished
