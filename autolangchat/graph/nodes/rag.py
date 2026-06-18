"""RAG (Retrieval-Augmented Generation) node.

Retrieves relevant KB chunks for the current user message and injects an
enhanced system message into the conversation before the LLM call.

Inputs from ``config["configurable"]``:
    chat_config     — ChatConfig (required)
    kb_store        — BaseKBStore instance or None (falls back to factory)
    embedding_client — BedrockEmbeddingClient or None
    auth_context_text — Pre-formatted auth-user context string or None

Returns a partial state update:
    messages    — full message list with the injected system message prepended
                  ahead of the current conversation turn; omitted when no
                  context is available.
    kb_results  — list of retrieved KB chunks (empty when RAG is disabled or
                  produced no results).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from langchain_core.runnables import RunnableConfig

from ..state import ChatState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Formatting helpers (moved from websocket_handler)
# ---------------------------------------------------------------------------


def _format_kb_context(kb_results: List[Dict[str, Any]]) -> str:
    """Format KB chunks for inclusion in the system prompt."""
    if not kb_results:
        return ""

    parts = ["RELEVANT KNOWLEDGE BASE CONTEXT:", "=" * 60]

    for i, result in enumerate(kb_results, 1):
        parts.append(f"\n[Context {i}] (Relevance: {result['similarity_score']:.2f})")

        if result.get("title"):
            parts.append(f"Title: {result['title']}")
        if result.get("source"):
            source_val = result["source"]
            label = "[Learned from validated corrections]" if source_val == "feedback" else "[Reference documentation]"
            parts.append(f"Source: {label}")
        if result.get("source_url"):
            parts.append(f"URL: {result['source_url']}")

        parts.append(f"\n{result['content']}\n")
        parts.append("-" * 60)

    parts += [
        "\nINSTRUCTIONS:",
        "- The context above is provided for your information only - the user cannot see it",
        "- Use the context to inform your response when relevant",
        "- When citing information from the context, reference the actual source Title and URL",
        "  Example: 'According to [Article Title](URL)...' or 'As mentioned in the documentation...'",
        "- DO NOT use internal references like '[Context 1]' or '[Context N]' - these mean nothing to the user",
        "- If the context is not relevant to the question, answer from your general knowledge",
        "- Always be accurate and acknowledge if you're unsure",
        "=" * 60,
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


async def rag_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Retrieve KB context and inject an enhanced system message.

    Reads the last user message from state, embeds it, runs a hybrid search
    against the KB store, then prepends a system message containing the
    retrieved context and any auth-user context provided via configurable.

    The node returns the full message list with the new system prompt prepended
    so the current user message remains visible to downstream nodes. Any prior
    system messages in the conversation history are replaced for this turn.

    When RAG is disabled, no KB store is wired, or retrieval returns nothing,
    the node is a cheap no-op that still clears ``kb_results`` for this turn.
    """
    configurable: Dict[str, Any] = config.get("configurable") or {}
    chat_config = configurable.get("chat_config")
    kb_store = configurable.get("kb_store")
    embedding_client = configurable.get("embedding_client")
    auth_context_text: Optional[str] = configurable.get("auth_context_text")

    messages: List[Dict[str, Any]] = state.get("messages") or []
    last_msg = messages[-1] if messages else {}

    # Only act on the incoming user turn.
    if last_msg.get("role") != "user":
        return {"kb_results": []}

    user_query: str = last_msg.get("content", "")
    kb_results: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # KB retrieval
    # ------------------------------------------------------------------
    rag_enabled = bool(chat_config and getattr(chat_config, "enable_rag", False))

    if rag_enabled and embedding_client is not None:
        try:
            _close_after = False
            if kb_store is None:
                from ...db import create_kb_store  # lazy import to avoid circular deps

                kb_store = create_kb_store(chat_config)
                _close_after = True

            query_embedding = await embedding_client.generate_embedding(
                text=user_query,
                model_id=chat_config.kb_embedding_model,
            )

            try:
                results = await asyncio.to_thread(
                    kb_store.hybrid_search,
                    query=user_query,
                    query_embedding=query_embedding,
                    limit=chat_config.kb_top_k_results,
                    min_score=chat_config.kb_similarity_threshold,
                    filters=None,
                    semantic_weight=chat_config.kb_semantic_weight,
                    keyword_weight=chat_config.kb_keyword_weight,
                )
            finally:
                if _close_after:
                    kb_store.close()

            logger.info(
                "RAG retrieval: %d chunk(s) (threshold=%.2f)",
                len(results),
                chat_config.kb_similarity_threshold,
            )
            if results:
                logger.debug("Top result score: %.4f", results[0]["similarity_score"])
            kb_results = results or []

        except Exception as exc:
            logger.error("RAG retrieval failed: %s", exc)
            kb_results = []

    elif rag_enabled and embedding_client is None:
        logger.warning("RAG: embedding_client not in configurable, skipping KB retrieval")

    # ------------------------------------------------------------------
    # System message injection
    # ------------------------------------------------------------------
    kb_context_text = _format_kb_context(kb_results) if kb_results else ""

    if not kb_context_text and not auth_context_text:
        # No context to inject — pass through unchanged.
        return {"kb_results": kb_results}

    base_system_prompt = chat_config.get_system_prompt() if chat_config else ""
    context_parts = [p for p in [kb_context_text, auth_context_text] if p]
    enhanced_system_prompt = "\n\n".join(context_parts + [base_system_prompt])

    logger.debug("Enhanced system prompt length: %d chars", len(enhanced_system_prompt))

    # Preserve the user/assistant conversation while replacing any prior
    # system prompt with the new RAG-enhanced one.
    preserved_messages = [msg for msg in messages if msg.get("role") != "system"]
    return {
        "messages": [{"role": "system", "content": enhanced_system_prompt}] + preserved_messages,
        "kb_results": kb_results,
    }
