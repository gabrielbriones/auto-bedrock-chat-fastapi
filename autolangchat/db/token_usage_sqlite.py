"""
SQLite-backed TokenUsageStore.

Zero-config implementation of the async
:class:`~.token_usage_base.BaseTokenUsageStore` interface using SQLite.
Uses the stdlib ``sqlite3`` module wrapped in :func:`asyncio.to_thread`
without adding a third-party async-SQLite dependency. Mirrors the
lifecycle/concurrency approach of :class:`~.feedback_sqlite.SQLiteFeedbackStore`.

Schema lives at
``autolangchat/db/sql/token_usage_schema_sqlite.sql``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from importlib import resources
from typing import Any, Dict, List, Optional

from .token_usage_base import BaseTokenUsageStore

logger = logging.getLogger(__name__)

# Columns added after the initial ``token_usage`` schema shipped. SQLite lacks
# ``ADD COLUMN IF NOT EXISTS``, so ``_migrate_columns_locked`` checks
# ``PRAGMA table_info`` and only adds the ones that are missing.
_ADDED_COLUMNS = (
    ("cache_read_tokens", "INTEGER NOT NULL DEFAULT 0 CHECK (cache_read_tokens >= 0)"),
    ("cache_write_tokens", "INTEGER NOT NULL DEFAULT 0 CHECK (cache_write_tokens >= 0)"),
)


def _dt_to_iso(value: datetime) -> str:
    """Serialize a datetime to a UTC ISO-8601 string.

    SQLite compares ``turn_ts`` as TEXT, so lexical order must match
    chronological order. Mixing tz-aware values with different offsets (or
    mixing aware and naive) would break ``ORDER BY``/range-filter results.
    We normalize to UTC and emit a fixed-width ``+00:00`` suffix so every
    row sorts correctly.

    Naive datetimes are *assumed* to be in the local timezone (matching
    ``datetime.now()`` without ``astimezone()``) and converted to UTC
    before formatting.
    """
    if value.tzinfo is None:
        value = value.astimezone()  # attach local tz
    return value.astimezone(timezone.utc).isoformat()


class SQLiteTokenUsageStore(BaseTokenUsageStore):
    """Async SQLite-backed token-usage store.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database. Parent directories are
        created on :meth:`open`. Use ``":memory:"`` for ephemeral test
        databases.
    init_schema:
        When True (default), apply ``token_usage_schema_sqlite.sql`` on
        :meth:`open`. The DDL uses ``IF NOT EXISTS`` so it's safe to re-run.
        Independently of this flag, :meth:`open` adds any columns introduced
        after the table was first created (e.g. ``cache_read_tokens``) to a
        pre-existing ``token_usage`` table, mirroring
        :class:`~.feedback_sqlite.SQLiteFeedbackStore`'s column migrations.

    Concurrency
    -----------
    A single :class:`sqlite3.Connection` opened with
    ``check_same_thread=False`` is shared across asyncio worker threads and
    serialized by a :class:`threading.Lock`. All blocking calls run via
    :func:`asyncio.to_thread`. WAL mode is enabled for concurrent reads.
    """

    SCHEMA_RESOURCE = ("autolangchat.db.sql", "token_usage_schema_sqlite.sql")

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
            "SQLiteTokenUsageStore ready (db_path=%s, init_schema=%s)",
            self._db_path,
            self._init_schema,
        )

    def _ensure_open_sync(self) -> None:
        """Idempotently open the SQLite connection.

        Called both from :meth:`open` (eager, via the FastAPI startup event
        when the host app uses ``on_event``) and from
        :meth:`_execute_write` (lazy fallback for hosts that use
        ``lifespan=`` — FastAPI silently ignores ``on_event`` registrations
        once a lifespan is set, so eager open cannot be relied on).
        """
        if self._conn is not None:
            return
        # Serialize the bootstrap so two concurrent first-use calls don't
        # race to connect / apply the DDL.
        with self._lock:
            if self._conn is not None:
                return
            self._open_sync_locked()

    def _open_sync_locked(self) -> None:
        if self._db_path != ":memory:":
            parent = os.path.dirname(self._db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        # ``check_same_thread=False`` is required because asyncio.to_thread
        # may execute callbacks on different worker threads. The lock above
        # serializes all access to keep sqlite3 happy.
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
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
        self._migrate_columns_locked(conn)
        self._conn = conn

    @staticmethod
    def _migrate_columns_locked(conn: sqlite3.Connection) -> None:
        """Idempotently add columns missing from a pre-existing table."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(token_usage)")}
        if not existing:
            # Table not created yet (init_schema=False on a fresh DB).
            return
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE token_usage ADD COLUMN {name} {ddl}")
                logger.info("Migrated token_usage table: added column %s", name)
        conn.commit()

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def record_turn(
        self,
        turn_id: str,
        session_id: str,
        user_id: Optional[str],
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        turn_ts: datetime,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        sql = """
            INSERT OR IGNORE INTO token_usage
                (id, session_id, user_id, model_id, input_tokens, output_tokens, turn_ts,
                 cache_read_tokens, cache_write_tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            turn_id,
            session_id,
            user_id,
            model_id,
            input_tokens,
            output_tokens,
            _dt_to_iso(turn_ts),
            cache_read_tokens,
            cache_write_tokens,
        )
        await asyncio.to_thread(self._execute_write, sql, params)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def list_by_user(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        sql = """
            SELECT session_id, model_id, input_tokens, output_tokens,
                   cache_read_tokens, cache_write_tokens, turn_ts
            FROM token_usage
            WHERE user_id = ?
            ORDER BY turn_ts DESC, id ASC
            LIMIT ? OFFSET ?
        """
        rows = await asyncio.to_thread(self._fetchall, sql, (user_id, limit, offset))
        return [
            {
                "session_id": r[0],
                "model_id": r[1],
                "input_tokens": r[2],
                "output_tokens": r[3],
                "cache_read_tokens": r[4],
                "cache_write_tokens": r[5],
                "turn_ts": r[6],
            }
            for r in rows
        ]

    async def aggregate_by_model(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT model_id, SUM(input_tokens), SUM(output_tokens),
                   SUM(cache_read_tokens), SUM(cache_write_tokens), COUNT(*)
            FROM token_usage
            GROUP BY model_id
            ORDER BY model_id ASC
        """
        rows = await asyncio.to_thread(self._fetchall, sql, ())
        return [
            {
                "model_id": r[0],
                "input_tokens": r[1],
                "output_tokens": r[2],
                "cache_read_tokens": r[3],
                "cache_write_tokens": r[4],
                "turn_count": r[5],
            }
            for r in rows
        ]

    async def aggregate_by_day(
        self,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, Any]]:
        if end <= start:
            raise ValueError("end must be after start")
        sql = """
            SELECT substr(turn_ts, 1, 10) AS day, SUM(input_tokens), SUM(output_tokens),
                   SUM(cache_read_tokens), SUM(cache_write_tokens), COUNT(*)
            FROM token_usage
            WHERE turn_ts >= ? AND turn_ts < ?
            GROUP BY day
            ORDER BY day ASC
        """
        rows = await asyncio.to_thread(self._fetchall, sql, (_dt_to_iso(start), _dt_to_iso(end)))
        return [
            {
                "date": r[0],
                "input_tokens": r[1],
                "output_tokens": r[2],
                "cache_read_tokens": r[3],
                "cache_write_tokens": r[4],
                "turn_count": r[5],
            }
            for r in rows
        ]

    async def aggregate_by_user(self, limit: int = 10) -> List[Dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        sql = """
            SELECT user_id, SUM(input_tokens), SUM(output_tokens),
                   SUM(cache_read_tokens), SUM(cache_write_tokens)
            FROM token_usage
            WHERE user_id IS NOT NULL
            GROUP BY user_id
            ORDER BY (SUM(input_tokens) + SUM(output_tokens)) DESC, user_id ASC
            LIMIT ?
        """
        rows = await asyncio.to_thread(self._fetchall, sql, (limit,))
        return [
            {
                "user_id": r[0],
                "input_tokens": r[1],
                "output_tokens": r[2],
                "cache_read_tokens": r[3],
                "cache_write_tokens": r[4],
            }
            for r in rows
        ]

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
            except Exception:
                self._conn.rollback()
                raise

    def _fetchall(self, sql: str, params: tuple) -> List[tuple]:
        self._ensure_open_sync()
        assert self._conn is not None
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()
