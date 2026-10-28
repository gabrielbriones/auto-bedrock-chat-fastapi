"""In-process internal tools: the ``InternalTool`` / ``ToolContext`` seam."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import autolangchat.artifacts as artifacts_module
from autolangchat.artifacts import ArtifactScope, ArtifactStore
from autolangchat.defaults import (
    ARTIFACT_READ_MAX_BYTES,
    ARTIFACT_READ_MAX_LINES,
    ARTIFACT_SEARCH_REGEX_MAX_LINE_CHARS,
)
from autolangchat.graph.tools.artifact_tools import (
    GREP_ARTIFACT_ALIAS,
    GREP_CONTENT,
    READ_ARTIFACT_RANGE_ALIAS,
    READ_CONTENT_RANGE,
    artifact_tool_names,
    build_artifact_tools,
    grep_content,
    read_content_range,
)
from autolangchat.graph.tools.internal import (
    ARTIFACT_UNAVAILABLE,
    CONTEXT_EXHAUSTED,
    INVALID_ARGUMENTS,
    TOOL_UNAVAILABLE,
    InternalTool,
    ToolContext,
    tool_error,
)
from tests.http_stubs import stub_http


def _desc(name="echo"):
    return {
        "name": name,
        "description": "Echo the arguments back.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    }


def _scope():
    return ArtifactScope(connection_id="connection", thread_id="thread", user_id="alice", generation=1)


def _ctx(store=None, scope=None):
    return ToolContext(artifact_store=store, artifact_scope=scope, context_budget=None, chat_config=None)


class TestInternalToolConstruction:
    def test_defaults(self):
        tool = InternalTool(name="echo", function_desc=_desc(), handler=AsyncMock())

        assert tool.requires_session is True
        assert tool.exposed is True

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="must not be empty"):
            InternalTool(name="", function_desc=_desc(""), handler=AsyncMock())

    def test_rejects_name_mismatch(self):
        with pytest.raises(ValueError, match="does not match"):
            InternalTool(name="echo", function_desc=_desc("other"), handler=AsyncMock())

    def test_rejects_missing_parameters(self):
        desc = {"name": "echo", "description": "No schema."}

        with pytest.raises(ValueError, match="parameters"):
            InternalTool(name="echo", function_desc=desc, handler=AsyncMock())

    def test_is_immutable(self):
        tool = InternalTool(name="echo", function_desc=_desc(), handler=AsyncMock())

        with pytest.raises(AttributeError):
            tool.name = "changed"


class TestInternalToolInvoke:
    async def test_passes_arguments_and_context_to_handler(self):
        handler = AsyncMock(return_value={"text": "hi"})
        tool = InternalTool(name="echo", function_desc=_desc(), handler=handler)
        ctx = _ctx(store=ArtifactStore(), scope=_scope())

        result = await tool.invoke({"text": "hi"}, ctx)

        assert result == {"text": "hi"}
        handler.assert_awaited_once_with({"text": "hi"}, ctx)

    @pytest.mark.parametrize(
        "store, scope",
        [(None, None), (ArtifactStore(), None), (None, _scope())],
        ids=["no-store-no-scope", "no-scope", "no-store"],
    )
    async def test_session_tool_without_session_is_unavailable(self, store, scope):
        handler = AsyncMock()
        tool = InternalTool(name="echo", function_desc=_desc(), handler=handler)

        result = await tool.invoke({"text": "hi"}, _ctx(store=store, scope=scope))

        assert result["error"] == TOOL_UNAVAILABLE
        assert "echo" in result["message"]
        handler.assert_not_awaited()

    async def test_sessionless_tool_runs_without_session(self):
        handler = AsyncMock(return_value="ok")
        tool = InternalTool(name="echo", function_desc=_desc(), handler=handler, requires_session=False)

        assert await tool.invoke({"text": "hi"}, _ctx()) == "ok"
        handler.assert_awaited_once()

    async def test_handler_exceptions_propagate(self):
        handler = AsyncMock(side_effect=RuntimeError("boom"))
        tool = InternalTool(name="echo", function_desc=_desc(), handler=handler, requires_session=False)

        with pytest.raises(RuntimeError, match="boom"):
            await tool.invoke({}, _ctx())


def test_tool_error_shape():
    assert tool_error("invalid_arguments", "bad range") == {"error": "invalid_arguments", "message": "bad range"}


# ---------------------------------------------------------------------------
# ToolManager integration: schemas, binding and dispatch
# ---------------------------------------------------------------------------

_HTTP_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/jobs": {
            "get": {"operationId": "list_jobs", "summary": "List jobs", "responses": {"200": {"description": "OK"}}}
        },
        "/echo": {"get": {"operationId": "echo", "summary": "HTTP echo", "responses": {"200": {"description": "OK"}}}},
    },
}


def _manager(internal_tools=(), spec=None):
    from autolangchat.config import ChatConfig
    from autolangchat.graph.tools.generator import ToolsGenerator
    from autolangchat.graph.tools.manager import ToolManager

    config = ChatConfig(model_id="test-model", excluded_paths=[])
    generator = ToolsGenerator(openapi_spec=spec or _HTTP_SPEC, config=config)
    manager = ToolManager(
        generated_tools=generator._generated_tools,
        config=config,
        base_url="http://test-api",
        internal_tools=internal_tools,
    )
    # Fail loudly instead of reaching the network; tests that need HTTP install stub_http.
    manager._http_client = httpx.AsyncClient(transport=httpx.MockTransport(_no_http))
    return manager, config


def _no_http(request):
    raise AssertionError(f"internal tools must not hit HTTP: {request.method} {request.url}")


def _tool(name="echo", handler=None, **kwargs):
    return InternalTool(name=name, function_desc=_desc(name), handler=handler or AsyncMock(return_value="ok"), **kwargs)


class TestToolManagerInternalTools:
    def test_without_internal_tools_schemas_are_unchanged(self):
        manager, config = _manager()

        assert {f["name"] for f in config.tools_desc["functions"]} == {"list_jobs", "echo"}
        assert manager.internal_tools == {}

    def test_exposed_tools_are_in_tools_desc_and_langchain_tools(self):
        manager, config = _manager([_tool("read_content_range")])

        names = [f["name"] for f in config.tools_desc["functions"]]
        assert "read_content_range" in names
        assert "read_content_range" in {t.name for t in config.langchain_tools}
        assert manager.get_statistics()["internal_tools_available"] == 1

    def test_hidden_aliases_are_not_bound(self):
        manager, config = _manager([_tool("read_content_range"), _tool("read_artifact_range", exposed=False)])

        assert "read_artifact_range" not in {f["name"] for f in config.tools_desc["functions"]}
        assert "read_artifact_range" not in {t.name for t in config.langchain_tools}
        assert "read_artifact_range" in manager.internal_tools

    def test_internal_name_reserves_over_http_tool(self, caplog):
        with caplog.at_level("WARNING"):
            manager, config = _manager([_tool("echo")])

        echo_descs = [f for f in config.tools_desc["functions"] if f["name"] == "echo"]
        assert echo_descs == [_desc("echo")]
        assert [t.name for t in config.langchain_tools].count("echo") == 1
        assert manager.get_statistics()["total_tools_available"] == 1
        assert "reserved by an internal tool" in caplog.text

    def test_duplicate_internal_names_rejected(self):
        with pytest.raises(ValueError, match="Duplicate internal tool name"):
            _manager([_tool("echo"), _tool("echo")])

    def test_internal_tools_survive_tool_refresh(self):
        manager, config = _manager([_tool("read_content_range")])

        manager._langchain_tools = None
        manager._sync_config()

        assert "read_content_range" in {f["name"] for f in config.tools_desc["functions"]}

    async def test_execute_tool_calls_dispatches_internal_with_context(self):
        handler = AsyncMock(return_value={"lines": "1: hi"})
        manager, _ = _manager([_tool("echo", handler)])
        requests = stub_http(manager, (200, {}))
        ctx = _ctx(store=ArtifactStore(), scope=_scope())

        results = await manager.execute_tool_calls(
            [{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}], tool_context=ctx
        )

        assert results == [{"tool_call_id": "c1", "name": "echo", "result": {"lines": "1: hi"}}]
        handler.assert_awaited_once_with({"text": "hi"}, ctx)
        assert requests == []

    async def test_execute_tool_calls_without_context_is_unavailable(self):
        handler = AsyncMock()
        manager, _ = _manager([_tool("echo", handler)])

        results = await manager.execute_tool_calls([{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}])

        assert results[0]["result"]["error"] == TOOL_UNAVAILABLE
        handler.assert_not_awaited()

    async def test_execute_tool_calls_validates_required_arguments(self):
        handler = AsyncMock()
        manager, _ = _manager([_tool("echo", handler)])

        results = await manager.execute_tool_calls([{"id": "c1", "name": "echo", "arguments": {}}])

        assert "Missing required arguments" in results[0]["error"]
        handler.assert_not_awaited()

    async def test_execute_tool_calls_reports_handler_exception(self):
        manager, _ = _manager([_tool("echo", AsyncMock(side_effect=RuntimeError("boom")), requires_session=False)])
        events = []

        async def on_event(event):
            events.append(event)

        results = await manager.execute_tool_calls(
            [{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}], on_tool_event=on_event
        )

        assert results[0]["error"] == "boom"
        assert [e["type"] for e in events] == ["tool_call_start", "tool_call_end"]
        assert events[-1]["status"] == "error"

    async def test_execute_tool_calls_mixes_internal_and_http(self):
        manager, _ = _manager([_tool("read_content_range", requires_session=False)])
        requests = stub_http(manager, (200, {"jobs": []}))

        results = await manager.execute_tool_calls(
            [
                {"id": "c1", "name": "list_jobs", "arguments": {}},
                {"id": "c2", "name": "read_content_range", "arguments": {"text": "x"}},
            ]
        )

        assert [r["result"] for r in results] == [{"jobs": []}, "ok"]
        assert [r.url.path for r in requests] == ["/jobs"]

    async def test_execute_tool_calls_dispatches_hidden_alias(self):
        handler = AsyncMock(return_value="alias-ok")
        manager, _ = _manager([_tool("grep_artifact", handler, exposed=False, requires_session=False)])

        results = await manager.execute_tool_calls([{"id": "c1", "name": "grep_artifact", "arguments": {"text": "x"}}])

        assert results[0]["result"] == "alias-ok"

    async def test_internal_calls_count_toward_max_tool_calls(self):
        manager, _ = _manager([_tool("echo", requires_session=False)])
        manager._config = manager._config.model_copy(update={"max_tool_calls": 1})

        results = await manager.execute_tool_calls(
            [{"id": f"c{i}", "name": "echo", "arguments": {"text": "x"}} for i in range(2)]
        )

        assert results[0]["result"] == "ok"
        assert "max_tool_calls" in results[1]["error"]

    async def test_call_tool_dispatches_internal(self):
        handler = AsyncMock(return_value="ok")
        manager, _ = _manager([_tool("echo", handler)])
        requests = stub_http(manager, (200, {}))
        ctx = _ctx(store=ArtifactStore(), scope=_scope())

        assert await manager.call_tool("echo", {"text": "hi"}, tool_context=ctx) == "ok"
        handler.assert_awaited_once_with({"text": "hi"}, ctx)
        assert requests == []

    async def test_call_tool_without_context_is_unavailable(self):
        manager, _ = _manager([_tool("echo")])

        result = await manager.call_tool("echo", {"text": "hi"})

        assert result["error"] == TOOL_UNAVAILABLE

    async def test_call_tool_missing_arguments_raises(self):
        from autolangchat.exceptions import ToolError

        manager, _ = _manager([_tool("echo")])

        with pytest.raises(ToolError, match="Missing required arguments"):
            await manager.call_tool("echo", {})

    async def test_langchain_wrapper_invokes_internal_without_session(self):
        handler = AsyncMock()
        _, config = _manager([_tool("read_content_range", handler)])
        lc_tool = next(t for t in config.langchain_tools if t.name == "read_content_range")

        result = await lc_tool.ainvoke({"text": "hi"})

        assert TOOL_UNAVAILABLE in result
        handler.assert_not_awaited()


# ---------------------------------------------------------------------------
# tools_execution_node: ToolContext built from config["configurable"]
# ---------------------------------------------------------------------------


def _live_scope():
    store = ArtifactStore()
    store.register_connection("connection", "alice")
    return store, store.activate("connection", "thread", "alice")


def _node_state(*names):
    tool_calls = [{"name": n, "args": {"text": "hi"}, "id": f"c{i}", "type": "tool_call"} for i, n in enumerate(names)]
    return {
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Read the artifact."},
            {"role": "assistant", "content": "", "tool_calls": tool_calls},
        ]
    }


async def _run_node(manager, chat_config, *names, **configurable):
    from autolangchat.graph.tools.tool_node import tools_execution_node

    config = {"configurable": {"tool_manager": manager, "chat_config": chat_config, **configurable}}
    update = await tools_execution_node(_node_state(*names), config)
    return update["messages"][-1]["tool_results"]


class TestToolsExecutionNodeContext:
    async def test_live_scope_reaches_handler_with_budget(self):
        from autolangchat.context_budget import ContextBudget

        handler = AsyncMock(return_value="ok")
        manager, chat_config = _manager([_tool("echo", handler)])
        store, scope = _live_scope()

        results = await _run_node(manager, chat_config, "echo", artifact_store=store, artifact_scope=scope)

        assert results[0]["result"] == "ok"
        ctx = handler.await_args.args[1]
        assert ctx.artifact_store is store
        assert ctx.artifact_scope == scope
        assert ctx.chat_config is chat_config
        assert isinstance(ctx.context_budget, ContextBudget)
        assert ctx.context_budget.message_tokens > 0

    async def test_no_scope_returns_tool_unavailable(self):
        handler = AsyncMock()
        manager, chat_config = _manager([_tool("echo", handler)])

        results = await _run_node(manager, chat_config, "echo")

        assert results[0]["result"]["error"] == TOOL_UNAVAILABLE
        handler.assert_not_awaited()

    @pytest.mark.parametrize("which", ["store-only", "scope-only", "wrong-types"])
    async def test_incomplete_artifact_context_fails_closed(self, which, caplog):
        handler = AsyncMock()
        manager, chat_config = _manager([_tool("echo", handler)])
        store, scope = _live_scope()
        configurable = {
            "store-only": {"artifact_store": store},
            "scope-only": {"artifact_scope": scope},
            "wrong-types": {"artifact_store": object(), "artifact_scope": {"connection_id": "connection"}},
        }[which]

        with caplog.at_level("WARNING", logger="autolangchat.graph.tools.tool_node"):
            results = await _run_node(manager, chat_config, "echo", **configurable)

        assert results[0]["result"]["error"] == TOOL_UNAVAILABLE
        handler.assert_not_awaited()
        assert "incomplete artifact context" in caplog.text

    async def test_artifacts_disabled_drops_scope(self):
        handler = AsyncMock()
        manager, chat_config = _manager([_tool("echo", handler)])
        store, scope = _live_scope()
        disabled = chat_config.model_copy(update={"artifact_enabled": False})

        results = await _run_node(manager, disabled, "echo", artifact_store=store, artifact_scope=scope)

        assert results[0]["result"]["error"] == TOOL_UNAVAILABLE
        handler.assert_not_awaited()

    async def test_sessionless_tool_runs_without_scope(self):
        manager, chat_config = _manager([_tool("echo", requires_session=False)])

        results = await _run_node(manager, chat_config, "echo")

        assert results[0]["result"] == "ok"

    async def test_http_only_round_skips_budget(self, monkeypatch):
        from autolangchat.graph.tools import tool_node

        for_turn = MagicMock()
        monkeypatch.setattr(tool_node.ContextBudget, "for_turn", for_turn)
        manager, chat_config = _manager([_tool("read_content_range")])
        stub_http(manager, (200, {"jobs": []}))

        results = await _run_node(manager, chat_config, "list_jobs")

        assert results[0]["result"] == {"jobs": []}
        for_turn.assert_not_called()

    async def test_budget_failure_still_runs_tool(self, monkeypatch, caplog):
        from autolangchat.graph.tools import tool_node

        monkeypatch.setattr(tool_node.ContextBudget, "for_turn", MagicMock(side_effect=RuntimeError("no model")))
        handler = AsyncMock(return_value="ok")
        manager, chat_config = _manager([_tool("echo", handler)])
        store, scope = _live_scope()

        with caplog.at_level("WARNING", logger="autolangchat.graph.tools.tool_node"):
            results = await _run_node(manager, chat_config, "echo", artifact_store=store, artifact_scope=scope)

        assert results[0]["result"] == "ok"
        assert handler.await_args.args[1].context_budget is None
        assert "context budget" in caplog.text


# ---------------------------------------------------------------------------
# read_content_range / grep_content handlers
# ---------------------------------------------------------------------------


def _budget(chars):
    """A ContextBudget stand-in whose allowance is ``chars`` for every content kind."""
    return SimpleNamespace(allowance_for=lambda kind: chars)


def _artifact_ctx(text, *, budget=None, config=None):
    store, scope = _live_scope()
    handle = store.put(scope, text, {"source": "tool_result"})
    return ToolContext(artifact_store=store, artifact_scope=scope, context_budget=budget, chat_config=config), handle


async def _page_bytes(ctx, handle, offset):
    pieces = []
    while offset is not None:
        page = await read_content_range({"handle": handle, "start_byte": offset}, ctx)
        if ctx.context_budget is not None:
            assert len(json.dumps(page)) <= ctx.context_budget.allowance_for("json")
        pieces.append(page["text"])
        offset = page["next_start_byte"]
    return "".join(pieces)


class TestReadContentRange:
    async def test_small_artifact_is_returned_numbered(self):
        ctx, handle = _artifact_ctx("a\nb\n")

        # LangChain's StructuredTool passes omitted optional arguments as None.
        result = await read_content_range(
            {"handle": handle, "start_line": None, "max_lines": None, "start_byte": None, "max_bytes": None}, ctx
        )

        assert result == {
            "text": "1: a\n2: b",
            "start_line": 1,
            "end_line": 2,
            "total_lines": 2,
            "has_more": False,
            "next_start_line": None,
        }

    async def test_default_page_is_200_lines(self):
        ctx, handle = _artifact_ctx("".join(f"line {n}\n" for n in range(1, 301)))

        result = await read_content_range({"handle": handle}, ctx)

        assert result["text"].split("\n")[0] == "1: line 1"
        assert (result["end_line"], result["next_start_line"], result["has_more"]) == (200, 201, True)
        assert result["total_lines"] == 300

    async def test_pages_through_artifact_and_each_page_fits_the_budget(self):
        text = "".join(f"row {n} {'é' * (n % 7)}\t\"q\"\n" for n in range(1, 1201))
        allowance = 4000
        ctx, handle = _artifact_ctx(text, budget=_budget(allowance))
        seen, start, pages = [], 1, 0

        while start is not None:
            page = await read_content_range({"handle": handle, "start_line": start, "max_lines": 500}, ctx)
            assert len(json.dumps(page)) <= allowance
            seen.extend(page["text"].split("\n"))
            pages += 1
            start = page["next_start_line"]

        assert seen == [f"{n}: {line}" for n, line in enumerate(text.splitlines(), 1)]
        assert pages > 1200 // 500

    async def test_empty_artifact(self):
        ctx, handle = _artifact_ctx("")

        assert await read_content_range({"handle": handle}, ctx) == {
            "text": "",
            "start_line": 1,
            "end_line": 0,
            "total_lines": 0,
            "has_more": False,
            "next_start_line": None,
        }
        past_end = await read_content_range({"handle": handle, "start_line": 2}, ctx)
        assert past_end["error"] == INVALID_ARGUMENTS
        empty_bytes = await read_content_range({"handle": handle, "start_byte": 0}, ctx)
        assert (empty_bytes["text"], empty_bytes["has_more"], empty_bytes["total_bytes"]) == ("", False, 0)

    async def test_start_line_past_end_is_invalid(self):
        ctx, handle = _artifact_ctx("a\nb\n")

        result = await read_content_range({"handle": handle, "start_line": 3}, ctx)

        assert result["error"] == INVALID_ARGUMENTS
        assert "2 lines" in result["message"]

    @pytest.mark.parametrize(
        "arguments",
        [
            {"start_line": 0},
            {"start_line": True},
            {"start_line": "2"},
            {"start_line": 1.5},
            {"max_lines": 0},
            {"max_lines": ARTIFACT_READ_MAX_LINES + 1},
            {"start_byte": -1},
            {"max_bytes": 3},
            {"max_bytes": ARTIFACT_READ_MAX_BYTES + 1},
            {"start_byte": 4},
            {"start_line": 1, "start_byte": 0},
            {"max_lines": 5, "max_bytes": 100},
            {"bogus": 1},
            {"handle": ""},
            {"handle": 5},
        ],
    )
    async def test_invalid_arguments_are_rejected_without_failed_reads(self, arguments):
        ctx, handle = _artifact_ctx("abc")

        result = await read_content_range({"handle": handle, **arguments}, ctx)

        assert result["error"] == INVALID_ARGUMENTS
        assert ctx.artifact_store.statistics()["failed_reads"] == 0

    async def test_configured_caps_reject_larger_requests(self):
        config = SimpleNamespace(artifact_read_max_lines=50, artifact_read_max_bytes=1000)
        ctx, handle = _artifact_ctx("".join(f"{'x' * 30}\n" for _ in range(100)), config=config)

        over_lines = await read_content_range({"handle": handle, "max_lines": 51}, ctx)
        over_bytes = await read_content_range({"handle": handle, "max_bytes": 1001}, ctx)
        default = await read_content_range({"handle": handle}, ctx)

        assert over_lines["error"] == over_bytes["error"] == INVALID_ARGUMENTS
        assert "between 1 and 50" in over_lines["message"]
        # 1000 bytes fit 32 lines of 31 bytes; the default page asks for min(200, 50).
        assert (default["end_line"], default["next_start_line"]) == (32, 33)
        assert "line_truncated" not in default

    async def test_long_line_after_other_lines_starts_the_next_page(self):
        config = SimpleNamespace(artifact_read_max_lines=ARTIFACT_READ_MAX_LINES, artifact_read_max_bytes=100)
        ctx, handle = _artifact_ctx("short\n" + "x" * 500 + "\nend\n", config=config)

        first = await read_content_range({"handle": handle}, ctx)
        second = await read_content_range({"handle": handle, "start_line": first["next_start_line"]}, ctx)

        assert (first["text"], first["next_start_line"], "line_truncated" in first) == ("1: short", 2, False)
        assert second["text"] == "2: " + "x" * 100
        assert second["line_truncated"] is True
        assert (second["next_start_byte"], second["next_start_line"]) == (106, 3)
        assert "start_byte=106" in second["hint"] and "start_line=3" in second["hint"]

    async def test_giant_single_line_pages_by_bytes(self):
        text = '{"items": [' + ", ".join(str(n) for n in range(30_000)) + "]}"
        ctx, handle = _artifact_ctx(text)

        first = await read_content_range({"handle": handle}, ctx)

        assert first["line_truncated"] is True
        assert (first["has_more"], first["next_start_line"]) == (True, None)
        assert first["next_start_byte"] == ARTIFACT_READ_MAX_BYTES
        assert first["text"].startswith('1: {"items"')
        assert first["text"][3:] + await _page_bytes(ctx, handle, first["next_start_byte"]) == text

    async def test_escaped_content_is_trimmed_to_the_budget(self):
        text = '\x00\u00e9\U0001f600"' * 3000
        allowance = 2000
        ctx, handle = _artifact_ctx(text, budget=_budget(allowance))

        first = await read_content_range({"handle": handle}, ctx)

        assert len(json.dumps(first)) <= allowance
        assert first["line_truncated"] is True
        assert first["text"][3:] + await _page_bytes(ctx, handle, first["next_start_byte"]) == text

    async def test_byte_mode_snaps_to_character_boundaries(self):
        ctx, handle = _artifact_ctx("a\u00e9\u20ac")

        result = await read_content_range({"handle": handle, "start_byte": 2, "max_bytes": 4}, ctx)

        assert result == {
            "text": "\u00e9",
            "start_byte": 1,
            "end_byte": 3,
            "total_bytes": 6,
            "has_more": True,
            "next_start_byte": 3,
        }

    async def test_binary_and_control_characters_are_predictable(self):
        ctx, handle = _artifact_ctx("\x00\x01bin\x1cnext\r\n\ufffd\n")

        result = await read_content_range({"handle": handle}, ctx)

        assert result["text"] == "1: \x00\x01bin\n2: next\n3: \ufffd"
        assert result["total_lines"] == 3
        assert json.loads(json.dumps(result)) == result


def _arguments(tool, handle):
    return {"handle": handle, "pattern": "a"} if tool is grep_content else {"handle": handle}


@pytest.mark.parametrize("tool", [read_content_range, grep_content])
class TestArtifactToolErrors:
    @pytest.mark.parametrize("unavailable", ["unknown", "other_thread", "foreign", "expired"])
    async def test_unavailable_handles_share_one_neutral_error(self, monkeypatch, tool, unavailable):
        clock = [0]
        monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
        store = ArtifactStore(max_age_seconds=10)
        store.register_connection("connection", "alice")
        scope = store.activate("connection", "thread", "alice")
        handle = store.put(scope, "secret alice data", {})
        if unavailable == "unknown":
            handle = "not-a-handle"
        elif unavailable == "other_thread":
            scope = store.activate("connection", "other-thread", "alice")
        elif unavailable == "foreign":
            store.register_connection("other", "bob")
            scope = store.activate("other", "thread", "bob")
        else:
            clock[0] = 10
        ctx = ToolContext(artifact_store=store, artifact_scope=scope, context_budget=None, chat_config=None)

        result = await tool(_arguments(tool, handle), ctx)

        assert result == tool_error(ARTIFACT_UNAVAILABLE, result["message"])
        assert result["message"] == (
            "This artifact is not available. It may have expired; re-run the tool that produced it to get a new handle."
        )
        assert store.statistics()["failed_reads"] == 1

    async def test_context_exhausted_before_touching_the_store(self, tool):
        ctx, _ = _artifact_ctx("abc", budget=_budget(600))

        result = await tool(_arguments(tool, "not-a-handle"), ctx)

        assert result["error"] == CONTEXT_EXHAUSTED
        assert ctx.artifact_store.statistics()["failed_reads"] == 0

    async def test_without_session_is_unavailable(self, tool):
        name = READ_CONTENT_RANGE if tool is read_content_range else GREP_CONTENT
        [internal] = [t for t in build_artifact_tools() if t.name == name]

        result = await internal.invoke(_arguments(tool, "h"), _ctx())

        assert result["error"] == TOOL_UNAVAILABLE


class TestGrepContent:
    async def test_literal_case_insensitive_by_default(self):
        ctx, handle = _artifact_ctx("Error: a.c\nerror abc\nok\n")

        result = await grep_content({"handle": handle, "pattern": "ERROR"}, ctx)
        literal_dot = await grep_content({"handle": handle, "pattern": "a.c"}, ctx)

        assert result == {"text": "1: Error: a.c\n2: error abc", "match_count": 2, "total_lines": 3, "truncated": False}
        assert literal_dot["text"] == "1: Error: a.c"

    async def test_regex_is_opt_in(self):
        ctx, handle = _artifact_ctx("Error: a.c\nerror abc\nok\n")

        result = await grep_content({"handle": handle, "pattern": "A.C$", "regex": True}, ctx)

        assert result["text"] == "1: Error: a.c\n2: error abc"

    async def test_context_lines_are_grouped_grep_style(self):
        text = "".join(f"line {n}{' hit' if n in (3, 5, 12) else ''}\n" for n in range(1, 16))
        ctx, handle = _artifact_ctx(text)

        result = await grep_content({"handle": handle, "pattern": "hit", "context_lines": 1}, ctx)

        assert result["text"] == (
            "2- line 2\n3: line 3 hit\n4- line 4\n5: line 5 hit\n6- line 6\n--\n"
            "11- line 11\n12: line 12 hit\n13- line 13"
        )
        assert result["match_count"] == 3

    async def test_max_matches_sets_truncated_with_hint(self):
        ctx, handle = _artifact_ctx("hit\n" * 5)

        result = await grep_content({"handle": handle, "pattern": "hit", "max_matches": 2}, ctx)

        assert (result["match_count"], result["truncated"]) == (2, True)
        assert "narrow the pattern" in result["hint"]

    @pytest.mark.parametrize(
        "arguments",
        [
            {"pattern": ""},
            {"pattern": 5},
            {"pattern": "a" * 201, "regex": True},
            {"pattern": "(", "regex": True},
            {"pattern": "a", "regex": "true"},
            {"pattern": "a", "context_lines": -1},
            {"pattern": "a", "context_lines": 21},
            {"pattern": "a", "max_matches": 0},
            {"pattern": "a", "max_matches": 201},
            {"pattern": "a", "bogus": 1},
        ],
    )
    async def test_invalid_arguments_are_rejected_without_failed_reads(self, arguments):
        ctx, handle = _artifact_ctx("a\n")

        result = await grep_content({"handle": handle, **arguments}, ctx)

        assert result["error"] == INVALID_ARGUMENTS
        assert ctx.artifact_store.statistics()["failed_reads"] == 0

    async def test_long_literal_pattern_is_allowed(self):
        ctx, handle = _artifact_ctx("a" * 300 + "\n")

        result = await grep_content({"handle": handle, "pattern": "A" * 250}, ctx)

        assert result["match_count"] == 1

    async def test_output_is_trimmed_to_the_budget(self):
        allowance = 3000
        ctx, handle = _artifact_ctx(("hit " + "\u00e9" * 100 + "\n") * 200, budget=_budget(allowance))

        result = await grep_content({"handle": handle, "pattern": "hit", "max_matches": 200}, ctx)

        assert len(json.dumps(result)) <= allowance
        assert 0 < result["match_count"] < 200
        assert result["truncated"] is True

    async def test_giant_lines_bound_regex_but_not_literal_search(self):
        ctx, handle = _artifact_ctx("x" * (ARTIFACT_SEARCH_REGEX_MAX_LINE_CHARS + 10) + "needle\n")

        regex = await grep_content({"handle": handle, "pattern": "needle", "regex": True}, ctx)
        literal = await grep_content({"handle": handle, "pattern": "needle"}, ctx)

        assert (regex["match_count"], regex["partially_searched_lines"]) == (0, 1)
        assert "literal pattern" in regex["hint"]
        assert literal["match_count"] == 1
        assert literal["text"].endswith("needle [line clipped]")
        assert "partially_searched_lines" not in literal


async def test_artifact_tools_dispatch_through_tool_manager():
    manager, config = _manager(build_artifact_tools())
    store, scope = _live_scope()
    handle = store.put(scope, "alpha\nbeta\n", {})
    ctx = ToolContext(artifact_store=store, artifact_scope=scope, context_budget=None, chat_config=config)

    results = await manager.execute_tool_calls(
        [
            {"id": "c1", "name": READ_CONTENT_RANGE, "arguments": {"handle": handle}},
            {"id": "c2", "name": GREP_CONTENT, "arguments": {"handle": handle, "pattern": "BETA"}},
        ],
        tool_context=ctx,
    )

    assert results[0]["result"]["text"] == "1: alpha\n2: beta"
    assert results[1]["result"]["text"] == "2: beta"
    assert {READ_CONTENT_RANGE, GREP_CONTENT} <= {f["name"] for f in config.tools_desc["functions"]}


# ---------------------------------------------------------------------------
# Hidden aliases
# ---------------------------------------------------------------------------


class TestArtifactToolAliases:
    def test_aliases_are_registered_but_hidden(self):
        tools = {t.name: t for t in build_artifact_tools()}

        assert set(tools) == {READ_CONTENT_RANGE, GREP_CONTENT, READ_ARTIFACT_RANGE_ALIAS, GREP_ARTIFACT_ALIAS}
        assert {name for name, t in tools.items() if t.exposed} == {READ_CONTENT_RANGE, GREP_CONTENT}
        assert tools[READ_ARTIFACT_RANGE_ALIAS].handler is tools[READ_CONTENT_RANGE].handler
        assert tools[GREP_ARTIFACT_ALIAS].handler is tools[GREP_CONTENT].handler
        assert all(t.requires_session for t in tools.values())

    def test_aliases_can_be_left_out(self):
        assert {t.name for t in build_artifact_tools(include_aliases=False)} == {READ_CONTENT_RANGE, GREP_CONTENT}
        assert artifact_tool_names(include_aliases=False) == {READ_CONTENT_RANGE, GREP_CONTENT}
        assert artifact_tool_names() == {
            READ_CONTENT_RANGE,
            GREP_CONTENT,
            READ_ARTIFACT_RANGE_ALIAS,
            GREP_ARTIFACT_ALIAS,
        }

    async def test_aliases_dispatch_to_the_same_handlers(self):
        manager, config = _manager(build_artifact_tools())
        store, scope = _live_scope()
        handle = store.put(scope, "alpha\nbeta\n", {})
        ctx = ToolContext(artifact_store=store, artifact_scope=scope, context_budget=None, chat_config=config)

        results = await manager.execute_tool_calls(
            [
                {"id": "c1", "name": READ_CONTENT_RANGE, "arguments": {"handle": handle}},
                {"id": "c2", "name": READ_ARTIFACT_RANGE_ALIAS, "arguments": {"handle": handle}},
                {"id": "c3", "name": GREP_CONTENT, "arguments": {"handle": handle, "pattern": "beta"}},
                {"id": "c4", "name": GREP_ARTIFACT_ALIAS, "arguments": {"handle": handle, "pattern": "beta"}},
            ],
            tool_context=ctx,
        )

        assert results[0]["result"] == results[1]["result"]
        assert results[2]["result"] == results[3]["result"]
        bound = {f["name"] for f in config.tools_desc["functions"]} | {t.name for t in config.langchain_tools}
        assert not bound & {READ_ARTIFACT_RANGE_ALIAS, GREP_ARTIFACT_ALIAS}


# ---------------------------------------------------------------------------
# AutoLangChatPlugin registration
# ---------------------------------------------------------------------------


def _plugin(artifact_enabled):
    """A bare plugin with only the tool wiring from ``__init__``.

    Like the other plugin tests, this skips the full constructor, which
    validates AWS credentials and builds Bedrock clients and stores that tool
    registration does not need.
    """
    from fastapi import FastAPI

    from autolangchat.config import ChatConfig
    from autolangchat.plugin import AutoLangChatPlugin

    app = FastAPI()

    @app.get("/jobs", operation_id="list_jobs")
    def list_jobs():
        return []

    @app.get("/read", operation_id=READ_CONTENT_RANGE)
    def colliding_read():
        return []

    plugin = object.__new__(AutoLangChatPlugin)
    plugin.app = app
    plugin.config = ChatConfig(
        model_id="test-model", AUTOCHAT_ENABLE_UI=False, AUTOCHAT_ARTIFACT_ENABLED=artifact_enabled
    )
    plugin._init_tools()
    return plugin


def _bound_names(plugin):
    return [f["name"] for f in plugin.config.tools_desc["functions"]], {t.name for t in plugin.config.langchain_tools}


class TestPluginRegistration:
    async def test_enabled_registers_tools_and_reserves_names(self, caplog):
        with caplog.at_level("WARNING", logger="autolangchat.graph.tools.generator"):
            plugin = _plugin(True)

        described, bound = _bound_names(plugin)
        assert described.count(READ_CONTENT_RANGE) == 1
        assert {"list_jobs", READ_CONTENT_RANGE, GREP_CONTENT} <= set(described)
        assert {READ_CONTENT_RANGE, GREP_CONTENT} <= bound
        assert not (set(described) | bound) & {READ_ARTIFACT_RANGE_ALIAS, GREP_ARTIFACT_ALIAS}
        assert set(plugin.tool_manager.internal_tools) == artifact_tool_names()
        # The colliding OpenAPI operation was skipped; the name routes to the internal tool.
        assert plugin._tools_generator.get_tool_metadata(READ_CONTENT_RANGE) is None
        assert "GET /read" in caplog.text

    async def test_update_tools_adds_internal_tools_back(self):
        plugin = _plugin(True)

        await plugin.update_tools()

        described, bound = _bound_names(plugin)
        assert described.count(READ_CONTENT_RANGE) == 1
        assert {"list_jobs", READ_CONTENT_RANGE, GREP_CONTENT} <= set(described)
        assert {READ_CONTENT_RANGE, GREP_CONTENT} <= bound
        assert plugin._tools_generator.get_tool_metadata(READ_CONTENT_RANGE) is None

    async def test_disabled_registers_nothing_and_reserves_nothing(self):
        plugin = _plugin(False)

        described, bound = _bound_names(plugin)
        assert dict(plugin.tool_manager.internal_tools) == {}
        assert GREP_CONTENT not in described and GREP_CONTENT not in bound
        # Without the internal tool the OpenAPI operation keeps its name.
        assert plugin._tools_generator.get_tool_metadata(READ_CONTENT_RANGE) is not None


async def test_invalid_utf8_reads_and_searches_predictably():
    # Tool responses are decoded with replacement before archiving, so bad bytes arrive as U+FFFD.
    text = b"ok \xff\xfe bytes\nend\n".decode("utf-8", errors="replace")
    ctx, handle = _artifact_ctx(text)

    page = await read_content_range({"handle": handle}, ctx)
    found = await grep_content({"handle": handle, "pattern": "\ufffd"}, ctx)
    raw = await read_content_range({"handle": handle, "start_byte": 0, "max_bytes": 5}, ctx)

    assert page["text"] == "1: ok \ufffd\ufffd bytes\n2: end"
    assert (found["match_count"], found["text"]) == (1, "1: ok \ufffd\ufffd bytes")
    # Byte paging never splits the 3-byte replacement character.
    assert (raw["text"], raw["next_start_byte"]) == ("ok ", 3)
