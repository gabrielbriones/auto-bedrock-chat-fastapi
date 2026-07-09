"""
Token usage storage — abstract interface.

Concrete backends live in:

* :mod:`autolangchat.db.token_usage_sqlite` (zero-config default)
* :mod:`autolangchat.db.token_usage_postgres` (production)

Use :func:`autolangchat.db.create_token_usage_store` to build the
backend selected by ``ChatConfig.token_usage_storage_type``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional


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
    ) -> None:
        """Persist one turn's token counts.

        Idempotent: a duplicate ``turn_id`` is silently ignored rather than
        raising, so callers may safely retry after a transient failure
        without risking double-counting.
        """
