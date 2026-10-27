"""Replace oversized tool results and user messages with session-artifact markers.

The preprocessing node estimates every pending message once, part by part (see
``context_budget.estimate_message_parts``), and hands the estimates of an over-threshold message to
:func:`replace_oversized_message`; nothing here estimates an original again. The original is stored in the
:class:`~autolangchat.artifacts.ArtifactStore` and replaced by a small, human-readable marker. A tool result that
cannot be stored is replaced by a clearly flagged bounded preview, so an unbounded payload is never reinserted; a
user message that cannot be stored raises ``ArtifactError``, which stops the turn explicitly.

Error entries are never replaced: HTTP errors are already capped by
``ToolManager`` and a 401 must stay intact for reactive auth handling.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Collection, Dict, List, Optional, Sequence, Set, Tuple

from ...artifacts import ArtifactError, ArtifactScope, ArtifactStore
from ...context_budget import estimate_message_parts, provider_for_model
from ...defaults import ARTIFACT_MARKER_ENTRY_KEY, ARTIFACT_MARKER_PREVIEW_MAX_CHARS
from .artifact_tools import artifact_tools_bound

logger = logging.getLogger(__name__)

MARKER_SOURCE = "tool_response"
USER_MESSAGE_SOURCE = "user_message"
# Key set on a ``tool_results`` entry whose ``result`` is an artifact marker, and on a user message that is one.
MARKER_ENTRY_KEY = ARTIFACT_MARKER_ENTRY_KEY
READ_HINT = "Call read_content_range(handle, start_line, max_lines) or grep_content(handle, pattern)"

_PLACEHOLDER_HANDLE = "0" * 36  # same length as a store handle (uuid4)

# An object with one long string and otherwise only short scalar fields (for example
# ``{"file_content": "..."}``) is archived as that string, so it keeps its real line breaks and can be read
# and searched by line. The other fields travel in the marker.
_ENVELOPE_MIN_TEXT_CHARS = 4096
_ENVELOPE_MAX_OTHER_FIELDS = 15
_ENVELOPE_MAX_OTHER_STRING_CHARS = 512


@dataclass(frozen=True)
class _Payload:
    """The text archived for one result and how it was derived from it."""

    text: str
    content_type: str
    # Set when ``text`` is one field of an object result; ``other_fields`` are its remaining short fields.
    content_field: Optional[str] = None
    other_fields: Optional[Dict[str, Any]] = None


@dataclass
class SpillOutcome:
    """Result of :func:`replace_oversized_message`."""

    # The message with its oversized content replaced; the input message itself when nothing was replaced.
    message: Dict[str, Any]
    # Entries replaced by an artifact marker.
    spilled: int = 0
    # Entries replaced by a flagged preview because they could not be archived.
    truncated: int = 0

    @property
    def changed(self) -> bool:
        return self.spilled + self.truncated > 0


def _entries(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The pieces of ``message`` that can be replaced, one per estimated part (``estimate_message_parts``).

    A tool message has its ``tool_results``. A user message with text has that text as a single ``{"result": text}``
    entry, unless it already is a marker. Any other message has none.
    """
    role = message.get("role")
    if role == "tool":
        return list(message.get("tool_results") or [])
    content = message.get("content")
    if role == "user" and isinstance(content, str) and content and not message.get(MARKER_ENTRY_KEY):
        return [{"result": content}]
    return []


