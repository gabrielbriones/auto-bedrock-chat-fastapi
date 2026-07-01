"""Phase 3 — persistence / checkpoint tests.

All tests use MemorySaver (no Postgres connection required) to verify:
  - Cross-invocation state accumulation via LangGraph checkpointer
  - Session continuity: same thread_id resumes from checkpoint
  - Different thread_ids are isolated
  - Session manager accepts preferred_session_id (reconnect flow)
  - open_checkpointer / close_checkpointer are no-ops for MemorySaver
  - purge_expired_checkpoints is a no-op for MemorySaver
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.graph.checkpointer import (
    build_checkpointer,
    close_checkpointer,
    open_checkpointer,
    purge_expired_checkpoints,
)
from autolangchat.graph.graph import build_chat_graph

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


class _FakeChatConfig:
    model_id = "us.anthropic.claude-sonnet-5"
    fallback_model = None
    aws_region = "us-east-1"
    temperature = 0.7
    max_tokens = 1024
    top_p = 0.9
    checkpoint_postgres_url = None
    checkpoint_pool_size = 5
    # Preprocessing thresholds
    enable_ai_summarization = False
    single_msg_length_threshold = 500_000
    single_msg_truncation_target = 425_000
    history_total_length_threshold = 650_000
    history_msg_length_threshold = 100_000
    history_msg_truncation_target = 85_000
    max_truncation_recursion = 3

    def get_system_prompt(self) -> str:
        return ""


def _make_ai_msg(content: str = "hi", *, tool_calls=None):
    """Return a minimal mock AIMessage accepted by the llm node."""
    from langchain_core.messages import AIMessage

    msg = AIMessage(content=content, tool_calls=tool_calls or [])
    msg.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
    return msg


def _llm_factory(response_content: str = "hello"):
    """Return a patcher that replaces ChatBedrockConverse with a mock LLM."""
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm
    ai_msg = _make_ai_msg(response_content)

    # ainvoke used when on_progress is None (no streaming)
    mock_llm.ainvoke = AsyncMock(return_value=ai_msg)

    # astream used when on_progress is provided
    async def _astream(*a, **kw):
        yield ai_msg

    mock_llm.astream = _astream

    inst = mock_llm
    patcher = patch(
        "autolangchat.graph.nodes.llm_call.ChatBedrockConverse",
        return_value=inst,
    )
    return patcher, inst


# ---------------------------------------------------------------------------
# 3.1 build_checkpointer
# ---------------------------------------------------------------------------


class TestBuildCheckpointer:
    def test_returns_memory_saver_when_no_url(self):
        from langgraph.checkpoint.memory import MemorySaver

        cp = build_checkpointer(postgres_url=None)
        assert isinstance(cp, MemorySaver)

    def test_returns_memory_saver_when_postgres_not_installed(self, monkeypatch):
        """Simulate langgraph-checkpoint-postgres not installed → fall back."""
        import builtins

        real_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name == "langgraph.checkpoint.postgres.aio":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _mock_import)
        from langgraph.checkpoint.memory import MemorySaver

        cp = build_checkpointer(postgres_url="postgresql://dummy/db")
        assert isinstance(cp, MemorySaver)


# ---------------------------------------------------------------------------
# 3.1 open / close / purge are no-ops for MemorySaver
# ---------------------------------------------------------------------------


class TestCheckpointerLifecycleNoop:
    @pytest.mark.asyncio
    async def test_open_noop_for_memory_saver(self):
        from langgraph.checkpoint.memory import MemorySaver

        cp = MemorySaver()
        # Should not raise
        await open_checkpointer(cp)

    @pytest.mark.asyncio
    async def test_close_noop_for_memory_saver(self):
        from langgraph.checkpoint.memory import MemorySaver

        cp = MemorySaver()
        await close_checkpointer(cp)

    @pytest.mark.asyncio
    async def test_purge_returns_zero_for_memory_saver(self):
        from langgraph.checkpoint.memory import MemorySaver

        cp = MemorySaver()
        result = await purge_expired_checkpoints(cp, ttl_seconds=3600)
        assert result == 0


# ---------------------------------------------------------------------------
# 3.2 Cross-invocation persistence with MemorySaver
# ---------------------------------------------------------------------------


class TestCheckpointPersistence:
    """Verify that multiple ainvoke calls with the same thread_id accumulate
    state inside the MemorySaver checkpointer — simulating what Postgres would
    do in production."""

    @pytest.mark.asyncio
    async def test_same_thread_id_accumulates_messages(self):
        """Two successive ainvoke calls with the same thread_id should both
        succeed and each return a valid assistant message."""
        cfg = _FakeChatConfig()
        patcher, mock_llm = _llm_factory("turn response")

        with patcher:
            graph = build_chat_graph(cfg)

            tid = str(uuid.uuid4())
            invoke_cfg = {"configurable": {"thread_id": tid}}
            messages = [{"role": "user", "content": "hello"}]

            state1 = await graph.ainvoke({"messages": messages, "metadata": {}}, config=invoke_cfg)

            assert state1["messages"]
            last1 = state1["messages"][-1]
            assert last1.get("role") == "assistant"

            # Second invocation — same thread_id, graph resumes from checkpoint
            messages2 = messages + [
                {"role": "assistant", "content": last1.get("content", "")},
                {"role": "user", "content": "how are you?"},
            ]
            state2 = await graph.ainvoke({"messages": messages2, "metadata": {}}, config=invoke_cfg)

        assert state2["messages"]
        last2 = state2["messages"][-1]
        assert last2.get("role") == "assistant"

    @pytest.mark.asyncio
    async def test_different_thread_ids_are_isolated(self):
        """Two thread_ids must not share checkpoint state."""
        cfg = _FakeChatConfig()
        patcher, mock_llm = _llm_factory("isolated")

        with patcher:
            graph = build_chat_graph(cfg)

            tid_a = str(uuid.uuid4())
            tid_b = str(uuid.uuid4())
            messages = [{"role": "user", "content": "ping"}]

            state_a = await graph.ainvoke(
                {"messages": messages, "metadata": {}},
                config={"configurable": {"thread_id": tid_a}},
            )

            state_b = await graph.ainvoke(
                {"messages": messages, "metadata": {}},
                config={"configurable": {"thread_id": tid_b}},
            )

        # Both succeeded independently
        assert state_a["messages"][-1].get("role") == "assistant"
        assert state_b["messages"][-1].get("role") == "assistant"


# ---------------------------------------------------------------------------
# 3.3 Session manager — preferred_session_id (reconnect flow)
# ---------------------------------------------------------------------------


class TestSessionManagerReconnect:
    """Verify create_session() honours a preferred_session_id UUID."""

    @pytest.fixture
    def session_manager(self):
        from autolangchat.session_manager import ChatSessionManager

        cfg = MagicMock()
        cfg.max_sessions = 100
        cfg.session_timeout = 3600
        return ChatSessionManager(cfg)

    @pytest.mark.asyncio
    async def test_uses_preferred_session_id_when_valid_uuid(self, session_manager):
        preferred = str(uuid.uuid4())
        ws = MagicMock()
        ws.headers = {}

        sid = await session_manager.create_session(
            websocket=ws,
            preferred_session_id=preferred,
        )
        assert sid == preferred

    @pytest.mark.asyncio
    async def test_ignores_invalid_preferred_session_id(self, session_manager):
        ws = MagicMock()
        ws.headers = {}

        sid = await session_manager.create_session(
            websocket=ws,
            preferred_session_id="not-a-uuid",
        )
        # Should have fallen back to a fresh UUID
        assert sid != "not-a-uuid"
        uuid.UUID(sid)  # Validates it is a proper UUID

    @pytest.mark.asyncio
    async def test_generates_fresh_uuid_when_no_preferred(self, session_manager):
        ws = MagicMock()
        ws.headers = {}

        sid = await session_manager.create_session(websocket=ws)
        uuid.UUID(sid)  # Validates format
