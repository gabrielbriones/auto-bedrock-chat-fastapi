"""Tool Manager — owns tool execution via HTTP.

This module provides:

- **Tool execution**: ``ToolManager`` executes tool calls via HTTP, applying
  authentication from an ``AuthInfo`` dataclass.
- **Authentication**: ``AuthInfo`` dataclass for transport-agnostic auth state.

Tool generation (OpenAPI spec parsing, LangChain wrapping) is handled by
``ToolsGenerator`` in ``generator.py``.  ``ToolManager`` receives the parsed
tool metadata dict and only concerns itself with HTTP dispatch.
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import httpx

from ...auth_handler import AuthenticationHandler, Credentials
from ...config import ChatConfig

logger = logging.getLogger(__name__)


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
    ):
        self._generated_tools = generated_tools
        self._config = config or ChatConfig()
        self._base_url = base_url.rstrip("/")
        self._http_client = httpx.AsyncClient(timeout=self._config.timeout)
        self._langchain_tools: Optional[List[Any]] = None
        self._total_tool_calls_executed: int = 0
        logger.info("ToolManager ready: %d tools", len(generated_tools))
        self._sync_config()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        """The resolved API base URL."""
        return self._base_url

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

        def _make_tool(func_name: str, tool_meta: Dict, fn_desc: Dict) -> Any:
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
            mgr = self

            async def _invoke(**kwargs: Any) -> str:
                result = await mgr._execute_single_tool_call(tool_meta, kwargs, auth_info=None)
                if isinstance(result, str):
                    return result
                return _json.dumps(result)

            return StructuredTool.from_function(
                coroutine=_invoke,
                name=func_name,
                description=fn_desc.get("description", f"Call {func_name}"),
                args_schema=args_model,
            )

        self._langchain_tools = [
            _make_tool(func_name, tool_meta, tool_meta["function_desc"])
            for func_name, tool_meta in self._generated_tools.items()
        ]
        logger.info("Generated %d LangChain tools", len(self._langchain_tools))
        return self._langchain_tools

    def _sync_config(self) -> None:
        """Populate ``config.tools_desc`` and ``config.langchain_tools`` from current state."""
        tools_desc = {
            "type": "function",
            "functions": [t["function_desc"] for t in self._generated_tools.values()],
        }
        self._config.tools_desc = tools_desc
        self._config.langchain_tools = self.generate_langchain_tools()

    async def execute_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        auth_info: Optional[AuthInfo] = None,
        on_progress: Optional[Callable] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a list of tool calls by making HTTP requests.

        Each tool call is validated, then dispatched as an HTTP request to
        the appropriate API endpoint.  Authentication headers are applied
        from ``auth_info`` when provided.

        Args:
            tool_calls: List of tool call dicts, each containing ``id``,
                ``name``, and ``arguments``.
            auth_info: Optional authentication data to apply to HTTP headers.
            on_progress: Optional async callback ``(message: str) -> None``
                invoked before each tool call for progress reporting.

        Returns:
            List of result dicts, each containing ``tool_call_id``, ``name``,
            and either ``result`` or ``error``.
        """
        results: List[Dict[str, Any]] = []
        capped_calls = tool_calls[: self._config.max_tool_calls]
        total_tools = len(capped_calls)

        for i, tool_call in enumerate(capped_calls, 1):
            function_name = tool_call.get("name")

            try:
                logger.debug(f"Executing tool call {i}/{total_tools}: {tool_call}")
                self._total_tool_calls_executed += 1

                # Optional progress callback
                if on_progress is not None:
                    await on_progress(f"Calling {function_name}... ({i}/{total_tools})")

                # Validate: tool exists
                tool_metadata = self._generated_tools.get(function_name)
                if not tool_metadata:
                    logger.warning(f"Unknown tool requested: {function_name}")
                    results.append(
                        {
                            "tool_call_id": tool_call.get("id"),
                            "name": function_name,
                            "error": f"Unknown tool: {function_name}",
                        }
                    )
                    continue

                # Validate: required arguments present
                arguments = tool_call.get("arguments", {})
                fn_desc = tool_metadata.get("function_desc", {})
                required = fn_desc.get("parameters", {}).get("required", [])
                missing = [p for p in required if p not in arguments]
                if missing:
                    logger.warning(f"Missing required args for tool {function_name}: {missing}")
                    results.append(
                        {
                            "tool_call_id": tool_call.get("id"),
                            "name": function_name,
                            "error": f"Missing required arguments: {missing}",
                        }
                    )
                    continue

                # Execute
                result = await self._execute_single_tool_call(tool_metadata, arguments, auth_info)

                results.append(
                    {
                        "tool_call_id": tool_call.get("id"),
                        "name": function_name,
                        "result": result,
                    }
                )

            except Exception as e:
                logger.error(f"Error executing tool call {function_name}: {str(e)}")
                results.append(
                    {
                        "tool_call_id": tool_call.get("id"),
                        "name": function_name,
                        "error": str(e),
                    }
                )

        return results

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
            "total_tools_available": len(self._generated_tools),
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
            The parsed response (JSON dict, or error dict on failure).
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

        # Make HTTP request
        try:
            if method in ["GET", "DELETE"]:
                response = await self._http_client.request(
                    method=method,
                    url=url,
                    params=query_params,
                    headers=headers,
                )
            else:  # POST, PUT, PATCH
                response = await self._http_client.request(
                    method=method,
                    url=url,
                    params=query_params,
                    json=query_params if query_params else None,
                    headers=headers,
                )

            # Parse response
            if response.status_code >= 400:
                return {
                    "error": f"HTTP {response.status_code}",
                    "details": response.text[:500],
                }

            try:
                return response.json()
            except Exception:
                return response.text

        except Exception as e:
            logger.error(f"Error executing HTTP request to {url}: {str(e)}")
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Utility function
# ---------------------------------------------------------------------------
