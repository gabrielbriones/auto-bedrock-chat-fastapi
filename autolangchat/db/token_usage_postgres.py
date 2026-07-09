"""
PostgreSQL-backed token-usage store.

Production backend for :class:`~.token_usage_base.BaseTokenUsageStore`.
Schema: ``autolangchat/db/sql/token_usage_schema.sql``.

Requires the optional ``[postgres]`` extra::

    pip install autolangchat[postgres]
"""

from __future__ import annotations

import logging
from datetime import datetime
from importlib import resources
from typing import Any, Optional, Tuple

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
