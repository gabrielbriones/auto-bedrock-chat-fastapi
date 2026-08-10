"""Preprocessing node — wraps MessagePreprocessor as a LangGraph node.

All four truncation/summarisation stages from MessagePreprocessor are
preserved exactly.  This node only handles format pass-through (messages
stay as plain dicts throughout the graph).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig

from ...defaults import DEFAULT_SUMMARIZATION_TEMPERATURE
from ...message_preprocessor import MessagePreprocessor
from ..state import ChatState
from .llm_call import _build_llm

logger = logging.getLogger(__name__)


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

    # Wire up the LLM directly when AI summarization is enabled.
    # A summarizer-specific config copy is used: separate model_id/temperature/
    # max_tokens/top_p overrides (falling back to the main chat config when
    # unset), no tools, and top_p only when no temperature is in effect
    # (avoids Claude ValidationException when both are set).
    llm_client = None
    if getattr(chat_config, "enable_ai_summarization", False):
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
