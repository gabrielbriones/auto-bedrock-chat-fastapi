"""
PostgreSQL-backed UserSettingsStore.

Production backend for :class:`~.user_settings_base.BaseUserSettingsStore`.
Schema: ``autolangchat/db/sql/user_settings_schema.sql``.

Requires the optional ``[postgres]`` extra::

    pip install autolangchat[postgres]
"""

from __future__ import annotations

import logging
from importlib import resources
from typing import Any, Dict, Optional, Tuple

from .user_settings_base import BaseUserSettingsStore

logger = logging.getLogger(__name__)

_SELECT_SQL = "SELECT user_id, settings, created_at, updated_at FROM user_settings WHERE user_id = %s"

_MISSING_DEPS_MSG = (
    "PostgresUserSettingsStore requires the optional PostgreSQL packages. "
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


class PostgresUserSettingsStore(BaseUserSettingsStore):
    """Async PostgreSQL-backed store for per-user chat settings.

    Parameters
    ----------
    connection_url:
        ``libpq``-style PostgreSQL connection URL.
    pool_min_size, pool_max_size:
        Bounds for the underlying :class:`psycopg_pool.AsyncConnectionPool`.
    init_schema:
        When ``True`` (default), execute ``sql/user_settings_schema.sql``
        against the database on :meth:`open`. Set ``False`` if a separate
        provisioning task owns the DDL lifecycle.
    """

    SCHEMA_RESOURCE = ("autolangchat.db.sql", "user_settings_schema.sql")

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
        logger.info("PostgresUserSettingsStore ready (init_schema=%s)", self._init_schema)

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
    # Reads
    # ------------------------------------------------------------------

    async def get_settings(self, user_id: str) -> Optional[Dict[str, Any]]:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(_SELECT_SQL, (user_id,))
            row = await cur.fetchone()
        return self._row_to_dict(row) if row else None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def get_or_create_settings(
        self,
        user_id: str,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        sql = """
            INSERT INTO user_settings (user_id, settings, created_at, updated_at)
                 VALUES (%s, %s, now(), now())
            ON CONFLICT (user_id) DO NOTHING
        """
        async with self._pool.connection() as conn, conn.cursor() as cur:
            # ``DO NOTHING`` keeps this race safe when the same user opens
            # several tabs at once: whichever connection wins inserts, the
            # others no-op, and everyone reads the same winning row back.
            await cur.execute(sql, (user_id, self._Jsonb(defaults or {})))
            await conn.commit()
            await cur.execute(_SELECT_SQL, (user_id,))
            row = await cur.fetchone()
        if row is None:  # pragma: no cover - only reachable if the row vanished mid-call
            raise RuntimeError(f"user_settings row for {user_id!r} disappeared during get-or-create")
        return self._row_to_dict(row)

    async def set_settings(self, user_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        sql = """
            INSERT INTO user_settings (user_id, settings, created_at, updated_at)
                 VALUES (%s, %s, now(), now())
            ON CONFLICT (user_id) DO UPDATE
                    SET settings = EXCLUDED.settings,
                        updated_at = now()
              RETURNING user_id, settings, created_at, updated_at
        """
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, (user_id, self._Jsonb(settings)))
            row = await cur.fetchone()
            await conn.commit()
        if row is None:  # pragma: no cover - RETURNING always yields a row for an upsert
            raise RuntimeError(f"user_settings upsert for {user_id!r} returned no row")
        return self._row_to_dict(row)

    async def delete_settings(self, user_id: str) -> None:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("DELETE FROM user_settings WHERE user_id = %s", (user_id,))
            await conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: tuple) -> Dict[str, Any]:
        user_id, settings, created_at, updated_at = row
        return {
            "user_id": user_id,
            # psycopg decodes JSONB for us; guard against a non-object
            # document so a hand-edited row can't break a chat session.
            "settings": settings if isinstance(settings, dict) else {},
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
            "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
        }
