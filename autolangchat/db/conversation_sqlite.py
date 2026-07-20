"""
SQLite-backed ConversationStore.

Zero-config implementation of the async
:class:`~.conversation_base.BaseConversationStore` interface using SQLite.
Uses the stdlib ``sqlite3`` module wrapped in :func:`asyncio.to_thread`
without adding a third-party async-SQLite dependency. Mirrors the
lifecycle/concurrency approach of
:class:`~.feedback_sqlite.SQLiteFeedbackStore`.

Schema lives at
``autolangchat/db/sql/conversation_schema_sqlite.sql``.
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
from typing import Any, Dict, List, Optional

from ..exceptions import ConversationNotFoundError
from .conversation_base import BaseConversationStore

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return the current time as a UTC ISO-8601 string.

    SQLite compares ``created_at``/``updated_at`` as TEXT, so lexical order
    must match chronological order — normalizing to UTC with a fixed-width
    ``+00:00`` suffix keeps every row sorting correctly, mirroring
    ``feedback_sqlite._dt_to_iso``.
    """
    return datetime.now(timezone.utc).isoformat()


class SQLiteConversationStore(BaseConversationStore):
    """Async SQLite-backed conversation metadata store.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database. Parent directories are
        created on :meth:`open`. Use ``":memory:"`` for ephemeral test
        databases.
    init_schema:
        When True (default), apply ``conversation_schema_sqlite.sql`` on
        :meth:`open`. The DDL uses ``IF NOT EXISTS`` so it's safe to re-run.
    max_conversations_per_user:
        When greater than 0 (default 100), :meth:`create_conversation` prunes
        the oldest (by ``updated_at``) conversations for a user beyond this
        cap. Set to 0 to disable pruning.

    Concurrency
    -----------
    A single :class:`sqlite3.Connection` opened with
    ``check_same_thread=False`` is shared across asyncio worker threads and
    serialized by a :class:`threading.Lock`. All blocking calls run via
    :func:`asyncio.to_thread`. WAL mode is enabled for concurrent reads.
    """

    SCHEMA_RESOURCE = ("autolangchat.db.sql", "conversation_schema_sqlite.sql")

    def __init__(
        self,
        db_path: str,
        init_schema: bool = True,
        max_conversations_per_user: int = 100,
    ) -> None:
        self._db_path = db_path
        self._init_schema = init_schema
        self._max_conversations_per_user = max_conversations_per_user
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        await asyncio.to_thread(self._ensure_open_sync)
        logger.info(
            "SQLiteConversationStore ready (db_path=%s, init_schema=%s, max_conversations_per_user=%s)",
            self._db_path,
            self._init_schema,
            self._max_conversations_per_user,
        )

    def _ensure_open_sync(self) -> None:
        """Idempotently open the SQLite connection.

        Called both from :meth:`open` (eager, via the FastAPI startup event
        when the host app uses ``on_event``) and from every
        ``_execute_write``/``_fetchone``/``_fetchall`` call (lazy fallback
        for hosts that use ``lifespan=`` — FastAPI silently ignores
        ``on_event`` registrations once a lifespan is set).
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
    # Writes
    # ------------------------------------------------------------------

    async def create_conversation(
        self,
        conversation_id: str,
        user_id: str,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = _now_iso()
        sql = """
            INSERT INTO conversations
                (id, user_id, title, created_at, updated_at, message_count, metadata, is_archived)
            VALUES (?, ?, ?, ?, ?, 0, ?, 0)
        """
        params = (conversation_id, user_id, title, now, now, json.dumps(metadata or {}))
        await asyncio.to_thread(self._execute_write, sql, params)
        if self._max_conversations_per_user > 0:
            await asyncio.to_thread(self._prune_oldest_sync, user_id, self._max_conversations_per_user)

    def _prune_oldest_sync(self, user_id: str, keep: int) -> None:
        self._ensure_open_sync()
        assert self._conn is not None
        with self._lock:
            try:
                self._conn.execute(
                    """
                    DELETE FROM conversations
                     WHERE user_id = ?
                       AND id NOT IN (
                           SELECT id FROM conversations
                            WHERE user_id = ?
                            ORDER BY updated_at DESC, id ASC
                            LIMIT ?
                       )
                    """,
                    (user_id, user_id, keep),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    async def update_conversation(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if title is None and metadata is None:
            raise ValueError("update_conversation requires title and/or metadata")
        await asyncio.to_thread(self._update_conversation_sync, conversation_id, title, metadata)

    def _update_conversation_sync(
        self,
        conversation_id: str,
        title: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        self._ensure_open_sync()
        assert self._conn is not None
        set_clauses = ["updated_at = ?"]
        params: List[Any] = [_now_iso()]
        if title is not None:
            set_clauses.append("title = ?")
            params.append(title)
        if metadata is not None:
            set_clauses.append("metadata = ?")
            params.append(json.dumps(metadata))
        params.append(conversation_id)

        with self._lock:
            try:
                cur = self._conn.execute(
                    f"UPDATE conversations SET {', '.join(set_clauses)} WHERE id = ?",
                    tuple(params),
                )
                self._conn.commit()
                found = cur.rowcount > 0
            except Exception:
                self._conn.rollback()
                raise
        if not found:
            raise ConversationNotFoundError(f"conversation {conversation_id} not found")

    async def record_turn(self, conversation_id: str) -> None:
        await asyncio.to_thread(self._record_turn_sync, conversation_id)

    def _record_turn_sync(self, conversation_id: str) -> None:
        self._ensure_open_sync()
        assert self._conn is not None
        with self._lock:
            try:
                cur = self._conn.execute(
                    "UPDATE conversations SET message_count = message_count + 1, updated_at = ? WHERE id = ?",
                    (_now_iso(), conversation_id),
                )
                self._conn.commit()
                found = cur.rowcount > 0
            except Exception:
                self._conn.rollback()
                raise
        if not found:
            raise ConversationNotFoundError(f"conversation {conversation_id} not found")

    async def delete_conversation(self, conversation_id: str) -> None:
        await asyncio.to_thread(self._execute_write, "DELETE FROM conversations WHERE id = ?", (conversation_id,))

    async def delete_all_conversations(self, user_id: str) -> int:
        return await asyncio.to_thread(self._delete_all_sync, user_id)

    def _delete_all_sync(self, user_id: str) -> int:
        self._ensure_open_sync()
        assert self._conn is not None
        with self._lock:
            try:
                cur = self._conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
                self._conn.commit()
                return cur.rowcount
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        sql = (
            "SELECT id, user_id, title, created_at, updated_at, message_count, metadata, is_archived "
            "FROM conversations WHERE id = ?"
        )
        row = await asyncio.to_thread(self._fetchone, sql, (conversation_id,))
        return self._row_to_dict(row) if row is not None else None

    async def list_conversations(
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
            SELECT id, user_id, title, created_at, updated_at, message_count, metadata, is_archived
            FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC, id ASC
            LIMIT ? OFFSET ?
        """
        rows = await asyncio.to_thread(self._fetchall, sql, (user_id, limit, offset))
        return [self._row_to_dict(r) for r in rows]

    async def get_conversation_count(self, user_id: str) -> int:
        row = await asyncio.to_thread(
            self._fetchone, "SELECT count(*) FROM conversations WHERE user_id = ?", (user_id,)
        )
        return int(row[0]) if row else 0

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

    def _fetchall(self, sql: str, params: tuple) -> List[tuple]:
        self._ensure_open_sync()
        assert self._conn is not None
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()

    @staticmethod
    def _row_to_dict(row: tuple) -> Dict[str, Any]:
        (
            conv_id,
            user_id,
            title,
            created_at,
            updated_at,
            message_count,
            metadata_raw,
            is_archived,
        ) = row
        return {
            "id": conv_id,
            "user_id": user_id,
            "title": title,
            "created_at": created_at,
            "updated_at": updated_at,
            "message_count": message_count,
            "metadata": json.loads(metadata_raw) if metadata_raw else {},
            "is_archived": bool(is_archived),
        }
