"""Ephemeral, bounded session artifacts; no filesystem or model-visible tools."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass
from threading import RLock
from typing import Any, Deque, Dict, Iterator, List, Optional, Protocol, Tuple

import re2

from .defaults import (
    ARTIFACT_READ_MAX_BYTES,
    ARTIFACT_READ_MAX_LINES,
    ARTIFACT_SEARCH_MAX_CONTEXT_LINES,
    ARTIFACT_SEARCH_MAX_LINE_CHARS,
    ARTIFACT_SEARCH_MAX_MATCHES,
    ARTIFACT_SEARCH_MAX_PATTERN_CHARS,
    ARTIFACT_SEARCH_REGEX_MAX_LINE_CHARS,
    DEFAULT_ARTIFACT_MAX_AGE_SECONDS,
    DEFAULT_ARTIFACT_MAX_BYTES,
    DEFAULT_ARTIFACT_MAX_BYTES_PER_SESSION,
    DEFAULT_ARTIFACT_MAX_ITEMS_PER_SESSION,
)

logger = logging.getLogger(__name__)


class ArtifactError(RuntimeError):
    """Artifact storage is unavailable, revoked, or at capacity."""


@dataclass(frozen=True)
class ArtifactScope:
    connection_id: str
    thread_id: str
    user_id: Optional[str]
    generation: int


@dataclass(frozen=True)
class _Artifact:
    scope: ArtifactScope
    text: str
    metadata: Dict[str, Any]
    digest: str
    size: int
    line_count: int
    expires_at: float


# Same line boundaries as ``str.splitlines``.
_LINE_BOUNDARY = r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]"


def _line_spans(text: str) -> Iterator[Tuple[int, int, int]]:
    """Yield ``(start, content_end, end)`` for each line; ``end`` includes the terminator."""
    start = 0
    for boundary in re.finditer(_LINE_BOUNDARY, text):
        yield start, boundary.start(), boundary.end()
        start = boundary.end()
    if start < len(text):
        yield start, len(text), len(text)


# Encode at most this many characters at a time to bound temporary allocations.
_UTF8_CHUNK_CHARS = 65536
# One UTF-8 character is at most four bytes; byte reads need room for one.
_UTF8_MAX_CHAR_BYTES = 4


def _utf8_len(text: str, stop: int) -> int:
    """Return how many bytes ``text[:stop]`` takes when encoded as UTF-8.

    Converts a character index into a byte offset. Python strings are indexed
    by character, but UTF-8 uses 1 to 4 bytes per character ("a" is 1 byte,
    "é" is 2, "€" is 3, an emoji is 4), so the byte offset can't be computed
    from ``stop`` alone; the prefix has to be encoded and measured.

    Example: ``_utf8_len("aé€x", 3) == 1 + 2 + 3 == 6``.

    The obvious ``len(text[:stop].encode("utf-8"))`` would copy the whole
    prefix (up to the full artifact, potentially many MiB) just to count it.
    Instead the prefix is encoded ``_UTF8_CHUNK_CHARS`` characters at a time
    and the chunk lengths are summed, so temporary memory stays bounded at
    one chunk (at most 4 x 64 Ki bytes) however large ``stop`` is. Splitting
    on character indices is always safe: each character encodes to the same
    bytes no matter which chunk it lands in.
    """
    return sum(
        # Bytes of one chunk: characters [offset, offset + chunk) clipped to stop.
        len(text[offset : min(offset + _UTF8_CHUNK_CHARS, stop)].encode("utf-8"))
        # Chunk start positions: 0, 65536, 131072, ... below stop.
        for offset in range(0, stop, _UTF8_CHUNK_CHARS)
    )


def _char_at_byte(text: str, byte_offset: int) -> Tuple[int, int]:
    """Map a UTF-8 byte offset back to a character position, snapping backwards.

    This is the inverse of ``_utf8_len``. The model pages giant single-line
    artifacts with ``start_byte``, but Python slices by character, so the byte
    offset must be turned into a character index first.

    A byte offset may land in the middle of a multi-byte character. For
    example, in ``"a€b"`` the bytes are ``61 | E2 82 AC | 62``; offsets 2 and 3
    point inside "€". Such an offset is moved back to the start of that
    character, so the read never begins with half a character.

    Returns:
        ``(char_index, byte_index)``: ``text[char_index:]`` is where the read
        starts, and ``byte_index <= byte_offset`` is the byte offset of that
        same position (equal to ``byte_offset`` unless it had to snap back).
        For ``"a€b"``: offset 1 -> ``(1, 1)``, 2 or 3 -> ``(1, 1)``,
        4 -> ``(2, 4)``. An offset past the end returns the end of the text.

    Algorithm: a forward scan in fixed-size character chunks, then an exact
    cut in the chunk that contains the target byte (see the step comments).
    It runs in O(byte_offset) time and uses at most one chunk of memory.
    """
    chars = consumed = 0  # Characters and UTF-8 bytes fully skipped so far.
    while chars < len(text):
        # Step 1: encode the next chunk of up to 64 Ki characters.
        chunk = text[chars : chars + _UTF8_CHUNK_CHARS]
        encoded = chunk.encode("utf-8")
        if consumed + len(encoded) > byte_offset:
            # Step 2: the target byte is inside this chunk. Keep only the bytes
            # before it. If the cut falls inside a multi-byte character, its
            # leading bytes are an incomplete sequence at the end; decoding with
            # errors="ignore" drops exactly that partial character (the rest is
            # valid UTF-8, since it came from encoding a str), which is the
            # "snap back to the previous character boundary" step.
            head = encoded[: byte_offset - consumed].decode("utf-8", errors="ignore")
            # Step 3: len(head) is how many whole characters precede the
            # boundary; re-encoding head gives that boundary's exact byte count.
            return chars + len(head), consumed + len(head.encode("utf-8"))
        # The whole chunk lies before the target: skip it and keep counting.
        chars += len(chunk)
        consumed += len(encoded)
    # byte_offset is at or past the end of the text.
    return chars, consumed


class _Hit(Protocol):
    """The part of a match object that both ``re`` and ``re2`` provide."""

    def start(self) -> int:
        """Return the index of the first matched character."""
        raise NotImplementedError

    def end(self) -> int:
        """Return the index after the last matched character."""
        raise NotImplementedError


class _Matcher(Protocol):
    """The part of a compiled pattern that both ``re`` and ``re2`` provide."""

    def search(self, text: str, pos: int = 0, endpos: Optional[int] = None) -> Optional[_Hit]:
        """Return the first match in ``text[pos:endpos]``, or ``None``."""
        raise NotImplementedError


def _compile_search_pattern(pattern: str, *, regex: bool, ignore_case: bool) -> _Matcher:
    """Compile a literal with ``re`` and a regex with RE2.

    RE2 runs in time linear in the input, so a caller-supplied expression such
    as ``(a+)+$`` cannot backtrack catastrophically or hold the GIL for long.
    The cost is that RE2 rejects backreferences and lookaround, which is
    reported like any other invalid expression.
    """
    if not regex:
        return re.compile(re.escape(pattern), re.IGNORECASE if ignore_case else 0)
    options = re2.Options()
    options.case_sensitive = not ignore_case
    options.log_errors = False
    try:
        return re2.compile(pattern, options)
    except re2.error as error:
        detail = error.args[0] if error.args else error
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise ValueError(f"Invalid regular expression: {detail}") from None


def _clip_line(line: str, hit: Optional[_Hit]) -> Tuple[str, bool]:
    """Bound a long line to a window that keeps the match visible when it fits."""
    if len(line) <= ARTIFACT_SEARCH_MAX_LINE_CHARS:
        return line, False
    offset = 0
    if hit is not None:
        slack = max(0, ARTIFACT_SEARCH_MAX_LINE_CHARS - (hit.end() - hit.start()))
        offset = max(0, min(hit.start() - slack // 2, len(line) - ARTIFACT_SEARCH_MAX_LINE_CHARS))
    return line[offset : offset + ARTIFACT_SEARCH_MAX_LINE_CHARS], True


class ArtifactStore:
    """Process-local storage with atomic quotas and connection/thread ownership."""

    def __init__(
        self,
        *,
        max_age_seconds: int = DEFAULT_ARTIFACT_MAX_AGE_SECONDS,
        max_bytes_per_session: int = DEFAULT_ARTIFACT_MAX_BYTES_PER_SESSION,
        max_items_per_session: int = DEFAULT_ARTIFACT_MAX_ITEMS_PER_SESSION,
        max_bytes: int = DEFAULT_ARTIFACT_MAX_BYTES,
    ):
        if min(max_age_seconds, max_bytes_per_session, max_items_per_session, max_bytes) <= 0:
            raise ValueError("Artifact limits must be positive")
        self.max_age_seconds = max_age_seconds
        self.max_bytes_per_session = max_bytes_per_session
        self.max_items_per_session = max_items_per_session
        self.max_bytes = max_bytes
        self._lock = RLock()
        self._connections: Dict[str, Optional[str]] = {}
        self._active: Dict[str, ArtifactScope] = {}
        self._artifacts: Dict[str, _Artifact] = {}
        self._generation = 0
        self._closed = False
        self._puts = 0
        self._expiries = 0
        self._quota_rejections = 0
        self._failed_reads = 0

    def register_connection(self, connection_id: str, user_id: Optional[str]) -> None:
        with self._lock:
            if self._closed or connection_id in self._connections:
                raise ArtifactError("Artifact connection cannot be registered")
            self._connections[connection_id] = user_id

    def activate(self, connection_id: str, thread_id: str, user_id: Optional[str]) -> ArtifactScope:
        with self._lock:
            if self._closed or connection_id not in self._connections or self._connections[connection_id] != user_id:
                raise ArtifactError("Artifact scope is unavailable")
            active = self._active.get(connection_id)
            if active is None or active.thread_id != thread_id:
                self._generation += 1
                active = ArtifactScope(connection_id, thread_id, user_id, self._generation)
                self._active[connection_id] = active
            return active

    def _validate(self, scope: ArtifactScope, *, read: bool = False) -> None:
        if self._closed or self._active.get(scope.connection_id) != scope:
            if read:
                self._failed_reads += 1
            raise ArtifactError("Artifact scope is unavailable")

    def _owned(self, scope: ArtifactScope, handle: str) -> _Artifact:
        """Return the scope's live artifact, counting a failed read otherwise; callers hold the lock."""
        self._validate(scope, read=True)
        self.expire()
        artifact = self._artifacts.get(handle)
        if artifact is None or (
            artifact.scope.connection_id,
            artifact.scope.thread_id,
            artifact.scope.user_id,
        ) != (scope.connection_id, scope.thread_id, scope.user_id):
            self._failed_reads += 1
            raise ArtifactError("Artifact is unavailable in this scope")
        return artifact

    def _revalidate(self, scope: ArtifactScope, handle: str, artifact: _Artifact) -> None:
        """Fail if the artifact expired, was deleted or lost its scope during an unlocked scan."""
        self._validate(scope, read=True)
        self.expire()
        if self._artifacts.get(handle) is not artifact:
            self._failed_reads += 1
            raise ArtifactError("Artifact is unavailable in this scope")

    def expire(self) -> None:
        with self._lock:
            now = time.monotonic()
            previous_count = len(self._artifacts)
            self._artifacts = {
                handle: artifact for handle, artifact in self._artifacts.items() if artifact.expires_at > now
            }
            self._expiries += previous_count - len(self._artifacts)

    def statistics(self) -> Dict[str, int]:
        """Return live payload gauges and lifetime counters in an atomic snapshot."""
        with self._lock:
            self.expire()
            return {
                "active_artifacts": len(self._artifacts),
                "bytes": sum(artifact.size for artifact in self._artifacts.values()),
                "puts": self._puts,
                "expiries": self._expiries,
                "quota_rejections": self._quota_rejections,
                "failed_reads": self._failed_reads,
            }

    def put(self, scope: ArtifactScope, text: str, metadata: Dict[str, Any]) -> str:
        """Store exact text, deduplicating identical payloads in the owning thread."""
        encoded = text.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        line_count = sum(1 for _ in _line_spans(text))
        with self._lock:
            self._validate(scope)
            self.expire()
            owned = []
            for handle, artifact in self._artifacts.items():
                if artifact.scope.connection_id == scope.connection_id:
                    owned.append(artifact)
                    if (
                        artifact.scope.thread_id == scope.thread_id
                        and artifact.digest == digest
                        and artifact.metadata == metadata
                    ):
                        return handle
            size = len(encoded)
            if (
                len(owned) >= self.max_items_per_session
                or sum(artifact.size for artifact in owned) + size > self.max_bytes_per_session
                or sum(artifact.size for artifact in self._artifacts.values()) + size > self.max_bytes
            ):
                self._quota_rejections += 1
                raise ArtifactError("Artifact storage quota exceeded; content was not archived")
            handle = str(uuid.uuid4())
            self._artifacts[handle] = _Artifact(
                scope, text, dict(metadata), digest, size, line_count, time.monotonic() + self.max_age_seconds
            )
            self._puts += 1
            logger.debug("Stored artifact, size %d bytes, id %s", size, handle)
            return handle

    def read_region(
        self,
        scope: ArtifactScope,
        handle: str,
        *,
        start_line: int = 1,
        max_lines: int = 100,
        max_bytes: int = ARTIFACT_READ_MAX_BYTES,
    ) -> Dict[str, Any]:
        """Read a bounded region internally; this is not an assistant tool.

        ``text`` is the region verbatim, line terminators included, capped at
        ``max_bytes`` UTF-8 bytes. ``lines`` holds the same 1-based lines
        without terminators; ``clipped`` marks a line cut short by the byte
        cap. The first returned line and any clipped line also report their
        ``start_byte`` so the rest can be read with :meth:`read_bytes`.
        """
        if start_line < 1 or not 1 <= max_lines <= ARTIFACT_READ_MAX_LINES:
            raise ValueError(f"Expected start_line >= 1 and 1 <= max_lines <= {ARTIFACT_READ_MAX_LINES}")
        if not 1 <= max_bytes <= ARTIFACT_READ_MAX_BYTES:
            raise ValueError(f"Expected 1 <= max_bytes <= {ARTIFACT_READ_MAX_BYTES}")
        with self._lock:
            artifact = self._owned(scope, handle)
        text = artifact.text
        parts: List[str] = []
        lines: List[Dict[str, Any]] = []
        remaining = max_bytes
        byte_truncated = False

        for number, (start, content_end, end) in enumerate(_line_spans(text), 1):
            if number >= start_line + max_lines or byte_truncated:
                break
            if number < start_line:
                continue
            # At most one character per remaining byte can fit.
            candidate = text[start : min(end, start + remaining)]
            bounded = candidate.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
            parts.append(bounded)
            remaining -= len(bounded.encode("utf-8"))
            byte_truncated = len(bounded) < end - start
            entry: Dict[str, Any] = {
                "line": number,
                "text": bounded[: content_end - start],
                "clipped": len(bounded) < content_end - start,
            }
            if entry["clipped"] or not lines:
                entry["start_byte"] = _utf8_len(text, start)
            lines.append(entry)

        with self._lock:
            self._revalidate(scope, handle, artifact)
            return {
                "text": "".join(parts),
                "lines": lines,
                "total_lines": artifact.line_count,
                "truncated": byte_truncated or start_line - 1 + max_lines < artifact.line_count,
                "metadata": dict(artifact.metadata),
            }

    def read_bytes(
        self,
        scope: ArtifactScope,
        handle: str,
        *,
        start_byte: int = 0,
        max_bytes: int = ARTIFACT_READ_MAX_BYTES,
    ) -> Dict[str, Any]:
        """Read a bounded UTF-8 byte range internally; this is not an assistant tool.

        Both ends snap back to character boundaries, so ``text`` is always
        valid and ``end_byte`` is the exact resume offset. ``max_bytes`` is at
        least one full character, so every read before the end makes progress.
        """
        if start_byte < 0:
            raise ValueError("Expected start_byte >= 0")
        if not _UTF8_MAX_CHAR_BYTES <= max_bytes <= ARTIFACT_READ_MAX_BYTES:
            raise ValueError(f"Expected {_UTF8_MAX_CHAR_BYTES} <= max_bytes <= {ARTIFACT_READ_MAX_BYTES}")
        with self._lock:
            artifact = self._owned(scope, handle)
        if start_byte > artifact.size:
            raise ValueError(f"start_byte {start_byte} is past the end of the artifact ({artifact.size} bytes)")
        start_char, start_at = _char_at_byte(artifact.text, start_byte)
        # At most one character per byte can fit.
        candidate = artifact.text[start_char : start_char + max_bytes]
        bounded = candidate.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        end_byte = start_at + len(bounded.encode("utf-8"))

        with self._lock:
            self._revalidate(scope, handle, artifact)
            return {
                "text": bounded,
                "start_byte": start_at,
                "end_byte": end_byte,
                "total_bytes": artifact.size,
                "truncated": end_byte < artifact.size,
                "metadata": dict(artifact.metadata),
            }

    def get_meta(self, scope: ArtifactScope, handle: str) -> Dict[str, Any]:
        """Describe an artifact without returning its text; this is not an assistant tool."""
        with self._lock:
            artifact = self._owned(scope, handle)
            return {
                "size_bytes": artifact.size,
                "total_lines": artifact.line_count,
                "expires_in_seconds": max(0, math.ceil(artifact.expires_at - time.monotonic())),
                "source": artifact.metadata.get("source"),
                "metadata": dict(artifact.metadata),
            }

    def search(
        self,
        scope: ArtifactScope,
        handle: str,
        pattern: str,
        *,
        context_lines: int = 0,
        max_matches: int = 50,
        regex: bool = False,
        ignore_case: bool = True,
        max_bytes: int = ARTIFACT_READ_MAX_BYTES,
    ) -> Dict[str, Any]:
        """Find matching lines internally; this is not an assistant tool.

        ``pattern`` is literal unless ``regex`` is set, in which case it uses
        RE2 syntax (linear-time; no backreferences or lookaround). Each line is
        matched on its own, without its terminator, so a match never spans
        lines. A regex only sees the first
        ``ARTIFACT_SEARCH_REGEX_MAX_LINE_CHARS`` characters of a longer line, to
        bound the work per line; ``partially_searched_lines``
        counts those lines. ``lines`` holds 1-based matching and context lines
        in order, without duplicates. Long lines are clipped to a window around
        the first match, and the output is capped at ``max_bytes``.
        ``truncated`` means more matching lines exist than were returned.
        """
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("Expected a non-empty pattern")
        if regex and len(pattern) > ARTIFACT_SEARCH_MAX_PATTERN_CHARS:
            raise ValueError(f"Expected a regex of at most {ARTIFACT_SEARCH_MAX_PATTERN_CHARS} characters")
        if not 0 <= context_lines <= ARTIFACT_SEARCH_MAX_CONTEXT_LINES:
            raise ValueError(f"Expected 0 <= context_lines <= {ARTIFACT_SEARCH_MAX_CONTEXT_LINES}")
        if not 1 <= max_matches <= ARTIFACT_SEARCH_MAX_MATCHES:
            raise ValueError(f"Expected 1 <= max_matches <= {ARTIFACT_SEARCH_MAX_MATCHES}")
        if not 1 <= max_bytes <= ARTIFACT_READ_MAX_BYTES:
            raise ValueError(f"Expected 1 <= max_bytes <= {ARTIFACT_READ_MAX_BYTES}")
        compiled = _compile_search_pattern(pattern, regex=regex, ignore_case=ignore_case)

        with self._lock:
            artifact = self._owned(scope, handle)
        text = artifact.text
        lines: List[Dict[str, Any]] = []
        remaining = max_bytes
        before: Deque[Tuple[int, int, int]] = deque(maxlen=context_lines)
        after = 0
        matches = 0
        total_lines = 0
        partially_searched = 0
        truncated = False

        def emit(number: int, line: str, hit: Optional[_Hit] = None) -> bool:
            nonlocal remaining
            clipped_text, clipped = _clip_line(line, hit)
            size = len(clipped_text.encode("utf-8"))
            if size > remaining:
                return False
            remaining -= size
            entry: Dict[str, Any] = {"line": number, "text": clipped_text, "match": hit is not None, "clipped": clipped}
            if hit is not None:
                entry["column"] = hit.start() + 1
            lines.append(entry)
            return True

        def context(number: int, start: int, stop: int) -> bool:
            # Slice only what can be returned; the line was already matched.
            return emit(number, text[start : min(stop, start + ARTIFACT_SEARCH_MAX_LINE_CHARS + 1)])

        for number, (start, stop, _) in enumerate(_line_spans(text), 1):
            total_lines = number
            if truncated:
                continue
            line = text[start:stop]
            if regex and len(line) > ARTIFACT_SEARCH_REGEX_MAX_LINE_CHARS:
                partially_searched += 1
                hit = compiled.search(line, 0, ARTIFACT_SEARCH_REGEX_MAX_LINE_CHARS)
            else:
                hit = compiled.search(line)
            if hit is None:
                if after:
                    after -= 1
                    context(number, start, stop)
                elif context_lines:
                    before.append((number, start, stop))
                continue
            if matches == max_matches:
                truncated = True
                continue
            emitted = all(context(*span) for span in before) and emit(number, line, hit)
            before.clear()
            if not emitted:
                truncated = True
                continue
            matches += 1
            after = context_lines

        with self._lock:
            self._revalidate(scope, handle, artifact)
            return {
                "lines": lines,
                "match_count": matches,
                "total_lines": total_lines,
                "truncated": truncated,
                "partially_searched_lines": partially_searched,
                "metadata": dict(artifact.metadata),
            }

    def contains(self, scope: ArtifactScope, handle: str) -> bool:
        """Report availability without counting a failed read; callers use it to decide whether to re-archive."""
        with self._lock:
            self._validate(scope)
            self.expire()
            artifact = self._artifacts.get(handle)
            return artifact is not None and (
                artifact.scope.connection_id,
                artifact.scope.thread_id,
                artifact.scope.user_id,
            ) == (scope.connection_id, scope.thread_id, scope.user_id)

    def deactivate(self, connection_id: str) -> None:
        """Revoke in-flight scopes without deleting other conversations' artifacts."""
        with self._lock:
            self._active.pop(connection_id, None)

    def delete_thread(self, thread_id: str, user_id: Optional[str]) -> None:
        with self._lock:
            self._artifacts = {
                handle: artifact
                for handle, artifact in self._artifacts.items()
                if (artifact.scope.thread_id, artifact.scope.user_id) != (thread_id, user_id)
            }
            for connection_id, scope in list(self._active.items()):
                if (scope.thread_id, scope.user_id) == (thread_id, user_id):
                    self.deactivate(connection_id)

    def delete_user(self, user_id: str) -> None:
        with self._lock:
            for connection_id, owner in list(self._connections.items()):
                if owner == user_id:
                    self.delete_session(connection_id)
                    self.register_connection(connection_id, user_id)

    def delete_session(self, connection_id: str) -> None:
        with self._lock:
            self._connections.pop(connection_id, None)
            self.deactivate(connection_id)
            self._artifacts = {
                handle: artifact
                for handle, artifact in self._artifacts.items()
                if artifact.scope.connection_id != connection_id
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._artifacts.clear()
            self._connections.clear()
            self._active.clear()
