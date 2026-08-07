"""
SQLite-backed UserSettingsStore.

Zero-config implementation of the async
:class:`~.user_settings_base.BaseUserSettingsStore` interface using SQLite.
Uses the stdlib ``sqlite3`` module wrapped in :func:`asyncio.to_thread`
without adding a third-party async-SQLite dependency. Mirrors the
lifecycle/concurrency approach of
:class:`~.conversation_sqlite.SQLiteConversationStore`.

Schema lives at
``autolangchat/db/sql/user_settings_schema_sqlite.sql``.
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
from typing import Any, Dict, Optional

from .user_settings_base import BaseUserSettingsStore

logger = logging.getLogger(__name__)

_SELECT_SQL = "SELECT user_id, settings, created_at, updated_at FROM user_settings WHERE user_id = ?"


def _now_iso() -> str:
    """Return the current time as a UTC ISO-8601 string.

    SQLite compares ``created_at``/``updated_at`` as TEXT, so lexical order
    must match chronological order — normalizing to UTC with a fixed-width
    ``+00:00`` suffix keeps every row sorting correctly, mirroring
    ``conversation_sqlite._now_iso``.
    """
    return datetime.now(timezone.utc).isoformat()


class SQLiteUserSettingsStore(BaseUserSettingsStore):
    """Async SQLite-backed per-user settings store.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database. Parent directories are
        created on :meth:`open`. Use ``":memory:"`` for ephemeral test
        databases.
    init_schema:
        When True (default), apply ``user_settings_schema_sqlite.sql`` on
        :meth:`open`. The DDL uses ``IF NOT EXISTS`` so it's safe to re-run.

    Concurrency
    -----------
    A single :class:`sqlite3.Connection` opened with
    ``check_same_thread=False`` is shared across asyncio worker threads and
    serialized by a :class:`threading.Lock`. All blocking calls run via
    :func:`asyncio.to_thread`. WAL mode is enabled for concurrent reads.
    """

    SCHEMA_RESOURCE = ("autolangchat.db.sql", "user_settings_schema_sqlite.sql")

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
            "SQLiteUserSettingsStore ready (db_path=%s, init_schema=%s)",
            self._db_path,
            self._init_schema,
        )

    def _ensure_open_sync(self) -> None:
        """Idempotently open the SQLite connection.

        Called both from :meth:`open` (eager, via the FastAPI startup event
        when the host app uses ``on_event``) and from every read/write
        helper (lazy fallback for hosts that use ``lifespan=`` — FastAPI
        silently ignores ``on_event`` registrations once a lifespan is set).
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
        self._conn = conn

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_settings(self, user_id: str) -> Optional[Dict[str, Any]]:
        row = await asyncio.to_thread(self._fetchone, _SELECT_SQL, (user_id,))
        return self._row_to_dict(row) if row is not None else None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def get_or_create_settings(
        self,
        user_id: str,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self._get_or_create_sync, user_id, defaults or {})

    def _get_or_create_sync(self, user_id: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_open_sync()
        assert self._conn is not None
        now = _now_iso()
        with self._lock:
            try:
                # ``INSERT OR IGNORE`` is SQLite's equivalent of Postgres'
                # ``ON CONFLICT (user_id) DO NOTHING``: whichever tab wins the
                # race inserts, the others no-op, and everyone reads the same
                # winning row back below.
                self._conn.execute(
                    "INSERT OR IGNORE INTO user_settings (user_id, settings, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, json.dumps(defaults), now, now),
                )
                self._conn.commit()
                row = self._conn.execute(_SELECT_SQL, (user_id,)).fetchone()
            except Exception:
                self._conn.rollback()
                raise
        if row is None:  # pragma: no cover - only reachable if the row vanished mid-call
            raise RuntimeError(f"user_settings row for {user_id!r} disappeared during get-or-create")
        return self._row_to_dict(row)

    async def set_settings(self, user_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._set_settings_sync, user_id, settings)

    def _set_settings_sync(self, user_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_open_sync()
        assert self._conn is not None
        now = _now_iso()
        payload = json.dumps(settings)
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO user_settings (user_id, settings, created_at, updated_at)
                         VALUES (?, ?, ?, ?)
                    ON CONFLICT (user_id) DO UPDATE
                            SET settings = excluded.settings,
                                updated_at = excluded.updated_at
                    """,
                    (user_id, payload, now, now),
                )
                self._conn.commit()
                row = self._conn.execute(_SELECT_SQL, (user_id,)).fetchone()
            except Exception:
                self._conn.rollback()
                raise
        if row is None:  # pragma: no cover - only reachable if the row vanished mid-call
            raise RuntimeError(f"user_settings row for {user_id!r} disappeared during upsert")
        return self._row_to_dict(row)

    async def delete_settings(self, user_id: str) -> None:
        await asyncio.to_thread(self._execute_write, "DELETE FROM user_settings WHERE user_id = ?", (user_id,))

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

    @staticmethod
    def _row_to_dict(row: tuple) -> Dict[str, Any]:
        user_id, settings_raw, created_at, updated_at = row
        return {
            "user_id": user_id,
            "settings": _loads_settings(user_id, settings_raw),
            "created_at": created_at,
            "updated_at": updated_at,
        }


def _loads_settings(user_id: str, raw: Any) -> Dict[str, Any]:
    """Decode the stored JSON document, degrading to ``{}`` on garbage.

    A corrupt or non-object payload must never break a user's chat session:
    the caller treats an empty document as "no overrides" and falls back to
    the server-side defaults.
    """
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring unparseable user_settings payload for user_id=%s", user_id)
        return {}
    if not isinstance(decoded, dict):
        logger.warning(
            "Ignoring non-object user_settings payload for user_id=%s (got %s)",
            user_id,
            type(decoded).__name__,
        )
        return {}
    return decoded
