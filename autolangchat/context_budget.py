"""Model-aware input-token budget estimator (``ContextBudget``).

Bedrock Converse exposes no count-tokens call before invocation, so any
"does this payload fit the model?" decision has to work from an *estimate*.
This module provides that estimate as pure functions (no I/O, no network):

* :func:`classify_content` labels text as ``prose``, ``json`` or
  ``log_tabular`` by inspecting only a bounded prefix
  (``CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS``), so it stays cheap on
  multi-MB payloads and never raises on empty or binary-like input.
* :func:`estimate_tokens` converts characters to tokens with a
  per-provider, per-kind chars/token ratio
  (``CONTEXT_BUDGET_CHARS_PER_TOKEN``). Providers without calibrated ratios
  use the most conservative ratio across calibrated providers
  (``CONTEXT_BUDGET_FALLBACK_CHARS_PER_TOKEN``). Estimates are rounded up.
* :class:`ContextBudget` computes how many input tokens are still free for
  a turn::

      remaining = max_input_tokens
                  - reserved output    (clamp_max_tokens(model_id, max_tokens))
                  - bound tool schemas (0 if the model can't call tools)
                  - current messages   (content + per-message overhead)
                  - safety margin      (safety_fraction * max_input_tokens)

  ``max_input_tokens`` comes from :func:`get_model_profile`, which handles
  region-prefixed inference-profile ids and ``CAPABILITY_OVERRIDES``. When
  the cap is unknown, ``DEFAULT_MAX_INPUT_TOKENS_UNKNOWN`` is used and a
  warning is logged (once per model id); this never raises.

Use :meth:`ContextBudget.for_turn` for the turn's effective model
(``chat_config.model_id`` is already override-resolved by the WebSocket
handler) and :meth:`ContextBudget.for_model` to re-evaluate against another
model, e.g. ``chat_config.fallback_model`` before a fallback retry.

These are estimates, not exact token accounting. The preprocessor
truncation thresholds and the emergency re-truncation in
``graph/nodes/llm_call.py`` remain the backstop when an estimate is wrong.
"""

from __future__ import annotations

import json
import logging
import math
import re
import string
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

from .config import split_model_id
from .defaults import (
    CONTEXT_BUDGET_CHARS_PER_TOKEN,
    CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS,
    CONTEXT_BUDGET_FALLBACK_CHARS_PER_TOKEN,
    CONTEXT_BUDGET_KIND_JSON,
    CONTEXT_BUDGET_KIND_LOG_TABULAR,
    CONTEXT_BUDGET_KIND_PROSE,
    CONTEXT_BUDGET_KINDS,
    CONTEXT_BUDGET_PER_MESSAGE_OVERHEAD_TOKENS,
    DEFAULT_CONTEXT_BUDGET_SAFETY_FRACTION,
    DEFAULT_MAX_INPUT_TOKENS_UNKNOWN,
)
from .model_capabilities import clamp_max_tokens, get_max_output_tokens, get_model_profile, supports_tool_calling

logger = logging.getLogger(__name__)

