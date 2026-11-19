"""Thread-safe per-turn usage event collection."""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from ..context_budget import provider_for_model
from ..model_capabilities import get_model_profile

UsagePurpose = Literal["chat", "summarization", "retry", "title", "embedding"]


def _token_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def cache_token_counts(usage_metadata: Any) -> Dict[str, int]:
    """Prompt-cache token counts from ``usage_metadata["input_token_details"]``.

    langchain-aws already folds Bedrock's ``cacheReadInputTokens`` and
    ``cacheWriteInputTokens`` into ``input_tokens``, so these are subsets of
    it, not additions. Cache writes are reported either as ``cache_creation``
    or, when Bedrock breaks them down by TTL, as ``ephemeral_5m/1h_input_tokens``
    (with ``cache_creation`` then zeroed), so all three are summed;
    ``cache_write_1h_tokens`` keeps the one-hour subset, which is priced
    differently. Returns ``{}`` when the provider reported no input-token
    details.
    """
    details = usage_metadata.get("input_token_details") if isinstance(usage_metadata, dict) else None
    if not isinstance(details, dict) or not details:
        return {}
    return {
        "cache_read_tokens": _token_count(details.get("cache_read")),
        "cache_write_tokens": _token_count(details.get("cache_creation"))
        + _token_count(details.get("ephemeral_5m_input_tokens"))
        + _token_count(details.get("ephemeral_1h_input_tokens")),
        "cache_write_1h_tokens": _token_count(details.get("ephemeral_1h_input_tokens")),
    }


@dataclass(frozen=True)
class UsageEvent:
    """Usage attributed to one provider invocation."""

    turn_id: str
    purpose: UsagePurpose
    provider: str
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Subset of cache_write_tokens written with the one-hour TTL.
    cache_write_1h_tokens: int = 0
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if not self.turn_id:
            raise ValueError("turn_id must not be empty")
        if self.purpose not in {"chat", "summarization", "retry", "title", "embedding"}:
            raise ValueError(f"unsupported usage purpose: {self.purpose}")
        for field_name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "cache_write_1h_tokens",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.cache_write_1h_tokens > self.cache_write_tokens:
            raise ValueError("cache_write_1h_tokens must not exceed cache_write_tokens")

    def to_dict(self) -> Dict[str, object]:
        result = asdict(self)
        result["ts"] = self.ts.isoformat()
        return result


class UsageLedger:
    """Mutable collection of events belonging to one chat turn."""

    def __init__(self, turn_id: Optional[str] = None) -> None:
        self.turn_id = turn_id or str(uuid.uuid4())
        self._events: List[UsageEvent] = []
        self._lock = threading.Lock()

    def record(
        self,
        *,
        purpose: UsagePurpose,
        provider: str,
        model_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_write_1h_tokens: int = 0,
    ) -> UsageEvent:
        event = UsageEvent(
            turn_id=self.turn_id,
            purpose=purpose,
            provider=provider,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_write_1h_tokens=cache_write_1h_tokens,
        )
        with self._lock:
            self._events.append(event)
        return event

    def record_usage_metadata(
        self,
        *,
        purpose: UsagePurpose,
        model_id: str,
        usage_metadata: Any,
    ) -> Optional[UsageEvent]:
        """Record a LangChain ``AIMessage.usage_metadata``; ``None`` if it is empty."""
        if not isinstance(usage_metadata, dict) or not usage_metadata:
            return None
        cache = cache_token_counts(usage_metadata)
        return self.record(
            purpose=purpose,
            provider=provider_for_model(model_id),
            model_id=model_id,
            input_tokens=_token_count(usage_metadata.get("input_tokens")),
            output_tokens=_token_count(usage_metadata.get("output_tokens")),
            cache_read_tokens=cache.get("cache_read_tokens", 0),
            cache_write_tokens=cache.get("cache_write_tokens", 0),
            cache_write_1h_tokens=cache.get("cache_write_1h_tokens", 0),
        )

    @property
    def events(self) -> List[UsageEvent]:
        with self._lock:
            return list(self._events)

    def summary(self) -> Dict[str, object]:
        events = self.events
        return {
            "input_tokens": sum(event.input_tokens for event in events),
            "output_tokens": sum(event.output_tokens for event in events),
            "cache_read": sum(event.cache_read_tokens for event in events),
            "cache_write": sum(event.cache_write_tokens for event in events),
        }

    def purpose_summary(self) -> Dict[str, Dict[str, int]]:
        """Input/output totals per purpose, for the purposes that made a call."""
        totals: Dict[str, Dict[str, int]] = {}
        for event in self.events:
            purpose = totals.setdefault(event.purpose, {"input_tokens": 0, "output_tokens": 0})
            purpose["input_tokens"] += event.input_tokens
            purpose["output_tokens"] += event.output_tokens
        return totals

    def context_fill(self) -> Optional[Dict[str, int]]:
        """How full the conversation leaves its model's input window, or ``None`` when unknown.

        The latest ``chat``/``retry`` call's prompt plus its answer is what the next turn starts from;
        ``None`` without such a call or when the model's ``max_input_tokens`` is not known.
        """
        calls = [event for event in self.events if event.purpose in ("chat", "retry")]
        if not calls:
            return None
        latest = calls[-1]
        window = get_model_profile(latest.model_id).get("max_input_tokens")
        if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
            return None
        return {"used_tokens": latest.input_tokens + latest.output_tokens, "max_tokens": window}
