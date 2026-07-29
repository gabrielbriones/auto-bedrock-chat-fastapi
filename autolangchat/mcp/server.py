"""MCP Streamable HTTP server — pure tool provider built on existing tool stack.

This module adapts the existing, domain-agnostic ``ToolsGenerator`` /
``ToolManager`` (autolangchat/graph/tools/) to the MCP ``tools/list`` /
``tools/call`` handlers, using the official ``mcp`` Python SDK's low-level
``Server`` and Streamable HTTP transport.

Deliberately thin: no static per-tool registration, no code generation. The
tool set exposed here is exactly whatever ``ToolsGenerator`` already compiled
from the configured OpenAPI spec — the same tools available to the
LangGraph/Bedrock chat loop, just reachable via a different transport.

The LangGraph/Bedrock orchestration loop (``graph/graph.py``) is
intentionally NOT reused here: MCP clients bring their own LLM and
tool-calling loop, so this endpoint is a pure tool provider.

Usage (wired automatically by ``plugin.py`` when ``config.mcp_enabled`` is
True; see ``AutoLangChatPlugin._setup_mcp_routes`` and ``_do_startup``)::

    server = build_mcp_server(tools_generator, tool_manager)
    session_manager = build_mcp_session_manager(server)
    # session_manager.run() must be entered once for the app's lifetime
    # (see plugin.py's startup/shutdown); handle_request is mounted as the
    # ASGI endpoint for the configured ``mcp_endpoint`` path.
"""

import logging
from typing import Any, Dict, List, Optional

from ..graph.tools.generator import ToolsGenerator
from ..graph.tools.manager import ToolManager
from .auth import build_auth_info_from_headers

logger = logging.getLogger(__name__)

try:
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "The MCP Python SDK is required for MCP server support. " "Install with: pip install autolangchat[mcp]"
    ) from _exc


def _build_mcp_tool_list(tools_generator: ToolsGenerator) -> List["types.Tool"]:
    """Convert ``ToolsGenerator.tools_desc`` into MCP ``Tool`` schema objects.

    Split out from ``build_mcp_server`` so the OpenAPI-derived-schema ->
    MCP-schema mapping can be unit tested without going through the full
    ``Server`` request-dispatch machinery.
    """
    functions = tools_generator.tools_desc.get("functions", [])
    return [
        types.Tool(
            name=fn["name"],
            description=fn.get("description", ""),
            inputSchema=fn.get("parameters") or {"type": "object", "properties": {}},
        )
        for fn in functions
    ]


def build_mcp_server(
    tools_generator: ToolsGenerator,
    tool_manager: ToolManager,
    name: str = "autolangchat",
    sso_session_store: Optional[Any] = None,
    sso_session_secret: Optional[str] = None,
) -> "Server":
    """Build an MCP ``Server`` wired to the existing tool-generation/execution stack.

    Registers:
    - ``tools/list``, backed by ``ToolsGenerator.tools_desc``.
    - ``tools/call``, backed by ``ToolManager.call_tool``. Auth is extracted
      per-request from the incoming HTTP headers (leg 1: MCP client -> MCP
      endpoint) via ``build_auth_info_from_headers`` and forwarded unchanged
      to the target REST API (leg 2) — see ``mcp/auth.py``. Exceptions
      raised by ``call_tool`` (unknown tool, missing arguments, HTTP
      failures) are caught by the SDK's ``call_tool`` decorator and surfaced
      as an MCP ``CallToolResult`` with ``isError=True`` — no manual error
      mapping needed here.

    Args:
        sso_session_store: ``SSOSessionStore`` instance (shared with the web
            UI), enabling recognition of SSO session tokens presented as
            ``Authorization: Bearer <session_token>``. ``None`` (the
            default) disables SSO recognition — every Bearer token is then
            treated as a plain bearer token, same as Phase 4.
        sso_session_secret: Signing secret for validating SSO session tokens
            (``config.sso_session_secret``). Required alongside
            ``sso_session_store`` for SSO recognition to take effect.
    """
    server = Server(name)

    @server.list_tools()
    async def _list_tools() -> List["types.Tool"]:
        return _build_mcp_tool_list(tools_generator)

    @server.call_tool()
    async def _call_tool(tool_name: str, arguments: Dict[str, Any]) -> Any:
        auth_info = None
        try:
            request = server.request_context.request
        except LookupError:
            request = None
        if request is not None and getattr(request, "headers", None) is not None:
            auth_info = build_auth_info_from_headers(
                request.headers,
                http_client=tool_manager.http_client,
                sso_session_store=sso_session_store,
                sso_session_secret=sso_session_secret,
            )

        return await tool_manager.call_tool(tool_name, arguments, auth_info=auth_info)

    return server


def build_mcp_session_manager(server: "Server", *, stateless: bool = True) -> "StreamableHTTPSessionManager":
    """Wrap an MCP ``Server`` in a Streamable HTTP session manager.

    ``stateless=True`` (the default) matches the "pure tool provider" model:
    each JSON-RPC request is handled independently with no persisted MCP
    session state, consistent with the per-request auth validation planned
    for the static auth types (bearer/basic/api key/oauth2/custom headers).
    The SSO auth path needs its own session store (``SSOSessionStore``)
    layered on top of this — orthogonal to this transport-level setting.
    """
    return StreamableHTTPSessionManager(app=server, stateless=stateless)
