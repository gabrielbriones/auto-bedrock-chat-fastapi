"""
PostgreSQL-backed feedback store (XMGPLAT-10417).

Production backend for :class:`~.feedback_base.BaseFeedbackStore`. Schema:
``auto_bedrock_chat_fastapi/db/sql/feedback_schema.sql``.

Requires the optional ``[postgres]`` extra::

    pip install auto-bedrock-chat-fastapi[postgres]
"""

from __future__ import annotations

import logging
from datetime import datetime
from importlib import resources
from typing import Any, Iterable, List, Optional, Sequence, Tuple
from uuid import UUID

from ..exceptions import FeedbackError, FeedbackNotFoundError, InvalidStatusTransitionError
from ..models import ALLOWED_REVIEW_TRANSITIONS, FeedbackEntry, FeedbackStats, Rating, ReviewStatus
from .feedback_base import BaseFeedbackStore

logger = logging.getLogger(__name__)


_MISSING_DEPS_MSG = (
    "PostgresFeedbackStore requires the optional PostgreSQL packages. "
    "Install them with:  pip install auto-bedrock-chat-fastapi[postgres]"
)


def _import_psycopg_async() -> Tuple[Any, Any, Any]:
    """Return ``(psycopg, AsyncConnectionPool, Jsonb)`` or raise ``ImportError``."""
    try:
        import psycopg  # noqa: F401
        from psycopg.types.json import Jsonb
        from psycopg_pool import AsyncConnectionPool

        return psycopg, AsyncConnectionPool, Jsonb
    except ImportError as exc:  # pragma: no cover - exercised in env-less CI
        raise ImportError(_MISSING_DEPS_MSG) from exc


# Columns selected from the ``feedback`` table, in fixed order. Used by the
# row → model mapper so query construction and decoding stay in sync.
_FEEDBACK_COLUMNS: Tuple[str, ...] = (
    "id",
    "session_id",
    "user_id",
    "query",
    "ai_response",
    "rating",
    "score",
    "correction_text",
    "user_comment",
    "kb_sources_used",
    "model_id",
    "review_status",
    "reviewer_id",
    "reviewer_tags",
    "reviewer_comment",
    "reviewed_at",
    "created_at",
)
_SELECT_COLS = ", ".join(_FEEDBACK_COLUMNS)


