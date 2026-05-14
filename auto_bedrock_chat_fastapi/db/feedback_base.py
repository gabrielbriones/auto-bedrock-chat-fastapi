"""
Feedback storage \u2014 abstract interface and authorization protocol.

Concrete backends live in:

* :mod:`auto_bedrock_chat_fastapi.db.feedback_postgres` (production)
* :mod:`auto_bedrock_chat_fastapi.db.feedback_sqlite` (zero-config default)

Use :func:`auto_bedrock_chat_fastapi.db.create_feedback_store` to build the
backend selected by ``ChatConfig.feedback_storage_type``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Protocol, Sequence
from uuid import UUID

from ..models import FeedbackEntry, FeedbackStats, ReviewStatus

logger = logging.getLogger(__name__)


class FeedbackAuthorizer(Protocol):
    """Pluggable authorization hook for feedback submission.

    The default implementation (:class:`AuthenticatedUserAuthorizer`) accepts
    any authenticated user. The dedicated access-control task is expected to
    swap this for a role/group-aware implementation without requiring a
    refactor of :class:`~auto_bedrock_chat_fastapi.websocket_handler.WebSocketChatHandler`.
    """

    def can_submit(self, user_id: Optional[str]) -> bool:
        """Return ``True`` if ``user_id`` may submit feedback."""


class AuthenticatedUserAuthorizer:
    """Default :class:`FeedbackAuthorizer` — any non-whitespace ``user_id`` passes.

    When ``allow_anonymous=True``, anonymous users (``user_id`` falsy) are
    also accepted. Intended for local development / standalone deployments
    where SSO/tool-auth is not configured.
    """

    def __init__(self, allow_anonymous: bool = False) -> None:
        self.allow_anonymous = allow_anonymous

    def can_submit(self, user_id: Optional[str]) -> bool:
        if user_id and user_id.strip():
            return True
        return self.allow_anonymous


class BaseFeedbackStore(ABC):
    """Abstract async data-access layer for feedback entries.

    Concrete backends (Postgres, SQLite) implement the same surface so the
    rest of the codebase \u2014 notably the WebSocket handler \u2014 doesn't
    depend on the storage technology.
    """

    @abstractmethod
    async def open(self) -> None:
        """Acquire any underlying resources and (optionally) bootstrap schema."""

    @abstractmethod
    async def close(self) -> None:
        """Release underlying resources."""

    @abstractmethod
    async def create(self, entry: FeedbackEntry) -> FeedbackEntry:
        """Persist a new feedback entry and return the stored row."""

    @abstractmethod
    async def get(self, feedback_id: UUID) -> Optional[FeedbackEntry]:
        """Return the entry with the given id, or ``None`` if missing."""

    @abstractmethod
    async def list_pending(self, limit: int = 50, offset: int = 0) -> List[FeedbackEntry]:
        """Return pending-review entries oldest-first (admin queue)."""

    @abstractmethod
    async def list_by_tags(self, tags: Sequence[str]) -> List[FeedbackEntry]:
        """Return entries whose ``reviewer_tags`` overlap with ``tags``."""

    @abstractmethod
    async def list_by_date_range(
        self,
        start: datetime,
        end: datetime,
        status: Optional[ReviewStatus] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[FeedbackEntry]:
        """Return entries created within ``[start, end)``, newest-first."""

    @abstractmethod
    async def update_review(
        self,
        feedback_id: UUID,
        status: ReviewStatus,
        reviewer_id: str,
        tags: Sequence[str],
        comment: Optional[str],
    ) -> FeedbackEntry:
        """Apply a reviewer decision; transactional with transition check."""

    @abstractmethod
    async def stats(self) -> FeedbackStats:
        """Return aggregate counts by status and rating."""

    async def __aenter__(self) -> "BaseFeedbackStore":
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
