"""Unit tests for the shared checkpoint -> client history projection."""

from autolangchat.history import format_history_messages


def test_drops_internal_system_prompt():
    history = format_history_messages(
        [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "hi", "metadata": {"message_id": "u1", "timestamp": "2026-01-01T00:00:00"}},
            {"role": "assistant", "content": "hello", "metadata": {"message_id": "a1"}},
        ]
    )

    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0] == {
        "message_id": "u1",
        "role": "user",
        "content": "hi",
        "timestamp": "2026-01-01T00:00:00",
        "tool_calls": [],
        "tool_results": [],
        "metadata": {"message_id": "u1", "timestamp": "2026-01-01T00:00:00"},
    }


def test_tool_messages_keep_structured_results_but_not_the_duplicate_json_blob():
    results = [{"tool_call_id": "c1", "name": "get_jobs", "result": {"count": 2}}]
    history = format_history_messages(
        [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "get_jobs", "args": {}}]},
            {"role": "tool", "content": '[{"count": 2}]', "tool_results": results, "metadata": {"timestamp": "t"}},
            {"role": "assistant", "content": "Two jobs.", "metadata": {"message_id": "a1"}},
        ]
    )

    assert [m["role"] for m in history] == ["assistant", "tool", "assistant"]
    assert history[1]["content"] == ""
    assert history[1]["tool_results"] == results
    assert history[0]["tool_calls"] == [{"id": "c1", "name": "get_jobs", "args": {}}]


def test_messages_without_metadata_get_null_id_and_timestamp():
    (only,) = format_history_messages([{"role": "user", "content": "legacy"}])

    assert only["message_id"] is None
    assert only["timestamp"] is None
    assert only["metadata"] == {}
