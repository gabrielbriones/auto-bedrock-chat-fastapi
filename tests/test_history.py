"""Unit tests for the shared checkpoint -> client history projection."""

from autolangchat.defaults import ARTIFACT_MARKER_ENTRY_KEY, ARTIFACT_MARKER_ORIGINAL_KEY
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


def _spilled_user_message(**extra):
    marker = {"artifact_handle": "h1", "source": "user_message", "size_bytes": 12_345, "preview": "JOB_ID = 1"}
    return {"role": "user", "content": str(marker), ARTIFACT_MARKER_ENTRY_KEY: True, **extra}


def test_spilled_user_message_shows_what_was_sent_not_the_marker():
    (only,) = format_history_messages([_spilled_user_message(**{ARTIFACT_MARKER_ORIGINAL_KEY: "JOB_ID = 1\nfull"})])

    assert only["content"] == "JOB_ID = 1\nfull"
    assert ARTIFACT_MARKER_ORIGINAL_KEY not in only


def test_spilled_user_message_without_the_original_shows_the_preview():
    (only,) = format_history_messages([_spilled_user_message()])

    assert only["content"].startswith("JOB_ID = 1\n\n")
    assert "12,345 bytes" in only["content"] and "artifact_handle" not in only["content"]


def test_unparseable_marker_falls_back_to_the_stored_content():
    message = {"role": "user", "content": "not a marker", ARTIFACT_MARKER_ENTRY_KEY: True}

    (only,) = format_history_messages([message])

    assert only["content"] == "not a marker"


def test_direct_skill_load_keeps_the_load_skill_round_and_its_flags():
    """A slash command's skill load (XMGPLAT-11862) shows up like a model-initiated one, flagged ``direct``."""
    entry = {
        "tool_call_id": "direct_1",
        "name": "load_skill",
        "result": "Skill: triage (revision 2)",
        "metadata": {"skill_ref": {"name": "triage", "revision": "2"}, "direct": True},
    }
    call = {"name": "load_skill", "args": {"name": "triage", "params": {}}, "id": "direct_1", "type": "tool_call"}
    history = format_history_messages(
        [
            {"role": "user", "content": "/triage", "metadata": {"message_id": "u1"}},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [call],
                "metadata": {"message_id": "d1", "direct": True},
            },
            {"role": "tool", "content": '["Skill: ..."]', "tool_results": [entry], "metadata": {"direct": True}},
            {"role": "assistant", "content": "Done.", "metadata": {"message_id": "a1"}},
        ]
    )

    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]
    assert history[0]["content"] == "/triage"
    assert history[1]["tool_calls"] == [call] and history[1]["metadata"]["direct"] is True
    assert history[2]["content"] == "" and history[2]["tool_results"] == [entry]
