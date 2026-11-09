"""Bedrock prompt caching: where ``cachePoint`` blocks go in a Converse request.

Bedrock caches request prefixes in the order ``tools -> system -> messages``
and only up to a ``cachePoint`` block, so this module decides where those
blocks go for a single LLM call (see
https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html):

* **System prompt.** ``rag_node`` puts the stable part of the system prompt
  (the configured base prompt) first and the per-turn KB/auth context after
  it, recording the split in the system message's metadata under
  :data:`SYSTEM_PREFIX_CHARS_KEY`. One checkpoint goes between the two, so a
  retrieval turn still reuses the cached tools + base prompt. Tool schemas are
  covered by that same prefix, so they never need a checkpoint of their own.
* **History first.** The remaining checkpoints go on the newest *request
  boundaries*: the message that ends this request, then the message that
  ended each earlier request (the one before each assistant reply). Round N+1
  of a tool loop therefore reads what round N wrote, and a follow-up turn
  within the TTL reads the previous turn.

Every checkpoint needs a cumulative prefix of at least the model's
``prompt_cache_min_tokens`` (estimated here). A smaller prefix is accepted by
Bedrock but never cached, so it would just waste one of the model's
``prompt_cache_max_checkpoints`` slots.

Nothing here runs unless :func:`prompt_caching_active` is true, i.e.
``ChatConfig.prompt_caching_enabled`` is on and the model supports caching.
Otherwise requests are byte-identical to the ones sent before caching existed.

Cache identity is the prefix itself: changing the system prompt (e.g. via
runtime config) or any earlier message changes the prefix and misses the
cache, so stale content is never served.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from .context_budget import estimate_tokens, estimate_tool_schema_tokens, provider_for_model
from .model_capabilities import (
    get_model_profile,
    get_prompt_cache_max_checkpoints,
    get_prompt_cache_max_tokens,
    get_prompt_cache_min_tokens,
    supports_prompt_cache_ttl,
    supports_prompt_caching,
    supports_tool_calling,
)

logger = logging.getLogger(__name__)

# System-message metadata key holding the length (in characters) of the
# stable prefix of ``content``; everything after it varies per turn.
SYSTEM_PREFIX_CHARS_KEY = "cache_prefix_chars"

DEFAULT_PROMPT_CACHE_TTL = "5m"


def prompt_caching_active(chat_config: Any, model_id: Optional[str]) -> bool:
    """Whether cachePoint blocks may be sent to ``model_id`` under ``chat_config``."""
    return bool(
        model_id and getattr(chat_config, "prompt_caching_enabled", False) is True and supports_prompt_caching(model_id)
    )


def cache_point_block(model_id: str, ttl: Optional[str]) -> Dict[str, Any]:
    """A Converse ``cachePoint`` content block for ``model_id``.

    ``ttl`` is omitted for the default 5 minutes, and when the model doesn't
    accept the requested TTL (5-minute-only models can reject the field).
    """
    cache_point: Dict[str, Any] = {"type": "default"}
    if ttl and ttl != DEFAULT_PROMPT_CACHE_TTL:
        if supports_prompt_cache_ttl(model_id, ttl):
            cache_point["ttl"] = ttl
        else:
            logger.debug("Model %s doesn't support prompt-cache ttl=%s; using the 5m default", model_id, ttl)
    return {"cachePoint": cache_point}


def build_system_message(stable: str, variable_parts: Iterable[Optional[str]]) -> Dict[str, Any]:
    """System message with the stable prompt first and per-turn context after it.

    The stable prefix length is recorded under :data:`SYSTEM_PREFIX_CHARS_KEY`
    so :func:`apply_cache_points` can put a checkpoint between the two parts.
    """
    variable = "\n\n".join(part for part in variable_parts if part)
    content = "\n\n".join(part for part in (stable, variable) if part)
    return {"role": "system", "content": content, "metadata": {SYSTEM_PREFIX_CHARS_KEY: len(stable)}}


def system_prefix_chars(message_dicts: Iterable[Dict[str, Any]]) -> Optional[int]:
    """Stable-prefix length recorded on the first system message, if any."""
    for message in message_dicts:
        if message.get("role") == "system":
            value = (message.get("metadata") or {}).get(SYSTEM_PREFIX_CHARS_KEY)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block if isinstance(block, str) else str(block.get("text") or "")
            for block in content
            if isinstance(block, (str, dict))
        )
    return str(content or "")


def _message_tokens(message: Any, provider: str) -> int:
    tokens = estimate_tokens(_content_text(getattr(message, "content", "")), provider=provider)
    for tool_call in getattr(message, "tool_calls", None) or []:
        tokens += estimate_tokens(str(tool_call.get("args") or ""), provider=provider)
    return tokens


def _with_cache_point(content: Any, cache_point: Dict[str, Any]) -> List[Any]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}, cache_point]
    return [*content, cache_point]


def _cacheable_size(tokens: int, min_tokens: int, max_tokens: Optional[int]) -> bool:
    return tokens >= min_tokens and (max_tokens is None or tokens <= max_tokens)


def apply_cache_points(
    lc_messages: List[Any],
    *,
    model_id: str,
    ttl: Optional[str] = DEFAULT_PROMPT_CACHE_TTL,
    system_prefix: Optional[int] = None,
    tools: Optional[List[Any]] = None,
) -> int:
    """Add cachePoint blocks to ``lc_messages`` in place; return how many were added.

    ``lc_messages`` are the LangChain messages about to be sent to
    ``model_id``. ``system_prefix`` is the stable-prefix length of the system
    message (see :func:`build_system_message`); ``None`` treats the whole
    system message as stable. ``tools`` are the tools bound to the call,
    counted towards the cached prefix.

    Callers must check :func:`prompt_caching_active` first.
    """
    from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

    budget = get_prompt_cache_max_checkpoints(model_id)
    min_tokens = get_prompt_cache_min_tokens(model_id) or 0
    max_tokens = get_prompt_cache_max_tokens(model_id)
    if budget <= 0:
        return 0

    provider = provider_for_model(model_id)
    cache_point = cache_point_block(model_id, ttl)
    tool_results_cacheable = bool(get_model_profile(model_id).get("prompt_cache_tool_results", True))
    bound_tools = tools if tools and supports_tool_calling(model_id) else None
    prefix_tokens = estimate_tool_schema_tokens(bound_tools, provider=provider) if bound_tools else 0
    placed = 0

    # Bedrock sends one merged system block list; the stable prefix is the
    # first system message's leading characters.
    first_system = next((i for i, m in enumerate(lc_messages) if isinstance(m, SystemMessage)), None)
    if first_system is not None:
        system_msg = lc_messages[first_system]
        text = system_msg.content if isinstance(system_msg.content, str) else None
        if text:
            split = len(text) if system_prefix is None else min(system_prefix, len(text))
            stable, variable = text[:split], text[split:]
            stable_tokens = prefix_tokens + estimate_tokens(stable, provider=provider)
            if stable.strip() and _cacheable_size(stable_tokens, min_tokens, max_tokens):
                blocks: List[Any] = [{"type": "text", "text": stable}, cache_point]
                if variable.strip():
                    blocks.append({"type": "text", "text": variable})
                lc_messages[first_system] = system_msg.model_copy(update={"content": blocks})
                placed += 1
                budget -= 1
            else:
                logger.debug(
                    "Prompt cache: system prefix ~%d tokens is below the %d-token minimum for %s",
                    stable_tokens,
                    min_tokens,
                    model_id,
                )
        prefix_tokens += sum(_message_tokens(m, provider) for m in lc_messages if isinstance(m, SystemMessage))

    # Cumulative prefix size at the end of every conversation message.
    cumulative: Dict[int, int] = {}
    running = prefix_tokens
    for index, message in enumerate(lc_messages):
        if isinstance(message, SystemMessage):
            continue
        running += _message_tokens(message, provider)
        cumulative[index] = running

    # Newest request boundary first: the last message, then the message just
    # before each assistant reply (the tail of the request that produced it).
    boundaries: List[int] = []
    for index in range(len(lc_messages) - 1, -1, -1):
        message = lc_messages[index]
        is_tail = index == len(lc_messages) - 1 or isinstance(lc_messages[index + 1], AIMessage)
        if is_tail and not isinstance(message, (SystemMessage, AIMessage)):
            boundaries.append(index)

    for index in boundaries:
        if budget <= 0:
            break
        message = lc_messages[index]
        # A user message right after a tool result is merged into the same
        # Bedrock message, so it carries the toolResult too.
        follows_tool_result = index > 0 and isinstance(lc_messages[index - 1], ToolMessage)
        if not tool_results_cacheable and (isinstance(message, ToolMessage) or follows_tool_result):
            continue
        if not _cacheable_size(cumulative[index], min_tokens, max_tokens):
            continue
        lc_messages[index] = message.model_copy(update={"content": _with_cache_point(message.content, cache_point)})
        placed += 1
        budget -= 1

    logger.debug("Prompt cache: placed %d cachePoint block(s) for %s", placed, model_id)
    return placed
