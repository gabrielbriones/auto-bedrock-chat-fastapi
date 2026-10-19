"""Preprocessing node — wraps MessagePreprocessor as a LangGraph node.

All four truncation/summarisation stages from MessagePreprocessor are
preserved exactly.  This node only handles format pass-through (messages
stay as plain dicts throughout the graph).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig

from ...artifacts import ArtifactError, ArtifactScope, ArtifactStore
from ...context_budget import ContextBudget, estimate_message_tokens
from ...defaults import DEFAULT_CONTEXT_BUDGET_SAFETY_FRACTION, DEFAULT_SUMMARIZATION_TEMPERATURE
from ...message_preprocessor import MessagePreprocessor
from ..state import ChatState

logger = logging.getLogger(__name__)


def _estimate_pending_message_budgets(messages: List[Dict], chat_config: Any) -> Dict[str, Any]:
    """Estimate each message after the last assistant message (or system prompt).

    A tool-call assistant message is also a boundary: the next invocation
    evaluates its new tool results rather than re-evaluating earlier input.
    Estimates do not reject, truncate, or replace messages.
    """
    start = 0
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") in ("assistant", "system"):
            start = index + 1
            break

    budget = ContextBudget.for_turn(chat_config)
    threshold = budget.max_input_tokens * getattr(
        chat_config, "context_budget_safety_fraction", DEFAULT_CONTEXT_BUDGET_SAFETY_FRACTION
    )
    estimates = []
    for index in range(start, len(messages)):
        estimated_tokens = estimate_message_tokens(messages[index], provider=budget.provider)
        exceeds_threshold = estimated_tokens > threshold
        estimates.append(
            {
                "message_index": index,
                "estimated_tokens": estimated_tokens,
                "exceeds_threshold": exceeds_threshold,
            }
        )
        logger.debug(
            "Message context estimate: model_id=%s message_index=%d "
            "estimated_tokens=%d threshold_tokens=%.1f exceeds_threshold=%s",
            budget.model_id,
            index,
            estimated_tokens,
            threshold,
            exceeds_threshold,
        )
    return {"model_id": budget.model_id, "threshold_tokens": threshold, "messages": estimates}


def _archive_pending_messages(messages: List[Dict], estimates: Dict[str, Any], config: RunnableConfig) -> List[Dict]:
    """Attach scoped handles without replacing the original model input."""
    configurable = config.get("configurable") or {}
    if not getattr(configurable.get("chat_config"), "artifact_enabled", True):
        return messages
    store = configurable.get("artifact_store")
    scope = configurable.get("artifact_scope")
    if store is None and scope is None:
        logger.debug("No live artifact scope supplied; messages remain unchanged")
        return messages
    if not isinstance(store, ArtifactStore) or not isinstance(scope, ArtifactScope):
        raise ArtifactError("Both artifact_store and artifact_scope are required")
    archived = list(messages)
    for estimate in estimates["messages"]:
        if not estimate["exceeds_threshold"]:
            continue
        index = estimate["message_index"]
        message = messages[index]
        message_metadata = dict(message.get("metadata") or {})
        handle = message_metadata.get("artifact_handle")
        if not isinstance(handle, str) or not store.contains(scope, handle):
            content = message.get("content", "")
            if message.get("role") == "user" and isinstance(content, str):
                text = content
            else:
                text = json.dumps(
                    {key: value for key, value in message.items() if key != "metadata"}, ensure_ascii=False
                )
            try:
                handle = store.put(scope, text, {"source": f"{message.get('role', 'unknown')}_message"})
            except ArtifactError:
                logger.error("Failed to archive message %d in the current artifact scope", index, exc_info=True)
                raise
        message_metadata["artifact_handle"] = handle
        archived[index] = {**message, "metadata": message_metadata}
    return archived


def prepare_message_artifacts(
    messages: List[Dict], chat_config: Any, config: RunnableConfig
) -> tuple[List[Dict], Dict[str, Any]]:
    """Estimate and archive pending messages for preprocessing or a model retry."""
    estimates = _estimate_pending_message_budgets(messages, chat_config)
    return _archive_pending_messages(messages, estimates, config), estimates


async def preprocess_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Truncate/summarise the conversation history before the LLM call.

    Wraps :meth:`MessagePreprocessor.preprocess_messages` which runs:

    * **Stage 1** — single-message truncation (>500 K chars → 425 K)
    * **Stage 2.1** — history-total per-message truncation (>650 K total)
    * **Stage 2.2** — middle-zone wipe (when AI summarisation is off)
    * **Stage 2.3** — all user/tool messages >100 K chars
    * **Stage 2.4** — recursive halving (up to 3 times)

    The ``on_progress`` callback in state is forwarded so the client
    receives typing indicators during AI summarisation.
    """
    messages: List[Dict] = state.get("messages", [])
    on_progress = (config.get("configurable") or {}).get("on_progress")
    metadata: Dict = dict(state.get("metadata") or {})

    # Retrieve the ChatConfig stored in the graph's configurable namespace
    chat_config = config.get("configurable", {}).get("chat_config")
    if chat_config is None:
        logger.warning("preprocess_node: chat_config not in configurable; skipping preprocessing")
        return {}

    messages, metadata["message_context_budget"] = prepare_message_artifacts(messages, chat_config, config)

    # Wire up the LLM directly when AI summarization is enabled.
    # A summarizer-specific config copy is used: separate model_id/temperature/
    # max_tokens/top_p overrides (falling back to the main chat config when
    # unset), no tools, and top_p only when no temperature is in effect
    # (avoids Claude ValidationException when both are set).
    llm_client = None
    if getattr(chat_config, "enable_ai_summarization", False):
        # Deferred to avoid a cycle with the LLM node's retry preparation.
        from .llm_call import _build_llm

        # Precedence: an explicit summarization_temperature always wins (top_p
        # dropped). Otherwise, an explicit summarization_top_p is honored with
        # no temperature. Only when neither is configured do we fall back to
        # the fixed DEFAULT_SUMMARIZATION_TEMPERATURE (temperature, no top_p).
        if chat_config.summarization_temperature is not None:
            resolved_temperature = chat_config.summarization_temperature
            resolved_top_p = None
        elif chat_config.summarization_top_p is not None:
            resolved_temperature = None
            resolved_top_p = chat_config.summarization_top_p
        else:
            resolved_temperature = DEFAULT_SUMMARIZATION_TEMPERATURE
            resolved_top_p = None

        summarizer_config = chat_config.model_copy(
            update={
                "model_id": chat_config.summarization_model_id or chat_config.model_id,
                "max_tokens": chat_config.summarization_max_tokens or chat_config.max_tokens,
                "temperature": resolved_temperature,
                "top_p": resolved_top_p,
                "langchain_tools": None,
            }
        )
        llm_client = _build_llm(summarizer_config.model_id, summarizer_config)
        logger.debug(
            "preprocess_node: AI summarization enabled — LLM client built "
            "(model=%s, temperature=%s, max_tokens=%s, top_p=%s)",
            summarizer_config.model_id,
            resolved_temperature,
            summarizer_config.max_tokens,
            summarizer_config.top_p,
        )

    preprocessor = MessagePreprocessor(config=chat_config, llm_client=llm_client)
    processed = await preprocessor.preprocess_messages(
        messages=messages,
        on_progress=on_progress,
    )

    preprocessing_applied = len(processed) != len(messages) or processed != messages
    metadata["preprocessing_applied"] = preprocessing_applied

    return {"messages": processed, "metadata": metadata}
