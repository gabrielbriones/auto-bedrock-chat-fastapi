"""Client-facing projection of LangGraph checkpoint messages.

Shared by the WebSocket ``history``/``conversation_loaded`` frames and the
REST ``GET /conversations/{id}/messages`` endpoint so both surfaces agree on
what a history message looks like.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

# Must exceed the longest gap between checkpoint writes in a live turn (a
# Bedrock call can take 300s and is retried once with another model id).
TOOL_LOOP_LEASE = timedelta(minutes=10)


def ends_mid_tool_loop(messages: List[Dict[str, Any]]) -> bool:
    """Whether ``messages`` end on a tool result or an assistant tool request."""
    if not messages:
        return False
    last = messages[-1]
    return last.get("role") == "tool" or (last.get("role") == "assistant" and bool(last.get("tool_calls")))


def has_recent_tool_loop(messages: List[Dict[str, Any]]) -> bool:
    """Whether ``messages`` end mid tool-loop within :data:`TOOL_LOOP_LEASE`.

    Checkpoints are shared across workers, so a recent tool-loop tail may
    belong to a turn still running elsewhere. Missing timestamps count as stale.
    """
    if not ends_mid_tool_loop(messages):
        return False
    try:
        ts = datetime.fromisoformat((messages[-1].get("metadata") or {}).get("timestamp") or "")
    except (TypeError, ValueError):
        return False
    return datetime.now(ts.tzinfo) - ts < TOOL_LOOP_LEASE


def format_history_messages(raw_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert checkpoint message dicts to the client-facing history shape.

    - ``system`` messages are the graph's own prompt (re-injected every turn
      by the RAG node) and are never part of what the user said or was told.
    - ``tool`` messages keep their structured ``tool_results`` but drop
      ``content``, which is the same payload ``json.dumps``-ed a second time
      and can run to megabytes per turn.
    """
    history: List[Dict[str, Any]] = []
    for m in raw_messages:
        role = m.get("role")
        if role == "system":
            continue
        metadata = m.get("metadata") or {}
        history.append(
            {
                "message_id": metadata.get("message_id"),
                "role": role,
                "content": "" if role == "tool" else m.get("content", ""),
                "timestamp": metadata.get("timestamp"),
                "tool_calls": m.get("tool_calls", []),
                "tool_results": m.get("tool_results", []),
                "metadata": metadata,
            }
        )
    return history
