"""Graph tool calling tests.

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
        async def _exec(calls, auth_info=None, on_progress=None, on_tool_event=None, tool_context=None):
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
# Oversized HTTP responses through the real ToolManager
# ---------------------------------------------------------------------------

_JOBS_SPEC: Dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {"/jobs": {"get": {"operationId": "get_jobs", "summary": "List jobs", "responses": {"200": {}}}}},
}


async def _bytes_stream(data: bytes):
    yield data


class TestTruncatedToolResponse:
    @pytest.mark.asyncio
    async def test_oversized_response_reaches_state_as_flagged_serializable_result(self):
        """A response over tool_response_max_bytes flows through tools_execution_node
        as a flagged, JSON-serializable result (never a raw object or an error)."""
        from autolangchat.config import ChatConfig
        from autolangchat.graph.tools.generator import ToolsGenerator
        from autolangchat.graph.tools.manager import ToolManager
        from tests.http_stubs import stub_http_stream

        config = ChatConfig(model_id="test-model", excluded_paths=[], AUTOCHAT_TOOL_RESPONSE_MAX_BYTES=64)
        generator = ToolsGenerator(openapi_spec=_JOBS_SPEC, config=config)
        tm = ToolManager(generated_tools=generator._generated_tools, config=config, base_url="http://test-api")
        stub_http_stream(tm, _bytes_stream(b"log line\n" * 100), **{"content-type": "text/plain"})

        state: ChatState = {
            "messages": [
                {"role": "user", "content": "show the logs"},
                {"role": "assistant", "content": "", "tool_calls": [{"name": "get_jobs", "args": {}, "id": "c1"}]},
            ],
            "metadata": {},
        }
        result = await tools_execution_node(state, {"configurable": {"tool_manager": tm, "auth_info": None}})

        tool_msg = result["messages"][-1]
        entry = tool_msg["tool_results"][0]
        assert "error" not in entry
        assert entry["result"]["truncated"] is True
        assert entry["result"]["bytes_read"] == 64
        assert entry["result"]["limit_bytes"] == 64
        assert json.loads(tool_msg["content"])[0]["truncated"] is True
        assert result["metadata"]["tool_result_sizes"][0]["bytes"] > 0

    @pytest.mark.asyncio
    async def test_truncated_result_is_spilled_within_default_quota_ratios(self):
        """The tools node passes the truncated result on as is; the preprocessing node then spills it into the
        store without a quota error and leaves only a marker. Sizes are the default ChatConfig cap and quotas
        scaled down by the same factor."""
        from autolangchat.artifacts import ArtifactStore
        from autolangchat.config import ChatConfig
        from autolangchat.graph.nodes.preprocess import prepare_message_artifacts
        from autolangchat.graph.tools.generator import ToolsGenerator
        from autolangchat.graph.tools.manager import ToolManager
        from tests.http_stubs import stub_http_stream

        defaults = ChatConfig(model_id="test-model", excluded_paths=[])
        scale = 64
        config = ChatConfig(
            model_id="test-model",
            excluded_paths=[],
            AUTOCHAT_TOOL_RESPONSE_MAX_BYTES=defaults.tool_response_max_bytes // scale,
        )
        generator = ToolsGenerator(openapi_spec=_JOBS_SPEC, config=config)
        tm = ToolManager(generated_tools=generator._generated_tools, config=config, base_url="http://test-api")
        cap = config.tool_response_max_bytes
        stub_http_stream(
            tm, _bytes_stream(b"log line of typical text\n" * (cap // 20)), **{"content-type": "text/plain"}
        )

        store = ArtifactStore(
            max_bytes_per_session=defaults.artifact_max_bytes_per_session // scale,
            max_bytes=defaults.artifact_max_bytes // scale,
        )
        store.register_connection("conn", None)
        scope = store.activate("conn", "thread", None)
        configurable = {
            "tool_manager": tm,
            "auth_info": None,
            "chat_config": config,
            "artifact_store": store,
            "artifact_scope": scope,
        }

        state: ChatState = {
            "messages": [
                {"role": "user", "content": "show the logs"},
                {"role": "assistant", "content": "", "tool_calls": [{"name": "get_jobs", "args": {}, "id": "c1"}]},
            ],
            "metadata": {},
        }
        result = await tools_execution_node(state, {"configurable": configurable})

        # The tools node estimates and stores nothing: the result is passed on exactly as the tool returned it.
        tool_msg = result["messages"][-1]
        assert "artifact_marker" not in tool_msg["tool_results"][0]
        assert tool_msg["tool_results"][0]["result"]["truncated"] is True
        assert result["metadata"]["tool_result_sizes"][0]["bytes"] > cap
        assert "spilled_artifacts" not in result["metadata"]
        assert store.statistics()["puts"] == 0

        processed, estimates = prepare_message_artifacts(result["messages"], config, {"configurable": configurable})

        assert estimates["messages"][-1]["exceeds_threshold"] is True
        tool_msg = processed[-1]
        entry = tool_msg["tool_results"][0]
        marker = entry["result"]
        assert entry["artifact_marker"] is True and marker["artifact_handle"]
        # The envelope's text is archived as is, and the truncation details stay visible to the model.
        assert marker["content_field"] == "text" and marker["content_type"] == "text/plain"
        assert marker["other_fields"]["truncated"] is True and marker["other_fields"]["limit_bytes"] == cap
        assert "truncated" not in marker
        assert len(tool_msg["content"]) < 5_000  # only the bounded preview, not the ~1 MiB original
        assert marker["size_bytes"] == cap
        assert "artifact_handle" not in (tool_msg.get("metadata") or {})
        assert store.statistics()["quota_rejections"] == 0
        assert store.statistics()["puts"] == 1
        assert store.read_region(scope, marker["artifact_handle"], max_lines=3)["truncated"] is True


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

        async def _exec(calls, auth_info=None, on_progress=None, on_tool_event=None, tool_context=None):
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

        async def _exec(calls, auth_info=None, on_progress=None, on_tool_event=None, tool_context=None):
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
# End-to-end: paging a session artifact through the internal tools
# ---------------------------------------------------------------------------


class TestArtifactPagingEndToEnd:
    @pytest.mark.asyncio
    async def test_model_pages_through_artifact(self):
        """llm → read_content_range (page 1) → llm → page 2 → llm → final, using the real stack."""
        from autolangchat.artifacts import ArtifactStore
        from autolangchat.config import ChatConfig
        from autolangchat.graph.tools.artifact_tools import READ_CONTENT_RANGE, build_artifact_tools
        from autolangchat.graph.tools.manager import ToolManager

        chat_config = ChatConfig(model_id=_FakeChatConfig.model_id, excluded_paths=[])
        tm = ToolManager(generated_tools={}, config=chat_config, internal_tools=build_artifact_tools())
        store = ArtifactStore()
        store.register_connection("conn", "alice")
        scope = store.activate("conn", "thread-paging", "alice")
        handle = store.put(scope, "".join(f"row {n}\n" for n in range(1, 6)), {"source": "tool_result"})

        def _read(call_id, start_line):
            return {
                "name": READ_CONTENT_RANGE,
                "args": {"handle": handle, "start_line": start_line, "max_lines": 3},
                "id": call_id,
                "type": "tool_call",
            }

        replies = [
            _make_ai_message_with_tools([_read("p1", 1)]),
            _make_ai_message_with_tools([_read("p2", 4)]),
            _make_ai_message_plain("The artifact has 5 rows."),
        ]
        bound_tools: List[Any] = []

        def llm_factory(*args, **kwargs):
            inst = MagicMock()

            def _bind(tools, **_):
                bound_tools.append([t.name for t in tools])
                return inst

            inst.bind_tools.side_effect = _bind
            inst.ainvoke = AsyncMock(return_value=replies.pop(0))
            inst.astream = _make_empty_astream()
            return inst

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse", side_effect=llm_factory):
            graph = build_chat_graph(chat_config, tool_manager=tm)
            result = await graph.ainvoke(
                {"user_message": "summarise the artifact"},
                config={
                    "configurable": {
                        "thread_id": "thread-paging",
                        "artifact_store": store,
                        "artifact_scope": scope,
                    }
                },
            )

        pages = [m["tool_results"][0]["result"] for m in result["messages"] if m["role"] == "tool"]
        assert [p["text"] for p in pages] == ["1: row 1\n2: row 2\n3: row 3", "4: row 4\n5: row 5"]
        assert [p["has_more"] for p in pages] == [True, False]
        assert [p["next_start_line"] for p in pages] == [4, None]
        assert all(p["total_lines"] == 5 for p in pages)
        assert result["messages"][-1]["content"] == "The artifact has 5 rows."
        assert result["metadata"]["tool_call_rounds"] == 2
        # The internal tool is bound to the model; hidden aliases are not.
        assert bound_tools and all(READ_CONTENT_RANGE in names for names in bound_tools)
        assert not any("read_artifact_range" in names for names in bound_tools)

    @pytest.mark.asyncio
    async def test_direct_ainvoke_without_scope_gets_tool_unavailable(self):
        from autolangchat.config import ChatConfig
        from autolangchat.graph.tools.artifact_tools import READ_CONTENT_RANGE, build_artifact_tools
        from autolangchat.graph.tools.internal import TOOL_UNAVAILABLE
        from autolangchat.graph.tools.manager import ToolManager

        chat_config = ChatConfig(model_id=_FakeChatConfig.model_id, excluded_paths=[])
        tm = ToolManager(generated_tools={}, config=chat_config, internal_tools=build_artifact_tools())
        call = {"name": READ_CONTENT_RANGE, "args": {"handle": "art_x"}, "id": "c1", "type": "tool_call"}
        replies = [_make_ai_message_with_tools([call]), _make_ai_message_plain("no access")]

        def llm_factory(*args, **kwargs):
            inst = MagicMock()
            inst.bind_tools.return_value = inst
            inst.ainvoke = AsyncMock(return_value=replies.pop(0))
            inst.astream = _make_empty_astream()
            return inst

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse", side_effect=llm_factory):
            graph = build_chat_graph(chat_config, tool_manager=tm)
            result = await graph.ainvoke(
                {"user_message": "read it"}, config={"configurable": {"thread_id": "thread-no-scope"}}
            )

        [tool_message] = [m for m in result["messages"] if m["role"] == "tool"]
        assert tool_message["tool_results"][0]["result"]["error"] == TOOL_UNAVAILABLE


# ---------------------------------------------------------------------------
# Spilling oversized tool results into session artifacts (XMGPLAT-11846)
# ---------------------------------------------------------------------------

_SMALL_CAP_MODEL = "qwen.qwen3-32b-v1:0"
_LARGE_CAP_MODEL = "us.anthropic.claude-sonnet-5"


def _chat_config(model_id: str, **overrides: Any):
    """A real ChatConfig for ``model_id`` (its field only accepts the env alias)."""
    from autolangchat.config import ChatConfig

    return ChatConfig(AUTOCHAT_MODEL_ID=model_id, excluded_paths=[], **overrides)


def _big_payload(rows: int = 2500) -> Dict[str, Any]:
    return {"rows": [{"id": i, "name": f"item-{i}", "value": i * 3} for i in range(rows)]}


def _live_scope(thread_id: str = "thread-spill"):
    from autolangchat.artifacts import ArtifactStore

    store = ArtifactStore()
    store.register_connection("conn", "alice")
    return store, store.activate("conn", thread_id, "alice")


def _spill_state(*names: str) -> ChatState:
    calls = [{"name": n, "args": {}, "id": f"c{i}", "type": "tool_call"} for i, n in enumerate(names)]
    return {
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": calls},
        ],
        "metadata": {},
    }


def _tools_config(results, chat_config, *, store_scope=None, internal=()):
    tm = MagicMock()
    tm.internal_tools = {name: object() for name in internal}
    tm.execute_tool_calls = AsyncMock(return_value=results)
    configurable: Dict[str, Any] = {"tool_manager": tm, "chat_config": chat_config}
    if store_scope is not None:
        configurable["artifact_store"], configurable["artifact_scope"] = store_scope
    return {"configurable": configurable}


async def _run_tools_node(results, chat_config, *, store_scope=None, internal=()):
    config = _tools_config(results, chat_config, store_scope=store_scope, internal=internal)
    return await tools_execution_node(_spill_state(*[r["name"] for r in results]), config)


async def _run_tools_then_preprocess(results, chat_config, *, store_scope=None, internal=()):
    """The tools node, then the preprocessing node, which is where oversized results are replaced."""
    from autolangchat.graph.nodes.preprocess import preprocess_node

    config = _tools_config(results, chat_config, store_scope=store_scope, internal=internal)
    update = await tools_execution_node(_spill_state(*[r["name"] for r in results]), config)
    processed = await preprocess_node({"messages": update["messages"], "metadata": update["metadata"]}, config)
    return update, processed


class TestToolResultReplacementAfterTools:
    @pytest.mark.asyncio
    async def test_tools_node_passes_results_on_and_preprocessing_replaces_them(self):
        store, scope = _live_scope()
        payload = _big_payload()

        update, processed = await _run_tools_then_preprocess(
            [{"tool_call_id": "c0", "name": "get_report", "result": payload}],
            _chat_config(_SMALL_CAP_MODEL),
            store_scope=(store, scope),
        )

        # The tools node estimates and stores nothing; its sizes are those of the results as returned.
        assert update["messages"][-1]["tool_results"][0]["result"] == payload
        assert update["metadata"]["tool_result_sizes"][0]["bytes"] == len(json.dumps(payload).encode())
        assert "spilled_artifacts" not in update["metadata"]
        entry = processed["messages"][-1]["tool_results"][0]
        assert entry["artifact_marker"] is True and entry["result"]["artifact_handle"]
        assert len(processed["messages"][-1]["content"]) < 5_000
        assert store.statistics()["puts"] == 1

    @pytest.mark.asyncio
    async def test_model_override_changes_the_outcome(self):
        """The same result is spilled for a small-cap model and kept for a large-cap one."""
        store, scope = _live_scope()
        payload = _big_payload()

        def results():
            return [{"tool_call_id": "c0", "name": "get_report", "result": payload}]

        _, small = await _run_tools_then_preprocess(
            results(), _chat_config(_SMALL_CAP_MODEL), store_scope=(store, scope)
        )
        _, large = await _run_tools_then_preprocess(
            results(), _chat_config(_LARGE_CAP_MODEL), store_scope=(store, scope)
        )

        assert small["messages"][-1]["tool_results"][0]["artifact_marker"] is True
        assert large["messages"][-1]["tool_results"][0]["result"] == payload
        assert store.statistics()["puts"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", ["no_scope", "artifacts_disabled"])
    async def test_flagged_preview_when_artifacts_cannot_be_used(self, case):
        store, scope = _live_scope()
        overrides = {"AUTOCHAT_ARTIFACT_ENABLED": False} if case == "artifacts_disabled" else {}
        chat_config = _chat_config(_SMALL_CAP_MODEL, **overrides)
        store_scope = (store, scope) if case == "artifacts_disabled" else None

        update, processed = await _run_tools_then_preprocess(
            [{"tool_call_id": "c0", "name": "get_report", "result": _big_payload()}],
            chat_config,
            store_scope=store_scope,
        )

        tool_msg = processed["messages"][-1]
        entry = tool_msg["tool_results"][0]
        assert entry["result"]["truncated"] is True
        assert "artifact_handle" not in entry["result"] and "artifact_marker" not in entry
        assert len(tool_msg["content"]) < 5_000
        assert update["metadata"]["tool_result_sizes"][0]["bytes"] > 10_000
        assert store.statistics()["puts"] == 0

    @pytest.mark.asyncio
    async def test_internal_tool_results_are_never_spilled(self):
        store, scope = _live_scope()
        # Over the replacement threshold of a small-cap model, but below the legacy truncation stages.
        page = {"text": "x" * 14_000, "has_more": True}
        _, processed = await _run_tools_then_preprocess(
            [
                {"tool_call_id": "c0", "name": "read_content_range", "result": page},
                {"tool_call_id": "c1", "name": "get_report", "result": _big_payload()},
            ],
            _chat_config(_SMALL_CAP_MODEL),
            store_scope=(store, scope),
            internal=("read_content_range",),
        )

        budget = processed["metadata"]["message_context_budget"]
        assert budget["messages"][-1]["parts"][0]["estimated_tokens"] > budget["threshold_tokens"]
        entries = processed["messages"][-1]["tool_results"]
        assert entries[0]["result"] == page and "artifact_marker" not in entries[0]
        assert entries[1]["artifact_marker"] is True
        assert store.statistics()["puts"] == 1


class TestInternalToolResultLogging:
    @pytest.mark.asyncio
    async def test_internal_tool_results_are_logged_with_clipped_text_and_visible_metadata(self, caplog):
        long_text = "442: fastforward_timer.cpiBase [fs]," + "0," * 5000
        grep_result = {"text": long_text, "match_count": 12, "total_lines": 4986, "truncated": False}
        error_result = {"error": "artifact_unavailable", "message": "This artifact is not available."}
        results = [
            {"tool_call_id": "c0", "name": "grep_content", "result": grep_result},
            {"tool_call_id": "c1", "name": "read_content_range", "result": error_result},
            {"tool_call_id": "c2", "name": "get_report", "result": {"body": "http result text " * 50}},
        ]
        with caplog.at_level("DEBUG", logger="autolangchat"):
            await _run_tools_node(
                results, _chat_config(_LARGE_CAP_MODEL), internal=("grep_content", "read_content_range")
            )

        lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Internal tool result")]
        assert len(lines) == 2  # one per internal tool result, none for the HTTP tool
        grep_line, error_line = lines
        assert grep_line.startswith("Internal tool result grep_content (call c0, ")
        # The text is cut short, but the fields after it stay visible.
        assert "442: fastforward_timer.cpiBase" in grep_line and "...(+" in grep_line
        assert '"match_count": 12' in grep_line and '"total_lines": 4986' in grep_line
        assert len(grep_line) < 800 and long_text not in grep_line
        assert '"error": "artifact_unavailable"' in error_line
        assert not any("http result text" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_no_preview_is_built_unless_debug_is_enabled(self, caplog):
        results = [{"tool_call_id": "c0", "name": "grep_content", "result": {"text": "x" * 5000}}]
        with caplog.at_level("INFO", logger="autolangchat"):
            await _run_tools_node(results, _chat_config(_LARGE_CAP_MODEL), internal=("grep_content",))

        assert not any(r.getMessage().startswith("Internal tool result") for r in caplog.records)


class TestSpillEndToEnd:
    @pytest.mark.asyncio
    async def test_model_reads_back_a_spilled_result(self):
        """llm -> HTTP tool (oversized) -> marker -> llm -> read_content_range(handle) -> llm, real stack."""
        import re

        from autolangchat.graph.tools.artifact_tools import READ_CONTENT_RANGE, build_artifact_tools
        from autolangchat.graph.tools.generator import ToolsGenerator
        from autolangchat.graph.tools.manager import ToolManager
        from tests.http_stubs import stub_http

        chat_config = _chat_config(_SMALL_CAP_MODEL)
        internal_tools = build_artifact_tools()
        generator = ToolsGenerator(
            openapi_spec=_JOBS_SPEC, config=chat_config, reserved_names=[t.name for t in internal_tools]
        )
        tm = ToolManager(
            generated_tools=generator._generated_tools,
            config=chat_config,
            base_url="http://test-api",
            internal_tools=internal_tools,
        )
        stub_http(tm, (200, _big_payload()))
        store, scope = _live_scope("thread-spill-e2e")

        model_inputs: List[Any] = []

        def reply(lc_messages, *args, **kwargs):
            model_inputs.append(lc_messages)
            if len(model_inputs) == 1:
                return _make_ai_message_with_tools([{"name": "get_jobs", "args": {}, "id": "j1", "type": "tool_call"}])
            if len(model_inputs) == 2:
                handle = re.search(r"'artifact_handle': '([0-9a-f-]{36})'", lc_messages[-1].content).group(1)
                args = {"handle": handle, "start_line": 1, "max_lines": 3}
                return _make_ai_message_with_tools(
                    [{"name": READ_CONTENT_RANGE, "args": args, "id": "r1", "type": "tool_call"}]
                )
            return _make_ai_message_plain("It has 2500 rows.")

        def llm_factory(*args, **kwargs):
            inst = MagicMock()
            inst.bind_tools.return_value = inst
            inst.ainvoke = AsyncMock(side_effect=reply)
            inst.astream = _make_empty_astream()
            return inst

        with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse", side_effect=llm_factory):
            graph = build_chat_graph(chat_config, tool_manager=tm)
            result = await graph.ainvoke(
                {"user_message": "how many rows?"},
                config={
                    "configurable": {
                        "thread_id": "thread-spill-e2e",
                        "artifact_store": store,
                        "artifact_scope": scope,
                    }
                },
            )

        spilled, page = [m["tool_results"][0] for m in result["messages"] if m["role"] == "tool"]
        marker = spilled["result"]
        assert spilled["artifact_marker"] is True and marker["read_hint"]
        # The model saw the marker (with the handle and how to read it), not the 2,500 rows.
        assert marker["artifact_handle"] in model_inputs[1][-1].content
        assert "read_content_range(handle" in model_inputs[1][-1].content
        assert "item-1999" not in json.dumps(result["messages"], default=str)
        # The handle in the marker is enough to page the original.
        assert page["name"] == READ_CONTENT_RANGE and "artifact_marker" not in page
        assert page["result"]["text"].startswith("1: {")
        assert page["result"]["total_lines"] == store.get_meta(scope, marker["artifact_handle"])["total_lines"] > 10_000
        assert page["result"]["has_more"] is True
        assert result["metadata"]["tool_call_rounds"] == 2
        assert result["messages"][-1]["content"] == "It has 2500 rows."


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


class TestFileDownloadEndToEnd:
    """The shape of the job file download endpoint: ``{"file_content": "<text>"}`` (XMGPLAT-11846)."""

    @pytest.mark.asyncio
    async def test_artifact_is_the_downloaded_file_and_is_read_by_line(self, caplog):
        import re

        from autolangchat.graph.tools.artifact_tools import GREP_CONTENT, READ_CONTENT_RANGE, build_artifact_tools
        from autolangchat.graph.tools.generator import ToolsGenerator
        from autolangchat.graph.tools.manager import ToolManager
        from tests.http_stubs import stub_http

        # ~1 MB of wide stats lines, as in a sim.stats download.
        file_text = "\n".join(
            f"cpu{i % 64}.counter_{i:05d}".ljust(60) + "# event counter detail " * 8 for i in range(5000)
        )
        file_bytes = len(file_text.encode())
        assert file_bytes > 900_000

        chat_config = _chat_config(_LARGE_CAP_MODEL)
        internal_tools = build_artifact_tools()
        generator = ToolsGenerator(
            openapi_spec=_JOBS_SPEC, config=chat_config, reserved_names=[t.name for t in internal_tools]
        )
        tm = ToolManager(
            generated_tools=generator._generated_tools,
            config=chat_config,
            base_url="http://test-api",
            internal_tools=internal_tools,
        )
        stub_http(tm, (200, {"file_content": file_text}))
        store, scope = _live_scope("thread-file")

        model_inputs: List[Any] = []

        def reply(lc_messages, *args, **kwargs):
            model_inputs.append(lc_messages)
            step = len(model_inputs)
            if step == 1:
                return _make_ai_message_with_tools([{"name": "get_jobs", "args": {}, "id": "d1", "type": "tool_call"}])
            handle = re.search(r"'artifact_handle': '([0-9a-f-]{36})'", model_inputs[1][-1].content).group(1)
            if step == 2:
                args = {"handle": handle, "start_line": 100, "max_lines": 2}
                call = {"name": READ_CONTENT_RANGE, "args": args, "id": "r1", "type": "tool_call"}
            elif step == 3:
                args = {"handle": handle, "pattern": "counter_04321", "context_lines": 1}
                call = {"name": GREP_CONTENT, "args": args, "id": "g1", "type": "tool_call"}
            else:
                return _make_ai_message_plain("done")
            return _make_ai_message_with_tools([call])

        def llm_factory(*args, **kwargs):
            inst = MagicMock()
            inst.bind_tools.return_value = inst
            inst.ainvoke = AsyncMock(side_effect=reply)
            inst.astream = _make_empty_astream()
            return inst

        with caplog.at_level("DEBUG", logger="autolangchat"):
            with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse", side_effect=llm_factory):
                graph = build_chat_graph(chat_config, tool_manager=tm)
                result = await graph.ainvoke(
                    {"user_message": "analyze sim.stats"},
                    config={
                        "configurable": {
                            "thread_id": "thread-file",
                            "artifact_store": store,
                            "artifact_scope": scope,
                        }
                    },
                )

        downloaded, page, hit = [m["tool_results"][0] for m in result["messages"] if m["role"] == "tool"]
        marker = downloaded["result"]
        handle = marker["artifact_handle"]
        # The artifact matches the downloaded file, byte for byte and line for line.
        assert marker["content_field"] == "file_content" and marker["content_type"] == "text/plain"
        assert marker["size_bytes"] == file_bytes == store.statistics()["bytes"]
        assert "line_count" not in marker and page["result"]["total_lines"] == 5000
        assert store.read_region(scope, handle, max_lines=1)["text"] == file_text.split("\n")[0] + "\n"
        # The model saw a small marker with real file lines, never the ~1 MB original...
        marker_text = model_inputs[1][-1].content
        assert len(marker_text) < 3_000 and "cpu0.counter_00000" in marker_text and "read_content_range(" in marker_text
        assert all(len(m.content) < 60_000 for step in model_inputs for m in step)
        assert not any(r.getMessage().startswith("Truncating message") for r in caplog.records)
        # ...and could read and search it by line.
        assert page["result"]["text"].startswith("100: cpu35.counter_00099")
        assert "4322: cpu33.counter_04321" in hit["result"]["text"]
        # The artifact id can be located in the DEBUG logs (the spill's and the store's own line), never above them.
        assert any(handle in r.getMessage() and r.name.endswith("spill") for r in caplog.records)
        assert any(r.getMessage() == f"Stored artifact, size {file_bytes} bytes, id {handle}" for r in caplog.records)
        assert not any(handle in r.getMessage() for r in caplog.records if r.levelname != "DEBUG")
        # The one estimate is logged before the original is stored, and each internal tool response is shown.
        records = [(r.name, r.getMessage()) for r in caplog.records]
        estimate = next(
            i
            for i, (_, m) in enumerate(records)
            if m.startswith("Message context estimate") and "exceeds_threshold=True" in m
        )
        stored = next(i for i, (n, m) in enumerate(records) if n == "autolangchat.artifacts" and "Stored" in m)
        spilled = next(i for i, (_, m) in enumerate(records) if m.startswith("Spilled tool result"))
        assert estimate < stored < spilled
        assert "parts=[get_jobs: " in records[estimate][1] and "kind=" in records[estimate][1]
        assert not any(m.startswith("Tool result context estimate") for _, m in records)
        read_line, grep_line = [m for _, m in records if m.startswith("Internal tool result")]
        assert "read_content_range" in read_line and "100: cpu35.counter_00099" in read_line
        assert '"total_lines": 5000' in read_line
        assert "grep_content" in grep_line and "4322: cpu33.counter_04321" in grep_line
        assert '"match_count": 1' in grep_line
