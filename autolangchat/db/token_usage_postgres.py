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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from .token_usage_base import BaseTokenUsageStore

if TYPE_CHECKING:
    from ..usage.ledger import UsageEvent

logger = logging.getLogger(__name__)

# Summed cost of priced turns (NULL when none are priced) and the count of unpriced turns.
_COST_AGGREGATES = "SUM(estimated_cost_micros)::BIGINT, COUNT(*) FILTER (WHERE estimated_cost_micros IS NULL)"


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
        provisioning task owns the DDL lifecycle. The DDL also carries the
        idempotent ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` migrations
        (e.g. ``cache_read_tokens``/``cache_write_tokens``) for tables created
        by earlier releases, so provisioning must re-apply it on upgrade.
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
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        estimated_cost_micros: Optional[int] = None,
        currency: Optional[str] = None,
        pricing_revision: Optional[str] = None,
        final: bool = True,
    ) -> None:
        sql = """
            INSERT INTO token_usage
                (id, session_id, user_id, model_id, input_tokens, output_tokens, turn_ts,
                 cache_read_tokens, cache_write_tokens, estimated_cost_micros, currency,
                 pricing_revision, final)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                input_tokens = EXCLUDED.input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                cache_read_tokens = EXCLUDED.cache_read_tokens,
                cache_write_tokens = EXCLUDED.cache_write_tokens,
                estimated_cost_micros = EXCLUDED.estimated_cost_micros,
                currency = EXCLUDED.currency,
                pricing_revision = EXCLUDED.pricing_revision,
                final = EXCLUDED.final
            WHERE NOT token_usage.final
        """
        params = (
            turn_id,
            session_id,
            user_id,
            model_id,
            input_tokens,
            output_tokens,
            turn_ts,
            cache_read_tokens,
            cache_write_tokens,
            estimated_cost_micros,
            currency,
            pricing_revision,
            final,
        )
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
            await conn.commit()

    async def record_events(self, events: Sequence[UsageEvent]) -> None:
        if not events:
            return
        sql = """
            INSERT INTO token_usage_events
                (id, turn_id, purpose, provider, model_id, input_tokens, output_tokens,
                 cache_read_tokens, cache_write_tokens, event_ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """
        rows = [
            (
                e.event_id,
                e.turn_id,
                e.purpose,
                e.provider,
                e.model_id,
                e.input_tokens,
                e.output_tokens,
                e.cache_read_tokens,
                e.cache_write_tokens,
                e.ts,
            )
            for e in events
        ]
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(sql, rows)
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
            SELECT session_id, model_id, input_tokens, output_tokens,
                   cache_read_tokens, cache_write_tokens, turn_ts,
                   id, estimated_cost_micros, currency, pricing_revision, final
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
                "cache_read_tokens": r[4],
                "cache_write_tokens": r[5],
                # Force UTC regardless of the connection's session timezone —
                # TIMESTAMPTZ always persists the correct instant, but the
                # *displayed* offset otherwise depends on session settings.
                # Matches aggregate_by_day's explicit UTC bucketing and the
                # SQLite backend's always-UTC TEXT storage.
                "turn_ts": r[6].astimezone(timezone.utc).isoformat() if hasattr(r[6], "astimezone") else r[6],
                "turn_id": r[7],
                "estimated_cost_micros": r[8],
                "currency": r[9],
                "pricing_revision": r[10],
                "final": bool(r[11]),
            }
            for r in rows
        ]

    async def aggregate_by_model(self) -> List[Dict[str, Any]]:
        sql = f"""
            SELECT model_id, SUM(input_tokens), SUM(output_tokens),
                   SUM(cache_read_tokens), SUM(cache_write_tokens), COUNT(*),
                   {_COST_AGGREGATES}
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
                "cache_read_tokens": r[3],
                "cache_write_tokens": r[4],
                "turn_count": r[5],
                "estimated_cost_micros": r[6],
                "unpriced_turn_count": r[7],
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
        sql = f"""
            SELECT (turn_ts AT TIME ZONE 'UTC')::date AS day,
                   SUM(input_tokens), SUM(output_tokens),
                   SUM(cache_read_tokens), SUM(cache_write_tokens), COUNT(*),
                   {_COST_AGGREGATES}
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
                "cache_read_tokens": r[3],
                "cache_write_tokens": r[4],
                "turn_count": r[5],
                "estimated_cost_micros": r[6],
                "unpriced_turn_count": r[7],
            }
            for r in rows
        ]

    async def aggregate_by_user(self, limit: int = 10) -> List[Dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        sql = f"""
            SELECT user_id, SUM(input_tokens), SUM(output_tokens),
                   SUM(cache_read_tokens), SUM(cache_write_tokens),
                   {_COST_AGGREGATES}
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
                "cache_read_tokens": r[3],
                "cache_write_tokens": r[4],
                "estimated_cost_micros": r[5],
                "unpriced_turn_count": r[6],
            }
            for r in rows
        ]
