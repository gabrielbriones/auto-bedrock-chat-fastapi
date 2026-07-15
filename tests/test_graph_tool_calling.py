"""Phase 2 — graph tool calling tests.

Tests the full multi-round tool loop:
    preprocess → llm (tool_calls) → tools_execution → llm (final answer) → END

Uses mocked ChatBedrockConverse and a mock ToolManager so no real AWS
credentials or HTTP calls are needed.
"""

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.graph.graph import build_chat_graph
from autolangchat.graph.state import ChatState
from autolangchat.graph.tools.tool_node import tools_execution_node

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeChatConfig:
    model_id = "us.anthropic.claude-sonnet-5"
    fallback_model = None
    aws_region = "us-east-1"
    temperature = 0.7
    max_tokens = 1024
    top_p = 0.9
    checkpoint_postgres_url = None
    enable_ai_summarization = False
    single_msg_length_threshold = 500_000
    single_msg_truncation_target = 425_000
    history_total_length_threshold = 650_000
    history_msg_length_threshold = 100_000
    history_msg_truncation_target = 85_000
    max_truncation_recursion = 3

    def get_system_prompt(self) -> str:
        return ""


def _make_ai_message_plain(content: str, usage: Dict | None = None) -> MagicMock:
    """Build a mock AIMessage with no tool calls."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = []
    msg.response_metadata = {"model_id": _FakeChatConfig.model_id}
    msg.usage_metadata = usage or {"input_tokens": 10, "output_tokens": 5}
    msg.__add__ = lambda self, other: self
    return msg


def _make_ai_message_with_tools(tool_calls: List[Dict], usage: Dict | None = None) -> MagicMock:
    """Build a mock AIMessage that requests tool calls."""
    msg = MagicMock()
    msg.content = ""
    msg.tool_calls = tool_calls  # LangChain format: [{"name": ..., "args": {...}, "id": ..., "type": "tool_call"}]
    msg.response_metadata = {"model_id": _FakeChatConfig.model_id}
    msg.usage_metadata = usage or {"input_tokens": 20, "output_tokens": 10}
    msg.__add__ = lambda self, other: self
    return msg


def _make_mock_tool_manager(tool_results: List[Dict]) -> MagicMock:
    """Build a mock ToolManager that returns preset results."""
    tm = MagicMock()
    tm.execute_tool_calls = AsyncMock(return_value=tool_results)
    return tm


# ---------------------------------------------------------------------------
# Tools execution node (unit tests)
# ---------------------------------------------------------------------------


class TestToolsExecutionNode:
    @pytest.mark.asyncio
    async def test_executes_tool_calls(self):
        """Node calls tool_manager and appends tool result message."""
        fake_results = [{"tool_call_id": "call_1", "name": "get_jobs", "result": {"jobs": []}}]
        tm = _make_mock_tool_manager(fake_results)

        state: ChatState = {
            "messages": [
                {"role": "user", "content": "list jobs"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"name": "get_jobs", "args": {"q": "all"}, "id": "call_1", "type": "tool_call"}],
                },
            ],
            "metadata": {},
        }
        config = {"configurable": {"tool_manager": tm, "auth_info": None}}

        result = await tools_execution_node(state, config)

        tm.execute_tool_calls.assert_awaited_once()
        call_args = tm.execute_tool_calls.call_args[0][0]
        assert call_args[0]["name"] == "get_jobs"
        assert call_args[0]["arguments"] == {"q": "all"}
        assert call_args[0]["id"] == "call_1"

        assert len(result["messages"]) == 3
        tool_msg = result["messages"][-1]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_results"] == fake_results

    @pytest.mark.asyncio
    async def test_increments_metadata_counters(self):
        """tool_call_rounds and total_tool_calls are incremented."""
        fake_results = [
            {"tool_call_id": "c1", "name": "get_jobs", "result": []},
            {"tool_call_id": "c2", "name": "get_job", "result": {}},
        ]
        tm = _make_mock_tool_manager(fake_results)

        state: ChatState = {
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"name": "get_jobs", "args": {}, "id": "c1"},
                        {"name": "get_job", "args": {"id": "42"}, "id": "c2"},
                    ],
                },
            ],
            "metadata": {"tool_call_rounds": 0, "total_tool_calls": 0},
        }
        config = {"configurable": {"tool_manager": tm}}

        result = await tools_execution_node(state, config)

        assert result["metadata"]["tool_call_rounds"] == 1
        assert result["metadata"]["total_tool_calls"] == 2

    @pytest.mark.asyncio
    async def test_no_tool_manager_returns_error_results(self):
        """Without tool_manager, node returns error results and does not raise."""
        state: ChatState = {
            "messages": [
                {"role": "user", "content": "list jobs"},
                {"role": "assistant", "content": "", "tool_calls": [{"name": "get_jobs", "args": {}, "id": "c1"}]},
            ],
            "metadata": {},
        }
        config = {"configurable": {}}

        result = await tools_execution_node(state, config)

        assert result["messages"][-1]["role"] == "tool"
        error_results = result["messages"][-1]["tool_results"]
        assert len(error_results) == 1
        assert "error" in error_results[0]

    @pytest.mark.asyncio
    async def test_converts_lc_args_to_arguments(self):
        """LangChain 'args' key is converted to ToolManager's 'arguments' key."""
        captured: List = []
        tm = MagicMock()

        async def _capture(calls, **kwargs):
            captured.extend(calls)
            return [{"tool_call_id": c["id"], "name": c["name"], "result": {}} for c in calls]

        tm.execute_tool_calls = _capture

        state: ChatState = {
            "messages": [
                {"role": "user", "content": "q"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"name": "search", "args": {"query": "test"}, "id": "x1", "type": "tool_call"}],
                },
            ],
            "metadata": {},
        }
        config = {"configurable": {"tool_manager": tm}}
        await tools_execution_node(state, config)

        assert captured[0]["arguments"] == {"query": "test"}
        assert "args" not in captured[0]

    @pytest.mark.asyncio
    async def test_on_progress_wraps_string_to_dict(self):
        """on_progress receives a dict (type=typing) not a plain string."""
        received: List = []
        tm = _make_mock_tool_manager([{"tool_call_id": "c1", "name": "fn", "result": {}}])

        async def on_progress(msg):
            received.append(msg)

        # Make execute_tool_calls actually invoke the on_progress callback
        async def _exec(calls, auth_info=None, on_progress=None):
            if on_progress:
                await on_progress("Calling fn... (1/1)")
            return [{"tool_call_id": "c1", "name": "fn", "result": {}}]

        tm.execute_tool_calls = _exec

        state: ChatState = {
            "messages": [
                {"role": "user", "content": "x"},
                {"role": "assistant", "content": "", "tool_calls": [{"name": "fn", "args": {}, "id": "c1"}]},
            ],
            "metadata": {},
        }
        config = {"configurable": {"tool_manager": tm, "on_progress": on_progress}}
        await tools_execution_node(state, config)

        assert len(received) == 1
        assert received[0]["type"] == "typing"
        assert isinstance(received[0]["message"], str)


