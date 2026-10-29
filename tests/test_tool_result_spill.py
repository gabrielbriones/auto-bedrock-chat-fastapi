"""Unit tests for replacing oversized tool results and user messages with artifact markers (XMGPLAT-11846).

The preprocessing node estimates every message once, part by part, and passes the estimates of an over-threshold
message to the single ``replace_oversized_message``; these tests make the estimates the same way.
"""

import ast
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from autolangchat.artifacts import ArtifactError, ArtifactStore
from autolangchat.context_budget import ContextBudget, estimate_message_parts, provider_for_model
from autolangchat.graph.tools import artifact_tools
from autolangchat.graph.tools.spill import MARKER_ENTRY_KEY, READ_HINT, replace_oversized_message

CLAUDE = "us.anthropic.claude-sonnet-5"
QWEN = "qwen.qwen3-32b-v1:0"


def _config(model_id=QWEN, **overrides):
    values = {
        "model_id": model_id,
        "max_tokens": 1000,
        "langchain_tools": None,
        "artifact_enabled": True,
        "context_budget_safety_fraction": 0.10,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _store_and_scope(**store_kwargs):
    store = ArtifactStore(**store_kwargs)
    store.register_connection("conn", "user")
    return store, store.activate("conn", "thread", "user")


def _configurable(store, scope):
    return {"artifact_store": store, "artifact_scope": scope}


def _entry(call_id="c1", name="get_report", result=None, **extra):
    return {"tool_call_id": call_id, "name": name, "result": result, **extra}


def _big_json(rows=2500):
    return {"rows": [{"id": i, "name": f"item-{i}", "value": i * 3} for i in range(rows)]}


def _tool_message(entries):
    return {
        "role": "tool",
        "content": json.dumps([e.get("result", e.get("error")) for e in entries]),
        "tool_results": entries,
        "metadata": {"timestamp": "t"},
    }


def _costs(entries, config=None):
    """The per-result estimates the preprocessing node makes for a tool message."""
    provider = ContextBudget.for_turn(config or _config()).provider
    return [part.tokens for part in estimate_message_parts(_tool_message(entries), provider)]


def _spill(entries, config=None, configurable=None, exempt_tools=(), threshold=None):
    config = config or _config()
    configurable = configurable or {}
    budget = ContextBudget.for_turn(config)
    if threshold is None:
        threshold = budget.max_input_tokens * config.context_budget_safety_fraction
    return replace_oversized_message(
        _tool_message(entries),
        _costs(entries, config),
        threshold,
        message_index=2,
        model_id=budget.model_id,
        chat_config=config,
        artifact_store=configurable.get("artifact_store"),
        artifact_scope=configurable.get("artifact_scope"),
        exempt_tools=exempt_tools,
    )


def _results(outcome):
    return outcome.message["tool_results"]


class TestSpillDecision:
    def test_small_result_untouched(self):
        store, scope = _store_and_scope()
        entries = [_entry(result={"ok": True})]
        outcome = _spill(entries, configurable=_configurable(store, scope))
        assert _results(outcome) == entries and not outcome.changed
        assert outcome.spilled == 0 and outcome.truncated == 0
        assert store.statistics()["puts"] == 0

    def test_estimates_that_do_not_match_the_results_leave_the_message_alone(self):
        message = _tool_message([_entry(result=_big_json())])
        outcome = replace_oversized_message(message, [], 1, message_index=2, model_id=QWEN, chat_config=_config())
        assert outcome.message is message and not outcome.changed

    def test_small_cap_model_replaces_what_large_cap_model_keeps(self):
        payload = _big_json()
        store, scope = _store_and_scope()
        small = _spill([_entry(result=payload)], _config(QWEN), _configurable(store, scope))
        large = _spill([_entry(result=payload)], _config(CLAUDE), _configurable(store, scope))
        assert small.spilled == 1
        assert large.spilled == 0 and _results(large)[0]["result"] == payload

    def test_input_not_mutated(self):
        store, scope = _store_and_scope()
        message = _tool_message([_entry(result=_big_json())])
        snapshot = copy.deepcopy(message)
        outcome = replace_oversized_message(
            message,
            _costs(message["tool_results"]),
            3276.8,
            message_index=2,
            model_id=QWEN,
            chat_config=_config(),
            artifact_store=store,
            artifact_scope=scope,
        )
        assert outcome.changed and outcome.message is not message
        assert message == snapshot

    def test_content_mirror_is_rebuilt_from_the_new_results(self):
        store, scope = _store_and_scope()
        entries = [
            _entry("1", "small", {"ok": 1}),
            _entry("2", "big", _big_json()),
            {"tool_call_id": "3", "name": "bad", "error": "boom"},
        ]
        message = _spill(entries, configurable=_configurable(store, scope)).message
        mirror = json.loads(message["content"])
        assert mirror[0] == {"ok": 1} and mirror[2] == "boom"
        assert mirror[1] == message["tool_results"][1]["result"]  # the marker, not the original
        assert len(message["content"]) < 5000
        assert message["metadata"] == {"timestamp": "t"} and message["role"] == "tool"

    def test_an_entry_that_is_not_a_dict_is_kept_beside_a_replaced_result(self):
        store, scope = _store_and_scope()
        entries = ["stray text", _entry("2", "big", _big_json())]
        message = {"role": "tool", "content": "[]", "tool_results": entries}
        provider = ContextBudget.for_turn(_config()).provider

        outcome = replace_oversized_message(
            message,
            [part.tokens for part in estimate_message_parts(message, provider)],
            3276.8,
            message_index=2,
            model_id=QWEN,
            chat_config=_config(),
            artifact_store=store,
            artifact_scope=scope,
        )

        assert outcome.spilled == 1
        stray, replaced = outcome.message["tool_results"]
        assert stray == "stray text" and replaced[MARKER_ENTRY_KEY] is True
        assert json.loads(outcome.message["content"]) == ["stray text", replaced["result"]]

    def test_message_over_threshold_replaces_the_largest_results_first(self, caplog):
        store, scope = _store_and_scope()
        sizes = [30_000, 42_000, 36_000, 24_000]
        entries = [_entry(call_id=f"c{i}", result={"data": "x" * s}) for i, s in enumerate(sizes)]
        costs = _costs(entries)
        # No result exceeds the threshold on its own, but the message does.
        threshold = 0.55 * sum(costs)
        assert max(costs) < threshold < sum(costs)

        with caplog.at_level("DEBUG", logger="autolangchat"):
            outcome = _spill(entries, configurable=_configurable(store, scope), threshold=threshold)

        assert [bool(r.get(MARKER_ENTRY_KEY)) for r in _results(outcome)] == [False, True, True, False]
        assert outcome.spilled == 2
        assert sum(_costs(_results(outcome))) <= threshold
        spills = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Spilled tool result")]
        assert len(spills) == 2 and all("reason=" not in m for m in spills)

    def test_a_result_over_the_threshold_logs_tokens_and_threshold_without_a_reason(self, caplog):
        store, scope = _store_and_scope()
        with caplog.at_level("INFO", logger="autolangchat"):
            _spill([_entry(name="big", result=_big_json())], configurable=_configurable(store, scope))
        [spilled] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Spilled tool result")]
        assert "bytes (~" in spilled and spilled.endswith("threshold 3277)")
        assert "reason=" not in spilled

    def test_the_message_total_is_left_alone_when_replacing_could_not_bring_it_under(self):
        store, scope = _store_and_scope()
        page = {"text": "y" * 100_000, "has_more": True}
        small = {"data": "z" * 20_000}
        entries = [_entry("1", "read_content_range", page), _entry("2", "get", small)]
        exempt_cost, small_cost = _costs(entries)
        threshold = 0.9 * exempt_cost  # the internal tool result alone exceeds it
        assert small_cost < threshold

        outcome = _spill(
            entries, configurable=_configurable(store, scope), exempt_tools={"read_content_range"}, threshold=threshold
        )
        assert _results(outcome) == entries and not outcome.changed
        assert store.statistics()["puts"] == 0


class TestMarkerShape:
    def test_marker_fields_and_preserved_identity(self):
        store, scope = _store_and_scope()
        payload = _big_json()
        outcome = _spill(
            [_entry(call_id="abc", name="get_report", result=payload, status_code=200)],
            configurable=_configurable(store, scope),
        )
        entry = _results(outcome)[0]
        marker = entry["result"]
        assert (entry["tool_call_id"], entry["name"], entry["status_code"]) == ("abc", "get_report", 200)
        assert entry[MARKER_ENTRY_KEY] is True
        assert marker["source"] == "tool_response"
        assert marker["tool_name"] == "get_report"
        assert marker["content_type"] == "application/json"
        assert marker["size_bytes"] > 10_000
        assert "line_count" not in marker  # the read tools report total_lines
        assert marker["lifetime"]
        assert "read_hint" not in marker
        assert marker["message"] == "Large content stored as a session artifact; only a preview is shown below."
        # The preview is the first characters as they are, not lines split and joined again.
        assert marker["preview"] == json.dumps(payload, ensure_ascii=False, indent=2)[:1000]
        stored = store.read_region(scope, marker["artifact_handle"], start_line=1, max_lines=5)
        assert stored["total_lines"] > 1000
        assert stored["metadata"]["source"] == "tool_response"
        assert str(payload["rows"][0]["name"]) in stored["text"]

    def test_read_hint_only_when_read_tools_registered(self):
        store, scope = _store_and_scope()
        tools = [SimpleNamespace(name="read_content_range"), SimpleNamespace(name="grep_content")]
        with_tools = _results(
            _spill([_entry(result=_big_json())], _config(langchain_tools=tools), _configurable(store, scope))
        )[0]["result"]
        only_one = _results(
            _spill([_entry(result=_big_json())], _config(langchain_tools=tools[:1]), _configurable(store, scope))
        )[0]["result"]
        no_tools = _results(_spill([_entry(result=_big_json())], _config(), _configurable(store, scope)))[0]["result"]
        disabled = _results(
            _spill(
                [_entry(result=_big_json())],
                _config(langchain_tools=tools, artifact_enabled=False),
                _configurable(store, scope),
            )
        )[0]["result"]
        assert with_tools["read_hint"] == READ_HINT
        assert "read_hint" not in no_tools
        assert "read_hint" not in disabled
        assert "read_hint" in only_one  # same check as llm_call: only read_content_range has to be bound

    def test_no_read_hint_for_model_without_tool_calling(self):
        store, scope = _store_and_scope()
        tools = [SimpleNamespace(name="read_content_range"), SimpleNamespace(name="grep_content")]
        with patch.object(artifact_tools, "supports_tool_calling", return_value=False):
            marker = _results(
                _spill([_entry(result=_big_json())], _config(langchain_tools=tools), _configurable(store, scope))
            )[0]["result"]
        assert "artifact_handle" in marker and "read_hint" not in marker

    def test_text_unicode_and_non_json_payloads(self):
        store, scope = _store_and_scope()
        text = "línea ñandú ✓ 日本語\n" * 4000
        outcome = _spill([_entry(result=text)], configurable=_configurable(store, scope))
        marker = _results(outcome)[0]["result"]
        assert marker["content_type"] == "text/plain"
        assert store.get_meta(scope, marker["artifact_handle"])["total_lines"] == 4000
        assert marker["size_bytes"] == len(text.encode("utf-8"))
        assert store.read_region(scope, marker["artifact_handle"], max_lines=1)["text"] == "línea ñandú ✓ 日本語\n"

    def test_single_giant_line_preview_is_bounded(self):
        store, scope = _store_and_scope()
        outcome = _spill([_entry(result="z" * 200_000)], configurable=_configurable(store, scope))
        assert _results(outcome)[0]["result"]["preview"] == "z" * 1000

    def test_preview_keeps_the_text_exactly_as_it_is(self):
        store, scope = _store_and_scope()
        text = "line one\r\nline two\r\n\tindented line\r\n" * 400  # CRLF endings, tabs, a cut in the middle of a line

        preview = _results(_spill([_entry(result=text)], configurable=_configurable(store, scope)))[0]["result"][
            "preview"
        ]

        assert preview == text[:1000] and "\r\n" in preview


class TestNeverSpilled:
    def test_errors_and_http_errors_are_untouched(self):
        store, scope = _store_and_scope()
        big = "e" * 200_000
        entries = [
            {"tool_call_id": "1", "name": "a", "error": big},
            {"tool_call_id": "2", "name": "b", "result": {"error": big, "details": big}, "status_code": 401},
            {"tool_call_id": "3", "name": "c", "result": big, "status_code": 500},
        ]
        outcome = _spill(entries, configurable=_configurable(store, scope))
        assert _results(outcome) == entries
        assert store.statistics()["puts"] == 0

    def test_existing_marker_is_not_respilled(self):
        store, scope = _store_and_scope()
        first = _spill([_entry(result=_big_json())], configurable=_configurable(store, scope))
        second = _spill(_results(first), configurable=_configurable(store, scope))
        assert _results(second) == _results(first) and second.spilled == 0

    def test_mixed_message_only_spills_oversized_success(self):
        store, scope = _store_and_scope()
        entries = [
            _entry("1", "small", {"ok": 1}),
            _entry("2", "big", _big_json()),
            {"tool_call_id": "3", "name": "bad", "error": "boom"},
        ]
        outcome = _spill(entries, configurable=_configurable(store, scope))
        results = _results(outcome)
        assert [bool(r.get(MARKER_ENTRY_KEY)) for r in results] == [False, True, False]
        assert results[0] == entries[0] and results[2] == entries[2]


class TestFlaggedPreviewFallback:
    @pytest.mark.parametrize("case", ["no_store", "partial", "quota"])
    def test_unarchivable_results_get_flagged_preview(self, case):
        payload = _big_json()
        config = _config()
        if case == "no_store":
            configurable = {}
        elif case == "partial":
            store, _ = _store_and_scope()
            configurable = {"artifact_store": store}  # scope missing
        else:
            store, scope = _store_and_scope(max_bytes_per_session=1024)
            configurable = _configurable(store, scope)

        outcome = _spill([_entry(result=payload)], config, configurable)
        entry = _results(outcome)[0]
        assert outcome.truncated == 1 and outcome.spilled == 0
        assert entry["result"]["truncated"] is True
        assert "artifact_handle" not in entry["result"]
        assert MARKER_ENTRY_KEY not in entry
        assert entry["tool_call_id"] == "c1"
        assert len(str(entry["result"])) < 5000

    def test_revoked_scope_gets_flagged_preview(self):
        store, scope = _store_and_scope()
        store.deactivate("conn")
        outcome = _spill([_entry(result=_big_json())], configurable=_configurable(store, scope))
        assert _results(outcome)[0]["result"]["truncated"] is True


class TestExemptTools:
    def test_exempt_tool_results_are_never_spilled(self):
        store, scope = _store_and_scope()
        page = {"text": "x" * 60_000, "has_more": True}
        entries = [_entry("1", "read_content_range", page), _entry("2", "grep_content", page)]
        outcome = _spill(
            entries, configurable=_configurable(store, scope), exempt_tools={"read_content_range", "grep_content"}
        )
        assert _results(outcome) == entries and not outcome.changed
        assert store.statistics()["puts"] == 0

    def test_exempt_results_still_count_toward_the_message_total(self):
        store, scope = _store_and_scope()
        exempt_page = {"text": "y" * 40_000}
        other = {"data": "z" * 40_000}
        entries = [_entry("1", "read_content_range", exempt_page), _entry("2", "get", other)]
        exempt_cost, other_cost = _costs(entries)
        # Neither result exceeds the threshold alone; together they do, and the exempt one fits within it.
        threshold = 1.5 * max(exempt_cost, other_cost)
        assert max(exempt_cost, other_cost) < threshold < exempt_cost + other_cost

        outcome = _spill(
            entries, configurable=_configurable(store, scope), exempt_tools={"read_content_range"}, threshold=threshold
        )
        results = _results(outcome)
        assert results[0]["result"] == exempt_page
        assert results[1].get(MARKER_ENTRY_KEY) is True


def _file_text(lines: int = 2000) -> str:
    return "\n".join(f"cpu{i % 64}.counter_{i:05d}".ljust(40) + f"{i * 7919:>14d}" for i in range(lines))


class TestTextEnvelope:
    """``{"file_content": "<text>"}`` is archived as the text itself, so it can be read and searched by line."""

    def test_envelope_is_archived_as_the_raw_text(self):
        store, scope = _store_and_scope()
        text = _file_text()
        outcome = _spill([_entry(result={"file_content": text})], configurable=_configurable(store, scope))

        marker = _results(outcome)[0]["result"]
        assert marker["content_type"] == "text/plain" and marker["content_field"] == "file_content"
        assert "other_fields" not in marker
        # The artifact is the file, not a JSON rendering of it.
        assert marker["size_bytes"] == len(text.encode()) == store.statistics()["bytes"]
        assert "line_count" not in marker
        assert store.get_meta(scope, marker["artifact_handle"])["total_lines"] == 2000
        assert marker["preview"].startswith("cpu0.counter_00000")
        page = store.read_region(scope, marker["artifact_handle"], start_line=1999, max_lines=2)
        assert page["text"].splitlines()[-1].startswith("cpu15.counter_01999") and page["total_lines"] == 2000
        [hit] = store.search(scope, marker["artifact_handle"], "counter_01234")["lines"]
        assert hit["line"] == 1235 and hit["text"].startswith("cpu")

    def test_other_short_fields_travel_in_the_marker(self):
        store, scope = _store_and_scope()
        result = {"text": _file_text(), "truncated": True, "bytes_read": 123, "content_type": "text/plain", "x": None}
        marker = _results(_spill([_entry(result=result)], configurable=_configurable(store, scope)))[0]["result"]

        assert marker["content_field"] == "text"
        assert marker["other_fields"] == {"truncated": True, "bytes_read": 123, "content_type": "text/plain", "x": None}

    @pytest.mark.parametrize("long_field", ["content", "sibling"])
    @pytest.mark.parametrize("key_length", [129, 200_000])
    @pytest.mark.parametrize("with_store", [False, True])
    def test_long_field_names_use_json_instead_of_inflating_the_marker(self, long_field, key_length, with_store):
        store, scope = _store_and_scope()
        key = "k" * key_length
        text = _file_text()
        result = {key: text} if long_field == "content" else {"text": text, key: "short"}
        expected = json.dumps(result, ensure_ascii=False, indent=2)

        outcome = _spill(
            [_entry(result=result)],
            configurable=_configurable(store, scope) if with_store else None,
        )

        marker = _results(outcome)[0]["result"]
        assert marker["content_type"] == "application/json"
        assert "content_field" not in marker and "other_fields" not in marker
        assert len(str(marker)) < 3000
        assert marker["size_bytes"] == len(expected.encode())
        if with_store:
            # Deduplication proves the artifact contains the complete object, including its original key.
            assert store.put(scope, expected, {"source": "tool_response", "content_type": "application/json"}) == (
                marker["artifact_handle"]
            )
            assert store.statistics()["puts"] == 1
        else:
            assert marker["truncated"] is True and "artifact_handle" not in marker

    def test_field_names_at_the_limit_still_use_the_text_envelope(self):
        store, scope = _store_and_scope()
        content_key, sibling_key = "c" * 128, "s" * 128
        result = {content_key: _file_text(), sibling_key: "short"}

        marker = _results(_spill([_entry(result=result)], configurable=_configurable(store, scope)))[0]["result"]

        assert marker["content_type"] == "text/plain"
        assert marker["content_field"] == content_key and marker["other_fields"] == {sibling_key: "short"}

    def test_multibyte_text_reports_utf8_size(self):
        store, scope = _store_and_scope()
        text = "ñandú ✓ 日本語 line\n" * 3000
        outcome = _spill([_entry(result={"body": text})], configurable=_configurable(store, scope))
        marker = _results(outcome)[0]["result"]
        assert marker["size_bytes"] == len(text.encode("utf-8")) > len(text)

    @pytest.mark.parametrize(
        "result",
        [
            {"a": "x" * 60_000, "b": "y" * 600},  # a second long string
            {"content": "x" * 60_000, "meta": {"name": "n"}},  # a nested field
            {"content": "x" * 60_000, "items": [1, 2]},  # a list field
            {"content": "x" * 3_000, "name": "short text"},  # text below the minimum
            {f"f{i}": "x" * 5_000 if i == 0 else i for i in range(40)},  # too many fields
            ["x" * 60_000],  # not an object
        ],
        ids=["two-long-strings", "nested", "list", "short-text", "many-fields", "list-result"],
    )
    def test_other_shapes_stay_pretty_printed_json(self, result):
        store, scope = _store_and_scope()
        config = _config(context_budget_safety_fraction=0.01)  # small threshold so every shape spills
        marker = _results(_spill([_entry(result=result)], config, _configurable(store, scope)))[0]["result"]

        assert marker["content_type"] == "application/json"
        assert "content_field" not in marker and "other_fields" not in marker
        text = store.read_region(scope, marker["artifact_handle"], max_lines=1000)["text"]
        assert text.startswith(("{\n", "[\n"))

    def test_flagged_preview_also_uses_the_text(self):
        text = _file_text()
        outcome = _spill([_entry(result={"file_content": text})])  # no store

        result = _results(outcome)[0]["result"]
        assert result["truncated"] is True and result["content_field"] == "file_content"
        assert result["preview"].startswith("cpu0.counter_00000")
        assert result["size_bytes"] == len(text.encode()) and "line_count" not in result


class TestSpillLogging:
    def _records(self, caplog):
        return [(r.name, r.levelname, r.getMessage()) for r in caplog.records]

    def test_spill_logs_tool_and_size_at_info_and_the_artifact_id_only_at_debug(self, caplog):
        store, scope = _store_and_scope()
        with caplog.at_level("DEBUG", logger="autolangchat"):
            outcome = _spill(
                [_entry(name="download_file", result={"file_content": _file_text()})],
                configurable=_configurable(store, scope),
            )
        marker = _results(outcome)[0]["result"]

        handle = marker["artifact_handle"]
        records = self._records(caplog)
        [info] = [m for n, lvl, m in records if n.endswith("spill") and lvl == "INFO"]
        assert "download_file" in info and f"{marker['size_bytes']:,} bytes" in info and handle not in info
        [stored] = [m for n, lvl, m in records if n.endswith("spill") and lvl == "DEBUG" and handle in m]
        assert "download_file" in stored
        # Only DEBUG lines name the handle: the spill's own and the store's one-line "Stored artifact".
        assert not any(handle in m for _, lvl, m in records if lvl != "DEBUG")
        [put] = [m for n, _, m in records if n == "autolangchat.artifacts"]
        assert put == f"Stored artifact, size {marker['size_bytes']} bytes, id {handle}"

    def test_an_over_threshold_result_that_cannot_shrink_is_logged_as_kept(self, caplog):
        store, scope = _store_and_scope()
        config = _config(context_budget_safety_fraction=0.0001)  # a threshold below any marker
        with caplog.at_level("DEBUG", logger="autolangchat"):
            outcome = _spill([_entry(name="tiny", result={"ok": True})], config, _configurable(store, scope))

        assert outcome.spilled == 0 and _results(outcome)[0]["result"] == {"ok": True}
        assert any("Tool result tiny" in m and "was kept" in m for _, _, m in self._records(caplog))

    def test_unarchived_result_logs_a_warning_without_a_handle(self, caplog):
        with caplog.at_level("INFO", logger="autolangchat"):
            _spill([_entry(name="download_file", result={"file_content": _file_text()})])  # no store

        [record] = [r for r in caplog.records if r.name.endswith("spill")]
        assert record.levelname == "WARNING" and "download_file" in record.getMessage()


def _user_message(text, **extra):
    return {"role": "user", "content": text, "metadata": {"message_id": "u1", "timestamp": "t"}, **extra}


def _replace_user(message, store, scope, config=None, threshold=100.0, tokens=None):
    """What the preprocessing node does for an oversized user message: its estimates, then the replacement."""
    config = config or _config()
    provider = provider_for_model(config.model_id)
    part_tokens = [part.tokens for part in estimate_message_parts(message, provider)] if tokens is None else [tokens]
    return replace_oversized_message(
        message,
        part_tokens,
        threshold,
        message_index=1,
        model_id=config.model_id,
        chat_config=config,
        artifact_store=store,
        artifact_scope=scope,
    )


def _marker_of(message):
    """The marker a replaced user message carries as its content: the text the model receives, as a dict."""
    return ast.literal_eval(message["content"])


class TestUserMessageReplacement:
    def test_replaced_by_the_marker_a_tool_result_gets(self):
        store, scope = _store_and_scope()
        text = "analyze this:\n" + _file_text() + "\nWhich counter is the largest?"
        message = _user_message(text)

        outcome = _replace_user(message, store, scope)

        assert outcome.changed and outcome.spilled == 1 and outcome.truncated == 0
        replaced = outcome.message
        marker = _marker_of(replaced)
        assert replaced[MARKER_ENTRY_KEY] is True and replaced["role"] == "user"
        # The marker names the handle itself, so the message carries none in its metadata.
        assert replaced["metadata"] == message["metadata"]
        # The model receives it the way it receives a tool result's marker: as str(marker).
        assert replaced["content"] == str(marker) and len(replaced["content"]) < 3_000
        assert marker["source"] == "user_message" and "tool_name" not in marker
        assert marker["content_type"] == "text/plain" and marker["size_bytes"] == len(text.encode())
        assert marker["preview"] == text[:1000]
        assert marker["lifetime"] and "read_hint" not in marker
        # The artifact is the message itself.
        handle = marker["artifact_handle"]
        page = store.read_region(scope, handle, start_line=1, max_lines=2)
        assert page["text"].startswith("analyze this:\ncpu0.counter_00000") and page["total_lines"] == 2002
        assert page["metadata"]["source"] == "user_message"
        assert store.get_meta(scope, handle)["size_bytes"] == len(text.encode()) == store.statistics()["bytes"]

    def test_user_message_and_tool_result_markers_are_one_marker(self):
        store, scope = _store_and_scope()
        text = _file_text()

        user = _marker_of(_replace_user(_user_message(text), store, scope).message)
        tool = _results(_spill([_entry(name="get_report", result=text)], configurable=_configurable(store, scope)))[0][
            "result"
        ]

        generic = "Large content stored as a session artifact; only a preview is shown below."
        assert user["message"] == tool["message"] == generic
        # Same fields in the same order; only a tool result names the tool, and `source` says where it came from.
        assert [key for key in tool if key != "tool_name"] == list(user)
        assert (tool["source"], tool["tool_name"], user["source"]) == ("tool_response", "get_report", "user_message")
        assert user["preview"] == tool["preview"] == text[:1000]

    def test_input_is_not_mutated(self):
        store, scope = _store_and_scope()
        message = _user_message(_file_text())
        snapshot = copy.deepcopy(message)

        outcome = _replace_user(message, store, scope)

        assert outcome.changed and outcome.message is not message
        assert message == snapshot

    def test_read_hint_only_when_read_tools_are_bound(self):
        store, scope = _store_and_scope()
        tools = [SimpleNamespace(name="read_content_range"), SimpleNamespace(name="grep_content")]
        text = _file_text()

        bound = _marker_of(_replace_user(_user_message(text), store, scope, _config(langchain_tools=tools)).message)
        unbound = _marker_of(_replace_user(_user_message(text), store, scope, _config()).message)

        assert bound["read_hint"] == READ_HINT and "read_hint" not in unbound
        assert bound["artifact_handle"] and unbound["artifact_handle"]  # the handle is named either way

    def test_a_message_the_marker_would_not_shrink_is_left_alone(self):
        store, scope = _store_and_scope()
        message = _user_message("short question")

        outcome = _replace_user(message, store, scope, threshold=0.0)

        assert outcome.message is message and not outcome.changed
        assert store.statistics()["puts"] == 0

    @pytest.mark.parametrize(
        "message",
        [
            {"role": "user", "content": ""},
            {"role": "user", "content": [{"type": "text", "text": "x" * 50_000}]},
            {"role": "assistant", "content": "x" * 50_000},
            {"role": "user", "content": "x" * 50_000, MARKER_ENTRY_KEY: True},
        ],
        ids=["empty", "list-content", "assistant", "already-a-marker"],
    )
    def test_other_messages_are_returned_as_they_are(self, message):
        store, scope = _store_and_scope()

        outcome = _replace_user(message, store, scope, tokens=1_000_000)

        assert outcome.message is message and not outcome.changed
        assert store.statistics()["puts"] == 0

    def test_multibyte_text_reports_its_utf8_size(self):
        store, scope = _store_and_scope()
        text = "ñandú ✓ 日本語 línea\n" * 3000

        marker = _marker_of(_replace_user(_user_message(text), store, scope).message)

        assert marker["size_bytes"] == len(text.encode("utf-8")) > len(text)

    def test_a_message_that_cannot_be_stored_raises(self):
        store, scope = _store_and_scope(max_bytes_per_session=1024)

        with pytest.raises(ArtifactError, match="quota"):
            _replace_user(_user_message(_file_text()), store, scope)

        assert store.statistics()["puts"] == 0

    def test_logs_name_the_message_and_keep_the_handle_out_of_info(self, caplog):
        store, scope = _store_and_scope()
        with caplog.at_level("DEBUG", logger="autolangchat"):
            outcome = _replace_user(_user_message(_file_text()), store, scope)

        handle = _marker_of(outcome.message)["artifact_handle"]
        records = [(r.name, r.levelname, r.getMessage()) for r in caplog.records]
        [info] = [m for n, lvl, m in records if n.endswith("spill") and lvl == "INFO"]
        assert info.startswith("Spilled user message 1: ") and "bytes (~" in info and "threshold 100" in info
        assert info.endswith("threshold 100)") and "reason=" not in info and handle not in info
        [debug] = [m for n, lvl, m in records if n.endswith("spill") and lvl == "DEBUG" and handle in m]
        assert debug == f"User message 1 is stored as artifact {handle}"
        assert not any(handle in m for _, lvl, m in records if lvl != "DEBUG")
        size = len(_file_text().encode())
        assert [m for n, _, m in records if n == "autolangchat.artifacts"] == [
            f"Stored artifact, size {size} bytes, id {handle}"
        ]

    def test_a_replacement_that_would_not_be_smaller_is_logged_as_kept(self, caplog):
        store, scope = _store_and_scope()
        with caplog.at_level("DEBUG", logger="autolangchat"):
            _replace_user(_user_message("short question"), store, scope, threshold=0.0)

        assert any(
            m.startswith("User message 1 (~") and m.endswith("was kept: a replacement would not be smaller")
            for m in (r.getMessage() for r in caplog.records)
        )

    def test_without_a_store_the_message_is_left_alone_and_nothing_is_reported(self, caplog):
        message = _user_message(_file_text())

        with caplog.at_level("DEBUG", logger="autolangchat"):
            outcome = _replace_user(message, None, None)

        # Unlike a tool result there is no flagged preview: the preprocessor's truncation takes care of it.
        assert outcome.message is message and not outcome.changed
        assert not [r for r in caplog.records if r.name.endswith("spill")]

    def test_estimates_that_do_not_match_the_message_leave_it_alone(self):
        store, scope = _store_and_scope()
        message = _user_message(_file_text())

        outcome = replace_oversized_message(
            message,
            [1_000_000, 5],
            100.0,
            message_index=1,
            model_id=QWEN,
            chat_config=_config(),
            artifact_store=store,
            artifact_scope=scope,
        )

        assert outcome.message is message and not outcome.changed
        assert store.statistics()["puts"] == 0
