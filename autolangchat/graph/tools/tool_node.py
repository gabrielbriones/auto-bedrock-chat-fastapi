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
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig

from ...auth_handler import can_refresh
from ..state import ChatState

logger = logging.getLogger(__name__)


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

    retry_calls = [normalized_calls[i] for i in failed_indices]
    retry_results = await tool_manager.execute_tool_calls(retry_calls, auth_info=auth_info)
    for i, new_result in zip(failed_indices, retry_results):
        tool_results[i] = new_result

    return any(tool_results[i].get("status_code") == 401 for i in failed_indices)


async def tools_execution_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Execute tool calls from the last assistant message and append results.

    Reads ``tool_manager``, ``auth_info``, and ``on_progress`` from
    ``config["configurable"]``.  If ``tool_manager`` is absent (e.g. during
    unit tests that don't inject one), the node returns an error result
    without raising so the graph can continue.

    Returns a partial state update with:
    - ``messages``: original messages + one new ``{"role": "tool", ...}`` message
    - ``metadata``: updated ``tool_call_rounds``/``total_tool_calls`` counters,
      plus ``auth_expired`` when reactive-mode refresh-and-retry didn't recover
      a 401.
    """
    configurable: Dict[str, Any] = config.get("configurable") or {}
    tool_manager = configurable.get("tool_manager")
    auth_info = configurable.get("auth_info")
    on_progress_raw = configurable.get("on_progress")
    chat_config = configurable.get("chat_config")

    messages: List[Dict[str, Any]] = list(state.get("messages") or [])
    metadata: Dict[str, Any] = dict(state.get("metadata") or {})

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

        logger.debug(
            "Executing %d tool call(s): %s",
            len(normalized_calls),
            [c["name"] for c in normalized_calls],
        )
        tool_results = await tool_manager.execute_tool_calls(
            normalized_calls,
            auth_info=auth_info,
            on_progress=_tool_progress if on_progress_raw is not None else None,
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

    logger.debug(
        "Tool execution complete: %d result(s), round %d",
        len(tool_results),
        metadata["tool_call_rounds"],
    )

    return {"messages": messages + [tool_msg], "metadata": metadata}
