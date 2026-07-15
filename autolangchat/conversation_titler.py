"""
Auto-title generation for persisted conversations.

Generates a short, human-readable title for a conversation from its first
turn(s), used to populate the conversation-list sidebar before the user
gives it an explicit name.

The heavy lifting (deciding *when* to call this, persisting the result via
``ConversationStore.update_conversation``, and notifying the client with a
``conversation_titled`` message) lives in the WebSocket handler — this
module only knows how to turn a list of message dicts into a title string.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TITLE_SYSTEM_PROMPT = (
    "Generate a short title (5\u20138 words max) capturing the main topic. "
    "Return only the title, no quotes or formatting."
)

# Safety-net cap on the generated title length. The system prompt asks for
# 5-8 words, but LLM output isn't guaranteed to comply — this keeps a
# misbehaving response from blowing out the sidebar UI or the `title`
# column's practical display width.
_MAX_TITLE_LENGTH = 80

# How many leading turns to include in the prompt transcript. The first
# user message (and, if present, the assistant's reply) is normally enough
# context to name the conversation; including more risks diluting the
# summary with later, unrelated turns.
_MAX_TRANSCRIPT_MESSAGES = 4

_FALLBACK_TITLE = "New Conversation"


async def generate_conversation_title(llm_client: Optional[Any], messages: List[Dict[str, Any]]) -> str:
    """Generate a short conversation title from the first turn(s).

    Parameters
    ----------
    llm_client:
        Any object exposing an async ``ainvoke(messages) -> AIMessage``
        method (e.g. a ``langchain_aws.ChatBedrockConverse`` instance).
        When ``None``, the fallback title is returned immediately without
        attempting a call.
    messages:
        Internal dict-format conversation messages (``{"role": ..., "content": ...}``),
        in chronological order — the same shape used throughout
        :mod:`autolangchat.graph`.

    Returns
    -------
    str
        The generated title, or a fallback derived from the first user
        message if the LLM call fails, returns empty content, or
        ``llm_client`` is ``None``.
    """
    fallback = _fallback_title(messages)

    if llm_client is None:
        return fallback

    transcript = _format_transcript(messages)
    if not transcript:
        return fallback

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        lc_messages = [
            SystemMessage(content=_TITLE_SYSTEM_PROMPT),
            HumanMessage(content=transcript),
        ]
        result = await llm_client.ainvoke(lc_messages)
        title = _sanitize_title(_extract_text(result))
        return title or fallback
    except Exception:
        logger.warning(
            "Conversation title generation failed; falling back to truncated first message.",
            exc_info=True,
        )
        return fallback


def _fallback_title(messages: List[Dict[str, Any]]) -> str:
    """Derive a fallback title from the first user message's content.

    Mirrors the plan's ``message[:50] + "..."`` rule: the first non-empty
    user message, truncated to 50 characters with an ellipsis if longer.
    """
    first_user_content = next(
        (m.get("content", "") for m in messages if m.get("role") == "user" and m.get("content")),
        "",
    )
    content = first_user_content.strip()
    if not content:
        return _FALLBACK_TITLE
    if len(content) <= 50:
        return content
    return content[:50] + "..."


def _format_transcript(messages: List[Dict[str, Any]]) -> str:
    """Render the leading turns as a ``role: content`` transcript for the prompt."""
    lines = []
    for m in messages[:_MAX_TRANSCRIPT_MESSAGES]:
        content = m.get("content")
        if not content:
            continue
        role = m.get("role", "user")
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _extract_text(ai_message: Any) -> str:
    """Extract plain text from a LangChain ``AIMessage``-like response.

    Mirrors ``graph.nodes.llm_call._from_langchain_message``: Claude via
    Bedrock Converse can return structured content (a list of content
    blocks) instead of a plain string.
    """
    content = getattr(ai_message, "content", ai_message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content) if content is not None else ""


def _sanitize_title(raw: str) -> str:
    """Normalize LLM output into a single-line, unquoted, length-capped title."""
    title = " ".join(raw.split())  # collapse whitespace/newlines
    # LLMs sometimes wrap the title in quotes despite instructions not to.
    title = title.strip().strip('"').strip("'").strip()
    if len(title) > _MAX_TITLE_LENGTH:
        title = title[:_MAX_TITLE_LENGTH].rstrip() + "..."
    return title
