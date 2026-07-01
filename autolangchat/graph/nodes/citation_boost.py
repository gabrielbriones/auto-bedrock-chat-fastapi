"""Citation-boost node.

After the LLM has produced a response, bumps the credibility_score of every
feedback document that was cited in the RAG results for this turn.

This runs at the end of the graph (after the final LLM call) whenever a
``kb_store`` is provided via ``config["configurable"]`` and the LLM has
successfully completed a turn. It is a no-op when ``kb_store`` is absent
or ``kb_credibility_citation_boost_enabled`` is False.

Inputs from ``config["configurable"]``:
    chat_config  — ChatConfig (required)
    kb_store     — BaseKBStore instance or None

State read:
    kb_results   — list of KB chunks from the RAG node; may be empty.

Returns ``{}`` — no state mutation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from langchain_core.runnables import RunnableConfig

from ..state import ChatState

logger = logging.getLogger(__name__)


async def citation_boost_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Boost credibility of feedback documents cited in this turn's RAG results."""
    configurable: Dict[str, Any] = config.get("configurable") or {}
    chat_config = configurable.get("chat_config")
    kb_store = configurable.get("kb_store")

    if (
        kb_store is None
        or chat_config is None
        or not getattr(chat_config, "kb_credibility_citation_boost_enabled", False)
    ):
        return {}

    kb_results = state.get("kb_results") or []
    if not kb_results:
        return {}

    cited_ids = list(dict.fromkeys(r.get("document_id") for r in kb_results if r.get("document_id")))
    if not cited_ids:
        return {}

    try:
        updated = await asyncio.to_thread(
            kb_store.adjust_credibility,
            cited_ids,
            chat_config.kb_credibility_citation_boost,
            chat_config.kb_credibility_removal_threshold,
        )
        logger.debug("Citation boost applied to %d doc(s) (%d updated)", len(cited_ids), updated)
    except Exception:
        logger.exception("Failed to apply citation boost to %d doc(s)", len(cited_ids))

    return {}
