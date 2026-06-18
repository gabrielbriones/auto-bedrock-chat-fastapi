"""LangGraph checkpoint factory.

Phase 1/2: MemorySaver — in-process, no persistence.
Phase 3:   AsyncPostgresSaver backed by an AsyncConnectionPool.

Usage
-----
Build time (sync, in __init__):
    checkpointer = build_checkpointer(postgres_url, pool_size)

Startup (async, in FastAPI startup event):
    await open_checkpointer(checkpointer)   # opens pool + creates schema

Shutdown (async):
    await close_checkpointer(checkpointer)  # closes pool gracefully
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def build_checkpointer(postgres_url: Optional[str] = None, pool_size: int = 5):
    """Return a LangGraph checkpointer (synchronous factory).

    When ``postgres_url`` is provided the checkpointer is backed by an
    ``AsyncConnectionPool`` (psycopg3).  The pool is created with
    ``open=False`` so the caller must call :func:`open_checkpointer`
    inside the FastAPI startup event before any graph invocations.

    Falls back to ``MemorySaver`` when ``postgres_url`` is ``None`` or the
    optional ``langgraph-checkpoint-postgres`` package is not installed.
    """
    if postgres_url:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg_pool import AsyncConnectionPool

            pool = AsyncConnectionPool(
                conninfo=postgres_url,
                min_size=0,
                max_size=max(pool_size, 4),  # min_size=0 means no eager connections
                open=False,  # opened asynchronously in open_checkpointer()
                kwargs={"autocommit": True, "prepare_threshold": 0},
            )
            checkpointer = AsyncPostgresSaver(pool)
            logger.info(
                "LangGraph checkpointer: AsyncPostgresSaver (Postgres, pool_size=%d)",
                pool_size,
            )
            return checkpointer
        except ImportError as exc:
            logger.warning(
                "langgraph-checkpoint-postgres or psycopg-pool not installed; "
                "falling back to MemorySaver. Install with: "
                "pip install langgraph-checkpoint-postgres psycopg[pool]. "
                "Error: %s",
                exc,
            )

    from langgraph.checkpoint.memory import MemorySaver

    logger.info("LangGraph checkpointer: MemorySaver (in-process, no persistence)")
    return MemorySaver()


async def open_checkpointer(checkpointer) -> None:
    """Open the connection pool and initialize the checkpoint schema.

    Must be called once from the FastAPI startup event after
    :func:`build_checkpointer`.  A no-op for ``MemorySaver``.
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        if not isinstance(checkpointer, AsyncPostgresSaver):
            return
    except ImportError:
        return

    try:
        from psycopg_pool import AsyncConnectionPool

        if isinstance(checkpointer.conn, AsyncConnectionPool):
            logger.info("Opening LangGraph Postgres connection pool…")
            await checkpointer.conn.open()
            logger.info("LangGraph Postgres connection pool open")
    except Exception:
        logger.exception("Failed to open LangGraph Postgres connection pool")
        raise

    logger.info("Initializing LangGraph checkpoint schema (setup)…")
    await checkpointer.setup()
    logger.info("LangGraph checkpoint schema ready")


async def close_checkpointer(checkpointer) -> None:
    """Close the connection pool gracefully.

    Must be called from the FastAPI shutdown event.  A no-op for
    ``MemorySaver``.
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        if not isinstance(checkpointer, AsyncPostgresSaver):
            return
    except ImportError:
        return

    try:
        from psycopg_pool import AsyncConnectionPool

        if isinstance(checkpointer.conn, AsyncConnectionPool):
            logger.info("Closing LangGraph Postgres connection pool…")
            await checkpointer.conn.close()
            logger.info("LangGraph Postgres connection pool closed")
    except Exception:
        logger.exception("Failed to close LangGraph Postgres connection pool")


async def purge_expired_checkpoints(checkpointer, ttl_seconds: int) -> int:
    """Delete all LangGraph checkpoints whose most recent ``ts`` is older than
    ``ttl_seconds``.

    Works by:
    1. Finding thread_ids whose latest checkpoint timestamp is older than the TTL.
    2. Deleting matching rows from ``checkpoints``, ``checkpoint_blobs``, and
       ``checkpoint_writes``.

    Returns the number of thread_ids purged.  A no-op (returns 0) for
    ``MemorySaver``.
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        if not isinstance(checkpointer, AsyncPostgresSaver):
            return 0
    except ImportError:
        return 0

    _FIND_OLD_THREADS = """
        SELECT thread_id
        FROM checkpoints
        GROUP BY thread_id
        HAVING MAX((checkpoint->>'ts')::timestamptz) < NOW() - INTERVAL '{seconds} seconds'
    """
    _DELETE_FROM = "DELETE FROM {table} WHERE thread_id = ANY(%s)"

    try:
        async with checkpointer._cursor() as cur:
            await cur.execute(_FIND_OLD_THREADS.format(seconds=int(ttl_seconds)))
            rows = await cur.fetchall()
            if not rows:
                return 0
            old_ids = [r["thread_id"] for r in rows]
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                await cur.execute(_DELETE_FROM.format(table=table), (old_ids,))
            logger.info(
                "Purged %d expired checkpoint thread(s) (ttl=%ds)",
                len(old_ids),
                ttl_seconds,
            )
            return len(old_ids)
    except Exception:
        logger.exception("Error during checkpoint expiry purge")
        return 0
