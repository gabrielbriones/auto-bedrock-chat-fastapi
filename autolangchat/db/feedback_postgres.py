"""
PostgreSQL-backed feedback store.

Production backend for :class:`~.feedback_base.BaseFeedbackStore`. Schema:
``autolangchat/db/sql/feedback_schema.sql``.

Requires the optional ``[postgres]`` extra::

    pip install autolangchat[postgres]
"""

from __future__ import annotations

import logging
from datetime import datetime
from importlib import resources
from typing import Any, Iterable, List, Optional, Sequence, Tuple
from uuid import UUID

from ..exceptions import FeedbackError, FeedbackNotFoundError, InvalidStatusTransitionError
from ..models import (
    ALLOWED_REVIEW_TRANSITIONS,
    FeedbackEntry,
    FeedbackListFilters,
    FeedbackStats,
    Rating,
    ReviewStatus,
    TagCount,
)
from .feedback_base import BaseFeedbackStore

logger = logging.getLogger(__name__)


_MISSING_DEPS_MSG = (
    "PostgresFeedbackStore requires the optional PostgreSQL packages. "
    "Install them with:  pip install autolangchat[postgres]"
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
    "conversation_history",
    "reviewer_comment",
    "reviewed_at",
    "integrated_into_kb_id",
    "integrated_at",
    "rolled_back_at",
    "rolled_back_by",
    "rollback_reason",
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

    SCHEMA_RESOURCE = ("autolangchat.db.sql", "feedback_schema.sql")

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
        await self._migrate_legacy_correction_rows()
        logger.info("PostgresFeedbackStore ready (init_schema=%s)", self._init_schema)

    async def close(self) -> None:
        """Close the connection pool."""
        await self._pool.close(timeout=5)

    async def _apply_schema(self) -> None:
        package, filename = self.SCHEMA_RESOURCE
        ddl = resources.files(package).joinpath(filename).read_text(encoding="utf-8")
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(ddl)
            await conn.commit()

    async def _migrate_legacy_correction_rows(self) -> None:
        """Rewrite legacy ``rating='correction'`` rows to ``'negative'``.

        Schemas allowed a third ``Rating`` value
        ``"correction"`` that was retired in favor of the orthogonal
        ``correction_text`` field. The ``feedback_rating`` enum
        definition uses ``IF NOT EXISTS``, so existing deployments may
        still have the old 3-value enum and rows that use it. This
        migration is idempotent: it's a no-op once all rows are
        ``'negative'``, and it stays safe to run repeatedly. The enum
        value itself is left in place for forward-compat — only the
        rows are rewritten.
        """
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    # Only run if 'correction' still exists in the enum; on
                    # fresh deployments it was never added, and querying for it
                    # raises "invalid input value for enum feedback_rating".
                    await cur.execute(
                        "SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid"
                        " WHERE t.typname = 'feedback_rating' AND e.enumlabel = 'correction'"
                    )
                    if await cur.fetchone() is None:
                        return  # enum value doesn't exist; nothing to migrate
                    await cur.execute("UPDATE feedback SET rating = 'negative' WHERE rating = 'correction'")
                    rowcount = cur.rowcount
                await conn.commit()
            if rowcount:
                logger.warning(
                    "migrated %d legacy feedback row(s) from rating='correction' to 'negative'",
                    rowcount,
                )
        except Exception as exc:  # pragma: no cover — defensive only
            # The table may not exist yet (init_schema=False on a
            # provisioning-from-scratch deploy), or the enum value may
            # already be gone. Either way, log and continue.
            logger.debug("legacy-correction migration skipped: %s", exc)

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
                review_status, reviewer_id, reviewer_tags,
                conversation_history, reviewer_comment,
                reviewed_at, integrated_into_kb_id, integrated_at,
                rolled_back_at, rolled_back_by, rollback_reason,
                created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s
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
            self._Jsonb(entry.conversation_history),
            entry.reviewer_comment,
            entry.reviewed_at,
            entry.integrated_into_kb_id,
            entry.integrated_at,
            entry.rolled_back_at,
            entry.rolled_back_by,
            entry.rollback_reason,
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
    
    async def delete(self, feedback_id: UUID) -> bool:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("DELETE FROM feedback WHERE id = %s", (feedback_id,))
            deleted = cur.rowcount > 0
            await conn.commit()
            return deleted

    # ------------------------------------------------------------------
    # Filtered list / count (T2)
    # ------------------------------------------------------------------

    def _build_filter_clauses(self, filters: FeedbackListFilters) -> Tuple[List[str], List[Any]]:
        """Build WHERE clauses + positional params for ``filters``.

        Filter values are bound via ``%s`` placeholders; nothing is
        interpolated into the SQL string. Returns ``(clauses, params)``
        with ``clauses`` empty when no filter is set.
        """
        clauses: List[str] = []
        params: List[Any] = []

        if filters.status is not None:
            clauses.append("review_status = %s")
            params.append(filters.status.value)
        if filters.rating is not None:
            clauses.append("rating = %s")
            params.append(filters.rating.value)
        if filters.has_correction is True:
            clauses.append("correction_text IS NOT NULL")
        elif filters.has_correction is False:
            clauses.append("correction_text IS NULL")
        if filters.user_id is not None:
            clauses.append("user_id = %s")
            params.append(filters.user_id)
        if filters.date_from is not None:
            clauses.append("created_at >= %s")
            params.append(filters.date_from)
        if filters.date_to is not None:
            clauses.append("created_at < %s")
            params.append(filters.date_to)
        if filters.tags:
            clauses.append("reviewer_tags && %s::text[]")
            params.append(list(filters.tags))
        if filters.has_integrated is True:
            clauses.append("integrated_into_kb_id IS NOT NULL")
        elif filters.has_integrated is False:
            clauses.append("integrated_into_kb_id IS NULL")
        return clauses, params

    async def list_entries(
        self,
        filters: FeedbackListFilters,
        limit: int = 50,
        offset: int = 0,
    ) -> List[FeedbackEntry]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        clauses, params = self._build_filter_clauses(filters)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        sql = f"""
            SELECT {_SELECT_COLS} FROM feedback
            {where}
            ORDER BY created_at DESC, id ASC
            LIMIT %s OFFSET %s
        """
        return await self._fetch_all(sql, tuple(params))

    async def count_entries(self, filters: FeedbackListFilters) -> int:
        clauses, params = self._build_filter_clauses(filters)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT count(*) FROM feedback {where}"  # nosec B608 - where built from constants
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                row = await cur.fetchone()
        return int(row[0]) if row else 0

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

                await cur.execute("SELECT count(*) FROM feedback WHERE correction_text IS NOT NULL")
                with_correction_row = await cur.fetchone()

                await cur.execute("SELECT count(*) FROM feedback WHERE integrated_into_kb_id IS NOT NULL")
                integrated_count_row = await cur.fetchone()

                # Top 10 reviewer tags via ``unnest`` of the TEXT[] column.
                # ``trim`` defends against any blank tags that slipped past
                # the ``_strip_reviewer_tags`` validator.
                await cur.execute(
                    """
                    SELECT tag, count(*) AS c
                      FROM feedback, unnest(reviewer_tags) AS tag
                     WHERE tag IS NOT NULL AND btrim(tag) <> ''
                     GROUP BY tag
                     ORDER BY c DESC, tag ASC
                     LIMIT 10
                    """
                )
                tag_rows = await cur.fetchall()

                # Age of the oldest pending entry, in hours. ``extract(epoch …)``
                # returns seconds; divide for hours. Returns NULL when nothing
                # is pending.
                await cur.execute(
                    """
                    SELECT EXTRACT(EPOCH FROM (now() - min(created_at))) / 3600.0
                      FROM feedback
                     WHERE review_status = %s
                    """,
                    (ReviewStatus.PENDING_REVIEW.value,),
                )
                oldest_row = await cur.fetchone()

        by_status = {ReviewStatus(s): int(c) for s, c in by_status_rows}
        by_rating = {Rating(r): int(c) for r, c in by_rating_rows}
        with_correction = int(with_correction_row[0]) if with_correction_row else 0
        integrated_count = int(integrated_count_row[0]) if integrated_count_row else 0
        top_tags = [TagCount(tag=t, count=int(c)) for t, c in tag_rows]
        if oldest_row and oldest_row[0] is not None:
            # Clamp tiny negative values that can appear if ``now()`` and
            # ``created_at`` cross a clock-skew boundary on different nodes.
            oldest_pending_hours: Optional[float] = max(float(oldest_row[0]), 0.0)
        else:
            oldest_pending_hours = None
        return FeedbackStats(
            total=total,
            by_status=by_status,
            by_rating=by_rating,
            with_correction=with_correction,
            integrated_count=integrated_count,
            top_tags=top_tags,
            oldest_pending_hours=oldest_pending_hours,
        )

    async def mark_integrated(
        self,
        feedback_id: UUID,
        kb_doc_id: str,
        integrated_at: datetime,
    ) -> FeedbackEntry:
        """Set ``integrated_into_kb_id`` and ``integrated_at`` on the row.

        Raises :exc:`~autolangchat.exceptions.FeedbackNotFoundError`
        if ``feedback_id`` does not exist.
        """
        sql = f"""
            UPDATE feedback
               SET integrated_into_kb_id = %s,
                   integrated_at         = %s
             WHERE id = %s
               AND integrated_into_kb_id IS NULL
            RETURNING {_SELECT_COLS}
        """
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (kb_doc_id, integrated_at, feedback_id))
                row = await cur.fetchone()
            await conn.commit()
        if row is None:
            # Either not found or already integrated; check which case it is.
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT integrated_into_kb_id FROM feedback WHERE id = %s",
                        (feedback_id,),
                    )
                    check = await cur.fetchone()
            if check is None:
                from ..exceptions import FeedbackNotFoundError  # local import avoids circular dep

                raise FeedbackNotFoundError(f"feedback {feedback_id} not found")
            from ..exceptions import AlreadyIntegratedError

            raise AlreadyIntegratedError(f"feedback {feedback_id} is already integrated into KB document '{check[0]}'")
        return self._row_to_entry(row)

    async def revert_integrated(
        self,
        kb_doc_id: str,
        rolled_back_at: datetime,
        rolled_back_by: str,
        reason: Optional[str] = None,
    ) -> int:
        """Clear synthesis provenance for entries linked to ``kb_doc_id``.

        Returns the count of rows updated.
        """
        sql = """
            UPDATE feedback
               SET integrated_into_kb_id = NULL,
                   integrated_at         = NULL,
                   rolled_back_at        = %s,
                   rolled_back_by        = %s,
                   rollback_reason       = %s
             WHERE integrated_into_kb_id = %s
        """
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (rolled_back_at, rolled_back_by, reason, kb_doc_id))
                count = cur.rowcount
            await conn.commit()
        return count

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
        if data.get("conversation_history") is None:
            data["conversation_history"] = []
        return FeedbackEntry.model_validate(data)
