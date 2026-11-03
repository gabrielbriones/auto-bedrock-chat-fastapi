"""
Token usage storage — abstract interface.

Concrete backends live in:

* :mod:`autolangchat.db.token_usage_sqlite` (zero-config default)
* :mod:`autolangchat.db.token_usage_postgres` (production)

Use :func:`autolangchat.db.create_token_usage_store` to build the
backend selected by ``ChatConfig.token_usage_storage_type``.

Token semantics
---------------
``input_tokens`` is the *total* prompt-token count for a turn, **including**
any prompt-cache tokens. ``cache_read_tokens`` (prompt tokens served from
the provider's prompt cache) and ``cache_write_tokens`` (prompt tokens
written to the prompt cache) are *subsets* of ``input_tokens``, so the
uncached input is ``input_tokens - cache_read_tokens - cache_write_tokens``.
Rankings/totals based on ``input_tokens + output_tokens`` therefore already
account for cached tokens.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional


class BaseTokenUsageStore(ABC):
    """Abstract async data-access layer for per-turn token-usage records.

    Concrete backends (SQLite, Postgres) implement the same surface so the
    rest of the codebase — notably the WebSocket handler — doesn't depend
    on the storage technology.
    """

    @abstractmethod
    async def open(self) -> None:
        """Acquire any underlying resources and (optionally) bootstrap schema."""

    @abstractmethod
    async def close(self) -> None:
        """Release underlying resources."""

    @abstractmethod
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
        """Persist one turn's token counts.

        ``input_tokens`` is the total prompt-token count, including cache
        tokens; ``cache_read_tokens`` / ``cache_write_tokens`` are subsets of
        it (see the module docstring) and default to ``0`` for providers or
        turns without prompt-cache information.

        Idempotent: a duplicate ``turn_id`` is silently ignored rather than
        raising, so callers may safely retry after a transient failure
        without risking double-counting.
        """

    @abstractmethod
    async def list_by_user(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return per-turn token-usage rows for ``user_id``, newest first.

        Each row is a ``dict`` with keys ``session_id``, ``model_id``,
        ``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
        ``cache_write_tokens``, ``turn_ts`` (an ISO-8601 string). The two
        cache counters are subsets of ``input_tokens``. Pagination follows the same contract as
        :meth:`~autolangchat.db.feedback_base.BaseFeedbackStore.list_pending`:
        ``limit`` must be positive, ``offset`` non-negative.
        """

    @abstractmethod
    async def aggregate_by_model(self) -> List[Dict[str, Any]]:
        """Return per-model aggregate token usage across all recorded turns.

        Each row is a ``dict`` with keys ``model_id``, ``input_tokens``
        (summed), ``output_tokens`` (summed), ``cache_read_tokens`` (summed),
        ``cache_write_tokens`` (summed), ``turn_count``. The cache counters
        are subsets of ``input_tokens``.
        """

    @abstractmethod
    async def aggregate_by_day(
        self,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, Any]]:
        """Return per-day aggregate token usage within ``[start, end)``.

        Day buckets are computed in UTC. This is enforced at query time by
        each backend's ``aggregate_by_day`` implementation (independent of
        DB session timezone settings) — ``record_turn`` does not itself
        guarantee ``turn_ts`` is UTC-normalized on write; callers are
        expected to pass a UTC-aware ``datetime``. Each row is a ``dict``
        with keys ``date`` (an ``"YYYY-MM-DD"`` string), ``input_tokens``
        (summed), ``output_tokens`` (summed), ``cache_read_tokens`` (summed),
        ``cache_write_tokens`` (summed), ``turn_count``. The cache counters
        are subsets of ``input_tokens``. ``end`` must be strictly after
        ``start``.
        """

    @abstractmethod
    async def aggregate_by_user(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the top ``limit`` users ranked by combined token usage.

        Ranking key is ``input_tokens + output_tokens`` (summed across all
        turns), descending — ``input_tokens`` already includes cache tokens,
        so they are not added again. Rows with a ``NULL``/``None`` ``user_id``
        (anonymous sessions) are excluded — there's no meaningful "user" to
        rank. Each row is a ``dict`` with keys ``user_id``, ``input_tokens``
        (summed), ``output_tokens`` (summed), ``cache_read_tokens`` (summed),
        ``cache_write_tokens`` (summed). ``limit`` must be positive.
        """
