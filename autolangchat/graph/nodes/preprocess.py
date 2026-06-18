"""Preprocessing node — wraps MessagePreprocessor as a LangGraph node.

All four truncation/summarisation stages from MessagePreprocessor are
preserved exactly.  This node only handles format pass-through (messages
stay as plain dicts throughout the graph).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig

from ...message_preprocessor import MessagePreprocessor
from ..state import ChatState

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

    preprocessor = MessagePreprocessor(config=chat_config)
    processed = await preprocessor.preprocess_messages(
        messages=messages,
        on_progress=on_progress,
    )

    preprocessing_applied = len(processed) != len(messages) or processed != messages
    metadata["preprocessing_applied"] = preprocessing_applied

    return {"messages": processed, "metadata": metadata}
