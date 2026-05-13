"""
SQLite-backed FeedbackStore (XMGPLAT-10417).

Zero-config alternative to the Postgres :class:`~.feedback_store.FeedbackStore`.
Uses the stdlib ``sqlite3`` module wrapped in :func:`asyncio.to_thread` so it
satisfies the same async :class:`~.feedback_store.BaseFeedbackStore` interface
without adding a third-party async-SQLite dependency.

Schema lives at ``auto_bedrock_chat_fastapi/sql/feedback_schema_sqlite.sql``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from importlib import resources
from typing import Any, Iterable, List, Optional, Sequence
from uuid import UUID

from ..exceptions import FeedbackError, FeedbackNotFoundError, InvalidStatusTransitionError
from ..models import ALLOWED_REVIEW_TRANSITIONS, FeedbackEntry, FeedbackStats, Rating, ReviewStatus
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
    "reviewer_comment",
    "reviewed_at",
    "created_at",
)
_SELECT_COLS = ", ".join(_FEEDBACK_COLUMNS)


def _dt_to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _iso_to_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    # ``datetime.fromisoformat`` handles the values produced by ``isoformat()``
    # including timezone offsets across all supported Python versions.
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
            entry.reviewer_comment,
            _dt_to_iso(entry.reviewed_at),
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
        assert self._conn is not None, "store not opened"
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

    async def stats(self) -> FeedbackStats:
        return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> FeedbackStats:
        assert self._conn is not None, "store not opened"
        with self._lock:
            cur = self._conn.execute("SELECT count(*) FROM feedback")
            total_row = cur.fetchone()
            total = int(total_row[0]) if total_row else 0

            cur = self._conn.execute("SELECT review_status, count(*) FROM feedback GROUP BY review_status")
            status_rows = cur.fetchall()

            cur = self._conn.execute("SELECT rating, count(*) FROM feedback GROUP BY rating")
            rating_rows = cur.fetchall()

        by_status = {ReviewStatus(s): int(c) for s, c in status_rows}
        by_rating = {Rating(r): int(c) for r, c in rating_rows}
        return FeedbackStats(total=total, by_status=by_status, by_rating=by_rating)

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

        # Convert datetimes from ISO strings.
        data["reviewed_at"] = _iso_to_dt(data["reviewed_at"])
        data["created_at"] = _iso_to_dt(data["created_at"])

        # Convert id from string back to UUID (Pydantic also accepts strings,
        # but doing it here keeps the contract symmetric with the Postgres
        # backend).
        data["id"] = UUID(str(data["id"]))

        return FeedbackEntry.model_validate(data)
