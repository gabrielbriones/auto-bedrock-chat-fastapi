"""Ephemeral, bounded session artifacts; no filesystem or model-visible tools."""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, Optional

from .defaults import (
    ARTIFACT_READ_MAX_BYTES,
    ARTIFACT_READ_MAX_LINES,
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
    expires_at: float


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
                scope, text, dict(metadata), digest, size, time.monotonic() + self.max_age_seconds
            )
            self._puts += 1
            logger.debug("Stored artifact, size %d bytes", size)
            return handle

    def read_region(
        self, scope: ArtifactScope, handle: str, *, start_line: int = 1, max_lines: int = 100
    ) -> Dict[str, Any]:
        """Read a bounded region internally; this is not an assistant tool."""
        if start_line < 1 or not 1 <= max_lines <= ARTIFACT_READ_MAX_LINES:
            raise ValueError(f"Expected start_line >= 1 and 1 <= max_lines <= {ARTIFACT_READ_MAX_LINES}")
        with self._lock:
            self._validate(scope, read=True)
            self.expire()
            artifact = self._artifacts.get(handle)
            if (
                artifact is None
                or artifact.scope.connection_id != scope.connection_id
                or artifact.scope.thread_id != scope.thread_id
                or artifact.scope.user_id != scope.user_id
            ):
                self._failed_reads += 1
                raise ArtifactError("Artifact is unavailable in this scope")
        parts: list[str] = []
        remaining = ARTIFACT_READ_MAX_BYTES
        total_lines = 0
        offset = 0
        byte_truncated = False

        def collect_line(end: int) -> None:
            nonlocal total_lines, offset, remaining, byte_truncated
            total_lines += 1
            if start_line <= total_lines < start_line + max_lines and not byte_truncated:
                # At most one character per remaining byte can fit.
                candidate = artifact.text[offset : min(end, offset + remaining)]
                encoded = candidate.encode("utf-8")
                bounded = encoded[:remaining].decode("utf-8", errors="ignore")
                parts.append(bounded)
                remaining -= len(bounded.encode("utf-8"))
                if len(bounded) < end - offset:
                    byte_truncated = True
            offset = end

        for boundary in re.finditer(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]", artifact.text):
            collect_line(boundary.end())
        if offset < len(artifact.text):
            collect_line(len(artifact.text))

        with self._lock:
            self._validate(scope, read=True)
            self.expire()
            if self._artifacts.get(handle) is not artifact:
                self._failed_reads += 1
                raise ArtifactError("Artifact is unavailable in this scope")
            return {
                "text": "".join(parts),
                "total_lines": total_lines,
                "truncated": byte_truncated or start_line - 1 + max_lines < total_lines,
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