# Classifier thresholds. Prose has few digits/punctuation relative to
# letters; logs, CSV and tabular dumps (timestamps, ids, separators) have many.
_LOG_SYMBOL_DENSITY_THRESHOLD = 0.25
# "Many short lines" heuristic for line-oriented output with little punctuation.
_LOG_MIN_LINES = 10
_LOG_MAX_AVG_LINE_LENGTH = 60
# Share of non-printable characters above which text is treated as binary-like
# and given the densest (most conservative) ratio.
_BINARY_NONPRINTABLE_THRESHOLD = 0.10
# Characters that can begin structured JSON array values after "[". Log-like
# alphabetic values are checked against complete JSON literals separately.
_JSON_ARRAY_SECOND_CHARS = frozenset('{["]')
# A numeric first array element must be followed by "," and another JSON
# value, so bracketed log prefixes like "[2026-10-15 ...", "[12:00:01]" or
# dmesg-style "[0.123] ..." are not mistaken for JSON.
_JSON_NUMERIC_ARRAY_START = re.compile(r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\s*,\s*[-\d{\["tfn]')

_PRINTABLE = frozenset(string.printable)
_SYMBOLS = frozenset(string.digits + string.punctuation)

_warned_unknown_models: Set[str] = set()


def provider_for_model(model_id: str) -> str:
    """Raw provider key used to look up ratios, e.g. ``"anthropic"``.

    Uses :func:`split_model_id` rather than ``get_model_provider`` because
    the latter returns UI display labels (``"Anthropic"``).
    """
    return split_model_id(model_id or "")[1].lower()


def chars_per_token(kind: str, provider: Optional[str] = None) -> float:
    """Chars/token ratio for ``kind`` and ``provider`` (raw provider key).

    Unknown providers (or ``None``) use the conservative fallback ratios;
    unknown kinds are treated as the densest kind.
    """
    table = CONTEXT_BUDGET_CHARS_PER_TOKEN.get((provider or "").lower(), CONTEXT_BUDGET_FALLBACK_CHARS_PER_TOKEN)
    ratio = table.get(kind)
    if ratio is None:
        ratio = min(table.values())
    return ratio


def _as_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload).decode("utf-8", errors="replace")
    try:
        return json.dumps(payload, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(payload)


def _looks_like_json(text: str, prefix: str) -> bool:
    stripped = prefix.lstrip()
    if not stripped or stripped[0] not in "{[":
        return False
    if len(text) <= CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS:
        try:
            json.loads(text)
            return True
        except (ValueError, RecursionError):
            # A leading "{" is still a strong JSON signal (e.g. truncated
            # or slightly malformed JSON); a leading "[" is too often a log.
            return stripped[0] == "{"
    if stripped[0] == "{":
        return True
    rest = stripped[1:].lstrip()
    if not rest or rest[0] in _JSON_ARRAY_SECOND_CHARS:
        return True
    for literal in ("true", "false", "null"):
        if rest.startswith(literal) and (len(rest) == len(literal) or rest[len(literal)] in ",] \t\r\n"):
            return True
    return _JSON_NUMERIC_ARRAY_START.match(rest) is not None


def classify_content(text: Any) -> str:
    """Classify ``text`` as ``prose``, ``json`` or ``log_tabular``.

    Only the first ``CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS`` characters
    are inspected. Non-string payloads are JSON-serialized first. Never
    raises; empty input is ``prose``.
    """
    text = _as_text(text)
    if not text:
        return CONTEXT_BUDGET_KIND_PROSE
    prefix = text[:CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS]

    if _looks_like_json(text, prefix):
        return CONTEXT_BUDGET_KIND_JSON

    # U+FFFD is what undecodable bytes become after errors="replace".
    nonprintable = sum(1 for ch in prefix if ch == "\ufffd" or (ch not in _PRINTABLE and not ch.isprintable()))
    if nonprintable / len(prefix) > _BINARY_NONPRINTABLE_THRESHOLD:
        return CONTEXT_BUDGET_KIND_LOG_TABULAR

    non_ws = [ch for ch in prefix if not ch.isspace()]
    if non_ws:
        symbols = sum(1 for ch in non_ws if ch in _SYMBOLS)
        if symbols / len(non_ws) >= _LOG_SYMBOL_DENSITY_THRESHOLD:
            return CONTEXT_BUDGET_KIND_LOG_TABULAR

    lines = [line for line in prefix.splitlines() if line.strip()]
    if len(lines) >= _LOG_MIN_LINES and sum(len(line) for line in lines) / len(lines) <= _LOG_MAX_AVG_LINE_LENGTH:
        return CONTEXT_BUDGET_KIND_LOG_TABULAR

    return CONTEXT_BUDGET_KIND_PROSE


@dataclass(frozen=True)
class TextEstimate:
    """Estimated tokens for some text, with the inputs the estimate was made from."""

    tokens: int
    chars: int
    kind: str
    chars_per_token: float
    # Which piece of a message this is, for logs: a tool result's tool name, ``content`` or ``tool_calls``.
    label: str = ""


def estimate_text(text: Any, kind: Optional[str] = None, provider: Optional[str] = None) -> TextEstimate:
    """Like :func:`estimate_tokens`, but also reports the characters, content kind and ratio it used."""
    text = _as_text(text)
    if not text:
        kind = kind or CONTEXT_BUDGET_KIND_PROSE
        return TextEstimate(0, 0, kind, chars_per_token(kind, provider))
    if kind is None:
        kind = classify_content(text)
    ratio = chars_per_token(kind, provider)
    return TextEstimate(math.ceil(len(text) / ratio), len(text), kind, ratio)


def estimate_tokens(text: Any, kind: Optional[str] = None, provider: Optional[str] = None) -> int:
    """Estimated input tokens for ``text`` (rounded up).

    ``kind`` defaults to :func:`classify_content`; ``provider`` is a raw
    provider key (see :func:`provider_for_model`), ``None`` for the
    conservative fallback ratios. Non-string payloads are JSON-serialized.
    """
    return estimate_text(text, kind, provider).tokens


def _tool_call_ids(tool_calls: Any) -> Set[str]:
    if not isinstance(tool_calls, list):
        return set()
    return {tc["id"] for tc in tool_calls if isinstance(tc, dict) and isinstance(tc.get("id"), str)}


def _labelled(estimate: TextEstimate, label: str, extra_tokens: int = 0) -> TextEstimate:
    return TextEstimate(estimate.tokens + extra_tokens, estimate.chars, estimate.kind, estimate.chars_per_token, label)


def _content_parts(
    content: Any, provider: Optional[str], skip_tool_use_ids: Optional[Set[str]] = None
) -> List[TextEstimate]:
    if content is None or content == "":
        return []
    if not isinstance(content, list):
        return [_labelled(estimate_text(content, provider=provider), "content")]
    parts: List[TextEstimate] = []
    for index, block in enumerate(content):
        if (
            skip_tool_use_ids
            and isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("id") in skip_tool_use_ids
        ):
            # Bedrock merges tool_use blocks with tool_calls by id, so the
            # call is sent once and is already charged via tool_calls.
            continue
        if isinstance(block, str):
            text = estimate_text(block, provider=provider)
        elif isinstance(block, dict) and isinstance(block.get("text"), str) and len(block) <= 3:
            # Plain text blocks: {"type": "text", "text": ..., ["index": n]}
            text = estimate_text(block["text"], provider=provider)
        else:
            text = estimate_text(block, kind=CONTEXT_BUDGET_KIND_JSON, provider=provider)
        parts.append(_labelled(text, f"content[{index}]"))
    return parts


def _tool_result_parts(message: Dict[str, Any], provider: Optional[str]) -> List[TextEstimate]:
    overhead = CONTEXT_BUDGET_PER_MESSAGE_OVERHEAD_TOKENS
    parts: List[TextEstimate] = []
    for tr in message.get("tool_results") or []:
        if not isinstance(tr, dict):
            parts.append(_labelled(estimate_text(tr, provider=provider), "result", overhead))
            continue
        text = estimate_text(str(tr.get("result") or tr.get("error", "")), provider=provider)
        name_and_id = estimate_tokens(
            f"{tr.get('name', '')}{tr.get('tool_call_id', '')}", kind=CONTEXT_BUDGET_KIND_JSON, provider=provider
        )
        parts.append(_labelled(text, str(tr.get("name") or "result"), overhead + name_and_id))
    return parts


def estimate_message_parts(message: Any, provider: Optional[str] = None) -> List[TextEstimate]:
    """Estimates for each part of one message, in order. Their tokens add up to :func:`estimate_message_tokens`.

    A part is a piece of text the model receives. For an internal ``tool`` message, each result is a part: it is sent
    as its own ``ToolMessage`` (as in ``llm_call._to_langchain_messages``), so it is charged its own framing and its
    name/id. For any other message the parts are its content (one part, or one per block of a list content) and its
    ``tool_calls``; the framing of the message is charged to its first part, and an empty message is a single empty part.

    ``tokens`` is the part's total. ``chars``, ``kind`` and ``chars_per_token`` describe its text, and ``label`` says
    which piece it is: a tool result's name, ``content`` (``content[i]`` for a block) or ``tool_calls``.

    Accepts the graph's internal message dicts (``role``/``content``/``tool_calls``/``tool_results``) or LangChain
    ``BaseMessage`` objects.
    """
    if isinstance(message, dict):
        if message.get("role") == "tool":
            return _tool_result_parts(message, provider)
        content = message.get("content")
        tool_calls = message.get("tool_calls")
    else:
        content = getattr(message, "content", message)
        tool_calls = getattr(message, "tool_calls", None)

    parts = _content_parts(content, provider, _tool_call_ids(tool_calls))
    if tool_calls:
        parts.append(
            _labelled(estimate_text(tool_calls, kind=CONTEXT_BUDGET_KIND_JSON, provider=provider), "tool_calls")
        )
    if not parts:
        parts.append(_labelled(estimate_text("", provider=provider), "content"))
    parts[0] = _labelled(parts[0], parts[0].label, CONTEXT_BUDGET_PER_MESSAGE_OVERHEAD_TOKENS)
    return parts


def estimate_message_tokens(message: Any, provider: Optional[str] = None) -> int:
    """Estimated input tokens for one message, including per-message overhead.

    The sum of :func:`estimate_message_parts`: an internal ``tool`` message expands into one ``ToolMessage`` per
    result, so each result is charged its own overhead.
    """
    return sum(part.tokens for part in estimate_message_parts(message, provider))


def estimate_messages_tokens(messages: Optional[Iterable[Any]], provider: Optional[str] = None) -> int:
    """Sum of :func:`estimate_message_tokens` over ``messages``."""
    return sum(estimate_message_tokens(m, provider) for m in (messages or []))


def _serialize_tool(tool: Any) -> str:
    if isinstance(tool, (dict, str)):
        return _as_text(tool)
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool

        return json.dumps(convert_to_openai_tool(tool), default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - estimate must never fail on an odd tool object
        name = getattr(tool, "name", "")
        description = getattr(tool, "description", "")
        args = getattr(tool, "args", None)
        return f"{name}{description}{_as_text(args)}"


def estimate_tool_schema_tokens(tools: Optional[Iterable[Any]], provider: Optional[str] = None) -> int:
    """Estimated input tokens for the JSON schemas of ``tools``.

    ``tools`` is what gets passed to ``bind_tools`` (LangChain tools or
    schema dicts). Schemas are serialized via ``convert_to_openai_tool``
    and estimated as JSON.
    """
    return sum(
        estimate_tokens(_serialize_tool(t), kind=CONTEXT_BUDGET_KIND_JSON, provider=provider) for t in (tools or [])
    )


def _max_input_tokens(model_id: str) -> "tuple[int, bool]":
    cap = get_model_profile(model_id).get("max_input_tokens") if model_id else None
    if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
        return cap, True
    if model_id not in _warned_unknown_models:
        _warned_unknown_models.add(model_id)
        logger.warning(
            "ContextBudget: unknown max_input_tokens for model '%s'; using conservative default of %d tokens",
            model_id,
            DEFAULT_MAX_INPUT_TOKENS_UNKNOWN,
        )
    return DEFAULT_MAX_INPUT_TOKENS_UNKNOWN, False


def _reserved_output_tokens(model_id: str, max_tokens: Optional[int]) -> int:
    reserved = clamp_max_tokens(model_id, max_tokens)
    if reserved is None:
        # No explicit max_tokens: Bedrock may use up to the model's output cap.
        reserved = get_max_output_tokens(model_id) or 0
    return max(0, reserved)


@dataclass(frozen=True)
class ContextBudget:
    """Input-token budget for one model invocation.

    Build with :meth:`for_turn` or :meth:`for_model`; all fields are token
    estimates except ``model_id``/``provider``/``max_input_tokens_known``.
    """

    model_id: str
    provider: str
    max_input_tokens: int
    max_input_tokens_known: bool
    reserved_output_tokens: int
    tool_schema_tokens: int
    message_tokens: int
    safety_margin_tokens: int

    @classmethod
    def for_model(
        cls,
        model_id: str,
        *,
        max_tokens: Optional[int] = None,
        tools: Optional[Iterable[Any]] = None,
        messages: Optional[Iterable[Any]] = None,
        safety_fraction: float = DEFAULT_CONTEXT_BUDGET_SAFETY_FRACTION,
    ) -> "ContextBudget":
        """Budget for ``model_id`` given the turn's ``tools`` and ``messages``.

        ``tools`` are reserved only if ``model_id`` supports tool calling,
        mirroring ``llm_call._build_llm`` (which skips ``bind_tools``
        otherwise). Use this to re-check against ``chat_config.fallback_model``.
        """
        model_id = model_id or ""
        provider = provider_for_model(model_id)
        max_input, known = _max_input_tokens(model_id)
        tool_tokens = estimate_tool_schema_tokens(tools, provider) if tools and supports_tool_calling(model_id) else 0
        fraction = min(max(float(safety_fraction), 0.0), 1.0)
        return cls(
            model_id=model_id,
            provider=provider,
            max_input_tokens=max_input,
            max_input_tokens_known=known,
            reserved_output_tokens=_reserved_output_tokens(model_id, max_tokens),
            tool_schema_tokens=tool_tokens,
            message_tokens=estimate_messages_tokens(messages, provider),
            safety_margin_tokens=math.ceil(max_input * fraction),
        )

    @classmethod
    def for_turn(cls, chat_config: Any, messages: Optional[Iterable[Any]] = None) -> "ContextBudget":
        """Budget for the turn's effective model (``chat_config.model_id``).

        ``chat_config`` is the per-turn config passed by the WebSocket
        handler, so runtime ``model_id``/``max_tokens`` overrides are
        already applied. Tool reservation uses ``chat_config.langchain_tools``
        (the tools actually bound to the model).
        """
        return cls.for_model(
            chat_config.model_id,
            max_tokens=getattr(chat_config, "max_tokens", None),
            tools=getattr(chat_config, "langchain_tools", None),
            messages=messages,
            safety_fraction=getattr(
                chat_config, "context_budget_safety_fraction", DEFAULT_CONTEXT_BUDGET_SAFETY_FRACTION
            ),
        )

    @property
    def remaining_tokens(self) -> int:
        """Input tokens still available for new content (never negative)."""
        used = self.reserved_output_tokens + self.tool_schema_tokens + self.message_tokens + self.safety_margin_tokens
        return max(0, self.max_input_tokens - used)

    def estimate(self, payload: Any, kind: Optional[str] = None) -> int:
        """Estimated tokens for ``payload`` using this budget's provider ratios."""
        return estimate_tokens(payload, kind=kind, provider=self.provider)

    def fits(self, payload: Any, kind: Optional[str] = None) -> bool:
        """Whether ``payload`` fits in :attr:`remaining_tokens` as a new message.

        Charges one per-message overhead, since a payload is inlined as its
        own message (tool result or user message).
        """
        return self.estimate(payload, kind) + CONTEXT_BUDGET_PER_MESSAGE_OVERHEAD_TOKENS <= self.remaining_tokens

    def allowance_for(self, kind: str) -> int:
        """Characters of content ``kind`` that fit in :attr:`remaining_tokens`.

        Accounts for one per-message overhead, like :meth:`fits`. Returns 0
        when nothing fits.
        """
        if kind not in CONTEXT_BUDGET_KINDS:
            raise ValueError(f"Unknown content kind {kind!r}; expected one of {CONTEXT_BUDGET_KINDS}")
        tokens = self.remaining_tokens - CONTEXT_BUDGET_PER_MESSAGE_OVERHEAD_TOKENS
        if tokens <= 0:
            return 0
        return math.floor(tokens * chars_per_token(kind, self.provider))

    def as_dict(self) -> Dict[str, Any]:
        """Plain-dict view (for logging/metadata)."""
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "max_input_tokens": self.max_input_tokens,
            "max_input_tokens_known": self.max_input_tokens_known,
            "reserved_output_tokens": self.reserved_output_tokens,
            "tool_schema_tokens": self.tool_schema_tokens,
            "message_tokens": self.message_tokens,
            "safety_margin_tokens": self.safety_margin_tokens,
            "remaining_tokens": self.remaining_tokens,
        }


__all__: List[str] = [
    "ContextBudget",
    "chars_per_token",
    "classify_content",
    "TextEstimate",
    "estimate_message_parts",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "estimate_text",
    "estimate_tokens",
    "estimate_tool_schema_tokens",
    "provider_for_model",
]
