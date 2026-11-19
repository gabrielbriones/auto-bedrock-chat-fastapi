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
    assert ledger.purpose_summary() == {
        "chat": {"input_tokens": 100, "output_tokens": 20},
        "summarization": {"input_tokens": 50, "output_tokens": 10},
    }


def test_purpose_summary_sums_repeated_purposes_and_omits_unused_ones():
    ledger = UsageLedger("turn-1")
    ledger.record(purpose="chat", provider="anthropic", model_id="model-a", input_tokens=100, output_tokens=20)
    ledger.record(purpose="chat", provider="anthropic", model_id="model-a", input_tokens=40, output_tokens=5)

    assert ledger.purpose_summary() == {"chat": {"input_tokens": 140, "output_tokens": 25}}
    assert UsageLedger("turn-2").purpose_summary() == {}


def test_context_fill_is_the_latest_chat_call_against_the_model_window():
    ledger = UsageLedger("turn-1")
    model = "us.anthropic.claude-sonnet-5"
    ledger.record(purpose="chat", provider="anthropic", model_id=model, input_tokens=1_000, output_tokens=50)
    ledger.record(purpose="chat", provider="anthropic", model_id=model, input_tokens=1_400, output_tokens=200)
    ledger.record(purpose="title", provider="anthropic", model_id=model, input_tokens=90, output_tokens=8)

    fill = ledger.context_fill()

    assert fill is not None
    assert fill["used_tokens"] == 1_600
    assert fill["max_tokens"] > fill["used_tokens"]


def test_context_fill_is_unknown_without_a_chat_call_or_a_known_window():
    no_chat = UsageLedger("turn-1")
    no_chat.record(purpose="title", provider="anthropic", model_id="us.anthropic.claude-sonnet-5", input_tokens=9)
    unknown_model = UsageLedger("turn-2")
    unknown_model.record(purpose="chat", provider="mystery", model_id="mystery.model", input_tokens=9)

    assert no_chat.context_fill() is None
    assert unknown_model.context_fill() is None


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
    assert event.cache_write_1h_tokens == 0


def test_record_usage_metadata_keeps_the_one_hour_cache_write_subset():
    ledger = UsageLedger("turn-1")

    event = ledger.record_usage_metadata(
        purpose="chat",
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        usage_metadata={
            "input_tokens": 500,
            "output_tokens": 12,
            "input_token_details": {"ephemeral_5m_input_tokens": 50, "ephemeral_1h_input_tokens": 70},
        },
    )

    assert event is not None
    assert (event.cache_write_tokens, event.cache_write_1h_tokens) == (120, 70)


def test_record_usage_metadata_ignores_missing_usage():
    ledger = UsageLedger("turn-1")

    assert ledger.record_usage_metadata(purpose="chat", model_id="m", usage_metadata=None) is None
    assert ledger.record_usage_metadata(purpose="chat", model_id="m", usage_metadata={}) is None
    assert ledger.events == []


def test_ledger_generates_a_turn_id_when_none_given():
    assert UsageLedger().turn_id != UsageLedger().turn_id
