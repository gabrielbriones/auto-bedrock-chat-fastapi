"""Async tool execution node for the LangGraph chat graph.

Replaces the Phase 1 stub with real HTTP execution via ``ToolManager``.

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
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig

from ..state import ChatState

logger = logging.getLogger(__name__)


async def tools_execution_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Execute tool calls from the last assistant message and append results.

    Reads ``tool_manager``, ``auth_info``, and ``on_progress`` from
    ``config["configurable"]``.  If ``tool_manager`` is absent (e.g. during
    unit tests that don't inject one), the node returns an error result
    without raising so the graph can continue.

    Returns a partial state update with:
    - ``messages``: original messages + one new ``{"role": "tool", ...}`` message
    - ``metadata``: updated ``tool_call_rounds`` and ``total_tool_calls`` counters
    """
    configurable: Dict[str, Any] = config.get("configurable") or {}
    tool_manager = configurable.get("tool_manager")
    auth_info = configurable.get("auth_info")
    on_progress_raw = configurable.get("on_progress")

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
