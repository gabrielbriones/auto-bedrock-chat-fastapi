"""``read_content_range`` and ``grep_content``: bounded reads of session artifacts.

Both are session-bound ``InternalTool`` handlers over ``ArtifactStore``. The
store does the scoped, revalidated reads; the handlers validate the model's
arguments, apply the configured and context-budget caps, and format the result.

Sizing
------
Tool results are inlined into the conversation as ``json.dumps`` output, which
ASCII-escapes text (``é`` becomes six characters, ``\\n`` two). The handlers
therefore size the returned text by its escaped length against the turn's
``ContextBudget`` allowance (using the strictest content kind), on top of the
``artifact_read_max_lines`` / ``artifact_read_max_bytes`` UTF-8 caps. When too
little budget is left to return useful content they report
``context_exhausted`` instead of reading.

Errors
------
- ``invalid_arguments``: unknown, mistyped or out-of-range arguments
  (including values above the configured caps), or a range past the end.
- ``artifact_unavailable``: unknown, expired, revoked or other-scope handles.
  The message is identical in every case, so it never reveals other sessions.
- ``context_exhausted``: the remaining context cannot fit a useful page.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

from ...artifacts import ArtifactError
from ...defaults import (
    ARTIFACT_READ_MAX_BYTES,
    ARTIFACT_READ_MAX_LINES,
    ARTIFACT_SEARCH_MAX_CONTEXT_LINES,
    ARTIFACT_SEARCH_MAX_MATCHES,
    ARTIFACT_SEARCH_MAX_PATTERN_CHARS,
    ARTIFACT_SEARCH_REGEX_MAX_LINE_CHARS,
    ARTIFACT_TOOL_DEFAULT_MAX_LINES,
    ARTIFACT_TOOL_DEFAULT_MAX_MATCHES,
    CONTEXT_BUDGET_KINDS,
)
from .internal import ARTIFACT_UNAVAILABLE, CONTEXT_EXHAUSTED, INVALID_ARGUMENTS, InternalTool, ToolContext, tool_error

READ_CONTENT_RANGE = "read_content_range"
GREP_CONTENT = "grep_content"
READ_ARTIFACT_RANGE_ALIAS = "read_artifact_range"
GREP_ARTIFACT_ALIAS = "grep_artifact"

# Escaped characters held back for the result's other fields (numbers, flags, hint).
_ENVELOPE_RESERVE_CHARS = 512
# Below this many escaped characters of text a page is not worth returning.
_MIN_TEXT_CHARS = 256
# One UTF-8 character is at most four bytes; a byte page must fit one.
_MIN_MAX_BYTES = 4

_UNAVAILABLE_MESSAGE = (
    "This artifact is not available. It may have expired; re-run the tool that produced it to get a new handle."
)
_EXHAUSTED_MESSAGE = (
    "Not enough context is left in this conversation to return artifact content. "
    "Answer with what you already have, or ask the user to start a new conversation."
)


class _InvalidArguments(ValueError):
    """A model argument failed validation; reported as ``invalid_arguments``."""


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def _given(arguments: Dict[str, Any], name: str) -> bool:
    # LangChain's StructuredTool passes omitted optional arguments as None.
    return arguments.get(name) is not None


def _check_names(arguments: Dict[str, Any], allowed: FrozenSet[str]) -> None:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise _InvalidArguments(f"Unknown argument(s): {', '.join(unknown)}")


def _str_arg(arguments: Dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise _InvalidArguments(f"{name} must be a non-empty string")
    return value


def _int_arg(arguments: Dict[str, Any], name: str, default: int, minimum: int, maximum: Optional[int] = None) -> int:
    if not _given(arguments, name):
        return default
    value = arguments[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _InvalidArguments(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        expected = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        raise _InvalidArguments(f"{name} must be {expected}, got {value}")
    return value


def _bool_arg(arguments: Dict[str, Any], name: str, default: bool) -> bool:
    if not _given(arguments, name):
        return default
    value = arguments[name]
    if not isinstance(value, bool):
        raise _InvalidArguments(f"{name} must be a boolean")
    return value


def _config_cap(ctx: ToolContext, field: str, default: int) -> int:
    value = getattr(ctx.chat_config, field, default)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def _char_cost(char: str) -> int:
    """Characters ``char`` occupies in ``json.dumps`` output (``ensure_ascii=True``)."""
    code = ord(char)
    if char in '"\\\b\f\n\r\t':
        return 2
    if 0x20 <= code < 0x7F:
        return 1
    return 12 if code > 0xFFFF else 6


def _escaped_len(text: str) -> int:
    return len(json.dumps(text)) - 2


def _escaped_prefix(text: str, limit: int) -> str:
    """Longest prefix of ``text`` whose JSON-escaped form fits in ``limit`` characters."""
    used = 0
    for index, char in enumerate(text):
        used += _char_cost(char)
        if used > limit:
            return text[:index]
    return text


def _text_room(ctx: ToolContext) -> Optional[int]:
    """Escaped characters of text the result may carry, or ``None`` without a budget."""
    budget = ctx.context_budget
    if budget is None:
        return None
    allowance = min(budget.allowance_for(kind) for kind in CONTEXT_BUDGET_KINDS)
    return allowance - _ENVELOPE_RESERVE_CHARS


async def _call_store(method: Callable[..., Dict[str, Any]], *args: Any, **kwargs: Any) -> Tuple[Any, Any]:
    """Run a store read off the event loop; return ``(result, None)`` or ``(None, tool_error)``."""
    try:
        return await asyncio.to_thread(method, *args, **kwargs), None
    except ArtifactError:
        return None, tool_error(ARTIFACT_UNAVAILABLE, _UNAVAILABLE_MESSAGE)
    except ValueError as error:
        return None, tool_error(INVALID_ARGUMENTS, str(error))


# ---------------------------------------------------------------------------
# read_content_range
# ---------------------------------------------------------------------------

_READ_ARGUMENTS = frozenset({"handle", "start_line", "max_lines", "start_byte", "max_bytes"})


async def read_content_range(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    """Page through an artifact by lines, or by UTF-8 bytes for giant single lines."""
    line_cap = _config_cap(ctx, "artifact_read_max_lines", ARTIFACT_READ_MAX_LINES)
    byte_cap = _config_cap(ctx, "artifact_read_max_bytes", ARTIFACT_READ_MAX_BYTES)
    try:
        _check_names(arguments, _READ_ARGUMENTS)
        handle = _str_arg(arguments, "handle")
        byte_mode = _given(arguments, "start_byte") or _given(arguments, "max_bytes")
        if byte_mode and (_given(arguments, "start_line") or _given(arguments, "max_lines")):
            raise _InvalidArguments("Use either start_line/max_lines or start_byte/max_bytes, not both")
        if byte_mode:
            start_byte = _int_arg(arguments, "start_byte", 0, minimum=0)
            max_bytes = _int_arg(arguments, "max_bytes", byte_cap, minimum=_MIN_MAX_BYTES, maximum=byte_cap)
        else:
            start_line = _int_arg(arguments, "start_line", 1, minimum=1)
            default_lines = min(ARTIFACT_TOOL_DEFAULT_MAX_LINES, line_cap)
            max_lines = _int_arg(arguments, "max_lines", default_lines, minimum=1, maximum=line_cap)
    except _InvalidArguments as error:
        return tool_error(INVALID_ARGUMENTS, str(error))

    room = _text_room(ctx)
    if room is not None and room < _MIN_TEXT_CHARS:
        return tool_error(CONTEXT_EXHAUSTED, _EXHAUSTED_MESSAGE)
    if byte_mode:
        return await _read_bytes(ctx, handle, start_byte, max_bytes, room)
    return await _read_lines(ctx, handle, start_line, max_lines, byte_cap, room)


async def _read_lines(
    ctx: ToolContext, handle: str, start_line: int, max_lines: int, byte_cap: int, room: Optional[int]
) -> Dict[str, Any]:
    region, error = await _call_store(
        ctx.artifact_store.read_region,
        ctx.artifact_scope,
        handle,
        start_line=start_line,
        max_lines=max_lines,
        max_bytes=byte_cap if room is None else min(byte_cap, room),
    )
    if error:
        return error
    total_lines = region["total_lines"]
    if start_line > max(total_lines, 1):
        return tool_error(
            INVALID_ARGUMENTS, f"start_line {start_line} is past the end of the artifact ({total_lines} lines)"
        )

    rendered: List[str] = []
    used = 0
    partial: Optional[Dict[str, Any]] = None
    for entry in region["lines"]:
        # A line the store cut short after other lines is re-read whole on the next page.
        if entry["clipped"] and rendered:
            break
        prefix = f"{entry['line']}: "
        body = entry["text"]
        cost = (2 if rendered else 0) + _escaped_len(prefix + body)
        clipped = entry["clipped"]
        if room is not None and used + cost > room:
            if rendered:
                break
            body = _escaped_prefix(body, room - _escaped_len(prefix))
            cost = _escaped_len(prefix + body)
            clipped = True
        rendered.append(prefix + body)
        used += cost
        if clipped:
            partial = {
                "line": entry["line"],
                "next_start_byte": entry["start_byte"] + len(body.encode("utf-8")),
            }
            break

    end_line = start_line + len(rendered) - 1
    next_start_line = end_line + 1 if end_line < total_lines else None
    result: Dict[str, Any] = {
        "text": "\n".join(rendered),
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": total_lines,
        "has_more": next_start_line is not None or partial is not None,
        "next_start_line": next_start_line,
    }
    if partial is not None:
        result["line_truncated"] = True
        result["next_start_byte"] = partial["next_start_byte"]
        hint = (
            f"Line {partial['line']} is longer than one page; continue it with start_byte={partial['next_start_byte']}"
        )
        if next_start_line is not None:
            hint += f", or skip to the next line with start_line={next_start_line}"
        result["hint"] = hint + "."
    return result


async def _read_bytes(
    ctx: ToolContext, handle: str, start_byte: int, max_bytes: int, room: Optional[int]
) -> Dict[str, Any]:
    chunk, error = await _call_store(
        ctx.artifact_store.read_bytes,
        ctx.artifact_scope,
        handle,
        start_byte=start_byte,
        max_bytes=max_bytes if room is None else min(max_bytes, room),
    )
    if error:
        return error
    text = chunk["text"]
    if room is not None and _escaped_len(text) > room:
        text = _escaped_prefix(text, room)
    end_byte = chunk["start_byte"] + len(text.encode("utf-8"))
    has_more = end_byte < chunk["total_bytes"]
    return {
        "text": text,
        "start_byte": chunk["start_byte"],
        "end_byte": end_byte,
        "total_bytes": chunk["total_bytes"],
        "has_more": has_more,
        "next_start_byte": end_byte if has_more else None,
    }


# ---------------------------------------------------------------------------
# grep_content
# ---------------------------------------------------------------------------

_GREP_ARGUMENTS = frozenset({"handle", "pattern", "context_lines", "max_matches", "regex"})


async def grep_content(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    """Find lines in an artifact: case-insensitive literal by default, regex on request."""
    byte_cap = _config_cap(ctx, "artifact_read_max_bytes", ARTIFACT_READ_MAX_BYTES)
    try:
        _check_names(arguments, _GREP_ARGUMENTS)
        handle = _str_arg(arguments, "handle")
        pattern = _str_arg(arguments, "pattern")
        regex = _bool_arg(arguments, "regex", False)
        if regex and len(pattern) > ARTIFACT_SEARCH_MAX_PATTERN_CHARS:
            raise _InvalidArguments(f"A regex pattern must be at most {ARTIFACT_SEARCH_MAX_PATTERN_CHARS} characters")
        context_lines = _int_arg(arguments, "context_lines", 0, minimum=0, maximum=ARTIFACT_SEARCH_MAX_CONTEXT_LINES)
        max_matches = _int_arg(
            arguments, "max_matches", ARTIFACT_TOOL_DEFAULT_MAX_MATCHES, minimum=1, maximum=ARTIFACT_SEARCH_MAX_MATCHES
        )
    except _InvalidArguments as error:
        return tool_error(INVALID_ARGUMENTS, str(error))

    room = _text_room(ctx)
    if room is not None and room < _MIN_TEXT_CHARS:
        return tool_error(CONTEXT_EXHAUSTED, _EXHAUSTED_MESSAGE)

    found, error = await _call_store(
        ctx.artifact_store.search,
        ctx.artifact_scope,
        handle,
        pattern,
        context_lines=context_lines,
        max_matches=max_matches,
        regex=regex,
        max_bytes=byte_cap if room is None else min(byte_cap, room),
    )
    if error:
        return error

    rendered: List[str] = []
    used = 0
    matches = 0
    truncated = found["truncated"]
    previous: Optional[int] = None
    entries = found["lines"]
    for index, entry in enumerate(entries):
        # grep style: "12: match", "12- context", "--" between non-adjacent groups.
        parts = ["--"] if previous is not None and entry["line"] != previous + 1 else []
        marker = ":" if entry["match"] else "-"
        suffix = " [line clipped]" if entry["clipped"] else ""
        parts.append(f"{entry['line']}{marker} {entry['text']}{suffix}")
        cost = sum(_escaped_len(part) + 2 for part in parts)
        if room is not None and used + cost > room:
            truncated = truncated or any(later["match"] for later in entries[index:])
            break
        rendered.extend(parts)
        used += cost
        previous = entry["line"]
        matches += entry["match"]

    result: Dict[str, Any] = {
        "text": "\n".join(rendered),
        "match_count": matches,
        "total_lines": found["total_lines"],
        "truncated": truncated,
    }
    hints = []
    if truncated:
        hints.append("More matches exist; narrow the pattern or lower context_lines.")
    if found["partially_searched_lines"]:
        result["partially_searched_lines"] = found["partially_searched_lines"]
        hints.append(
            f"The regex only searched the first {ARTIFACT_SEARCH_REGEX_MAX_LINE_CHARS} characters of "
            f"{found['partially_searched_lines']} long line(s); use a literal pattern to search them fully."
        )
    if hints:
        result["hint"] = " ".join(hints)
    return result


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_HANDLE_SCHEMA = {
    "type": "string",
    "description": "Artifact handle from a message that says content was archived.",
}

READ_CONTENT_RANGE_DESC: Dict[str, Any] = {
    "name": READ_CONTENT_RANGE,
    "description": (
        "Read part of an archived artifact (content that was too large to show inline). "
        "Returns 1-based, line-numbered text ('12: ...'). Page with next_start_line while has_more is true. "
        "For a single huge line (e.g. minified JSON), page by bytes with start_byte/max_bytes and next_start_byte "
        "instead. Do not combine line and byte arguments."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "handle": _HANDLE_SCHEMA,
            "start_line": {"type": "integer", "description": "First line to read, 1-based. Default 1."},
            "max_lines": {
                "type": "integer",
                "description": f"Maximum lines to return. Default {ARTIFACT_TOOL_DEFAULT_MAX_LINES}.",
            },
            "start_byte": {
                "type": "integer",
                "description": "Byte-paging mode: UTF-8 byte offset to start at (use next_start_byte).",
            },
            "max_bytes": {
                "type": "integer",
                "description": "Byte-paging mode: maximum UTF-8 bytes to return.",
            },
        },
        "required": ["handle"],
    },
}

GREP_CONTENT_DESC: Dict[str, Any] = {
    "name": GREP_CONTENT,
    "description": (
        "Search an archived artifact for lines containing a pattern. Matching is a case-insensitive literal "
        "substring by default; set regex=true for an RE2 regular expression (no backreferences or lookaround; "
        f"matched one line at a time, at most {ARTIFACT_SEARCH_MAX_PATTERN_CHARS} characters). Returns grep-style "
        "lines ('12: match', '12- context', "
        "'--' between groups); use read_content_range to read around a match."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "handle": _HANDLE_SCHEMA,
            "pattern": {"type": "string", "description": "Text (or regex, with regex=true) to search for."},
            "context_lines": {
                "type": "integer",
                "description": f"Lines of context before and after each match, 0-{ARTIFACT_SEARCH_MAX_CONTEXT_LINES}. "
                "Default 0.",
            },
            "max_matches": {
                "type": "integer",
                "description": f"Maximum matching lines to return, 1-{ARTIFACT_SEARCH_MAX_MATCHES}. "
                f"Default {ARTIFACT_TOOL_DEFAULT_MAX_MATCHES}.",
            },
            "regex": {"type": "boolean", "description": "Treat pattern as a regular expression. Default false."},
        },
        "required": ["handle", "pattern"],
    },
}


def build_artifact_tools(*, include_aliases: bool = True) -> List[InternalTool]:
    """The artifact tools, ready to pass to ``ToolManager(internal_tools=...)``.

    With ``include_aliases`` the hidden aliases (``read_artifact_range`` /
    ``grep_artifact``) are added: they share the handlers and are dispatchable
    by name, but are never bound to the model (``exposed=False``).
    """
    tools = [
        InternalTool(name=READ_CONTENT_RANGE, function_desc=READ_CONTENT_RANGE_DESC, handler=read_content_range),
        InternalTool(name=GREP_CONTENT, function_desc=GREP_CONTENT_DESC, handler=grep_content),
    ]
    if include_aliases:
        tools += [
            InternalTool(
                name=READ_ARTIFACT_RANGE_ALIAS,
                function_desc={**READ_CONTENT_RANGE_DESC, "name": READ_ARTIFACT_RANGE_ALIAS},
                handler=read_content_range,
                exposed=False,
            ),
            InternalTool(
                name=GREP_ARTIFACT_ALIAS,
                function_desc={**GREP_CONTENT_DESC, "name": GREP_ARTIFACT_ALIAS},
                handler=grep_content,
                exposed=False,
            ),
        ]
    return tools


def artifact_tool_names(*, include_aliases: bool = True) -> FrozenSet[str]:
    """Names reserved by the artifact tools, for ``ToolsGenerator(reserved_names=...)``."""
    return frozenset(tool.name for tool in build_artifact_tools(include_aliases=include_aliases))
