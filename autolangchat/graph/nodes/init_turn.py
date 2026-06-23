"""init_turn node — prepend the incoming user message to conversation history.

Why this node exists
--------------------
The websocket handler passes only ``user_message`` (and ``metadata: {}``) to
``graph.ainvoke()``.  Because ``messages`` is *not* included in the ``ainvoke``
input, LangGraph's checkpointer automatically carries the full conversation
history forward untouched (``total=False`` TypedDict pass-through behaviour).

This node consumes ``user_message``, appends it to the checkpoint messages,
and clears the field so it does not persist between turns.  All downstream
nodes receive the full conversation history exactly as before — without the
handler needing to call ``aget_state`` and prepend manually.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from langchain_core.runnables import RunnableConfig

from ..state import ChatState

logger = logging.getLogger(__name__)


async def init_turn_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Append the new user message to checkpointed conversation history.

    Reads ``user_message`` from state, appends it as a ``{"role": "user",
    "content": ...}`` dict to the existing ``messages`` list, then clears
    ``user_message`` so it is not persisted in the checkpoint.

    Returns a partial state update with:
    - ``messages``: full history including the new user message
    - ``metadata``: reset to ``{}`` so per-turn stats (tool_call_rounds, etc.) start fresh
    - ``user_message``: cleared to ``None``
    """
    user_message = state.get("user_message") or ""
    messages = list(state.get("messages") or [])
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    chat_config = configurable.get("chat_config")

    if user_message:
        messages = messages + [{"role": "user", "content": user_message}]
        logger.debug(
            "init_turn: appended user message (%d chars), history now %d messages", len(user_message), len(messages)
        )
    else:
        logger.warning("init_turn: user_message is empty, passing history through unchanged")

    metadata: Dict[str, Any] = {}  # reset per-turn stats
    if thread_id is not None:
        metadata["thread_id"] = thread_id
    if chat_config is not None:
        metadata["model_id"] = getattr(chat_config, "model_id", None)
        metadata["temperature"] = getattr(chat_config, "temperature", None)
        metadata["max_tokens"] = getattr(chat_config, "max_tokens", None)
        metadata["top_p"] = getattr(chat_config, "top_p", None)

    return {
        "messages": messages,
        "user_message": None,  # consumed — do not persist
        "metadata": metadata,
    }
