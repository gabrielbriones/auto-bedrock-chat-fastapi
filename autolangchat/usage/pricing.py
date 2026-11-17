"""Versioned per-model token pricing and cost estimation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from importlib import resources
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from ..config import split_model_id
from .ledger import UsageEvent, UsageLedger

_PRICING_RESOURCE = ("autolangchat.usage", "pricing.json")
# Rates are per million tokens, so tokens * rate is already in micros.
_MICROS_PER_MILLION_TOKENS = Decimal(1)


@dataclass(frozen=True)
class CostEstimate:
    """Estimated cost in micros of the configured currency unit."""

    estimated_cost_micros: int
    currency: str
    pricing_revision: str

    @property
    def estimated_cost(self) -> float:
        return self.estimated_cost_micros / 1_000_000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "estimated_cost": self.estimated_cost,
            "estimated_cost_micros": self.estimated_cost_micros,
            "currency": self.currency,
            "pricing_revision": self.pricing_revision,
        }


def load_pricing_table(pricing_table: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Load packaged prices, or use a runtime-provided table verbatim."""
    if pricing_table is not None:
        return dict(pricing_table)
    return _packaged_pricing_table()


@lru_cache(maxsize=1)
def _packaged_pricing_table() -> Dict[str, Any]:
    return json.loads(resources.files(_PRICING_RESOURCE[0]).joinpath(_PRICING_RESOURCE[1]).read_text("utf-8"))


def estimate_cost(
    events: Iterable[UsageEvent],
    pricing_table: Optional[Mapping[str, Any]] = None,
) -> Optional[CostEstimate]:
    """Estimate event cost; return ``None`` if any provider/model is unpriced.

    Cached token counts are subsets of input tokens, so they are priced at
    their cache rates and removed from the uncached input quantity.
    """
    table = load_pricing_table(pricing_table)
    revision = str(table.get("pricing_revision", "custom"))
    models = table.get("models", {})
    total_micros = Decimal(0)
    currencies = set()

    for event in events:
        model = _lookup_model(models, event.provider, event.model_id)
        if model is None:
            return None
        currency = str(model.get("currency", table.get("currency", "USD")))
        currencies.add(currency)
        if len(currencies) != 1:
            return None

        input_rate = _rate(model, "input")
        output_rate = _rate(model, "output")
        cache_read_rate = _rate(model, "cache_read")
        cache_write_rate = _rate(model, "cache_write")
        if None in (input_rate, output_rate, cache_read_rate, cache_write_rate):
            return None
        uncached_input = event.input_tokens - event.cache_read_tokens - event.cache_write_tokens
        if uncached_input < 0:
            return None
        total_micros += (
            Decimal(uncached_input) * input_rate
            + Decimal(event.output_tokens) * output_rate
            + Decimal(event.cache_read_tokens) * cache_read_rate
            + Decimal(event.cache_write_tokens) * cache_write_rate
        ) * _MICROS_PER_MILLION_TOKENS

    return CostEstimate(
        estimated_cost_micros=int(total_micros.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        currency=next(iter(currencies), str(table.get("currency", "USD"))),
        pricing_revision=revision,
    )


def summarize_usage(
    ledger: UsageLedger,
    *,
    final: bool,
    pricing_table: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], Optional[CostEstimate]]:
    """Client-facing ``usage`` summary for a turn plus its cost estimate."""
    estimate = estimate_cost(ledger.events, pricing_table)
    summary: Dict[str, Any] = {
        **ledger.summary(),
        "estimated_cost": estimate.estimated_cost if estimate else None,
        "currency": estimate.currency if estimate else None,
        "final": final,
    }
    return summary, estimate


def _lookup_model(models: Mapping[str, Any], provider: str, model_id: str) -> Optional[Mapping[str, Any]]:
    """Exact ``provider:model_id`` entry, else the entry for the id without its region prefix."""
    model = models.get(f"{provider}:{model_id}")
    if model is None:
        region_prefix, _ = split_model_id(model_id)
        if region_prefix is not None:
            model = models.get(f"{provider}:{model_id[len(region_prefix) + 1:]}")
    return model if isinstance(model, Mapping) else None


def _rate(model: Mapping[str, Any], category: str) -> Optional[Decimal]:
    value = model.get(f"{category}_per_million")
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    return result if result >= 0 else None