class PostgresFeedbackStore(BaseFeedbackStore):
    """Async PostgreSQL-backed store for user feedback on AI responses.

    Parameters
    ----------
    connection_url:
        ``libpq``-style PostgreSQL connection URL.
    pool_min_size, pool_max_size:
        Bounds for the underlying :class:`psycopg_pool.AsyncConnectionPool`.
    init_schema:
        When ``True`` (default), execute ``sql/feedback_schema.sql`` against
        the database on :meth:`open`. Set ``False`` if a separate
        provisioning task owns the DDL lifecycle.
    """

    SCHEMA_RESOURCE = ("auto_bedrock_chat_fastapi.db.sql", "feedback_schema.sql")

    def __init__(
        self,
        connection_url: str,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
        init_schema: bool = True,
    ) -> None:
        psycopg, AsyncConnectionPool, Jsonb = _import_psycopg_async()
        self._psycopg = psycopg
        self._Jsonb = Jsonb
        self._connection_url = connection_url
        self._init_schema = init_schema
        self._pool: Any = AsyncConnectionPool(
            conninfo=connection_url,
            min_size=pool_min_size,
            max_size=pool_max_size,
            open=False,
            kwargs={"autocommit": False},
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Open the connection pool and (optionally) bootstrap the schema."""
        await self._pool.open()
        if self._init_schema:
            await self._apply_schema()
        logger.info("PostgresFeedbackStore ready (init_schema=%s)", self._init_schema)

    async def close(self) -> None:
        """Close the connection pool."""
        await self._pool.close()

    async def _apply_schema(self) -> None:
        package, filename = self.SCHEMA_RESOURCE
        ddl = resources.files(package).joinpath(filename).read_text(encoding="utf-8")
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(ddl)
            await conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(self, entry: FeedbackEntry) -> FeedbackEntry:
        """Insert ``entry`` and return the persisted row.

        The values already present on ``entry`` are persisted as-is,
        including ``id`` and ``created_at``. Those fields are typically
        pre-populated by :class:`FeedbackEntry` defaults and may be
        overridden explicitly in tests.
        """
        sql = f"""
            INSERT INTO feedback (
                id, session_id, user_id, query, ai_response,
                rating, score, correction_text, user_comment,
                kb_sources_used, model_id,
                review_status, reviewer_id, reviewer_tags, reviewer_comment,
                reviewed_at, created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s, %s
            )
            RETURNING {_SELECT_COLS}
        """
        params = (
            entry.id,
            entry.session_id,
            entry.user_id,
            entry.query,
            entry.ai_response,
            entry.rating.value,
            entry.score,
            entry.correction_text,
            entry.user_comment,
            self._Jsonb(entry.kb_sources_used),
            entry.model_id,
            entry.review_status.value,
            entry.reviewer_id,
            list(entry.reviewer_tags),
            entry.reviewer_comment,
            entry.reviewed_at,
            entry.created_at,
        )
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
            await conn.commit()
        assert row is not None  # RETURNING always yields a row on INSERT
        return self._row_to_entry(row)

    async def get(self, feedback_id: UUID) -> Optional[FeedbackEntry]:
        sql = f"SELECT {_SELECT_COLS} FROM feedback WHERE id = %s"
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (feedback_id,))
                row = await cur.fetchone()
        return self._row_to_entry(row) if row else None

    async def list_pending(self, limit: int = 50, offset: int = 0) -> List[FeedbackEntry]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        sql = f"""
            SELECT {_SELECT_COLS} FROM feedback
            WHERE review_status = %s
            ORDER BY created_at ASC, id ASC
            LIMIT %s OFFSET %s
        """
        return await self._fetch_all(sql, (ReviewStatus.PENDING_REVIEW.value, limit, offset))

    async def list_by_tags(self, tags: Sequence[str]) -> List[FeedbackEntry]:
        """Return entries whose ``reviewer_tags`` overlap with ``tags``.

        Caller-supplied tags are stripped and empty entries dropped; if no
        non-empty tags remain, returns an empty list without querying.
        """
        normalized = [t.strip() for t in tags if t and t.strip()]
        if not normalized:
            return []
        sql = f"""
            SELECT {_SELECT_COLS} FROM feedback
            WHERE reviewer_tags && %s::text[]
            ORDER BY created_at DESC, id ASC
        """
        return await self._fetch_all(sql, (normalized,))

    async def list_by_date_range(
        self,
        start: datetime,
        end: datetime,
        status: Optional[ReviewStatus] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[FeedbackEntry]:
        """Return entries with ``start <= created_at < end``, newest first."""
        if end <= start:
            raise ValueError("end must be after start")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        clauses = ["created_at >= %s", "created_at < %s"]
        params: List[Any] = [start, end]
        if status is not None:
            clauses.append("review_status = %s")
            params.append(status.value)
        params.extend([limit, offset])

        sql = f"""
            SELECT {_SELECT_COLS} FROM feedback
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC, id ASC
            LIMIT %s OFFSET %s
        """
        return await self._fetch_all(sql, tuple(params))

    async def update_review(
        self,
        feedback_id: UUID,
        status: ReviewStatus,
        reviewer_id: str,
        tags: Sequence[str],
        comment: Optional[str],
    ) -> FeedbackEntry:
        """Apply a reviewer decision and return the updated entry.

        ``reviewer_id`` is stripped of surrounding whitespace and must be
        non-empty (mirrors the DB CHECK constraint). ``tags`` are stripped,
        empty entries dropped, and duplicates removed while preserving the
        caller's order.

        Raises
        ------
        FeedbackNotFoundError
            if no entry exists with ``feedback_id``.
        InvalidStatusTransitionError
            if the current ``review_status`` cannot transition to ``status``,
            or if ``status`` is ``pending_review``.
        ValueError
            if ``reviewer_id`` is empty / whitespace-only.
        """
        if status == ReviewStatus.PENDING_REVIEW:
            raise InvalidStatusTransitionError("Cannot transition into 'pending_review' via update_review")
        reviewer_id = (reviewer_id or "").strip()
        if not reviewer_id:
            raise ValueError("reviewer_id is required")
        normalized_tags = list(dict.fromkeys(t.strip() for t in tags if t and t.strip()))

        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT review_status FROM feedback WHERE id = %s FOR UPDATE",
                    (feedback_id,),
                )
                current = await cur.fetchone()
                if current is None:
                    raise FeedbackNotFoundError(f"feedback {feedback_id} not found")

                current_status = ReviewStatus(current[0])
                allowed = ALLOWED_REVIEW_TRANSITIONS.get(current_status, frozenset())
                if status not in allowed:
                    raise InvalidStatusTransitionError(f"Cannot transition {current_status.value} -> {status.value}")

                update_sql = f"""
                    UPDATE feedback
                    SET review_status    = %s,
                        reviewer_id      = %s,
                        reviewer_tags    = %s::text[],
                        reviewer_comment = %s,
                        reviewed_at      = now()
                    WHERE id = %s
                    RETURNING {_SELECT_COLS}
                """
                await cur.execute(
                    update_sql,
                    (
                        status.value,
                        reviewer_id,
                        normalized_tags,
                        comment,
                        feedback_id,
                    ),
                )
                row = await cur.fetchone()
            await conn.commit()
        assert row is not None
        return self._row_to_entry(row)

    async def stats(self) -> FeedbackStats:
        """Return aggregate counts across the feedback table."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT count(*) FROM feedback")
                total_row = await cur.fetchone()
                total = int(total_row[0]) if total_row else 0

                await cur.execute("SELECT review_status, count(*) FROM feedback GROUP BY review_status")
                by_status_rows = await cur.fetchall()

                await cur.execute("SELECT rating, count(*) FROM feedback GROUP BY rating")
                by_rating_rows = await cur.fetchall()

        by_status = {ReviewStatus(s): int(c) for s, c in by_status_rows}
        by_rating = {Rating(r): int(c) for r, c in by_rating_rows}
        return FeedbackStats(total=total, by_status=by_status, by_rating=by_rating)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_all(self, sql: str, params: Tuple[Any, ...]) -> List[FeedbackEntry]:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: Iterable[Any]) -> FeedbackEntry:
        """Map a row selected via ``_SELECT_COLS`` to a :class:`FeedbackEntry`."""
        values = list(row)
        if len(values) != len(_FEEDBACK_COLUMNS):
            raise FeedbackError(
                f"Unexpected feedback row width: got {len(values)}, " f"expected {len(_FEEDBACK_COLUMNS)}"
            )
        data = dict(zip(_FEEDBACK_COLUMNS, values))
        # psycopg returns JSONB as a Python object already and TEXT[] as list[str].
        # Normalize NULL collections so Pydantic sees the right defaults.
        if data["kb_sources_used"] is None:
            data["kb_sources_used"] = []
        if data["reviewer_tags"] is None:
            data["reviewer_tags"] = []
        return FeedbackEntry.model_validate(data)
