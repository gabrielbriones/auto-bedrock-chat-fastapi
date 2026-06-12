"""
SQLite-backed FeedbackStore.

Zero-config implementation of the async
:class:`~.feedback_base.BaseFeedbackStore` interface using SQLite.
Uses the stdlib ``sqlite3`` module wrapped in :func:`asyncio.to_thread`
without adding a third-party async-SQLite dependency.

Schema lives at
``auto_bedrock_chat_fastapi/db/sql/feedback_schema_sqlite.sql``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from importlib import resources
from typing import Any, Iterable, List, Optional, Sequence
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


_FEEDBACK_COLUMNS = (
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
    "created_at",
)
_SELECT_COLS = ", ".join(_FEEDBACK_COLUMNS)


def _dt_to_iso(value: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime to a UTC ISO-8601 string.

    SQLite compares ``created_at`` / ``reviewed_at`` as TEXT, so lexical
    order must match chronological order. Mixing tz-aware values with
    different offsets (or mixing aware and naive) would break range
    filters and ``ORDER BY`` results. We normalize to UTC and emit a
    fixed-width ``+00:00`` suffix so every row sorts correctly.

    Naive datetimes are *assumed* to be in the local timezone (matching
    ``datetime.now()`` without ``astimezone()``) and converted to UTC
    before formatting.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.astimezone()  # attach local tz
    return value.astimezone(timezone.utc).isoformat()


def _iso_to_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    # ``datetime.fromisoformat`` handles the values produced by ``isoformat()``
    # including the ``+00:00`` UTC suffix produced by ``_dt_to_iso``.
    return datetime.fromisoformat(value)


class SQLiteFeedbackStore(BaseFeedbackStore):
    """Async SQLite-backed feedback store.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database. Parent directories are
        created on :meth:`open`. Use ``":memory:"`` for ephemeral test
        databases.
    init_schema:
        When True (default), apply ``feedback_schema_sqlite.sql`` on
        :meth:`open`. The DDL uses ``IF NOT EXISTS`` so it's safe to re-run.

    Concurrency
    -----------
    A single :class:`sqlite3.Connection` opened with
    ``check_same_thread=False`` is shared across asyncio worker threads and
    serialized by a :class:`threading.Lock`. All blocking calls run via
    :func:`asyncio.to_thread`. WAL mode is enabled for concurrent reads.
    """

    SCHEMA_RESOURCE = ("auto_bedrock_chat_fastapi.db.sql", "feedback_schema_sqlite.sql")

    def __init__(self, db_path: str, init_schema: bool = True) -> None:
        self._db_path = db_path
        self._init_schema = init_schema
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        await asyncio.to_thread(self._ensure_open_sync)
        logger.info(
            "SQLiteFeedbackStore ready (db_path=%s, init_schema=%s)",
            self._db_path,
            self._init_schema,
        )

    def _ensure_open_sync(self) -> None:
        """Idempotently open the SQLite connection.

        Called both from :meth:`open` (eager, via the FastAPI startup
        event when the host app uses ``on_event``) and from every
        ``_execute_write``/``_fetchone``/``_fetchall`` call (lazy fallback
        for hosts that use ``lifespan=`` — FastAPI silently ignores
        ``on_event`` registrations once a lifespan is set, so eager open
        cannot be relied on).
        """
        if self._conn is not None:
            return
        # Serialize the bootstrap so two concurrent first-use calls don't
        # race to connect / apply the DDL.
        with self._lock:
            if self._conn is not None:
                return
            self._open_sync_locked()

    def _open_sync(self) -> None:
        # Backwards-compatible alias retained for tests / external callers.
        self._ensure_open_sync()

    def _open_sync_locked(self) -> None:
        if self._db_path != ":memory:":
            parent = os.path.dirname(self._db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        # ``check_same_thread=False`` is required because asyncio.to_thread
        # may execute callbacks on different worker threads. The lock above
        # serializes all access to keep sqlite3 happy.
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL improves concurrent read performance and reduces "database is
        # locked" errors under light write contention. Skip for in-memory
        # because journal_mode is irrelevant there.
        if self._db_path != ":memory:":
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                # Some filesystems (e.g. NFS) reject WAL; fall back silently.
                pass
        if self._init_schema:
            ddl = resources.files(self.SCHEMA_RESOURCE[0]).joinpath(self.SCHEMA_RESOURCE[1]).read_text(encoding="utf-8")
            conn.executescript(ddl)
            conn.commit()
        # Idempotent legacy-data migration: Schemas allowed a
        # third ``rating`` value ``"correction"`` that was retired in
        # favor of the orthogonal ``correction_text`` field. The new
        # CHECK constraint forbids that value, but existing rows in
        # long-lived dev DBs survive (SQLite doesn't re-evaluate CHECK
        # on existing data) and need to be normalized to ``"negative"``.
        try:
            cur = conn.execute("UPDATE feedback SET rating = 'negative' WHERE rating = 'correction'")
            if cur.rowcount:
                logger.warning(
                    "migrated %d legacy feedback row(s) from rating='correction' to 'negative'",
                    cur.rowcount,
                )
            conn.commit()
        except sqlite3.OperationalError:
            # Table doesn't exist yet (init_schema=False on a fresh DB);
            # safe to skip — no legacy rows can exist.
            pass
        # Idempotent migration: add conversation_history column for
        # databases created before XMGPLAT-10683. SQLite lacks
        # ADD COLUMN IF NOT EXISTS; catch the "duplicate column" error.
        try:
            conn.execute("ALTER TABLE feedback ADD COLUMN conversation_history TEXT NOT NULL DEFAULT '[]'")
            conn.commit()
        except sqlite3.OperationalError:
            # Column already exists (normal case) or table doesn't exist.
            pass
        # Idempotent migration: add integrated_into_kb_id and integrated_at
        # columns for databases created before synthesis support was added.
        try:
            conn.execute("ALTER TABLE feedback ADD COLUMN integrated_into_kb_id TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            # Column already exists (normal case) or table doesn't exist.
            pass
        try:
            conn.execute("ALTER TABLE feedback ADD COLUMN integrated_at TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            # Column already exists (normal case) or table doesn't exist.
            pass
        self._conn = conn

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(self, entry: FeedbackEntry) -> FeedbackEntry:
        params = (
            str(entry.id),
            entry.session_id,
            entry.user_id,
            entry.query,
            entry.ai_response,
            entry.rating.value,
            entry.score,
            entry.correction_text,
            entry.user_comment,
            json.dumps(entry.kb_sources_used),
            entry.model_id,
            entry.review_status.value,
            entry.reviewer_id,
            json.dumps(list(entry.reviewer_tags)),
            json.dumps(entry.conversation_history),
            entry.reviewer_comment,
            _dt_to_iso(entry.reviewed_at),
            entry.integrated_into_kb_id,
            _dt_to_iso(entry.integrated_at),
            _dt_to_iso(entry.created_at),
        )
        sql = f"""
            INSERT INTO feedback ({_SELECT_COLS})
            VALUES ({", ".join("?" for _ in _FEEDBACK_COLUMNS)})
        """  # nosec B608 - column list is a constant tuple
        await asyncio.to_thread(self._execute_write, sql, params)
        # Round-trip via fetch to get the canonical normalized form.
        fetched = await self.get(entry.id)
        if fetched is None:  # pragma: no cover - defensive
            raise FeedbackError("Failed to read back inserted feedback row")
        return fetched

    async def get(self, feedback_id: UUID) -> Optional[FeedbackEntry]:
        sql = f"SELECT {_SELECT_COLS} FROM feedback WHERE id = ?"
        row = await asyncio.to_thread(self._fetchone, sql, (str(feedback_id),))
        return self._row_to_entry(row) if row is not None else None

    async def list_pending(self, limit: int = 50, offset: int = 0) -> List[FeedbackEntry]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        sql = f"""
            SELECT {_SELECT_COLS} FROM feedback
            WHERE review_status = ?
            ORDER BY created_at ASC, id ASC
            LIMIT ? OFFSET ?
        """
        rows = await asyncio.to_thread(self._fetchall, sql, (ReviewStatus.PENDING_REVIEW.value, limit, offset))
        return [self._row_to_entry(r) for r in rows]

    async def list_by_tags(self, tags: Sequence[str]) -> List[FeedbackEntry]:
        normalized = [t.strip() for t in tags if t and t.strip()]
        if not normalized:
            return []
        # SQLite has no array contains; use json_each to test overlap.
        # The IN clause is built with a fixed number of placeholders to keep
        # the query parameterized (no string interpolation of user input).
        placeholders = ", ".join("?" for _ in normalized)
        sql = f"""
            SELECT {_SELECT_COLS} FROM feedback
            WHERE EXISTS (
                SELECT 1 FROM json_each(feedback.reviewer_tags) je
                WHERE je.value IN ({placeholders})
            )
            ORDER BY created_at DESC, id ASC
        """
        rows = await asyncio.to_thread(self._fetchall, sql, tuple(normalized))
        return [self._row_to_entry(r) for r in rows]

    async def list_by_date_range(
        self,
        start: datetime,
        end: datetime,
        status: Optional[ReviewStatus] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[FeedbackEntry]:
        if end <= start:
            raise ValueError("end must be after start")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        clauses = ["created_at >= ?", "created_at < ?"]
        params: List[Any] = [_dt_to_iso(start), _dt_to_iso(end)]
        if status is not None:
            clauses.append("review_status = ?")
            params.append(status.value)
        params.extend([limit, offset])

        sql = f"""
            SELECT {_SELECT_COLS} FROM feedback
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC, id ASC
            LIMIT ? OFFSET ?
        """
        rows = await asyncio.to_thread(self._fetchall, sql, tuple(params))
        return [self._row_to_entry(r) for r in rows]

    # ------------------------------------------------------------------
    # Filtered list / count (T2)
    # ------------------------------------------------------------------

    def _build_filter_clauses(self, filters: FeedbackListFilters) -> tuple[List[str], List[Any]]:
        """Build the WHERE clauses + parameter list for a ``FeedbackListFilters``.

        All filter values are bound as positional parameters; nothing
        from the filter is interpolated into the SQL string.
        Returns ``(clauses, params)``; ``clauses`` is empty when no
        filter is set so callers can decide whether to emit ``WHERE``.
        """
        clauses: List[str] = []
        params: List[Any] = []

        if filters.status is not None:
            clauses.append("review_status = ?")
            params.append(filters.status.value)
        if filters.rating is not None:
            clauses.append("rating = ?")
            params.append(filters.rating.value)
        if filters.has_correction is True:
            clauses.append("correction_text IS NOT NULL")
        elif filters.has_correction is False:
            clauses.append("correction_text IS NULL")
        if filters.user_id is not None:
            clauses.append("user_id = ?")
            params.append(filters.user_id)
        if filters.date_from is not None:
            clauses.append("created_at >= ?")
            params.append(_dt_to_iso(filters.date_from))
        if filters.date_to is not None:
            clauses.append("created_at < ?")
            params.append(_dt_to_iso(filters.date_to))
        if filters.tags:
            placeholders = ", ".join("?" for _ in filters.tags)
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(feedback.reviewer_tags) je " f"WHERE je.value IN ({placeholders}))"
            )
            params.extend(filters.tags)
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
            LIMIT ? OFFSET ?
        """
        rows = await asyncio.to_thread(self._fetchall, sql, tuple(params))
        return [self._row_to_entry(r) for r in rows]

    async def count_entries(self, filters: FeedbackListFilters) -> int:
        clauses, params = self._build_filter_clauses(filters)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT count(*) FROM feedback {where}"  # nosec B608 - where built from constants
        row = await asyncio.to_thread(self._fetchone, sql, tuple(params))
        return int(row[0]) if row else 0

    async def update_review(
        self,
        feedback_id: UUID,
        status: ReviewStatus,
        reviewer_id: str,
        tags: Sequence[str],
        comment: Optional[str],
    ) -> FeedbackEntry:
        if status == ReviewStatus.PENDING_REVIEW:
            raise InvalidStatusTransitionError("Cannot transition into 'pending_review' via update_review")
        reviewer_id = (reviewer_id or "").strip()
        if not reviewer_id:
            raise ValueError("reviewer_id is required")
        normalized_tags = list(dict.fromkeys(t.strip() for t in tags if t and t.strip()))

        return await asyncio.to_thread(
            self._update_review_sync,
            feedback_id,
            status,
            reviewer_id,
            normalized_tags,
            comment,
        )

    def _update_review_sync(
        self,
        feedback_id: UUID,
        status: ReviewStatus,
        reviewer_id: str,
        normalized_tags: List[str],
        comment: Optional[str],
    ) -> FeedbackEntry:
        self._ensure_open_sync()
        assert self._conn is not None  # narrow for type checkers
        # SQLite's default isolation gives us an implicit transaction that
        # spans both the SELECT and the UPDATE under a single lock-held
        # block, so the read-modify-write is atomic.
        with self._lock:
            try:
                cur = self._conn.execute(
                    "SELECT review_status FROM feedback WHERE id = ?",
                    (str(feedback_id),),
                )
                current = cur.fetchone()
                if current is None:
                    raise FeedbackNotFoundError(f"feedback {feedback_id} not found")

                current_status = ReviewStatus(current[0])
                allowed = ALLOWED_REVIEW_TRANSITIONS.get(current_status, frozenset())
                if status not in allowed:
                    raise InvalidStatusTransitionError(f"Cannot transition {current_status.value} -> {status.value}")

                reviewed_at_iso = _dt_to_iso(datetime.now().astimezone())
                self._conn.execute(
                    """
                    UPDATE feedback
                       SET review_status    = ?,
                           reviewer_id      = ?,
                           reviewer_tags    = ?,
                           reviewer_comment = ?,
                           reviewed_at      = ?
                     WHERE id = ?
                    """,
                    (
                        status.value,
                        reviewer_id,
                        json.dumps(normalized_tags),
                        comment,
                        reviewed_at_iso,
                        str(feedback_id),
                    ),
                )
                self._conn.commit()

                cur = self._conn.execute(
                    f"SELECT {_SELECT_COLS} FROM feedback WHERE id = ?",
                    (str(feedback_id),),
                )
                row = cur.fetchone()
            except Exception:
                self._conn.rollback()
                raise
        if row is None:  # pragma: no cover - defensive
            raise FeedbackError("Updated feedback row vanished after commit")
        return self._row_to_entry(row)

    async def mark_integrated(
        self,
        feedback_id: UUID,
        kb_doc_id: str,
        integrated_at: datetime,
    ) -> FeedbackEntry:
        """Set ``integrated_into_kb_id`` and ``integrated_at`` on the row.

        Raises :exc:`~auto_bedrock_chat_fastapi.exceptions.FeedbackNotFoundError`
        if ``feedback_id`` does not exist.
        """
        return await asyncio.to_thread(
            self._mark_integrated_sync,
            feedback_id,
            kb_doc_id,
            integrated_at,
        )

    def _mark_integrated_sync(
        self,
        feedback_id: UUID,
        kb_doc_id: str,
        integrated_at: datetime,
    ) -> FeedbackEntry:
        self._ensure_open_sync()
        assert self._conn is not None
        integrated_at_iso = _dt_to_iso(integrated_at)
        with self._lock:
            try:
                cur = self._conn.execute(
                    """
                    UPDATE feedback
                       SET integrated_into_kb_id = ?,
                           integrated_at         = ?
                     WHERE id = ?
                       AND integrated_into_kb_id IS NULL
                    """,
                    (kb_doc_id, integrated_at_iso, str(feedback_id)),
                )
                if cur.rowcount == 0:
                    # Either not found or already integrated; check the row.
                    check = self._conn.execute(
                        "SELECT integrated_into_kb_id FROM feedback WHERE id = ?",
                        (str(feedback_id),),
                    ).fetchone()
                    self._conn.rollback()
                    if check is None:
                        from ..exceptions import FeedbackNotFoundError

                        raise FeedbackNotFoundError(f"feedback {feedback_id} not found")
                    from ..exceptions import AlreadyIntegratedError

                    raise AlreadyIntegratedError(
                        f"feedback {feedback_id} is already integrated into KB document '{check[0]}'"
                    )
                self._conn.commit()
                cur = self._conn.execute(
                    f"SELECT {_SELECT_COLS} FROM feedback WHERE id = ?",
                    (str(feedback_id),),
                )
                row = cur.fetchone()
            except Exception:
                self._conn.rollback()
                raise
        if row is None:  # pragma: no cover — defensive
            from ..exceptions import FeedbackError

            raise FeedbackError("Integrated feedback row vanished after commit")
        return self._row_to_entry(row)

    async def stats(self) -> FeedbackStats:
        return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> FeedbackStats:
        self._ensure_open_sync()
        assert self._conn is not None  # narrow for type checkers
        with self._lock:
            cur = self._conn.execute("SELECT count(*) FROM feedback")
            total_row = cur.fetchone()
            total = int(total_row[0]) if total_row else 0

            cur = self._conn.execute("SELECT review_status, count(*) FROM feedback GROUP BY review_status")
            status_rows = cur.fetchall()

            cur = self._conn.execute("SELECT rating, count(*) FROM feedback GROUP BY rating")
            rating_rows = cur.fetchall()

            cur = self._conn.execute("SELECT count(*) FROM feedback WHERE correction_text IS NOT NULL")
            with_correction_row = cur.fetchone()

            cur = self._conn.execute("SELECT count(*) FROM feedback WHERE integrated_into_kb_id IS NOT NULL")
            integrated_count_row = cur.fetchone()

            # Top 10 reviewer tags by frequency. ``json_each`` explodes the
            # JSON-encoded ``reviewer_tags`` array into one row per tag so we
            # can GROUP BY tag value across the whole table. Empty tags are
            # excluded defensively even though ``_strip_reviewer_tags``
            # already prunes them on the way in.
            cur = self._conn.execute(
                """
                SELECT je.value AS tag, count(*) AS c
                  FROM feedback, json_each(feedback.reviewer_tags) je
                 WHERE je.value IS NOT NULL AND je.value != ''
                 GROUP BY je.value
                 ORDER BY c DESC, je.value ASC
                 LIMIT 10
                """
            )
            tag_rows = cur.fetchall()

            # Oldest pending entry — used by the admin dashboard to surface
            # review-queue lag. Returns ``None`` when nothing is pending.
            cur = self._conn.execute(
                """
                SELECT min(created_at) FROM feedback
                 WHERE review_status = ?
                """,
                (ReviewStatus.PENDING_REVIEW.value,),
            )
            oldest_row = cur.fetchone()

        by_status = {ReviewStatus(s): int(c) for s, c in status_rows}
        by_rating = {Rating(r): int(c) for r, c in rating_rows}
        with_correction = int(with_correction_row[0]) if with_correction_row else 0
        integrated_count = int(integrated_count_row[0]) if integrated_count_row else 0
        top_tags = [TagCount(tag=t, count=int(c)) for t, c in tag_rows]
        oldest_iso = oldest_row[0] if oldest_row else None
        if oldest_iso:
            oldest_dt = _iso_to_dt(oldest_iso)
            # ``_dt_to_iso`` normalizes to UTC, so the stored string is
            # always tz-aware. Compute the age against ``now(UTC)`` to
            # avoid naive/aware subtraction errors.
            assert oldest_dt is not None
            delta = datetime.now(timezone.utc) - oldest_dt
            oldest_pending_hours: Optional[float] = max(delta.total_seconds() / 3600.0, 0.0)
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

    # ------------------------------------------------------------------
    # Internal helpers (run inside ``asyncio.to_thread``)
    # ------------------------------------------------------------------

    def _execute_write(self, sql: str, params: tuple) -> None:
        self._ensure_open_sync()
        assert self._conn is not None  # narrow type for mypy / static checkers
        with self._lock:
            try:
                self._conn.execute(sql, params)
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                # Surface CHECK-constraint violations as ValueError so the
                # WebSocket handler can convert them to ``invalid_feedback``
                # the same way it handles Pydantic ``ValidationError``.
                raise ValueError(str(exc)) from exc
            except Exception:
                self._conn.rollback()
                raise

    def _fetchone(self, sql: str, params: tuple) -> Optional[tuple]:
        self._ensure_open_sync()
        assert self._conn is not None
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchone()

    def _fetchall(self, sql: str, params: tuple) -> List[tuple]:
        self._ensure_open_sync()
        assert self._conn is not None
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()

    @staticmethod
    def _row_to_entry(row: Iterable[Any]) -> FeedbackEntry:
        values = list(row)
        if len(values) != len(_FEEDBACK_COLUMNS):
            raise FeedbackError(
                f"Unexpected feedback row width: got {len(values)}, " f"expected {len(_FEEDBACK_COLUMNS)}"
            )
        data = dict(zip(_FEEDBACK_COLUMNS, values))

        # JSON-decode collection columns; tolerate NULL.
        kb_raw = data["kb_sources_used"]
        data["kb_sources_used"] = json.loads(kb_raw) if kb_raw else []
        tags_raw = data["reviewer_tags"]
        data["reviewer_tags"] = json.loads(tags_raw) if tags_raw else []
        history_raw = data.get("conversation_history")
        data["conversation_history"] = json.loads(history_raw) if history_raw else []

        # Convert datetimes from ISO strings.
        data["reviewed_at"] = _iso_to_dt(data["reviewed_at"])
        data["integrated_at"] = _iso_to_dt(data["integrated_at"])
        data["created_at"] = _iso_to_dt(data["created_at"])

        # Convert id from string to UUID; integrated_into_kb_id stays as str.
        data["id"] = UUID(str(data["id"]))
        # integrated_into_kb_id is a plain TEXT document ID (not UUID); leave as-is.

        return FeedbackEntry.model_validate(data)
