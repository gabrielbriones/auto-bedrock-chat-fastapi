"""Tool Manager — owns tool execution via HTTP and in-process internal tools.

This module provides:

- **Tool execution**: ``ToolManager`` executes tool calls via HTTP, applying
  authentication from an ``AuthInfo`` dataclass.  Calls whose name matches a
  registered ``InternalTool`` are dispatched to its in-process handler instead.
- **Authentication**: ``AuthInfo`` dataclass for transport-agnostic auth state.

Tool generation (OpenAPI spec parsing, LangChain wrapping) is handled by
``ToolsGenerator`` in ``generator.py``.  ``ToolManager`` receives the parsed
tool metadata dict and only concerns itself with dispatch.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional

import httpx

from ...auth_handler import AuthenticationHandler, Credentials
from ...config import ChatConfig
from ...exceptions import ToolError, ToolHTTPError
from .bounded_decoding import ACCEPT_ENCODING, iter_decoded_chunks
from .internal import InternalTool, ToolContext

logger = logging.getLogger(__name__)

# Upper bound on how much of an HTTP error body is read; only the first 500
# characters end up in ToolHTTPError.details.
_ERROR_BODY_READ_MAX_BYTES = 4096


@dataclass
class _RawResponse:
    """A fully-read (possibly size-capped) HTTP response body."""

    status_code: int
    content_type: str
    encoding: str
    body: bytes
    truncated: bool

    def text(self) -> str:
        try:
            return self.body.decode(self.encoding, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# AuthInfo — lightweight, session-free authentication data
# ---------------------------------------------------------------------------


@dataclass
class AuthInfo:
    """Authentication information for tool call execution.

    This is a transport-agnostic representation of auth state.  The WebSocket
    handler (or any other caller) builds an ``AuthInfo`` from the session's
    ``Credentials`` / ``AuthenticationHandler`` before passing it to
    ``ToolManager`` or the LangGraph ``tools_node``.

    Attributes:
        credentials: The ``Credentials`` dataclass (bearer token, API key, etc.).
        auth_handler: The ``AuthenticationHandler`` that knows how to apply
            ``credentials`` to HTTP request headers.
        metadata: Session metadata dict containing user info, display name, etc.
    """

    credentials: Optional[Credentials] = None
    auth_handler: Optional[AuthenticationHandler] = None
    metadata: Optional[Dict[str, Any]] = None

    @property
    def is_authenticated(self) -> bool:
        """Return ``True`` if usable credentials are present."""
        if self.credentials is None:
            return False
        auth_type_str = self.credentials.get_auth_type_string()
        return auth_type_str != "none"


# ---------------------------------------------------------------------------
# ToolManager
# ---------------------------------------------------------------------------


class ToolManager:
    """Executes tool calls via HTTP and syncs ``ChatConfig`` on construction.

    Receives the parsed tool metadata from ``ToolsGenerator`` and handles
    HTTP dispatch, authentication, and progress reporting.  On construction
    ``config.tools_desc`` and ``config.langchain_tools`` are populated
    automatically so the rest of the plugin needs no additional wiring.

    Args:
        generated_tools: Dict ``{func_name: {path, method, operation,
            function_desc}}`` produced by ``ToolsGenerator._generated_tools``.
        config: Application configuration (``ChatConfig`` instance).
        base_url: Resolved API base URL for HTTP calls.
        internal_tools: In-process tools dispatched by name before HTTP.
            Their names are reserved: a generated tool with the same name is
            logged and left out of the bound schemas. Pass the same names as
            ``ToolsGenerator(reserved_names=...)`` so collisions are dropped at
            generation time and never reach MCP either. Tools with
            ``exposed=False`` (aliases) can be dispatched but are never bound.

    Example::

        generator = ToolsGenerator(app=app, config=config)
        tool_manager = ToolManager(
            generated_tools=generator._generated_tools,
            config=config,
            base_url=generator.get_api_base_url(),
        )
        # config.tools_desc and config.langchain_tools are already set
    """

    def __init__(
        self,
        *,
        generated_tools: Dict[str, Any],
        config: Optional[ChatConfig] = None,
        base_url: str = "http://localhost:8000",
        internal_tools: Optional[Iterable[InternalTool]] = None,
    ):
        self._generated_tools = generated_tools
        self._internal_tools: Dict[str, InternalTool] = {}
        for tool in internal_tools or ():
            if tool.name in self._internal_tools:
                raise ValueError(f"Duplicate internal tool name: {tool.name!r}")
            self._internal_tools[tool.name] = tool
        self._config = config or ChatConfig()
        self._base_url = base_url.rstrip("/")
        self._http_client = httpx.AsyncClient(timeout=self._config.timeout)
        self._langchain_tools: Optional[List[Any]] = None
        self._total_tool_calls_executed: int = 0
        logger.info(
            "ToolManager ready: %d tools, %d internal tools",
            len(generated_tools),
            len(self._internal_tools),
        )
        self._sync_config()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        """The resolved API base URL."""
        return self._base_url

    @property
    def internal_tools(self) -> Mapping[str, InternalTool]:
        """Read-only view of registered internal tools (including hidden aliases), by name."""
        return MappingProxyType(self._internal_tools)

    def _http_tools(self, *, log_collisions: bool = False) -> Dict[str, Any]:
        """Generated HTTP tools, minus any whose name is reserved by an internal tool."""
        if not self._internal_tools:
            return self._generated_tools
        tools: Dict[str, Any] = {}
        for name, meta in self._generated_tools.items():
            if name in self._internal_tools:
                if log_collisions:
                    logger.warning("Skipping generated tool %r: name is reserved by an internal tool", name)
                continue
            tools[name] = meta
        return tools

    def _exposed_internal_tools(self) -> List[InternalTool]:
        return [t for t in self._internal_tools.values() if t.exposed]

    @property
    def http_client(self) -> httpx.AsyncClient:
        """The shared ``httpx.AsyncClient`` used for tool-call HTTP dispatch.

        Exposed so other transports (e.g. the MCP ``tools/call`` handler's
        OAuth2 Client Credentials token exchange) can reuse the same
        connection pool instead of opening a new one per request.
        """
        return self._http_client

    def generate_langchain_tools(self) -> List[Any]:
        """Return LangChain ``StructuredTool`` objects for every generated tool.

        Built once on first call and cached. Cleared when ``_generated_tools``
        is replaced (e.g. after ``update_tools()``).
        """
        if self._langchain_tools is not None:
            return self._langchain_tools

        try:
            from langchain_core.tools import StructuredTool
        except ImportError:  # pragma: no cover
            raise ImportError("langchain-core is required. Install with: pip install langchain-core")

        import json as _json

        from pydantic import Field, create_model

        _JSON_TYPE_TO_PYTHON: Dict[str, type] = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        def _make_tool(func_name: str, fn_desc: Dict, invoke: Callable[[Dict[str, Any]], Awaitable[Any]]) -> Any:
            parameters = fn_desc.get("parameters", {})
            properties = parameters.get("properties", {})
            required_params = set(parameters.get("required", []))

            field_defs: Dict[str, Any] = {}
            for p_name, p_schema in properties.items():
                py_type = _JSON_TYPE_TO_PYTHON.get(p_schema.get("type", "string"), str)
                desc = p_schema.get("description", p_name)
                if p_name in required_params:
                    field_defs[p_name] = (py_type, Field(..., description=desc))
                else:
                    from typing import Optional as _Optional

                    field_defs[p_name] = (_Optional[py_type], Field(None, description=desc))

            args_model = create_model(f"{func_name}_args", **field_defs)

            async def _invoke(**kwargs: Any) -> str:
                result = await invoke(kwargs)
                if isinstance(result, str):
                    return result
                return _json.dumps(result)

            return StructuredTool.from_function(
                coroutine=_invoke,
                name=func_name,
                description=fn_desc.get("description", f"Call {func_name}"),
                args_schema=args_model,
            )

        def _http_invoker(tool_meta: Dict[str, Any]) -> Callable[[Dict[str, Any]], Awaitable[Any]]:
            return lambda kwargs: self._execute_single_tool_call(tool_meta, kwargs, auth_info=None)

        def _internal_invoker(tool: InternalTool) -> Callable[[Dict[str, Any]], Awaitable[Any]]:
            # Invoked outside the graph there is no per-turn context, so
            # session-bound tools fail closed with tool_unavailable.
            return lambda kwargs: tool.invoke(kwargs, _empty_tool_context(self._config))

        self._langchain_tools = [
            _make_tool(func_name, tool_meta["function_desc"], _http_invoker(tool_meta))
            for func_name, tool_meta in self._http_tools().items()
        ] + [_make_tool(t.name, t.function_desc, _internal_invoker(t)) for t in self._exposed_internal_tools()]
        logger.info("Generated %d LangChain tools", len(self._langchain_tools))
        return self._langchain_tools

    def _sync_config(self) -> None:
        """Populate ``config.tools_desc`` and ``config.langchain_tools`` from current state.

        Includes the schemas of exposed internal tools so ``bind_tools`` sees them.
        """
        tools_desc = {
            "type": "function",
            "functions": [t["function_desc"] for t in self._http_tools(log_collisions=True).values()]
            + [t.function_desc for t in self._exposed_internal_tools()],
        }
        self._config.tools_desc = tools_desc
        self._config.langchain_tools = self.generate_langchain_tools()

    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        auth_info: Optional[AuthInfo] = None,
        tool_context: Optional[ToolContext] = None,
    ) -> Any:
        """Execute a single named tool call and return its raw result.

        Thin public wrapper around ``_execute_single_tool_call`` for callers
        outside the LangGraph/Bedrock batch loop (e.g. the MCP ``tools/call``
        handler) that invoke one tool by name and need either the raw result
        or a raised exception. Unlike ``execute_tool_calls()`` — which is
        batch-oriented, applies ``max_tool_calls`` capping, and reports
        failures as ``{"error": ...}`` dict entries instead of raising — this
        method performs a single lookup/validation/dispatch and lets
        exceptions propagate so callers can map them to their own error
        format (e.g. a JSON-RPC error response).

        Internal tools (including hidden aliases) are matched by name before
        the HTTP tools and run in-process with ``tool_context``.

        Args:
            name: Registered tool name (an internal tool or a key in
                ``_generated_tools``).
            arguments: Arguments for the tool call.
            auth_info: Optional authentication data for the HTTP request.
            tool_context: Per-call context for internal tools. When omitted,
                session-bound internal tools return ``tool_unavailable``.

        Returns:
            The parsed response (JSON dict, list, or string) from the tool's
            HTTP endpoint. A response over ``config.tool_response_max_bytes``
            is returned as a flagged ``{"truncated": True, ...}`` dict with the
            text read so far (see ``_execute_single_tool_call``).

        Raises:
            ToolError: If ``name`` is not a registered tool, or required
                arguments are missing.
        """
        internal_tool = self._internal_tools.get(name)
        if internal_tool is not None:
            fn_desc = internal_tool.function_desc
        else:
            tool_metadata = self._generated_tools.get(name)
            if not tool_metadata:
                raise ToolError(f"Unknown tool: {name}")
            fn_desc = tool_metadata.get("function_desc", {})

        required = fn_desc.get("parameters", {}).get("required", [])
        missing = [p for p in required if p not in arguments]
        if missing:
            raise ToolError(f"Missing required arguments for tool {name}: {missing}")

        self._total_tool_calls_executed += 1
        if internal_tool is not None:
            return await internal_tool.invoke(arguments, tool_context or _empty_tool_context(self._config))
        return await self._execute_single_tool_call(tool_metadata, arguments, auth_info)

    async def execute_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        auth_info: Optional[AuthInfo] = None,
        on_progress: Optional[Callable] = None,
        on_tool_event: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        tool_context: Optional[ToolContext] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a list of tool calls concurrently.

        Each tool call is validated, then (if valid) dispatched concurrently
        via ``asyncio.gather()``: internal tools run their in-process handler
        with ``tool_context``; all others become an HTTP request to the
        appropriate API endpoint. Authentication headers are applied from
        ``auth_info`` when provided.

        Args:
            tool_calls: List of tool call dicts, each containing ``id``,
                ``name``, and ``arguments``.
            auth_info: Optional authentication data to apply to HTTP headers.
            on_progress: Optional async callback ``(message: str) -> None``
                invoked for each valid tool call, before execution starts,
                for progress reporting.
            on_tool_event: Optional async callback receiving structured
                ``tool_call_start`` / ``tool_call_end`` event dicts, so a client
                can render each call live as it starts and as it finishes.
            tool_context: Per-turn context for internal tools. When omitted,
                session-bound internal tools return ``tool_unavailable``.

        Returns:
            List of result dicts (in the original ``tool_calls`` order), each
            containing ``tool_call_id``, ``name``, and either ``result`` or
            ``error``.
        """
        limit = self._config.max_tool_calls
        if limit is None:
            capped_calls = tool_calls
            skipped_calls: List[Dict[str, Any]] = []
        else:
            capped_calls = tool_calls[:limit]
            skipped_calls = tool_calls[limit:]
        total_tools = len(capped_calls)

        if skipped_calls:
            logger.warning(
                f"max_tool_calls cap ({limit}) exceeded: "
                f"{len(skipped_calls)} tool call(s) will be skipped and returned as errors "
                f"to satisfy the model's toolResult requirement."
            )

        # Pre-allocate result slots so ordering is preserved regardless of
        # completion order once tasks are gathered concurrently.
        results: List[Optional[Dict[str, Any]]] = [None] * total_tools
        pending_indices: List[int] = []
        pending_coros: List[Any] = []

        async def _emit(event: Dict[str, Any]) -> None:
            if on_tool_event is None:
                return
            try:
                await on_tool_event(event)
            except Exception as e:
                logger.error(f"Error in on_tool_event callback for {event.get('name')}: {e}")

        async def _emit_start(tool_call: Dict[str, Any], position: int) -> None:
            await _emit(
                {
                    "type": "tool_call_start",
                    "tool_call_id": tool_call.get("id"),
                    "name": tool_call.get("name"),
                    "arguments": tool_call.get("arguments", {}),
                    "position": position,
                    "total": len(tool_calls),
                }
            )

        async def _emit_end(tool_call: Dict[str, Any], error: Optional[str], started: float) -> None:
            await _emit(
                {
                    "type": "tool_call_end",
                    "tool_call_id": tool_call.get("id"),
                    "name": tool_call.get("name"),
                    "status": "success" if error is None else "error",
                    "error": error,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )

        async def _run_and_report(tool_call: Dict[str, Any], coro: Awaitable[Any]) -> Any:
            # Equivalent to gather(return_exceptions=True) for this call, but reports
            # each call's completion as soon as it lands instead of once all have.
            started = time.monotonic()
            try:
                outcome = await coro
            except asyncio.CancelledError as e:
                await _emit_end(tool_call, str(e) or "Cancelled", started)
                raise
            except Exception as e:
                await _emit_end(tool_call, str(e) or type(e).__name__, started)
                raise
            await _emit_end(tool_call, None, started)
            return outcome

        for i, tool_call in enumerate(capped_calls):
            position = i + 1
            function_name = tool_call.get("name")
            self._total_tool_calls_executed += 1

            # Validate: tool exists (internal tools take precedence over HTTP)
            internal_tool = self._internal_tools.get(function_name)
            tool_metadata = None if internal_tool is not None else self._generated_tools.get(function_name)
            if internal_tool is None and not tool_metadata:
                logger.warning(f"Unknown tool requested: {function_name}")
                results[i] = {
                    "tool_call_id": tool_call.get("id"),
                    "name": function_name,
                    "error": f"Unknown tool: {function_name}",
                }
                await _emit_start(tool_call, position)
                await _emit_end(tool_call, results[i]["error"], time.monotonic())
                continue

            # Validate: required arguments present
            arguments = tool_call.get("arguments", {})
            fn_desc = (
                internal_tool.function_desc if internal_tool is not None else tool_metadata.get("function_desc", {})
            )
            required = fn_desc.get("parameters", {}).get("required", [])
            missing = [p for p in required if p not in arguments]
            if missing:
                logger.warning(f"Missing required args for tool {function_name}: {missing}")
                results[i] = {
                    "tool_call_id": tool_call.get("id"),
                    "name": function_name,
                    "error": f"Missing required arguments: {missing}",
                }
                await _emit_start(tool_call, position)
                await _emit_end(tool_call, results[i]["error"], time.monotonic())
                continue

            logger.debug(f"Executing tool call {position}/{total_tools}: {tool_call}")

            # Progress is reported before scheduling, in original call order,
            # so messages stay meaningful even though execution below happens
            # concurrently.
            if on_progress is not None:
                try:
                    await on_progress(f"Calling {function_name}... ({position}/{total_tools})")
                except Exception as e:
                    logger.error(f"Error in on_progress callback for {function_name}: {e}")

            await _emit_start(tool_call, position)
            pending_indices.append(i)
            if internal_tool is not None:
                coro = internal_tool.invoke(arguments, tool_context or _empty_tool_context(self._config))
            else:
                coro = self._execute_single_tool_call(tool_metadata, arguments, auth_info)
            pending_coros.append(_run_and_report(tool_call, coro))

        if pending_coros:
            outcomes = await asyncio.gather(*pending_coros, return_exceptions=True)
            for idx, outcome in zip(pending_indices, outcomes):
                tool_call = capped_calls[idx]
                function_name = tool_call.get("name")
                result_entry: Dict[str, Any] = {"tool_call_id": tool_call.get("id"), "name": function_name}
                # _execute_single_tool_call() raises ToolHTTPError for an HTTP
                # >=400 response, or lets a network-level exception propagate --
                # both are real Exception instances here (asyncio.gather's own
                # return_exceptions=True), so they're identified by type, never
                # by sniffing dict keys (which would risk misclassifying an
                # arbitrary successful JSON payload reusing the same key names,
                # e.g. {"status_code": 200, "data": ...}).
                if isinstance(outcome, ToolHTTPError):
                    logger.error(f"Error executing tool call {function_name}: {outcome}")
                    # Preserve the legacy (pre-auth-expiration-feature) shape --
                    # the HTTP error nested under "result" as exactly {"error",
                    # "details"}, with no other keys -- as every caller/the LLM
                    # already saw it, so auth_expiration_behaviour="none" (and
                    # any other caller not opted into the new reactive-mode
                    # handling) keeps 100% the same tool_msg content. status_code
                    # is only surfaced at the top level, purely for tool_node.py's
                    # reactive-mode detection.
                    result_entry["result"] = {
                        "error": str(outcome),
                        "details": outcome.details,
                    }
                    result_entry["status_code"] = outcome.status_code
                elif isinstance(outcome, Exception):
                    logger.error(f"Error executing tool call {function_name}: {str(outcome)}")
                    result_entry["error"] = str(outcome)
                else:
                    result_entry["result"] = outcome
                results[idx] = result_entry

        final_results: List[Dict[str, Any]] = [r for r in results if r is not None]

        # Return stub errors for tool calls that were dropped by the cap so that
        # every tool_call ID in the assistant message has a matching toolResult.
        for offset, skipped in enumerate(skipped_calls):
            tc_id = skipped.get("id", "")
            tc_name = skipped.get("name", "")
            if not tc_id:
                logger.error(
                    "Skipped tool call has no ID; stub toolResult cannot satisfy "
                    "Bedrock's toolUse contract. Check upstream tool call normalization."
                )
                continue
            skipped_error = (
                f"Tool call skipped: exceeded max_tool_calls limit "
                f"({limit}). Reduce the number of tool calls in a single turn."
            )
            final_results.append(
                {
                    "tool_call_id": tc_id,
                    "name": tc_name,
                    "error": skipped_error,
                }
            )
            await _emit_start(skipped, total_tools + offset + 1)
            await _emit_end(skipped, skipped_error, time.monotonic())

        return final_results

    async def shutdown(self) -> None:
        """Close the internal HTTP client and release resources.

        Call this during application shutdown to avoid leaking
        connections / file descriptors.
        """
        try:
            await self._http_client.aclose()
            logger.info("ToolManager HTTP client closed")
        except Exception as e:
            logger.error(f"Error closing ToolManager HTTP client: {e}")

    def get_statistics(self) -> Dict[str, Any]:
        """Return tool execution statistics."""
        return {
            "total_tool_calls_executed": self._total_tool_calls_executed,
            "total_tools_available": len(self._http_tools()),
            "internal_tools_available": len(self._exposed_internal_tools()),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_single_tool_call(
        self,
        tool_metadata: Dict[str, Any],
        arguments: Dict[str, Any],
        auth_info: Optional[AuthInfo] = None,
    ) -> Any:
        """Execute a single tool call via HTTP.

        Args:
            tool_metadata: Tool metadata from ``ToolsGenerator`` (path, method, etc.).
            arguments: Validated arguments for the tool call.
            auth_info: Optional authentication data for the request.

        Returns:
            The parsed successful response (JSON dict/list, or plain text if
            not JSON-decodable). If the body exceeds
            ``config.tool_response_max_bytes`` it is not parsed; a flagged dict
            ``{"truncated": True, "message", "bytes_read", "limit_bytes",
            "content_type", "text"}`` holding the text read so far is returned.

        Raises:
            ToolHTTPError: If the response status is >=400 (``details`` is the
                first 500 characters of a size-bounded read of the body).
            Exception: Network-level failures (connection, timeout, etc.)
                propagate as raised by the underlying HTTP client.
        """
        method: str = tool_metadata["method"]
        path: str = tool_metadata["path"]

        # Build URL with path parameter substitution
        url = f"{self._base_url}{path}"

        # Replace path parameters
        path_params = {}
        for param_name, param_value in arguments.items():
            if f"{{{param_name}}}" in path:
                path_params[param_name] = param_value
                url = url.replace(f"{{{param_name}}}", str(param_value))

        # Remaining params are query or body
        query_params = {k: v for k, v in arguments.items() if k not in path_params}

        # Build headers
        headers = {}
        if auth_info and auth_info.auth_handler and auth_info.credentials:
            headers = await auth_info.auth_handler.apply_auth_to_headers(headers)

        # Only advertise encodings that bounded_decoding can decode with a capped output.
        if not any(name.lower() == "accept-encoding" for name in headers):
            headers["Accept-Encoding"] = ACCEPT_ENCODING

        if method in ["GET", "DELETE"]:
            request = self._http_client.build_request(method=method, url=url, params=query_params, headers=headers)
        else:  # POST, PUT, PATCH
            request = self._http_client.build_request(
                method=method,
                url=url,
                params=query_params,
                json=query_params if query_params else None,
                headers=headers,
            )

        # Only the request/read itself is wrapped -- a raised ToolHTTPError
        # below must propagate to the caller (execute_tool_calls()'s gather, or
        # call_tool()'s direct await), not get swallowed here too.
        try:
            raw = await self._read_capped_response(request)
        except Exception as e:
            logger.error(f"Error executing HTTP request to {url}: {str(e)}")
            raise

        if raw.status_code >= 400:
            raise ToolHTTPError(raw.status_code, raw.text()[:500])

        if raw.truncated:
            limit = self._config.tool_response_max_bytes
            logger.warning(
                "Tool response from %s exceeded tool_response_max_bytes (%d); returning truncated result", url, limit
            )
            return {
                "truncated": True,
                "message": f"Response exceeded the {limit}-byte limit and was truncated; only the first {limit} bytes are included.",
                "bytes_read": len(raw.body),
                "limit_bytes": limit,
                "content_type": raw.content_type,
                "text": raw.text(),
            }

        try:
            return json.loads(raw.body)
        except Exception:
            return raw.text()

    async def _read_capped_response(self, request: httpx.Request) -> _RawResponse:
        """Send ``request`` and read at most the configured number of body bytes.

        The cap counts decoded bytes (so a compressed body is limited by its
        decompressed size) and is lower for error responses. Decompression is
        done here in bounded steps (see ``bounded_decoding``), never by httpx,
        so a highly compressed body cannot allocate far beyond the cap. Reading
        stops as soon as the cap is exceeded, and the response is always closed.

        Raises:
            ToolError: If the response uses a ``Content-Encoding`` other than
                identity, gzip or deflate.
        """
        response = await self._http_client.send(request, stream=True)
        try:
            limit = self._config.tool_response_max_bytes
            if response.status_code >= 400:
                limit = min(limit, _ERROR_BODY_READ_MAX_BYTES)

            buf = bytearray()
            truncated = False
            async for chunk in iter_decoded_chunks(response):
                remaining = limit - len(buf)
                if len(chunk) > remaining:
                    buf += chunk[:remaining]
                    truncated = True
                    break
                buf += chunk

            return _RawResponse(
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
                encoding=response.encoding or "utf-8",
                body=bytes(buf),
                truncated=truncated,
            )
        finally:
            await response.aclose()


# ---------------------------------------------------------------------------
# Utility function
# ---------------------------------------------------------------------------


def _empty_tool_context(config: Any) -> ToolContext:
    """Context for internal calls made without a live session (session-bound tools fail closed)."""
    return ToolContext(artifact_store=None, artifact_scope=None, context_budget=None, chat_config=config)
