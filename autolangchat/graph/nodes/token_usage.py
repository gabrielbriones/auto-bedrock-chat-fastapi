"""Token-usage summary and recording node.

Terminal node that summarizes and (optionally) persists per-turn usage after
the LLM (and any citation-boost bookkeeping) has completed. Runs for *every*
``chat_graph`` caller -- WebSocket handler, standalone scripts, future
callers -- instead of being tied to one particular transport.

Usage is measured regardless of ``token_usage_enabled``: the turn's
:class:`~autolangchat.usage.ledger.UsageLedger` (one event per AI call --
chat rounds, retries, summarization chunks) is summed and priced, and the
summary is attached to the final assistant message as ``metadata["usage"]``
(``input_tokens``, ``output_tokens``, ``cache_read``, ``cache_write``,
``estimated_cost``, ``currency``, ``final``). Persistence additionally
requires a ``token_usage_store`` and ``chat_config.token_usage_enabled``.

Inputs from ``config["configurable"]``:
    chat_config             — ChatConfig (required)
    usage_ledger            — per-turn UsageLedger; when absent, one ``chat``
                              event is synthesized from the turn metadata and
                              its cost is reported as unknown
    usage_finalized_later   — True when the caller will record more events
                              after the graph (e.g. a title call), so the
                              summary is marked ``final: false``
    token_usage_store       — BaseTokenUsageStore instance or None
    thread_id               — LangGraph thread id; used as the session_id fallback
    session_id              — optional explicit session id (overrides thread_id)
    user_id                 — optional explicit user id

The last message's ``message_id`` is set to the ledger's ``turn_id`` so the
client, the persisted ``token_usage`` row and later ``usage_updated`` frames
share one id.

Store writes are bounded by ``chat_config.token_usage_write_timeout``
(default 5s); failed writes are queued for retry (see
:mod:`autolangchat.usage.persistence`).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Mapping, Optional

from langchain_core.runnables import RunnableConfig

from ...context_budget import provider_for_model
from ...usage.ledger import UsageLedger
from ...usage.persistence import persist_turn_usage
from ...usage.pricing import summarize_usage
from ..state import ChatState

logger = logging.getLogger(__name__)


def _ledger_from_metadata(metadata: Mapping[str, Any], turn_id: str, model_id: str) -> Optional[UsageLedger]:
    """Single ``chat`` event from accumulated turn metadata, for callers without a ledger."""
    input_tokens = metadata.get("input_tokens")
    output_tokens = metadata.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        return None
    ledger = UsageLedger(turn_id)
    ledger.record(
        purpose="chat",
        provider=provider_for_model(model_id),
        model_id=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=metadata.get("cache_read_tokens") or 0,
        cache_write_tokens=metadata.get("cache_write_tokens") or 0,
    )
    return ledger


async def token_usage_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Attach the turn's usage summary to the last message and persist it when enabled."""
    configurable: Dict[str, Any] = config.get("configurable") or {}
    chat_config = configurable.get("chat_config")
    if chat_config is None:
        return {}

    metadata: Dict[str, Any] = state.get("metadata") or {}
    model_id = metadata.get("model_id") or chat_config.model_id
    messages = state.get("messages") or []
    last_message = messages[-1] if messages else {}
    last_metadata = last_message.get("metadata") or {}

    ledger = configurable.get("usage_ledger")
    per_call = ledger is not None
    if ledger is None:
        ledger = _ledger_from_metadata(metadata, last_metadata.get("message_id") or str(uuid.uuid4()), model_id)
        if ledger is None:
            return {}

    updated_metadata = {**last_metadata, "message_id": ledger.turn_id}
    if ledger.events:
        pricing_table = getattr(chat_config, "pricing_table", None)
        summary, estimate = summarize_usage(
            ledger,
            final=not configurable.get("usage_finalized_later", False),
            pricing_table=pricing_table if isinstance(pricing_table, Mapping) else None,
        )
        if not per_call:
            # The aggregate no longer says which model or cache TTL each round used, nor how big the
            # latest prompt was.
            summary, estimate = {**summary, "estimated_cost": None, "currency": None}, None
            summary.pop("context", None)
        updated_metadata["usage"] = summary
        await _persist(configurable, chat_config, ledger, model_id, estimate, summary["final"])

    if not messages or updated_metadata == last_metadata:
        return {}
    return {"messages": [*messages[:-1], {**last_message, "metadata": updated_metadata}]}


async def _persist(
    configurable: Mapping[str, Any],
    chat_config: Any,
    ledger: UsageLedger,
    model_id: str,
    estimate: Any,
    final: bool,
) -> None:
    store = configurable.get("token_usage_store")
    if store is None or not getattr(chat_config, "token_usage_enabled", False):
        return
    session_id = configurable.get("session_id") or configurable.get("thread_id")
    if not session_id:
        # record_turn requires a non-null session_id.
        return
    await persist_turn_usage(
        store,
        ledger,
        session_id=session_id,
        user_id=configurable.get("user_id"),
        model_id=model_id,
        estimate=estimate,
        final=final,
        timeout=getattr(chat_config, "token_usage_write_timeout", 5.0),
    )
