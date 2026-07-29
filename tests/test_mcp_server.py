"""Tests for the MCP (Model Context Protocol) server adapter (XMGPLAT-11065).

Verifies the ``ToolsGenerator``/``ToolManager`` -> MCP ``tools/list`` /
``tools/call`` mapping in ``autolangchat/mcp/server.py``. Requires the
optional ``mcp`` extra (``pip install autolangchat[mcp]``) — the whole module
is skipped if it isn't installed.
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

mcp = pytest.importorskip("mcp", reason="mcp SDK not installed (optional [mcp] extra)")
from mcp import types  # noqa: E402

from autolangchat.config import ChatConfig  # noqa: E402
from autolangchat.exceptions import ToolError  # noqa: E402
from autolangchat.graph.tools.generator import ToolsGenerator  # noqa: E402
from autolangchat.mcp.server import _build_mcp_tool_list, build_mcp_server, build_mcp_session_manager  # noqa: E402

_SPEC: Dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/jobs": {
            "get": {
                "operationId": "list_jobs",
                "summary": "List all jobs",
                "parameters": [
                    {
                        "name": "status",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string", "description": "Filter by job status"},
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/jobs/{job_id}": {
            "get": {
                "operationId": "get_job",
                "summary": "Get a single job by ID",
                "parameters": [
                    {
                        "name": "job_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "description": "The job identifier"},
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
    },
}


def _make_generator() -> ToolsGenerator:
    config = ChatConfig(model_id="test-model", excluded_paths=[])
    return ToolsGenerator(openapi_spec=_SPEC, config=config)


class _FakeToolManager:
    """Minimal stand-in for ``ToolManager`` — only ``call_tool``/``http_client`` are exercised here."""

    def __init__(self):
        self.call_tool = AsyncMock(return_value={"ok": True})
        self.http_client = MagicMock(name="http_client")


class TestBuildMcpToolList:
    def test_converts_tools_desc_to_mcp_tools(self):
        generator = _make_generator()
        tools = _build_mcp_tool_list(generator)

        assert {t.name for t in tools} == {"list_jobs", "get_job"}
        assert all(isinstance(t, types.Tool) for t in tools)

    def test_input_schema_matches_generated_parameters(self):
        generator = _make_generator()
        tools = _build_mcp_tool_list(generator)

        get_job = next(t for t in tools if t.name == "get_job")
        assert get_job.inputSchema["type"] == "object"
        assert "job_id" in get_job.inputSchema["properties"]
        assert "job_id" in get_job.inputSchema["required"]


class TestBuildMcpServer:
    @pytest.mark.asyncio
    async def test_list_tools_handler_returns_generated_tools(self):
        generator = _make_generator()
        tool_manager = _FakeToolManager()
        server = build_mcp_server(generator, tool_manager)

        handler = server.request_handlers[types.ListToolsRequest]
        result = await handler(types.ListToolsRequest())

        tool_names = {t.name for t in result.root.tools}
        assert tool_names == {"list_jobs", "get_job"}

    @pytest.mark.asyncio
    async def test_call_tool_handler_delegates_to_tool_manager(self):
        generator = _make_generator()
        tool_manager = _FakeToolManager()
        server = build_mcp_server(generator, tool_manager)

        handler = server.request_handlers[types.CallToolRequest]
        request = types.CallToolRequest(params=types.CallToolRequestParams(name="list_jobs", arguments={}))
        result = await handler(request)

        # No request context is active when calling the handler directly
        # (outside of server.run()), so auth_info is None -- see
        # TestCallToolAuthWiring below for the request-context-aware path.
        tool_manager.call_tool.assert_awaited_once_with("list_jobs", {}, auth_info=None)
        assert result.root.isError is False
        assert result.root.structuredContent == {"ok": True}

    @pytest.mark.asyncio
    async def test_call_tool_handler_surfaces_tool_error_as_error_result(self):
        generator = _make_generator()
        tool_manager = _FakeToolManager()
        tool_manager.call_tool = AsyncMock(side_effect=ToolError("Unknown tool: does_not_exist"))
        server = build_mcp_server(generator, tool_manager)

        handler = server.request_handlers[types.CallToolRequest]
        request = types.CallToolRequest(params=types.CallToolRequestParams(name="does_not_exist", arguments={}))
        result = await handler(request)

        assert result.root.isError is True
        assert "Unknown tool" in result.root.content[0].text


class TestCallToolAuthWiring:
    """Verifies ``_call_tool`` extracts auth from the active MCP request context.

    Simulates ``server.run()`` having set the ``request_ctx`` contextvar (the
    SDK's own mechanism for exposing per-request data to handlers) so these
    tests exercise the real header -> ``AuthInfo`` -> ``ToolManager.call_tool``
    path without needing a full HTTP round trip.
    """

    @staticmethod
    def _set_request_context(headers):
        from mcp.server.lowlevel.server import request_ctx
        from mcp.shared.context import RequestContext

        class _FakeHttpRequest:
            def __init__(self, headers):
                self.headers = headers

        token = request_ctx.set(
            RequestContext(
                request_id="1",
                meta=None,
                session=None,
                lifespan_context=None,
                request=_FakeHttpRequest(headers),
            )
        )
        return token

    @pytest.mark.asyncio
    async def test_bearer_token_header_flows_to_auth_info(self):
        from mcp.server.lowlevel.server import request_ctx

        generator = _make_generator()
        tool_manager = _FakeToolManager()
        server = build_mcp_server(generator, tool_manager)
        handler = server.request_handlers[types.CallToolRequest]
        request = types.CallToolRequest(params=types.CallToolRequestParams(name="list_jobs", arguments={}))

        token = self._set_request_context({"Authorization": "Bearer tok123"})
        try:
            await handler(request)
        finally:
            request_ctx.reset(token)

        _, kwargs = tool_manager.call_tool.call_args
        assert kwargs["auth_info"].credentials.bearer_token == "tok123"

    @pytest.mark.asyncio
    async def test_no_recognized_headers_yields_none_auth_info(self):
        from mcp.server.lowlevel.server import request_ctx

        generator = _make_generator()
        tool_manager = _FakeToolManager()
        server = build_mcp_server(generator, tool_manager)
        handler = server.request_handlers[types.CallToolRequest]
        request = types.CallToolRequest(params=types.CallToolRequestParams(name="list_jobs", arguments={}))

        token = self._set_request_context({"Content-Type": "application/json"})
        try:
            await handler(request)
        finally:
            request_ctx.reset(token)

        _, kwargs = tool_manager.call_tool.call_args
        assert kwargs["auth_info"] is None


class TestBuildMcpSessionManager:
    def test_returns_session_manager_wrapping_server(self):
        generator = _make_generator()
        tool_manager = _FakeToolManager()
        server = build_mcp_server(generator, tool_manager)

        session_manager = build_mcp_session_manager(server)

        assert session_manager.app is server
        assert session_manager.stateless is True

    def test_stateless_flag_can_be_overridden(self):
        generator = _make_generator()
        tool_manager = _FakeToolManager()
        server = build_mcp_server(generator, tool_manager)

        session_manager = build_mcp_session_manager(server, stateless=False)

        assert session_manager.stateless is False
