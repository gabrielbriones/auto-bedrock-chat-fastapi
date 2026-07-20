"""
PostgreSQL-backed ConversationStore.

Production backend for :class:`~.conversation_base.BaseConversationStore`.
Schema: ``autolangchat/db/sql/conversation_schema.sql``.

Requires the optional ``[postgres]`` extra::

    pip install autolangchat[postgres]
"""

from __future__ import annotations

import logging
from importlib import resources
from typing import Any, Dict, List, Optional, Tuple

from ..exceptions import ConversationNotFoundError
from .conversation_base import BaseConversationStore

logger = logging.getLogger(__name__)


_MISSING_DEPS_MSG = (
    "PostgresConversationStore requires the optional PostgreSQL packages. "
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


class PostgresConversationStore(BaseConversationStore):
    """Async PostgreSQL-backed store for per-user conversation metadata.

    Parameters
    ----------
    connection_url:
        ``libpq``-style PostgreSQL connection URL.
    pool_min_size, pool_max_size:
        Bounds for the underlying :class:`psycopg_pool.AsyncConnectionPool`.
    init_schema:
        When ``True`` (default), execute ``sql/conversation_schema.sql``
        against the database on :meth:`open`. Set ``False`` if a separate
        provisioning task owns the DDL lifecycle.
    max_conversations_per_user:
        When greater than 0 (default 100), :meth:`create_conversation` prunes
        the oldest (by ``updated_at``) conversations for a user beyond this
        cap. Set to 0 to disable pruning.
    """

    SCHEMA_RESOURCE = ("autolangchat.db.sql", "conversation_schema.sql")

    def __init__(
        self,
        connection_url: str,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
        init_schema: bool = True,
        max_conversations_per_user: int = 100,
    ) -> None:
        psycopg, AsyncConnectionPool, Jsonb = _import_psycopg_async()
        self._psycopg = psycopg
        self._Jsonb = Jsonb
        self._connection_url = connection_url
        self._init_schema = init_schema
        self._max_conversations_per_user = max_conversations_per_user
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
        logger.info(
            "PostgresConversationStore ready (init_schema=%s, max_conversations_per_user=%s)",
            self._init_schema,
            self._max_conversations_per_user,
        )

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
        sql = """
            INSERT INTO conversations
                (id, user_id, title, created_at, updated_at, message_count, metadata, is_archived)
            VALUES (%s, %s, %s, now(), now(), 0, %s, false)
        """
        params = (conversation_id, user_id, title, self._Jsonb(metadata or {}))
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            await conn.commit()
        if self._max_conversations_per_user > 0:
            await self._prune_oldest(user_id, self._max_conversations_per_user)

    async def _prune_oldest(self, user_id: str, keep: int) -> None:
        sql = """
            DELETE FROM conversations
             WHERE user_id = %s
               AND id NOT IN (
                   SELECT id FROM conversations
                    WHERE user_id = %s
                    ORDER BY updated_at DESC, id ASC
                    LIMIT %s
               )
        """
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, (user_id, user_id, keep))
            await conn.commit()

    async def update_conversation(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if title is None and metadata is None:
            raise ValueError("update_conversation requires title and/or metadata")

        set_clauses = ["updated_at = now()"]
        params: List[Any] = []
        if title is not None:
            set_clauses.append("title = %s")
            params.append(title)
        if metadata is not None:
            set_clauses.append("metadata = %s")
            params.append(self._Jsonb(metadata))
        params.append(conversation_id)

        sql = f"UPDATE conversations SET {', '.join(set_clauses)} WHERE id = %s"
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, tuple(params))
            found = cur.rowcount > 0
            await conn.commit()
        if not found:
            raise ConversationNotFoundError(f"conversation {conversation_id} not found")

    async def record_turn(self, conversation_id: str) -> None:
        sql = "UPDATE conversations SET message_count = message_count + 1, updated_at = now() WHERE id = %s"
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, (conversation_id,))
            found = cur.rowcount > 0
            await conn.commit()
        if not found:
            raise ConversationNotFoundError(f"conversation {conversation_id} not found")

    async def delete_conversation(self, conversation_id: str) -> None:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))
            await conn.commit()

    async def delete_all_conversations(self, user_id: str) -> int:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("DELETE FROM conversations WHERE user_id = %s", (user_id,))
            deleted = cur.rowcount
            await conn.commit()
            return deleted

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        sql = (
            "SELECT id, user_id, title, created_at, updated_at, message_count, metadata, is_archived "
            "FROM conversations WHERE id = %s"
        )
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, (conversation_id,))
            row = await cur.fetchone()
        return self._row_to_dict(row) if row else None

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
            WHERE user_id = %s
            ORDER BY updated_at DESC, id ASC
            LIMIT %s OFFSET %s
        """
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, (user_id, limit, offset))
            rows = await cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def get_conversation_count(self, user_id: str) -> int:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM conversations WHERE user_id = %s", (user_id,))
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: tuple) -> Dict[str, Any]:
        (
            conv_id,
            user_id,
            title,
            created_at,
            updated_at,
            message_count,
            metadata,
            is_archived,
        ) = row
        return {
            "id": str(conv_id),
            "user_id": user_id,
            "title": title,
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
            "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
            "message_count": message_count,
            "metadata": metadata or {},
            "is_archived": bool(is_archived),
        }
