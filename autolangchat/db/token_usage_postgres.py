"""
PostgreSQL-backed token-usage store.

Production backend for :class:`~.token_usage_base.BaseTokenUsageStore`.
Schema: ``autolangchat/db/sql/token_usage_schema.sql``.

Requires the optional ``[postgres]`` extra::

    pip install autolangchat[postgres]
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from importlib import resources
from typing import Any, Dict, List, Optional, Tuple

from .token_usage_base import BaseTokenUsageStore

logger = logging.getLogger(__name__)


_MISSING_DEPS_MSG = (
    "PostgresTokenUsageStore requires the optional PostgreSQL packages. "
    "Install them with:  pip install autolangchat[postgres]"
)


def _import_psycopg_async() -> Tuple[Any, Any]:
    """Return ``(psycopg, AsyncConnectionPool)`` or raise ``ImportError``."""
    try:
        import psycopg  # noqa: F401
        from psycopg_pool import AsyncConnectionPool

        return psycopg, AsyncConnectionPool
    except ImportError as exc:  # pragma: no cover - exercised in env-less CI
        raise ImportError(_MISSING_DEPS_MSG) from exc


class PostgresTokenUsageStore(BaseTokenUsageStore):
    """Async PostgreSQL-backed store for per-turn token-usage records.

    Parameters
    ----------
    connection_url:
        ``libpq``-style PostgreSQL connection URL.
    pool_min_size, pool_max_size:
        Bounds for the underlying :class:`psycopg_pool.AsyncConnectionPool`.
    init_schema:
        When ``True`` (default), execute ``sql/token_usage_schema.sql``
        against the database on :meth:`open`. Set ``False`` if a separate
        provisioning task owns the DDL lifecycle.
    """

    SCHEMA_RESOURCE = ("autolangchat.db.sql", "token_usage_schema.sql")

    def __init__(
        self,
        connection_url: str,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
        init_schema: bool = True,
    ) -> None:
        _psycopg, AsyncConnectionPool = _import_psycopg_async()
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
        logger.info("PostgresTokenUsageStore ready (init_schema=%s)", self._init_schema)

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

    async def record_turn(
        self,
        turn_id: str,
        session_id: str,
        user_id: Optional[str],
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        turn_ts: datetime,
    ) -> None:
        sql = """
            INSERT INTO token_usage
                (id, session_id, user_id, model_id, input_tokens, output_tokens, turn_ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """
        params = (
            turn_id,
            session_id,
            user_id,
            model_id,
            input_tokens,
            output_tokens,
            turn_ts,
        )
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
            await conn.commit()

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
            SELECT session_id, model_id, input_tokens, output_tokens, turn_ts
            FROM token_usage
            WHERE user_id = %s
            ORDER BY turn_ts DESC, id ASC
            LIMIT %s OFFSET %s
        """
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (user_id, limit, offset))
                rows = await cur.fetchall()
        return [
            {
                "session_id": r[0],
                "model_id": r[1],
                "input_tokens": r[2],
                "output_tokens": r[3],
                # Force UTC regardless of the connection's session timezone —
                # TIMESTAMPTZ always persists the correct instant, but the
                # *displayed* offset otherwise depends on session settings.
                # Matches aggregate_by_day's explicit UTC bucketing and the
                # SQLite backend's always-UTC TEXT storage.
                "turn_ts": r[4].astimezone(timezone.utc).isoformat() if hasattr(r[4], "astimezone") else r[4],
            }
            for r in rows
        ]

    async def aggregate_by_model(self) -> List[Dict[str, Any]]:
        sql = """
            SELECT model_id, SUM(input_tokens), SUM(output_tokens), COUNT(*)
            FROM token_usage
            GROUP BY model_id
            ORDER BY model_id ASC
        """
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql)
                rows = await cur.fetchall()
        return [
            {
                "model_id": r[0],
                "input_tokens": r[1],
                "output_tokens": r[2],
                "turn_count": r[3],
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
        # Bucket by UTC calendar date regardless of the connection's session
        # timezone. This enforces the API contract that day buckets are
        # computed in UTC — record_turn does not itself normalize turn_ts to
        # UTC (it persists whatever tz-aware datetime the caller supplies),
        # so this explicit conversion is what actually guarantees UTC
        # bucketing here, independent of DB session settings.
        sql = """
            SELECT (turn_ts AT TIME ZONE 'UTC')::date AS day,
                   SUM(input_tokens), SUM(output_tokens), COUNT(*)
            FROM token_usage
            WHERE turn_ts >= %s AND turn_ts < %s
            GROUP BY day
            ORDER BY day ASC
        """
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (start, end))
                rows = await cur.fetchall()
        return [
            {
                "date": str(r[0]),
                "input_tokens": r[1],
                "output_tokens": r[2],
                "turn_count": r[3],
            }
            for r in rows
        ]

    async def aggregate_by_user(self, limit: int = 10) -> List[Dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        sql = """
            SELECT user_id, SUM(input_tokens), SUM(output_tokens)
            FROM token_usage
            WHERE user_id IS NOT NULL
            GROUP BY user_id
            ORDER BY (SUM(input_tokens) + SUM(output_tokens)) DESC, user_id ASC
            LIMIT %s
        """
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (limit,))
                rows = await cur.fetchall()
        return [
            {
                "user_id": r[0],
                "input_tokens": r[1],
                "output_tokens": r[2],
            }
            for r in rows
        ]
