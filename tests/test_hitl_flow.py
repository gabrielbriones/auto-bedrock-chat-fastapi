"""Phase 4 — HITL (Human-in-the-Loop) flow tests.

Tests cover:
1. interrupt fires and state contains __interrupt__ payload
2. Resume with proceed=True → report generated
3. Resume with proceed=False → batch_cancelled state
4. WebSocket handler: batch_start sends confirmation_required
5. WebSocket handler: confirm (proceed=True) sends batch_finished with report
6. WebSocket handler: confirm (proceed=False) sends batch_cancelled
7. WebSocket handler: batch_start with no batch_graph configured sends error
8. Regression: chat path is unaffected by batch graph addition
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.graph.batch_graph import build_batch_graph

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


class _FakeChatConfig:
    model_id = "test-model"
    fallback_model = None
    aws_region = "us-east-1"
    temperature = 0.7
    max_tokens = 1024
    top_p = 0.9
    checkpoint_postgres_url = None
    checkpoint_pool_size = 5
    # Chat graph thresholds
    enable_ai_summarization = False
    single_msg_length_threshold = 500_000
    single_msg_truncation_target = 425_000
    history_total_length_threshold = 650_000
    history_msg_length_threshold = 100_000
    history_msg_truncation_target = 85_000
    max_truncation_recursion = 3


def _make_batch_graph():
    graph = build_batch_graph(_FakeChatConfig())
    return graph


def _empty_state(**overrides):
    base = {
        "job_ids": [],
        "completed_jobs": [],
        "job_results": {},
        "failed_jobs": [],
        "report": None,
        "cancelled": False,
        "metadata": {},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 4.3 HITL interrupt / resume via batch graph
# ---------------------------------------------------------------------------


class TestHITLInterrupt:
    @pytest.mark.asyncio
    async def test_interrupt_fires_with_correct_payload(self):
        """confirm node must emit __interrupt__ with type=confirmation_required."""
        graph = _make_batch_graph()
        tid = f"hitl-{uuid.uuid4().hex[:8]}"
        cfg = {"configurable": {"thread_id": tid, "chat_config": _FakeChatConfig()}}

        state = await graph.ainvoke(_empty_state(job_ids=["j1"]), config=cfg)

        assert "__interrupt__" in state, "graph should pause at request_confirmation"
        intr = state["__interrupt__"]
        # Can be a list or a single Interrupt object
        payload = intr[0] if isinstance(intr, list) else intr
        value = payload.value if hasattr(payload, "value") else payload
        assert value.get("type") == "confirmation_required"
        assert "message" in value

    @pytest.mark.asyncio
    async def test_resume_true_generates_report(self):
        from langgraph.types import Command

        graph = _make_batch_graph()
        tid = f"hitl-resume-{uuid.uuid4().hex[:8]}"
        cfg = {"configurable": {"thread_id": tid, "chat_config": _FakeChatConfig()}}

        await graph.ainvoke(_empty_state(job_ids=["j1"]), config=cfg)
        mock_llm = MagicMock()
        ai_msg = MagicMock()
        ai_msg.content = "Mock report"
        mock_llm.ainvoke = AsyncMock(return_value=ai_msg)
        with patch("autolangchat.graph.batch_graph.ChatBedrockConverse", return_value=mock_llm):
            state = await graph.ainvoke(Command(resume={"proceed": True}), config=cfg)

        assert state.get("report") == "Mock report"
        assert not state.get("cancelled")
        mock_llm.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_resume_false_cancels_without_report(self):
        from langgraph.types import Command

        graph = _make_batch_graph()
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock()
        tid = f"hitl-cancel-{uuid.uuid4().hex[:8]}"
        cfg = {"configurable": {"thread_id": tid, "chat_config": _FakeChatConfig()}}

        await graph.ainvoke(_empty_state(job_ids=["j1"]), config=cfg)
        with patch("autolangchat.graph.batch_graph.ChatBedrockConverse", return_value=mock_llm):
            state = await graph.ainvoke(Command(resume={"proceed": False}), config=cfg)

        assert state.get("cancelled") is True
        assert state.get("report") is None
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_job_interrupt_shows_correct_counts(self):
        """Interrupt payload must reflect actual completed/failed counts."""
        from langgraph.types import Command

        async def _proc(job_id, config=None):
            if job_id == "j2":
                raise RuntimeError("j2 failed")
            return {"data": job_id}

        graph = _make_batch_graph()
        tid = f"hitl-counts-{uuid.uuid4().hex[:8]}"
        cfg = {"configurable": {"thread_id": tid, "chat_config": _FakeChatConfig(), "job_processor": _proc}}

        state = await graph.ainvoke(_empty_state(job_ids=["j1", "j2", "j3"]), config=cfg)
        assert "__interrupt__" in state
        intr = state["__interrupt__"]
        payload = intr[0] if isinstance(intr, list) else intr
        value = payload.value if hasattr(payload, "value") else payload
        assert value["completed_count"] == 2  # j1, j3
        assert value["failed_count"] == 1  # j2


# ---------------------------------------------------------------------------
# 4.3 WebSocket handler integration
# ---------------------------------------------------------------------------


class TestWebSocketBatchHandlers:
    """Test _handle_batch_start and _handle_batch_confirm via the handler class."""

    def _make_handler(self, batch_graph=None):
        from autolangchat.websocket_handler import WebSocketChatHandler

        chat_graph_mock = MagicMock()
        session_mgr = MagicMock()
        session_mgr.get_session = AsyncMock()
        config = MagicMock()
        config.timeout = 30
        config.feedback_allow_anonymous = False

        handler = WebSocketChatHandler(
            session_manager=session_mgr,
            config=config,
            chat_graph=chat_graph_mock,
            batch_graph=batch_graph,
            app_base_url="http://localhost:8001",
        )
        return handler

    def _make_session(self):
        session = MagicMock()
        session.session_id = str(uuid.uuid4())
        session.metadata = {}
        return session

    @pytest.mark.asyncio
    async def test_batch_start_no_graph_sends_error(self):
        """When batch_graph is None, batch_start returns error."""
        handler = self._make_handler(batch_graph=None)
        ws = MagicMock()
        ws.send_text = AsyncMock()

        messages_sent = []

        async def _capture(ws, msg):
            messages_sent.append(msg)

        handler._send_message = _capture
        handler._send_error = _capture

        session = self._make_session()
        handler.session_manager.get_session.return_value = session

        await handler._handle_batch_start(ws, {"type": "batch_start", "job_ids": ["j1"]})
        assert any("not configured" in str(m) for m in messages_sent)

    @pytest.mark.asyncio
    async def test_batch_start_empty_job_ids_sends_error(self):
        graph = _make_batch_graph()
        handler = self._make_handler(batch_graph=graph)
        ws = MagicMock()

        messages_sent = []

        async def _send(ws, msg):
            messages_sent.append(msg)

        handler._send_message = AsyncMock(side_effect=_send)
        handler._send_error = AsyncMock(side_effect=_send)

        session = self._make_session()
        handler.session_manager.get_session.return_value = session

        await handler._handle_batch_start(ws, {"type": "batch_start", "job_ids": []})
        assert any("job_ids" in str(m) for m in messages_sent)

    @pytest.mark.asyncio
    async def test_batch_start_sends_confirmation_required(self):
        """Full flow: batch_start with jobs → should send confirmation_required."""
        graph = _make_batch_graph()
        handler = self._make_handler(batch_graph=graph)
        ws = MagicMock()

        messages_sent: List[Dict] = []

        async def _send(ws, msg):
            messages_sent.append(msg)

        handler._send_message = _send
        handler._send_error = _send

        session = self._make_session()
        handler.session_manager.get_session.return_value = session

        await handler._handle_batch_start(
            ws,
            {
                "type": "batch_start",
                "job_ids": ["j1", "j2"],
                "batch_id": "test-batch-001",
            },
        )

        types_sent = [m.get("type") for m in messages_sent if isinstance(m, dict)]
        assert "batch_started" in types_sent
        assert "confirmation_required" in types_sent

    @pytest.mark.asyncio
    async def test_batch_confirm_proceed_sends_batch_finished(self):
        """confirm proceed=True → batch_finished with report."""
        from langgraph.types import Command

        graph = _make_batch_graph()
        handler = self._make_handler(batch_graph=graph)
        ws = MagicMock()

        messages_sent: List[Dict] = []

        async def _send(ws, msg):
            messages_sent.append(msg)

        handler._send_message = _send
        handler._send_error = _send

        session = self._make_session()
        handler.session_manager.get_session.return_value = session

        bid = f"test-{uuid.uuid4().hex[:6]}"
        cfg = {"configurable": {"thread_id": bid, "chat_config": _FakeChatConfig()}}

        # Prime the graph so it's waiting at the interrupt
        await graph.ainvoke(_empty_state(job_ids=["j1"]), config=cfg)

        # Mock LLM for the generate_report node
        mock_llm = MagicMock()
        ai_msg = MagicMock()
        ai_msg.content = "Mock report"
        mock_llm.ainvoke = AsyncMock(return_value=ai_msg)
        with patch("autolangchat.graph.batch_graph.ChatBedrockConverse", return_value=mock_llm):
            await handler._handle_batch_confirm(ws, {"type": "confirm", "batch_id": bid, "proceed": True})

        types_sent = [m.get("type") for m in messages_sent if isinstance(m, dict)]
        assert "batch_finished" in types_sent
        finished = next(m for m in messages_sent if isinstance(m, dict) and m.get("type") == "batch_finished")
        assert finished.get("report") == "Mock report"

    @pytest.mark.asyncio
    async def test_batch_confirm_cancel_sends_batch_cancelled(self):
        """confirm proceed=False → batch_cancelled message."""
        graph = _make_batch_graph()
        handler = self._make_handler(batch_graph=graph)
        ws = MagicMock()

        messages_sent: List[Dict] = []

        async def _send(ws, msg):
            messages_sent.append(msg)

        handler._send_message = _send
        handler._send_error = _send

        session = self._make_session()
        handler.session_manager.get_session.return_value = session

        bid = f"test-{uuid.uuid4().hex[:6]}"
        cfg = {"configurable": {"thread_id": bid, "chat_config": _FakeChatConfig()}}
        await graph.ainvoke(_empty_state(job_ids=["j1"]), config=cfg)

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock()
        with patch("autolangchat.graph.batch_graph.ChatBedrockConverse", return_value=mock_llm):
            await handler._handle_batch_confirm(ws, {"type": "confirm", "batch_id": bid, "proceed": False})

        types_sent = [m.get("type") for m in messages_sent if isinstance(m, dict)]
        assert "batch_cancelled" in types_sent


# ---------------------------------------------------------------------------
# 4.5 Regression: chat path unaffected
# ---------------------------------------------------------------------------


class TestChatPathRegression:
    """Ensure the existing chat graph is not affected by Phase 4 additions."""

    @pytest.mark.asyncio
    async def test_chat_graph_works_alongside_batch_graph(self):
        """Build both graphs from the same config; verify chat graph still works."""
        from langchain_core.messages import AIMessage

        from autolangchat.graph.graph import build_chat_graph

        ai_msg = AIMessage(content="chat ok")
        ai_msg.usage_metadata = {"input_tokens": 5, "output_tokens": 3}
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.ainvoke = AsyncMock(return_value=ai_msg)

        chat_graph = build_chat_graph(_FakeChatConfig())
        batch_graph = build_batch_graph(_FakeChatConfig())

        # Chat graph still works (patch at invocation time)
        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse", return_value=mock_llm):
            state = await chat_graph.ainvoke(
                {"messages": [{"role": "user", "content": "hello"}], "metadata": {}},
                config={"configurable": {"thread_id": str(uuid.uuid4())}},
            )
        assert state["messages"][-1]["role"] == "assistant"
        assert state["messages"][-1]["content"] == "chat ok"
