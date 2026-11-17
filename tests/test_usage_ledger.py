from autolangchat.usage.ledger import UsageLedger


def test_ledger_keeps_each_invocation_and_sums_usage():
    ledger = UsageLedger("turn-1")
    ledger.record(purpose="chat", provider="anthropic", model_id="model-a", input_tokens=100, output_tokens=20)
    ledger.record(
        purpose="summarization",
        provider="anthropic",
        model_id="model-a",
        input_tokens=50,
        output_tokens=10,
        cache_read_tokens=15,
    )

    assert len(ledger.events) == 2
    assert ledger.events[0].event_id != ledger.events[1].event_id
    assert all(event.turn_id == "turn-1" for event in ledger.events)
    assert ledger.summary() == {
        "input_tokens": 150,
        "output_tokens": 30,
        "cache_read": 15,
        "cache_write": 0,
    }


def test_ledger_rejects_negative_counts():
    ledger = UsageLedger("turn-1")

    try:
        ledger.record(purpose="chat", provider="anthropic", model_id="model-a", input_tokens=-1)
    except ValueError as exc:
        assert "input_tokens" in str(exc)
    else:
        raise AssertionError("negative token count should be rejected")


def test_record_usage_metadata_extracts_tokens_and_cache_subsets():
    ledger = UsageLedger("turn-1")

    event = ledger.record_usage_metadata(
        purpose="title",
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        usage_metadata={
            "input_tokens": 500,
            "output_tokens": 12,
            "input_token_details": {"cache_read": 300, "cache_creation": 0, "ephemeral_5m_input_tokens": 50},
        },
    )

    assert event is not None
    assert (event.purpose, event.provider, event.model_id) == (
        "title",
        "anthropic",
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )
    assert (event.input_tokens, event.output_tokens, event.cache_read_tokens, event.cache_write_tokens) == (
        500,
        12,
        300,
        50,
    )


def test_record_usage_metadata_ignores_missing_usage():
    ledger = UsageLedger("turn-1")

    assert ledger.record_usage_metadata(purpose="chat", model_id="m", usage_metadata=None) is None
    assert ledger.record_usage_metadata(purpose="chat", model_id="m", usage_metadata={}) is None
    assert ledger.events == []


def test_ledger_generates_a_turn_id_when_none_given():
    assert UsageLedger().turn_id != UsageLedger().turn_id
