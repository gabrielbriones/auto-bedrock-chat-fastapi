"""Preprocessing node — wraps MessagePreprocessor as a LangGraph node.

All four truncation/summarisation stages from MessagePreprocessor are
preserved exactly.  This node only handles format pass-through (messages
stay as plain dicts throughout the graph).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from langchain_core.runnables import RunnableConfig

from ...artifacts import ArtifactError, ArtifactScope, ArtifactStore
from ...context_budget import ContextBudget, TextEstimate, estimate_message_parts
from ...defaults import DEFAULT_CONTEXT_BUDGET_SAFETY_FRACTION, DEFAULT_SUMMARIZATION_TEMPERATURE
from ...message_preprocessor import MessagePreprocessor, has_artifact_markers
from ..state import ChatState
from ..tools.spill import replace_oversized_message

logger = logging.getLogger(__name__)


def _describe_parts(parts: List[TextEstimate]) -> str:
    """What each part's estimate was made from, for the debug log: tokens, characters, kind and ratio."""
    return "; ".join(
        f"{part.label}: {part.tokens} tokens ({part.chars:,} chars, kind={part.kind}, {part.chars_per_token:.2f} chars/token)"
        for part in parts
    )


def _estimate_pending_message_budgets(messages: List[Dict], chat_config: Any) -> Dict[str, Any]:
    """Estimate each message after the last assistant message (or system prompt).

    A tool-call assistant message is also a boundary: the next invocation
    evaluates its new tool results rather than re-evaluating earlier input.
    This is the only place the context budget of pending messages is
    estimated. Every message also carries the estimate of each of its parts
    (``parts``: a result of a tool message, the text of a user message), which
    :func:`_archive_pending_messages` uses to decide which parts to replace.
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
        parts = estimate_message_parts(messages[index], provider=budget.provider)
        estimated_tokens = sum(part.tokens for part in parts)
        exceeds_threshold = estimated_tokens > threshold
        estimates.append(
            {
                "message_index": index,
                "estimated_tokens": estimated_tokens,
                "exceeds_threshold": exceeds_threshold,
                "parts": [{"estimated_tokens": part.tokens} for part in parts],
            }
        )
        logger.debug(
            "Message context estimate: model_id=%s message_index=%d "
            "estimated_tokens=%d threshold_tokens=%.1f exceeds_threshold=%s%s",
            budget.model_id,
            index,
            estimated_tokens,
            threshold,
            exceeds_threshold,
            f" parts=[{_describe_parts(parts)}]" if logger.isEnabledFor(logging.DEBUG) else "",
        )
    return {"model_id": budget.model_id, "threshold_tokens": threshold, "messages": estimates}


def _artifact_target(
    chat_config: Any, configurable: Dict[str, Any]
) -> tuple[Optional[ArtifactStore], Optional[ArtifactScope]]:
    """The live artifact store and scope; ``(None, None)`` when artifacts are disabled or none were supplied."""
    if not getattr(chat_config, "artifact_enabled", True):
        return None, None
    store = configurable.get("artifact_store")
    scope = configurable.get("artifact_scope")
    if store is None and scope is None:
        logger.debug("No live artifact scope supplied; oversized messages are not archived")
        return None, None
    if not isinstance(store, ArtifactStore) or not isinstance(scope, ArtifactScope):
        raise ArtifactError("Both artifact_store and artifact_scope are required")
    return store, scope


def _archive_pending_messages(
    messages: List[Dict], estimates: Dict[str, Any], chat_config: Any, config: RunnableConfig
) -> List[Dict]:
    """Replace oversized tool results and user messages by artifact markers, and copy any other oversized message.

    Works from the estimates only: it never estimates a message itself. Oversized tool results are replaced even
    without a live artifact scope (by flagged previews), so an unbounded payload never reaches the model. An
    oversized user message is replaced only when its original can be stored, and is otherwise left to the
    preprocessor's truncation. A message that is not replaced, for example one that a marker would not shrink, is
    copied into the store and tagged with a handle, keeping its content.
    """
    configurable = config.get("configurable") or {}
    store, scope = _artifact_target(chat_config, configurable)
    exempt_tools = getattr(configurable.get("tool_manager"), "internal_tools", None) or {}
    archived = list(messages)
    for estimate in estimates["messages"]:
        if not estimate["exceeds_threshold"]:
            continue
        index = estimate["message_index"]
        message = messages[index]
        try:
            outcome = replace_oversized_message(
                message,
                [part["estimated_tokens"] for part in estimate["parts"]],
                estimates["threshold_tokens"],
                message_index=index,
                model_id=estimates["model_id"],
                chat_config=chat_config,
                artifact_store=store,
                artifact_scope=scope,
                exempt_tools=exempt_tools,
            )
        except ArtifactError:
            logger.error("Failed to store message %d as a session artifact", index, exc_info=True)
            raise
        if outcome.changed:
            archived[index] = outcome.message
            continue
        if store is None or scope is None:
            continue
        if has_artifact_markers(message):
            # Spilled originals are already stored; archiving the marker message would duplicate them.
            continue
        message_metadata = dict(message.get("metadata") or {})
        handle = message_metadata.get("artifact_handle")
        if not isinstance(handle, str) or not store.contains(scope, handle):
            content = message.get("content", "")
            tool_results = message.get("tool_results")
            if message.get("role") == "user" and isinstance(content, str):
                text = content
            elif message.get("role") == "tool" and isinstance(tool_results, list):
                # ``content`` is a JSON mirror of ``tool_results``; storing both would keep every result twice.
                text = json.dumps(tool_results, ensure_ascii=False)
            else:
                text = json.dumps(
                    {key: value for key, value in message.items() if key != "metadata"}, ensure_ascii=False
                )
            try:
                handle = store.put(scope, text, {"source": f"{message.get('role', 'unknown')}_message"})
            except ArtifactError:
                logger.error("Failed to store a copy of message %d in the current artifact scope", index, exc_info=True)
                raise
            logger.debug(
                "Stored a copy of message %d (role=%s, ~%d tokens) as artifact %s; the message keeps its content",
                index,
                message.get("role", "unknown"),
                estimate["estimated_tokens"],
                handle,
            )
        message_metadata["artifact_handle"] = handle
        archived[index] = {**message, "metadata": message_metadata}
    return archived


def prepare_message_artifacts(
    messages: List[Dict], chat_config: Any, config: RunnableConfig
) -> tuple[List[Dict], Dict[str, Any]]:
    """Estimate pending messages once, then replace or copy the oversized ones into the artifact store.

    Used by the preprocessing node and by model retries, which bypass it.
    """
    estimates = _estimate_pending_message_budgets(messages, chat_config)
    return _archive_pending_messages(messages, estimates, chat_config, config), estimates


async def preprocess_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Replace oversized tool results, then truncate/summarise the history before the LLM call.

    First, :func:`prepare_message_artifacts` estimates the pending messages
    once, replaces oversized tool results and user messages with artifact
    markers, and copies other oversized messages into the artifact store.
    Then it wraps
    :meth:`MessagePreprocessor.preprocess_messages` which runs:

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
