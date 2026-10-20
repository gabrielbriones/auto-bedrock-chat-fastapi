import logging
from unittest.mock import patch

import pytest

from autolangchat.context_budget import estimate_message_tokens
from autolangchat.graph.nodes.llm_call import _log_conversation


def test_logs_tool_calls_and_results_only_for_their_message_roles(caplog):
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "search"}],
            "tool_results": [{"name": "ignored-assistant-result"}],
            "metadata": {"usage": {"input_tokens": 12, "output_tokens": 34}},
        },
        {
            "role": "tool",
            "content": "done",
            "tool_calls": [{"id": "ignored-tool-call", "name": "search"}],
            "tool_results": [{"name": "search", "tool_call_id": "call-1"}],
        },
        {
            "role": "user",
            "content": "hello",
            "tool_calls": [{"id": "ignored-user-call", "name": "search"}],
            "tool_results": [{"name": "ignored-user-result"}],
        },
    ]

    with caplog.at_level(logging.DEBUG, logger="autolangchat.graph.nodes.llm_call"):
        _log_conversation(messages, "test")

    assert "tool_calls=1" in caplog.text
    assert "input_tokens=12" in caplog.text
    assert "output_tokens=34" in caplog.text
    assert "tool_results=1" in caplog.text
    assert "tool_call[0] id=call-1 name=search" in caplog.text
    assert "tool_result[0] name=search tool_call_id=call-1" in caplog.text
    assert "ignored-assistant-result" not in caplog.text
    assert "ignored-tool-call" not in caplog.text
    assert "ignored-user-call" not in caplog.text
    assert "ignored-user-result" not in caplog.text


@pytest.mark.parametrize("provider", [None, "anthropic", "meta"])
def test_logs_estimates_for_full_user_and_tool_payloads(caplog, provider):
    messages = [
        {"role": "user", "content": "Explain this result. " * 100},
        {
            "role": "tool",
            "content": "",
            "tool_results": [
                {"name": "search", "tool_call_id": "call-1", "result": '{"value": 42}' * 100},
                {"name": "search", "tool_call_id": "call-2", "error": "not found"},
            ],
        },
    ]

    with caplog.at_level(logging.DEBUG, logger="autolangchat.graph.nodes.llm_call"):
        _log_conversation(messages, "test", provider=provider)

    assert (
        f"user ({len(messages[0]['content']):,} chars, "
        f"estimated_tokens={estimate_message_tokens(messages[0], provider=provider)})"
    ) in caplog.text
    assert (
        f"tool (0 chars, estimated_tokens={estimate_message_tokens(messages[1], provider=provider)}, " "tool_results=2)"
    ) in caplog.text


def test_does_not_estimate_tokens_when_debug_logging_is_disabled(caplog):
    with caplog.at_level(logging.INFO, logger="autolangchat.graph.nodes.llm_call"):
        with patch("autolangchat.graph.nodes.llm_call.estimate_message_tokens") as estimate:
            _log_conversation([{"role": "user", "content": "hello"}], "test")

    estimate.assert_not_called()
