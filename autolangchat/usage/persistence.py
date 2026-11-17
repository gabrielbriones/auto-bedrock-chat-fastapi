"""Best-effort persistence of turn usage with a bounded retry queue."""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Deque, Optional

from .ledger import UsageLedger
from .pricing import CostEstimate

logger = logging.getLogger(__name__)

PendingWrite = Callable[[], Awaitable[None]]

DEFAULT_RETRY_QUEUE_SIZE = 500


class UsageWriteQueue:
    """Retry failed idempotent store writes, dropping the oldest when full."""

    def __init__(self, maxlen: int = DEFAULT_RETRY_QUEUE_SIZE) -> None:
        self._pending: Deque[PendingWrite] = deque()
        self._maxlen = maxlen
        self._lock = asyncio.Lock()

    def __len__(self) -> int:
        return len(self._pending)

    async def submit(self, write: PendingWrite, timeout: float) -> bool:
        """Attempt ``write`` (queueing it on failure); after a success, retry queued writes."""
        if not await self._attempt(write, timeout):
            if len(self._pending) >= self._maxlen:
                self._pending.popleft()
                logger.warning("Token-usage retry queue full; dropped the oldest pending write")
            self._pending.append(write)
            return False
        if self._pending and not self._lock.locked():
            async with self._lock:
                while self._pending:
                    pending = self._pending.popleft()
                    if not await self._attempt(pending, timeout):
                        self._pending.appendleft(pending)
                        break
        return True

    @staticmethod
    async def _attempt(write: PendingWrite, timeout: float) -> bool:
        try:
            await asyncio.wait_for(write(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning("Timed out writing token usage; queued for retry")
        except Exception:
            logger.exception("Failed to write token usage; queued for retry")
        return False


_queues: "weakref.WeakKeyDictionary[Any, UsageWriteQueue]" = weakref.WeakKeyDictionary()


def write_queue_for(store: Any) -> UsageWriteQueue:
    queue = _queues.get(store)
    if queue is None:
        queue = UsageWriteQueue()
        _queues[store] = queue
    return queue


async def persist_turn_usage(
    store: Any,
    ledger: UsageLedger,
    *,
    session_id: str,
    user_id: Optional[str],
    model_id: str,
    estimate: Optional[CostEstimate],
    final: bool,
    timeout: float,
) -> bool:
    """Write the ledger's events and upsert its ``token_usage`` row.

    Both writes are idempotent, so a queued retry may safely replay them.
    Returns ``False`` when the write was queued for retry.
    """
    events = ledger.events
    summary = ledger.summary()
    turn_ts = min((event.ts for event in events), default=datetime.now(timezone.utc))

    async def _write() -> None:
        await store.record_turn(
            turn_id=ledger.turn_id,
            session_id=session_id,
            user_id=user_id,
            model_id=model_id,
            input_tokens=summary["input_tokens"],
            output_tokens=summary["output_tokens"],
            turn_ts=turn_ts,
            cache_read_tokens=summary["cache_read"],
            cache_write_tokens=summary["cache_write"],
            estimated_cost_micros=estimate.estimated_cost_micros if estimate else None,
            currency=estimate.currency if estimate else None,
            pricing_revision=estimate.pricing_revision if estimate else None,
            final=final,
        )
        # After the turn row, so a missing events table can't block it.
        await store.record_events(events)

    return await write_queue_for(store).submit(_write, timeout)