# ---------------------------------------------------------------------------
# Full graph round-trip with tool calling
# ---------------------------------------------------------------------------


class TestGraphToolLoop:
    @pytest.mark.asyncio
    async def test_single_tool_round(self, fake_config):
        """Graph: user → llm (tool call) → tools → llm (final) → END."""
        tool_call = {"name": "get_jobs", "args": {"status": "running"}, "id": "call_1", "type": "tool_call"}
        first_ai = _make_ai_message_with_tools([tool_call])
        final_ai = _make_ai_message_plain("You have 3 running jobs.")

        tool_results = [{"tool_call_id": "call_1", "name": "get_jobs", "result": {"jobs": [1, 2, 3]}}]
        tm = _make_mock_tool_manager(tool_results)

        call_count = {"n": 0}

        def llm_factory(*args, **kwargs):
            inst = MagicMock()
            inst.bind_tools.return_value = inst  # bind_tools must return same mock
            call_count["n"] += 1
            if call_count["n"] == 1:
                inst.ainvoke = AsyncMock(return_value=first_ai)
            else:
                inst.ainvoke = AsyncMock(return_value=final_ai)
            inst.astream = _make_empty_astream()
            return inst

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse", side_effect=llm_factory):
            graph = build_chat_graph(fake_config, tool_manager=tm)
            result = await graph.ainvoke(
                {"user_message": "list running jobs"},
                config={"configurable": {"thread_id": "test-tool-round-1"}},
            )

        messages = result["messages"]
        roles = [m["role"] for m in messages]
        assert roles == ["user", "assistant", "tool", "assistant"]
        assert messages[-1]["content"] == "You have 3 running jobs."
        assert result["metadata"]["tool_call_rounds"] == 1
        assert result["metadata"]["total_tool_calls"] == 1

    @pytest.mark.asyncio
    async def test_multi_round_tool_calling(self, fake_config):
        """Graph loops through two tool rounds before returning a final answer."""
        tool_call_1 = {"name": "get_jobs", "args": {}, "id": "c1", "type": "tool_call"}
        tool_call_2 = {"name": "get_job", "args": {"id": "5"}, "id": "c2", "type": "tool_call"}

        ai_round1 = _make_ai_message_with_tools([tool_call_1])
        ai_round2 = _make_ai_message_with_tools([tool_call_2])
        ai_final = _make_ai_message_plain("Job 5 is complete.")

        results_1 = [{"tool_call_id": "c1", "name": "get_jobs", "result": {"ids": [5]}}]
        results_2 = [{"tool_call_id": "c2", "name": "get_job", "result": {"id": 5, "status": "done"}}]

        tool_exec_count = {"n": 0}
        tm = MagicMock()

        async def _exec(calls, auth_info=None, on_progress=None):
            tool_exec_count["n"] += 1
            return results_1 if tool_exec_count["n"] == 1 else results_2

        tm.execute_tool_calls = _exec

        llm_call_count = {"n": 0}

        def llm_factory(*args, **kwargs):
            inst = MagicMock()
            inst.bind_tools.return_value = inst  # bind_tools must return same mock
            llm_call_count["n"] += 1
            if llm_call_count["n"] == 1:
                inst.ainvoke = AsyncMock(return_value=ai_round1)
            elif llm_call_count["n"] == 2:
                inst.ainvoke = AsyncMock(return_value=ai_round2)
            else:
                inst.ainvoke = AsyncMock(return_value=ai_final)
            inst.astream = _make_empty_astream()
            return inst

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse", side_effect=llm_factory):
            graph = build_chat_graph(fake_config, tool_manager=tm)
            result = await graph.ainvoke(
                {"user_message": "what is job 5?"},
                config={"configurable": {"thread_id": "test-multi-round"}},
            )

        roles = [m["role"] for m in result["messages"]]
        assert roles == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
        assert result["metadata"]["tool_call_rounds"] == 2
        assert result["metadata"]["total_tool_calls"] == 2
        assert result["messages"][-1]["content"] == "Job 5 is complete."

    @pytest.mark.asyncio
    async def test_auth_info_passed_to_tool_manager(self, fake_config):
        """auth_info from configurable is forwarded to execute_tool_calls."""
        tool_call = {"name": "get_jobs", "args": {}, "id": "c1", "type": "tool_call"}
        first_ai = _make_ai_message_with_tools([tool_call])
        final_ai = _make_ai_message_plain("done")

        call_kwargs: List = []
        tm = MagicMock()

        async def _exec(calls, auth_info=None, on_progress=None):
            call_kwargs.append({"auth_info": auth_info})
            return [{"tool_call_id": "c1", "name": "get_jobs", "result": {}}]

        tm.execute_tool_calls = _exec

        fake_auth = MagicMock()
        fake_auth.is_authenticated = True

        call_count = {"n": 0}

        def llm_factory(*args, **kwargs):
            inst = MagicMock()
            inst.bind_tools.return_value = inst  # bind_tools must return same mock
            call_count["n"] += 1
            inst.ainvoke = AsyncMock(return_value=first_ai if call_count["n"] == 1 else final_ai)
            inst.astream = _make_empty_astream()
            return inst

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse", side_effect=llm_factory):
            graph = build_chat_graph(fake_config, tool_manager=tm)
            await graph.ainvoke(
                {"messages": [{"role": "user", "content": "q"}], "metadata": {}},
                config={"configurable": {"thread_id": "test-auth", "auth_info": fake_auth}},
            )

        assert len(call_kwargs) == 1
        assert call_kwargs[0]["auth_info"] is fake_auth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_config():
    return _FakeChatConfig()


def _make_empty_astream():
    async def _astream(messages):
        return
        yield

    return _astream