def _rebuild(message: Dict[str, Any], entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """``message`` with its replaced ``entries`` put back (see :func:`_entries`)."""
    if message.get("role") == "tool":
        mirror = json.dumps(
            [e.get("result", e.get("error")) if isinstance(e, dict) else e for e in entries], default=str
        )
        return {**message, "content": mirror, "tool_results": entries}
    return {**message, "content": str(entries[0]["result"]), MARKER_ENTRY_KEY: True}


def _is_spillable(entry: Any, exempt_tools: Collection[str]) -> bool:
    if not isinstance(entry, dict) or entry.get(MARKER_ENTRY_KEY) or "error" in entry:
        return False
    if entry.get("name") in exempt_tools:
        return False
    status_code = entry.get("status_code")
    if isinstance(status_code, int) and status_code >= 400:
        return False
    return entry.get("result") not in (None, "")


def _entry_tokens(role: str, entry: Dict[str, Any], provider: str) -> int:
    """Tokens of ``entry`` as the model receives it in a message of ``role``; only ever measured on replacements."""
    if role == "tool":
        message = {"role": "tool", "tool_results": [entry]}
    else:
        message = {"role": role, "content": str(entry["result"])}
    return sum(part.tokens for part in estimate_message_parts(message, provider))


def _is_short_scalar(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) <= _ENVELOPE_MAX_OTHER_STRING_CHARS
    return value is None or isinstance(value, (bool, int, float))


def _text_envelope(result: Any) -> Optional[_Payload]:
    """Split ``{"<field>": "<long text>", <short scalar fields>}`` into the text and the other fields."""
    if not isinstance(result, dict) or not 1 <= len(result) <= _ENVELOPE_MAX_OTHER_FIELDS + 1:
        return None
    strings = [(key, value) for key, value in result.items() if isinstance(value, str)]
    if not strings:
        return None
    field_name, text = max(strings, key=lambda item: len(item[1]))
    if len(text) < _ENVELOPE_MIN_TEXT_CHARS:
        return None
    other = {key: value for key, value in result.items() if key != field_name}
    if not all(_is_short_scalar(value) for value in other.values()):
        return None
    return _Payload(text, "text/plain", field_name, other or None)


def _payload(result: Any) -> _Payload:
    """What to archive for ``result``; structured results are pretty-printed so they are line-addressable."""
    if isinstance(result, str):
        return _Payload(result, "text/plain")
    envelope = _text_envelope(result)
    if envelope is not None:
        return envelope
    try:
        return _Payload(json.dumps(result, ensure_ascii=False, indent=2, default=str), "application/json")
    except (TypeError, ValueError, RecursionError):
        return _Payload(str(result), "text/plain")


def _preview(text: str) -> str:
    return text[:ARTIFACT_MARKER_PREVIEW_MAX_CHARS]


def _lifetime(store: Optional[ArtifactStore]) -> str:
    max_age = getattr(store, "max_age_seconds", None)
    if isinstance(max_age, int) and max_age > 0:
        return f"current connection, at most {max_age // 3600 or 1}h (subject to quota)"
    return "current connection (subject to TTL/quota)"


def _base_marker(payload: _Payload, size_bytes: int, *, source: str, tool_name: Optional[str]) -> Dict[str, Any]:
    marker: Dict[str, Any] = {"source": source}
    if tool_name is not None:
        marker["tool_name"] = tool_name
    marker["content_type"] = payload.content_type
    if payload.content_field is not None:
        marker["content_field"] = payload.content_field
        if payload.other_fields:
            marker["other_fields"] = payload.other_fields
    marker.update(size_bytes=size_bytes, preview=_preview(payload.text))
    return marker


def _artifact_marker(
    payload: _Payload,
    size_bytes: int,
    handle: str,
    store: Optional[ArtifactStore],
    read_hint: bool,
    *,
    source: str,
    tool_name: Optional[str] = None,
) -> Dict[str, Any]:
    """The marker that stands in for stored content, whatever it was: a tool result or a user message."""
    marker: Dict[str, Any] = {
        "artifact_handle": handle,
        "message": "Large content stored as a session artifact; only a preview is shown below.",
        **_base_marker(payload, size_bytes, source=source, tool_name=tool_name),
        "lifetime": _lifetime(store),
    }
    if read_hint:
        marker["read_hint"] = READ_HINT
    return marker


def _truncated_preview(entry: Dict[str, Any], payload: _Payload, size_bytes: int) -> Dict[str, Any]:
    return {
        "truncated": True,
        "message": (
            "Tool result is too large for the model context and could not be stored; "
            "only a truncated preview is shown below."
        ),
        **_base_marker(payload, size_bytes, source=MARKER_SOURCE, tool_name=entry.get("name", "")),
    }


def _replacement(
    role: str,
    entry: Dict[str, Any],
    cost: int,
    provider: str,
    store: Optional[ArtifactStore],
    scope: Optional[ArtifactScope],
    read_hint: bool,
) -> Tuple[Optional[Dict[str, Any]], bool, int]:
    """Build the replacement for ``entry`` of a message of ``role``, estimated at ``cost`` tokens.

    Returns ``(new_entry, archived, new_cost)``. ``new_entry`` is ``None`` (and ``new_cost`` is ``cost``)
    if nothing smaller is possible; ``archived`` is ``False`` for a flagged preview, which only a tool result can
    become: an error storing a user message is raised instead.
    """
    payload = _payload(entry["result"])
    text = payload.text
    is_tool = role == "tool"
    origin = (
        {"source": MARKER_SOURCE, "tool_name": entry.get("name", "")} if is_tool else {"source": USER_MESSAGE_SOURCE}
    )

    if store is not None and scope is not None:
        # Size the marker with an approximate size first, so nothing is stored when it would not shrink the result.
        draft = _artifact_marker(payload, len(text), _PLACEHOLDER_HANDLE, store, read_hint, **origin)
        marker_cost = _entry_tokens(role, {**entry, "result": draft, MARKER_ENTRY_KEY: True}, provider)
        if marker_cost >= cost:
            return None, False, cost
        try:
            handle = store.put(scope, text, {"source": origin["source"], "content_type": payload.content_type})
            stored = store.get_meta(scope, handle)
        except (ArtifactError, UnicodeError):
            if not is_tool:
                raise
            logger.warning(
                "Failed to archive tool result of %s; falling back to a truncated preview",
                entry.get("name"),
                exc_info=True,
            )
        else:
            marker = _artifact_marker(payload, stored["size_bytes"], handle, store, read_hint, **origin)
            return {**entry, "result": marker, MARKER_ENTRY_KEY: True}, True, marker_cost

    if not is_tool:
        return None, False, cost
    size_bytes = len(text) if text.isascii() else len(text.encode("utf-8", errors="replace"))
    truncated = {**entry, "result": _truncated_preview(entry, payload, size_bytes)}
    truncated_cost = _entry_tokens(role, truncated, provider)
    if truncated_cost >= cost:
        return None, False, cost
    return truncated, False, truncated_cost


def replace_oversized_message(
    message: Dict[str, Any],
    part_tokens: Sequence[int],
    threshold_tokens: float,
    *,
    message_index: int,
    model_id: str,
    chat_config: Any,
    artifact_store: Optional[ArtifactStore] = None,
    artifact_scope: Optional[ArtifactScope] = None,
    exempt_tools: Collection[str] = (),
) -> SpillOutcome:
    """Replace the oversized parts of a tool or user ``message`` with an artifact marker.

    ``part_tokens[i]`` is the estimate of the message's i-th part made by the preprocessing node, which owns the
    context budget estimate (``context_budget.estimate_message_parts``): a result of a tool message, or the text of
    a user message. The original is not estimated again, only the small replacement, which is kept only when it is
    smaller. A part is replaced when it alone exceeds ``threshold_tokens``. If the message as a whole still exceeds
    it, the largest remaining parts are replaced until it fits, unless the parts that can never be replaced already
    exceed it on their own.

    In a tool message, errors, HTTP statuses >= 400, existing markers and results of ``exempt_tools`` (internal
    tools such as ``read_content_range``, whose output is already sized to the budget) are left untouched, but
    still count toward the message total. ``tool_call_id``, ``name`` and ``status_code`` are preserved and the
    ``content`` mirror is rebuilt from the new results. A result that cannot be stored becomes a flagged preview
    (``truncated=True``), which is what happens to every oversized result when the store or scope is missing.

    A user message gains ``artifact_marker: True`` and its ``content`` becomes the marker as the model receives a
    tool result's marker (``str(marker)``, see ``_to_langchain_messages``). Its metadata carries no
    ``artifact_handle``, so the model is told about the handle once, by the marker. Without a store it is left as it
    is. An error from the store is not caught (``ArtifactError``): a message that cannot be stored stops the turn
    explicitly, as it does when a copy of it is archived.

    Any other message, a user message that already is a marker or has no text, and a replacement that would not be
    smaller are returned as they are. The input message and its entries are not mutated. ``artifact_store`` and
    ``artifact_scope`` must already be validated by the caller (see ``preprocess._artifact_target``).
    """
    outcome = SpillOutcome(message)
    role = message.get("role")
    entries = _entries(message)
    candidates = {i for i, entry in enumerate(entries) if _is_spillable(entry, exempt_tools)}
    store, scope = artifact_store, artifact_scope
    if store is None or scope is None:
        store = scope = None
    # Only a tool result can fall back to a flagged preview; a user message is replaced only if it can be stored.
    if not candidates or len(part_tokens) != len(entries) or (role != "tool" and store is None):
        return outcome

    provider = provider_for_model(model_id)
    read_hint = store is not None and artifact_tools_bound(chat_config, model_id)
    costs = list(part_tokens)
    attempted: Set[int] = set()

    def replace(index: int) -> None:
        attempted.add(index)
        entry, tokens = entries[index], costs[index]
        subject = f"tool result {entry.get('name')}" if role == "tool" else f"user message {message_index}"
        sentence_subject = subject[:1].upper() + subject[1:]
        new_entry, archived, new_tokens = _replacement(role, entry, tokens, provider, store, scope, read_hint)
        if new_entry is None:
            logger.debug("%s (~%d tokens) was kept: a replacement would not be smaller", sentence_subject, tokens)
            return
        entries[index], costs[index] = new_entry, new_tokens
        size_bytes = new_entry["result"]["size_bytes"]
        if archived:
            outcome.spilled += 1
            logger.info(
                "Spilled %s: %s bytes (~%d tokens, threshold %.0f)",
                subject,
                f"{size_bytes:,}",
                tokens,
                threshold_tokens,
            )
            # The handle is only logged at DEBUG; it is also in the marker that reaches the model and the client.
            logger.debug("%s is stored as artifact %s", sentence_subject, new_entry["result"]["artifact_handle"])
        else:
            outcome.truncated += 1
            logger.warning(
                "%s (%s bytes, ~%d tokens) is too large and was not archived; replaced by a truncated preview",
                sentence_subject,
                f"{size_bytes:,}",
                tokens,
            )

    for index in sorted(candidates):
        if costs[index] > threshold_tokens:
            replace(index)

    # Errors, internal tool results and existing markers keep their tokens however many results are replaced.
    fixed = sum(tokens for index, tokens in enumerate(costs) if index not in candidates)
    if fixed <= threshold_tokens:
        while sum(costs) > threshold_tokens:
            remaining = [index for index in sorted(candidates) if index not in attempted]
            if not remaining:
                break
            replace(max(remaining, key=lambda index: costs[index]))

    if outcome.changed:
        outcome.message = _rebuild(message, entries)
    return outcome
