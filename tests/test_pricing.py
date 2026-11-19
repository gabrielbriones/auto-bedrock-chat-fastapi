from autolangchat.usage.ledger import UsageEvent, UsageLedger
from autolangchat.usage.pricing import estimate_cost, summarize_usage


def _event(**overrides):
    values = {
        "turn_id": "turn-1",
        "purpose": "chat",
        "provider": "anthropic",
        "model_id": "model-a",
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "cache_read_tokens": 200_000,
        "cache_write_tokens": 100_000,
    }
    values.update(overrides)
    return UsageEvent(**values)


def test_estimate_cost_prices_uncached_and_cache_tokens_separately():
    table = {
        "pricing_revision": "test-v1",
        "currency": "USD",
        "models": {
            "anthropic:model-a": {
                "input_per_million": 3,
                "output_per_million": 15,
                "cache_read_per_million": 0.3,
                "cache_write_per_million": 3.75,
            }
        },
    }

    estimate = estimate_cost([_event()], table)

    assert estimate is not None
    assert estimate.estimated_cost_micros == 4_035_000
    assert estimate.currency == "USD"
    assert estimate.pricing_revision == "test-v1"


def test_estimate_cost_returns_none_for_unknown_model():
    assert estimate_cost([_event()]) is None


def test_one_hour_cache_writes_use_their_own_rate():
    table = {
        "pricing_revision": "test-v1",
        "currency": "USD",
        "models": {
            "anthropic:model-a": {
                "input_per_million": 3,
                "output_per_million": 15,
                "cache_read_per_million": 0.3,
                "cache_write_per_million": 3.75,
                "cache_write_1h_per_million": 6,
            }
        },
    }

    # 100k writes: 40k at the 1h rate (240_000) and 60k at the 5m rate (225_000).
    estimate = estimate_cost([_event(cache_write_1h_tokens=40_000)], table)

    assert estimate is not None
    assert estimate.estimated_cost_micros == 4_035_000 - 375_000 + 225_000 + 240_000


def test_one_hour_cache_writes_without_a_rate_are_unpriced():
    table = {
        "models": {
            "anthropic:model-a": {
                "input_per_million": 3,
                "output_per_million": 15,
                "cache_read_per_million": 0.3,
                "cache_write_per_million": 3.75,
            }
        },
    }

    assert estimate_cost([_event(cache_write_1h_tokens=1)], table) is None
    assert estimate_cost([_event()], table) is not None


_TIERED = {
    "models": {
        "anthropic:model-a": {
            "input_per_million": 1,
            "output_per_million": 5,
            "cache_read_per_million": 0.1,
            "cache_write_per_million": 1.25,
            "long_context": {
                "above_input_tokens": 100_000,
                "input_per_million": 5,
                "output_per_million": 25,
                "cache_read_per_million": 0.5,
                "cache_write_per_million": 6.25,
            },
        }
    },
}


def test_long_context_rates_apply_per_call_above_the_threshold():
    at_threshold = _event(input_tokens=100_000, output_tokens=1_000, cache_read_tokens=0, cache_write_tokens=0)
    above = _event(input_tokens=100_001, output_tokens=1_000, cache_read_tokens=0, cache_write_tokens=0)

    assert estimate_cost([at_threshold], _TIERED).estimated_cost_micros == 100_000 + 5_000
    assert estimate_cost([above], _TIERED).estimated_cost_micros == 500_005 + 25_000
    assert estimate_cost([at_threshold, above], _TIERED).estimated_cost_micros == 105_000 + 525_005


def test_cached_tokens_count_towards_the_long_context_threshold():
    # 50k uncached + 60k cache read: the prompt is 110k, so every part uses the long-context rates.
    event = _event(input_tokens=110_000, output_tokens=0, cache_read_tokens=60_000, cache_write_tokens=0)

    assert estimate_cost([event], _TIERED).estimated_cost_micros == 250_000 + 30_000


def test_packaged_table_prices_current_anthropic_and_openai_models():
    for provider, model_id in (
        ("anthropic", "us.anthropic.claude-sonnet-5"),
        ("anthropic", "global.anthropic.claude-opus-5-5"),
        ("anthropic", "us.anthropic.claude-haiku-5-5"),
        ("openai", "us.openai.gpt-6.1-sol"),
        ("openai", "openai.gpt-6-luna"),
    ):
        event = _event(provider=provider, model_id=model_id, cache_read_tokens=0, cache_write_tokens=0)
        assert estimate_cost([event]) is not None, model_id


_TABLE = {
    "pricing_revision": "test-v2",
    "currency": "USD",
    "models": {
        "anthropic:primary": {
            "input_per_million": 3,
            "output_per_million": 15,
            "cache_read_per_million": 0.3,
            "cache_write_per_million": 3.75,
        },
        "anthropic:fallback": {
            "input_per_million": 1,
            "output_per_million": 5,
            "cache_read_per_million": 0.1,
            "cache_write_per_million": 1.25,
        },
    },
}


def test_each_event_is_priced_with_its_own_model():
    events = [
        _event(model_id="primary", input_tokens=1_000, output_tokens=100, cache_read_tokens=0, cache_write_tokens=0),
        _event(
            purpose="retry",
            model_id="fallback",
            input_tokens=1_000,
            output_tokens=100,
            cache_read_tokens=0,
            cache_write_tokens=0,
        ),
    ]

    estimate = estimate_cost(events, _TABLE)

    # primary: 1000 * 3 + 100 * 15; fallback: 1000 * 1 + 100 * 5
    assert estimate.estimated_cost_micros == 4_500 + 1_500


def test_one_unknown_model_makes_the_whole_estimate_unknown():
    events = [_event(model_id="primary"), _event(model_id="mystery")]

    assert estimate_cost(events, _TABLE) is None


def test_region_prefixed_model_id_uses_bare_model_rates():
    table = {"models": {"anthropic:anthropic.primary": _TABLE["models"]["anthropic:primary"]}}
    regional = _event(
        model_id="us.anthropic.primary",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )

    assert estimate_cost([regional], table).estimated_cost_micros == 3_000_000


def test_packaged_table_prices_a_known_bedrock_model():
    event = _event(
        model_id="global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )

    estimate = estimate_cost([event])

    assert estimate is not None
    assert estimate.estimated_cost_micros == 3_000_000
    assert estimate.currency == "USD"


def test_summarize_usage_reports_null_cost_for_unknown_models():
    ledger = UsageLedger("turn-1")
    ledger.record(purpose="chat", provider="anthropic", model_id="mystery", input_tokens=10, output_tokens=5)

    summary, estimate = summarize_usage(ledger, final=False, pricing_table=_TABLE)

    assert estimate is None
    assert summary == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read": 0,
        "cache_write": 0,
        "by_purpose": {"chat": {"input_tokens": 10, "output_tokens": 5}},
        "estimated_cost": None,
        "currency": None,
        "final": False,
    }


def test_summarize_usage_reports_cost_in_currency_units():
    ledger = UsageLedger("turn-1")
    ledger.record(purpose="chat", provider="anthropic", model_id="primary", input_tokens=1_000, output_tokens=100)

    summary, estimate = summarize_usage(ledger, final=True, pricing_table=_TABLE)

    assert estimate.estimated_cost_micros == 4_500
    assert summary["estimated_cost"] == 0.0045
    assert summary["currency"] == "USD"
    assert summary["final"] is True
