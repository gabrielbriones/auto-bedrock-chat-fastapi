"""Async tool execution node for the LangGraph chat graph.

Real HTTP execution via ``ToolManager``.

Message format
--------------
State messages are plain dicts, not LangChain ``BaseMessage`` objects.
Tool calls from the LLM are stored in the assistant message as:

    {"name": "get_jobs", "args": {"q": "running"}, "id": "call_xyz", "type": "tool_call"}

(This is the format that ``ChatBedrockConverse`` / ``AIMessage.tool_calls``
produces, which we carry through unchanged in ``_from_langchain_message``.)

The node converts them to ``ToolManager``'s format (``args`` → ``arguments``)
and calls ``ToolManager.execute_tool_calls()``.  Results are appended as a
``{"role": "tool", ...}`` message so ``_to_langchain_messages`` in
``llm_call.py`` can convert them to ``ToolMessage`` objects for the next LLM
invocation.

Auth / progress
---------------
``auth_info`` and ``on_progress`` are read from ``config["configurable"]``
(same as all other nodes).

Reactive auth-expiration handling
----------------------------------
When ``chat_config.auth_expiration_behaviour`` is ``"reactive"`` or ``"both"``,
a 401 result is treated as an expired credential rather than a plain tool
failure: this node (not ``ToolManager``, which stays a pure HTTP-dispatch
component) refreshes the SSO session via ``refresh_sso_session_if_needed()``,
updates ``auth_info.credentials.bearer_token``, and retries only the failed
calls once. If refresh isn't possible (e.g. manual bearer token) or fails,
``metadata["auth_expired"]`` is set so ``_handle_chat_message`` can send the
``auth_expired`` WebSocket message after the graph turn completes.

Internal tools
--------------
In-process tools (``InternalTool``) get a ``ToolContext`` built here from
``config["configurable"]``: ``artifact_store`` / ``artifact_scope`` (injected by
the WebSocket handler when ``artifact_enabled``), ``chat_config``, and a
``ContextBudget`` for the current state messages. The budget is only computed
when the round calls an internal tool. Without a valid store and scope (MCP,
direct ``chat_graph.ainvoke`` callers, artifacts disabled) the context carries
none, so session-bound tools fail closed with ``tool_unavailable``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Mapping

from langchain_core.runnables import RunnableConfig

from ...artifacts import ArtifactScope, ArtifactStore
from ...auth_handler import can_refresh
from ...context_budget import ContextBudget
from ..state import ChatState
from .internal import ToolContext

logger = logging.getLogger(__name__)

_INTERNAL_TOOL_LOG_PREVIEW_CHARS = 500


def _clip_strings(value: Any, limit: int) -> Any:
    """Copy of ``value`` with every string longer than ``limit`` cut short, so a log line stays readable."""
    if isinstance(value, str):
        return value if len(value) <= limit else f"{value[:limit]}...(+{len(value) - limit:,} chars)"
    if isinstance(value, dict):
        return {key: _clip_strings(item, limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip_strings(item, limit) for item in value]
    return value


def _result_preview(entry: Dict[str, Any]) -> str:
    """The result (or error) of a ``tool_results`` entry as JSON, with long strings clipped."""
    payload = entry.get("result", entry.get("error"))
    return json.dumps(_clip_strings(payload, _INTERNAL_TOOL_LOG_PREVIEW_CHARS), ensure_ascii=False, default=str)


def _build_tool_context(
    configurable: Dict[str, Any],
    messages: List[Dict[str, Any]],
    *,
    needs_budget: bool,
) -> ToolContext:
    """Build the per-turn ``ToolContext`` for internal tools from ``configurable``.

    The store and scope are passed through only when both are valid and
    artifacts are enabled; otherwise both are ``None`` so session-bound tools
    return ``tool_unavailable`` instead of touching another scope.
    """
    chat_config = configurable.get("chat_config")
    store = configurable.get("artifact_store")
    scope = configurable.get("artifact_scope")

    if not getattr(chat_config, "artifact_enabled", True):
        store = scope = None
    elif not (isinstance(store, ArtifactStore) and isinstance(scope, ArtifactScope)):
        if store is not None or scope is not None:
            logger.warning("Ignoring incomplete artifact context: both artifact_store and artifact_scope are required")
        store = scope = None

    budget = None
    if needs_budget and chat_config is not None:
        try:
            budget = ContextBudget.for_turn(chat_config, messages)
        except Exception:
            logger.warning("Could not compute the context budget for internal tools", exc_info=True)

    return ToolContext(artifact_store=store, artifact_scope=scope, context_budget=budget, chat_config=chat_config)


async def _handle_reactive_auth_expiration(
    tool_results: List[Dict[str, Any]],
    normalized_calls: List[Dict[str, Any]],
    tool_manager: Any,
    auth_info: Any,
    configurable: Dict[str, Any],
) -> bool:
    """Refresh-and-retry any 401 results in place, in a reactive/both turn.

    Mutates ``tool_results`` (splicing in retried outcomes) and
    ``auth_info.credentials.bearer_token`` (on successful refresh).

    Returns:
        ``True`` if an ``auth_expired`` signal should be surfaced to the
        client (no refresh path available, or the refresh/retry failed).
    """
    failed_indices = [i for i, r in enumerate(tool_results) if r.get("status_code") == 401]
    if not failed_indices:
        return False

    credentials = getattr(auth_info, "credentials", None)
    sso_session_store = configurable.get("sso_session_store")
    sso_provider = configurable.get("sso_provider")

    sso_session_id = (getattr(credentials, "metadata", None) or {}).get("sso_session_id")
    sso_session = sso_session_store.get_session(sso_session_id) if sso_session_store and sso_session_id else None

    if not can_refresh(credentials, sso_session) or sso_provider is None:
        logger.info("Reactive auth-expiration: no refresh path available for this credential")
        return True

    from ...sso.sso_session_store import refresh_sso_session_if_needed

    refreshed_session = await refresh_sso_session_if_needed(sso_session_store, sso_provider, sso_session_id)
    if refreshed_session is None:
        logger.warning("Reactive auth-expiration: refresh failed for sso_session=%s", sso_session_id)
        return True

    credentials.bearer_token = refreshed_session.get("access_token")
    # Reissue the session token too (not just the bearer token) -- otherwise
    # its embedded JWT exp claim still reflects the pre-refresh app-session
    # expiry, and the next message fails validation at that original deadline
    # despite the store-side session having been successfully extended. Same
    # reasoning as the proactive path in websocket_handler.py.
    chat_config = configurable.get("chat_config")
    sso_session_secret = getattr(chat_config, "sso_session_secret", None)
    if sso_session_secret:
        credentials.session_token = sso_session_store.generate_session_token(
            session_id=sso_session_id,
            sso_session_secret=sso_session_secret,
        )

    retry_calls = [normalized_calls[i] for i in failed_indices]
    retry_results = await tool_manager.execute_tool_calls(retry_calls, auth_info=auth_info)
    for i, new_result in zip(failed_indices, retry_results):
        tool_results[i] = new_result

    return any(tool_results[i].get("status_code") == 401 for i in failed_indices)


async def tools_execution_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Execute tool calls from the last assistant message and append results.

    Reads ``tool_manager``, ``auth_info``, ``on_progress`` and the artifact
    context (see ``_build_tool_context``) from ``config["configurable"]``.
    If ``tool_manager`` is absent (e.g. during
    unit tests that don't inject one), the node returns an error result
    without raising so the graph can continue.

    Returns a partial state update with:
    - ``messages``: original messages + one new ``{"role": "tool", ...}`` message
    - ``metadata``: updated ``tool_call_rounds``/``total_tool_calls`` counters,
      plus ``auth_expired`` when reactive-mode refresh-and-retry didn't recover
      a 401.

    Results are passed on as the tools returned them. Oversized ones are
    replaced by artifact markers in the preprocessing node, the single place
    that estimates the context budget (see ``spill.py``).
    """
    configurable: Dict[str, Any] = config.get("configurable") or {}
    tool_manager = configurable.get("tool_manager")
    auth_info = configurable.get("auth_info")
    on_progress_raw = configurable.get("on_progress")
    chat_config = configurable.get("chat_config")

    messages: List[Dict[str, Any]] = list(state.get("messages") or [])
    metadata: Dict[str, Any] = dict(state.get("metadata") or {})
    # Without a tool_manager every result is an error and there are no internal tools.
    internal_tools: Mapping[str, Any] = {}

    last_msg = messages[-1] if messages else {}
    raw_tool_calls: List[Dict[str, Any]] = last_msg.get("tool_calls") or []

    if not raw_tool_calls:
        logger.warning("tools_execution_node reached but last message has no tool_calls")
        return {}

    if tool_manager is None:
        logger.error("tools_execution_node: no tool_manager in configurable — cannot execute tools")
        tool_results = [
            {
                "tool_call_id": tc.get("id", ""),
                "name": tc.get("name", ""),
                "error": "Tool execution unavailable: tool_manager not configured",
            }
            for tc in raw_tool_calls
        ]
    else:
        # LangChain tool_calls use "args"; ToolManager expects "arguments"
        normalized_calls = [
            {
                "id": tc.get("id", ""),
                "name": tc.get("name", ""),
                "arguments": tc.get("args") or tc.get("arguments") or {},
            }
            for tc in raw_tool_calls
        ]

        # Wrap on_progress to match ToolManager's string-based callback signature
        async def _tool_progress(msg: str) -> None:
            if on_progress_raw is not None:
                try:
                    await on_progress_raw(
                        {
                            "type": "typing",
                            "message": msg,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )
                except Exception:
                    pass

        # Structured per-call start/end events, so a client can show each call live.
        async def _tool_event(event: Dict[str, Any]) -> None:
            if on_progress_raw is not None:
                try:
                    await on_progress_raw({**event, "timestamp": datetime.now().isoformat()})
                except Exception:
                    pass

        internal_tools = getattr(tool_manager, "internal_tools", None) or {}
        tool_context = _build_tool_context(
            configurable,
            messages,
            needs_budget=any(c["name"] in internal_tools for c in normalized_calls),
        )

        logger.debug(
            "Executing %d tool call(s): %s",
            len(normalized_calls),
            [c["name"] for c in normalized_calls],
        )
        tool_results = await tool_manager.execute_tool_calls(
            normalized_calls,
            auth_info=auth_info,
            on_progress=_tool_progress if on_progress_raw is not None else None,
            on_tool_event=_tool_event if on_progress_raw is not None else None,
            tool_context=tool_context,
        )

        auth_expiration_behaviour = getattr(chat_config, "auth_expiration_behaviour", "none")
        if auth_expiration_behaviour in ("reactive", "both") and auth_info is not None:
            if await _handle_reactive_auth_expiration(
                tool_results, normalized_calls, tool_manager, auth_info, configurable
            ):
                metadata["auth_expired"] = True

    # Append tool results as a single "tool" role message
    tool_msg: Dict[str, Any] = {
        "role": "tool",
        "content": json.dumps([r.get("result", r.get("error")) for r in tool_results]),
        "tool_results": tool_results,
        "metadata": {"timestamp": datetime.now().isoformat()},
    }

    metadata["tool_call_rounds"] = metadata.get("tool_call_rounds", 0) + 1
    metadata["total_tool_calls"] = metadata.get("total_tool_calls", 0) + len(tool_results)

    # Name and size only: arguments carry user-supplied data and this is logged at INFO and sent to the client.
    tool_result_sizes = [
        {
            "name": r.get("name"),
            "bytes": len(json.dumps(r.get("result", r.get("error")), default=str).encode()),
        }
        for r in tool_results
    ]
    logger.info("Tool result sizes: %s", [{"name": r["name"], "bytes": r["bytes"]} for r in tool_result_sizes])
    metadata["tool_result_sizes"] = metadata.get("tool_result_sizes", []) + tool_result_sizes

    # The bounded pages the model asked for from an artifact; HTTP tool results are only logged by size above.
    if logger.isEnabledFor(logging.DEBUG):
        for entry, size in zip(tool_results, tool_result_sizes):
            if entry.get("name") in internal_tools:
                logger.debug(
                    "Internal tool result %s (call %s, %s bytes): %s",
                    entry.get("name"),
                    entry.get("tool_call_id"),
                    f"{size['bytes']:,}",
                    _result_preview(entry),
                )

    logger.debug(
        "Tool execution complete: %d result(s), round %d",
        len(tool_results),
        metadata["tool_call_rounds"],
    )

    return {"messages": messages + [tool_msg], "metadata": metadata}
