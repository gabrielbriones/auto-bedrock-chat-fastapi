"""Token-usage recording node.

Terminal node that persists per-turn token usage after the LLM (and any
citation-boost bookkeeping) has completed. Runs for *every* ``chat_graph``
caller -- WebSocket handler, standalone scripts, future callers -- instead of
being tied to one particular transport, following the same "optional
post-turn node, no-op unless configured" shape as ``citation_boost_node``.

This runs at the end of the graph whenever a ``token_usage_store`` is
provided via ``config["configurable"]`` and ``chat_config.token_usage_enabled``
is True. It is a no-op otherwise, or when the turn never produced token
counts (e.g. a provider response that omits usage metadata).

Inputs from ``config["configurable"]``:
    chat_config       — ChatConfig (required)
    token_usage_store — BaseTokenUsageStore instance or None
    thread_id         — LangGraph thread id; used as the session_id fallback
    session_id        — optional explicit session id (overrides thread_id)
    user_id           — optional explicit user id

State read:
    metadata  — top-level turn metadata (input_tokens/output_tokens/model_id),
                accumulated across tool-call rounds by llm_call_node.
    messages  — the last message's own ``metadata`` dict carries message_id.

Returns ``{}`` — no state mutation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from langchain_core.runnables import RunnableConfig

from ..state import ChatState

logger = logging.getLogger(__name__)


async def token_usage_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Record per-turn token usage via ``token_usage_store.record_turn(...)``."""
    configurable: Dict[str, Any] = config.get("configurable") or {}
    chat_config = configurable.get("chat_config")
    token_usage_store = configurable.get("token_usage_store")

    if token_usage_store is None or chat_config is None or not getattr(chat_config, "token_usage_enabled", False):
        return {}

    metadata: Dict[str, Any] = state.get("metadata") or {}
    input_tokens = metadata.get("input_tokens")
    output_tokens = metadata.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        return {}

    messages = state.get("messages") or []
    last_message = messages[-1] if messages else {}
    # Fall back to a fresh id only if the graph didn't produce one (tests).
    message_id = (last_message.get("metadata") or {}).get("message_id") or str(uuid.uuid4())

    session_id = configurable.get("session_id") or configurable.get("thread_id")
    if not session_id:
        # BaseTokenUsageStore.record_turn requires a non-null session_id;
        # neither an explicit session_id nor a thread_id fallback is
        # available, so there is nothing safe to record.
        return {}
    user_id = configurable.get("user_id")

    try:
        await token_usage_store.record_turn(
            turn_id=message_id,
            session_id=session_id,
            user_id=user_id,
            model_id=metadata.get("model_id") or chat_config.model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            turn_ts=datetime.now(timezone.utc),
        )
    except Exception:
        logger.exception("Failed to record token usage for turn_id=%s", message_id)

    return {}
