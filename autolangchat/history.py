"""Client-facing projection of LangGraph checkpoint messages.

Shared by the WebSocket ``history``/``conversation_loaded`` frames and the
REST ``GET /conversations/{id}/messages`` endpoint so both surfaces agree on
what a history message looks like.
"""

from __future__ import annotations

from typing import Any, Dict, List


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
