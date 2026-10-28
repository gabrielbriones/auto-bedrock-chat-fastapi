"""Artifact storage isolation, lifetime, and graph integration."""

import asyncio
import json
import logging
import re
import sys
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

import autolangchat.artifacts as artifacts_module
from autolangchat.artifacts import ArtifactError, ArtifactStore
from autolangchat.config import ChatConfig
from autolangchat.defaults import (
    ARTIFACT_READ_MAX_BYTES,
    ARTIFACT_SEARCH_MAX_LINE_CHARS,
    ARTIFACT_SEARCH_REGEX_MAX_LINE_CHARS,
)
from autolangchat.graph.graph import build_chat_graph
from autolangchat.graph.nodes.llm_call import llm_call_node
from autolangchat.graph.nodes.preprocess import preprocess_node
from autolangchat.session_manager import ChatSessionManager
from autolangchat.websocket_handler import WebSocketChatHandler


def _store(**limits):
    store = ArtifactStore(**limits)
    store.register_connection("connection", "alice")
    return store, store.activate("connection", "thread", "alice")


def test_statistics_are_aggregate_snapshots_and_exclude_dedup_hits():
    store, scope = _store()
    assert store.statistics() == {
        "active_artifacts": 0,
        "bytes": 0,
        "puts": 0,
        "expiries": 0,
        "quota_rejections": 0,
        "failed_reads": 0,
    }
    handle = store.put(scope, "\u00e9", {"private": "metadata"})
    assert store.put(scope, "\u00e9", {"private": "metadata"}) == handle
    assert store.contains(scope, handle)
    store.read_region(scope, handle)
    snapshot = store.statistics()
    assert snapshot == {
        "active_artifacts": 1,
        "bytes": 2,
        "puts": 1,
        "expiries": 0,
        "quota_rejections": 0,
        "failed_reads": 0,
    }
    snapshot["puts"] = -1
    assert store.statistics()["puts"] == 1


@pytest.mark.parametrize("cleanup", ["thread", "user", "session", "close"])
def test_statistics_after_cleanup_preserve_lifetime_counters(cleanup):
    store, scope = _store()
    store.put(scope, "first", {})
    store.register_connection("other", "bob")
    other = store.activate("other", "thread", "bob")
    store.put(other, "second", {})
    if cleanup == "thread":
        store.delete_thread("thread", "alice")
    elif cleanup == "user":
        store.delete_user("alice")
    elif cleanup == "session":
        store.delete_session("connection")
    else:
        store.close()
    stats = store.statistics()
    assert stats["active_artifacts"] == (0 if cleanup == "close" else 1)
    assert stats["bytes"] == (0 if cleanup == "close" else 6)
    assert stats["puts"] == 2
    assert stats["expiries"] == 0


def test_statistics_expire_payloads_once(monkeypatch):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    store, scope = _store(max_age_seconds=10)
    handle = store.put(scope, "expired", {})
    clock[0] = 5
    store.put(scope, "live", {})
    clock[0] = 10
    assert store.statistics() == {
        "active_artifacts": 1,
        "bytes": 4,
        "puts": 2,
        "expiries": 1,
        "quota_rejections": 0,
        "failed_reads": 0,
    }
    store.expire()
    assert not store.contains(scope, handle)
    with pytest.raises(ArtifactError):
        store.read_region(scope, handle)
    assert store.statistics()["expiries"] == 1
    assert store.statistics()["failed_reads"] == 1
    clock[0] = 15
    assert store.statistics()["expiries"] == 2
    assert store.statistics()["bytes"] == 0


@pytest.mark.parametrize("unavailable", ["unknown", "foreign", "inactive", "closed"])
def test_statistics_count_each_unavailable_read_once(unavailable):
    store, scope = _store()
    handle = store.put(scope, "private", {})
    if unavailable == "unknown":
        handle = "unknown"
    elif unavailable == "foreign":
        store.register_connection("other", "bob")
        scope = store.activate("other", "thread", "bob")
    elif unavailable == "inactive":
        store.deactivate("connection")
    else:
        store.close()
    with pytest.raises(ArtifactError):
        store.read_region(scope, handle)
    assert store.statistics()["failed_reads"] == 1


@pytest.mark.parametrize("unavailable", ["unknown", "foreign", "inactive", "closed"])
def test_contains_probe_does_not_count_as_failed_read(unavailable):
    store, scope = _store()
    handle = store.put(scope, "private", {})
    if unavailable == "unknown":
        handle = "unknown"
    elif unavailable == "foreign":
        store.register_connection("other", "bob")
        scope = store.activate("other", "thread", "bob")
    elif unavailable == "inactive":
        store.deactivate("connection")
    else:
        store.close()
    if unavailable in ("unknown", "foreign"):
        assert not store.contains(scope, handle)
    else:
        with pytest.raises(ArtifactError):
            store.contains(scope, handle)
    assert store.statistics()["failed_reads"] == 0


def test_statistics_do_not_count_invalid_arguments_or_failed_writes_as_reads():
    store, scope = _store()
    handle = store.put(scope, "text", {})
    with pytest.raises(ValueError):
        store.read_region(scope, handle, start_line=0)
    store.deactivate("connection")
    with pytest.raises(ArtifactError):
        store.put(scope, "late", {})
    assert store.statistics()["failed_reads"] == 0
    assert store.statistics()["quota_rejections"] == 0


@pytest.mark.parametrize(
    "limits",
    [{"max_items_per_session": 4}, {"max_bytes_per_session": 4}, {"max_bytes": 4}],
)
def test_statistics_count_concurrent_puts_and_quota_rejections(limits):
    store, scope = _store(**limits)

    def put(index):
        try:
            store.put(scope, str(index), {})
        except ArtifactError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(put, range(8)))
    assert sum(results) == 4
    assert store.statistics() == {
        "active_artifacts": 4,
        "bytes": 4,
        "puts": 4,
        "expiries": 0,
        "quota_rejections": 4,
        "failed_reads": 0,
    }


@pytest.mark.parametrize("quota", ["items", "session_bytes", "store_bytes"])
def test_concurrent_distinct_puts_enforce_exact_quota_across_threads(quota):
    limits = {
        "items": {"max_items_per_session": 3},
        "session_bytes": {"max_bytes_per_session": 7},
        "store_bytes": {"max_bytes": 7},
    }
    store, scope = _store(**limits[quota])
    scopes = [scope] * 8
    if quota == "store_bytes":
        scopes = []
        for index in range(8):
            connection_id = f"connection-{index}"
            store.register_connection(connection_id, f"user-{index}")
            scopes.append(store.activate(connection_id, "thread", f"user-{index}"))
    ready = Barrier(8, timeout=5)

    def put(index):
        ready.wait()
        try:
            return store.put(scopes[index], "\u00e9", {"index": index})
        except ArtifactError as error:
            assert "quota exceeded" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        handles = list(executor.map(put, range(8)))
    accepted = [(scope, handle) for scope, handle in zip(scopes, handles) if handle is not None]
    assert len(accepted) == 3
    assert len({handle for _, handle in accepted}) == 3
    for scope, handle in accepted:
        assert store.read_region(scope, handle)["text"] == "\u00e9"
    assert store.statistics() == {
        "active_artifacts": 3,
        "bytes": 6,
        "puts": 3,
        "expiries": 0,
        "quota_rejections": 5,
        "failed_reads": 0,
    }


@pytest.mark.parametrize("mutation", ["expire", "thread", "user", "session", "close"])
def test_read_racing_with_expiry_or_deletion_fails_without_partial_result(monkeypatch, mutation):
    clock = [0]
    monkeypatch.setattr(artifacts_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    store, scope = _store(max_age_seconds=10)
    handle = store.put(scope, "first\nsecond\nthird\n", {})
    scanning = Event()
    resume = Event()
    finditer = re.finditer

    def scan(*args):
        for boundary in finditer(*args):
            yield boundary
            scanning.set()
            assert resume.wait(timeout=5), "Read was not released after mutation"

    monkeypatch.setattr(artifacts_module.re, "finditer", scan)
    with ThreadPoolExecutor(max_workers=1) as executor:
        reading = executor.submit(store.read_region, scope, handle)
        try:
            assert scanning.wait(timeout=5), "Read never reached the scanning phase"
            if mutation == "expire":
                clock[0] = 10
                store.expire()
            elif mutation == "thread":
                store.delete_thread(scope.thread_id, scope.user_id)
            elif mutation == "user":
                store.delete_user(scope.user_id)
            elif mutation == "session":
                store.delete_session(scope.connection_id)
            else:
                store.close()
        finally:
            resume.set()
        with pytest.raises(ArtifactError, match="unavailable"):
            reading.result(timeout=5)
    assert store.statistics() == {
        "active_artifacts": 0,
        "bytes": 0,
        "puts": 1,
        "expiries": int(mutation == "expire"),
        "quota_rejections": 0,
        "failed_reads": 1,
    }


def test_isolation_switching_and_reconnection():
    store, scope = _store()
    handle = store.put(scope, "full\ntext\n", {"source": "user_message"})
    store.register_connection("second", "alice")
    other = store.activate("second", "thread", "alice")
    with pytest.raises(ArtifactError):
        store.read_region(other, handle)
    switched = store.activate("connection", "other-thread", "alice")
    with pytest.raises(ArtifactError):
        store.read_region(switched, handle)
    with pytest.raises(ArtifactError):
        store.put(scope, "late turn", {})
    restored = store.activate("connection", "thread", "alice")
    assert store.read_region(restored, handle)["text"] == "full\ntext\n"
    assert store.put(restored, "full\ntext\n", {"source": "user_message"}) == handle
    store.delete_session("connection")
    with pytest.raises(ArtifactError):
        store.put(restored, "late turn", {})
    store.register_connection("connection", "alice")
    new_scope = store.activate("connection", "thread", "alice")
    with pytest.raises(ArtifactError):
        store.read_region(new_scope, handle)


def test_expiry_and_bounded_reads(monkeypatch):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    store, scope = _store(max_age_seconds=10)
    handle = store.put(scope, "\u00e9" * 40_000, {})
    region = store.read_region(scope, handle)
    assert len(region["text"].encode("utf-8")) == 65536
    assert region["truncated"]
    clock[0] = 10
    assert not store.contains(scope, handle)
    with pytest.raises(ArtifactError):
        store.read_region(scope, handle)
    store.close()
    with pytest.raises(ArtifactError):
        store.put(scope, "closed", {})


@pytest.mark.parametrize("text", ["", "a", "a\n", "\n\n", "a\r\nb\rc\n", "a\v\f\x1c\x1d\x1e\x85\u2028\u2029b"])
@pytest.mark.parametrize("start_line,max_lines", [(1, 1), (2, 3), (20, 2)])
def test_region_reads_preserve_splitlines_semantics(text, start_line, max_lines):
    store, scope = _store()
    handle = store.put(scope, text, {})
    lines = text.splitlines(keepends=True)
    result = store.read_region(scope, handle, start_line=start_line, max_lines=max_lines)
    assert result["text"] == "".join(lines[start_line - 1 : start_line - 1 + max_lines])
    assert result["total_lines"] == len(lines)
    assert result["truncated"] is (start_line - 1 + max_lines < len(lines))


def test_line_boundaries_match_splitlines_for_every_code_point():
    # Every code point once, plus every break followed by \n so \r\n is the only two-character break.
    text = "".join(map(chr, range(sys.maxunicode + 1)))
    text += "".join(line[-1] + "\n" for line in text.splitlines(keepends=True)[:-1])
    ends, end = [], 0
    for line in text.splitlines(keepends=True):
        end += len(line)
        ends.append(end)
    assert [end for _, _, end in artifacts_module._line_spans(text)] == ends
    assert [start for start, _, _ in artifacts_module._line_spans(text)] == [0] + ends[:-1]


@pytest.mark.parametrize("text", ["a\n" * 500_000, "\u20ac" * 1_000_000])
def test_region_read_temporary_allocations_are_bounded(text):
    store, scope = _store()
    handle = store.put(scope, text, {})
    tracemalloc.start()
    try:
        result = store.read_region(scope, handle)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_048_576
    assert len(result["text"].encode("utf-8")) <= 65536
    assert result["truncated"] is True


def test_byte_cap_does_not_skip_partial_unicode_to_append_later_lines():
    store, scope = _store()
    text = "a" * 65534 + "\u20ac\nlater\n"
    handle = store.put(scope, text, {})
    result = store.read_region(scope, handle)
    assert result["text"] == text.encode("utf-8")[:65536].decode("utf-8", errors="ignore")
    assert result["truncated"] is True


@pytest.mark.parametrize("invalidate", ["delete", "switch", "expire"])
def test_region_read_revalidates_after_scanning(monkeypatch, invalidate):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    store, scope = _store(max_age_seconds=10)
    handle = store.put(scope, "first\nsecond\n", {})
    finditer = re.finditer

    def scan(*args):
        for boundary in finditer(*args):
            yield boundary
        if invalidate == "delete":
            store.delete_session("connection")
        elif invalidate == "switch":
            store.activate("connection", "other", "alice")
        else:
            clock[0] = 10

    monkeypatch.setattr(re, "finditer", scan)
    with pytest.raises(ArtifactError):
        store.read_region(scope, handle)
    assert store.statistics()["failed_reads"] == 1
    assert store.statistics()["expiries"] == (1 if invalidate == "expire" else 0)


def test_get_meta_describes_artifact_without_counting_reads(monkeypatch):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    store, scope = _store(max_age_seconds=10)
    handle = store.put(scope, "\u00e9\nsecond\n", {"source": "tool_result", "tool": "jobs"})
    clock[0] = 2.5
    meta = store.get_meta(scope, handle)
    assert meta == {
        "size_bytes": 10,
        "total_lines": 2,
        "expires_in_seconds": 8,
        "source": "tool_result",
        "metadata": {"source": "tool_result", "tool": "jobs"},
    }
    meta["metadata"]["source"] = "changed"
    assert store.get_meta(scope, handle)["source"] == "tool_result"
    assert store.get_meta(scope, store.put(scope, "", {}))["source"] is None
    assert store.statistics()["failed_reads"] == 0


@pytest.mark.parametrize("text", ["", "a", "a\n", "\n\n", "a\r\nb\rc\n", "a\v\f\x1c\x1d\x1e\x85\u2028\u2029b"])
def test_get_meta_and_search_line_counts_match_splitlines(text):
    store, scope = _store()
    handle = store.put(scope, text, {})
    lines = text.splitlines()
    assert store.get_meta(scope, handle)["total_lines"] == len(lines)
    result = store.search(scope, handle, ".", regex=True, max_matches=20)
    assert result["total_lines"] == len(lines)
    assert [(entry["line"], entry["text"]) for entry in result["lines"]] == [
        (number, line) for number, line in enumerate(lines, 1) if line
    ]


@pytest.mark.parametrize("operation", ["get_meta", "search", "read_bytes"])
@pytest.mark.parametrize("unavailable", ["unknown", "foreign", "other_thread", "inactive", "closed", "expired"])
def test_get_meta_and_search_fail_closed_and_count_failed_reads(monkeypatch, operation, unavailable):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    store, scope = _store(max_age_seconds=10)
    handle = store.put(scope, "private", {})
    if unavailable == "unknown":
        handle = "unknown"
    elif unavailable == "foreign":
        store.register_connection("other", "bob")
        scope = store.activate("other", "thread", "bob")
    elif unavailable == "other_thread":
        scope = store.activate("connection", "other-thread", "alice")
    elif unavailable == "inactive":
        store.deactivate("connection")
    elif unavailable == "closed":
        store.close()
    else:
        clock[0] = 10
    with pytest.raises(ArtifactError, match="unavailable") as error:
        if operation == "get_meta":
            store.get_meta(scope, handle)
        elif operation == "read_bytes":
            store.read_bytes(scope, handle)
        else:
            store.search(scope, handle, "private")
    assert "private" not in str(error.value) and "alice" not in str(error.value)
    assert store.statistics()["failed_reads"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pattern": ""},
        {"pattern": "a" * 201, "regex": True},
        {"pattern": "(", "regex": True},
        {"pattern": r"(a)\1", "regex": True},
        {"pattern": "(?=a)", "regex": True},
        {"pattern": "(?<=a)b", "regex": True},
        {"pattern": "a", "context_lines": 21},
        {"pattern": "a", "max_matches": 0},
        {"pattern": "a", "max_matches": 201},
    ],
)
def test_search_rejects_invalid_arguments_without_counting_reads(kwargs):
    store, scope = _store()
    handle = store.put(scope, "a\n", {})
    with pytest.raises(ValueError):
        store.search(scope, handle, **kwargs)
    assert store.statistics()["failed_reads"] == 0


def test_search_literal_is_case_insensitive_and_regex_is_opt_in():
    store, scope = _store()
    handle = store.put(scope, "Error: a.c\nerror abc\nok\n", {})
    assert [entry["line"] for entry in store.search(scope, handle, "ERROR")["lines"]] == [1, 2]
    assert [entry["line"] for entry in store.search(scope, handle, "a.c")["lines"]] == [1]
    assert [entry["line"] for entry in store.search(scope, handle, "a.c", regex=True)["lines"]] == [1, 2]
    assert [entry["line"] for entry in store.search(scope, handle, "Error", ignore_case=False)["lines"]] == [1]
    assert store.search(scope, handle, "([")["lines"] == []
    long_literal = store.search(scope, handle, "a" * 201)
    assert long_literal["lines"] == [] and long_literal["match_count"] == 0


def test_search_regex_runs_in_linear_time_on_catastrophic_patterns():
    store, scope = _store()
    handle = store.put(scope, ("a" * 5000 + "b\n") * 20 + "needle\n", {})
    started = time.monotonic()
    for pattern in ("(a+)+$", "(a|aa)+$", "(a*)*c", "(.*a){20}x"):
        result = store.search(scope, handle, pattern, regex=True)
        assert result["match_count"] == 0
    assert [e["line"] for e in store.search(scope, handle, "(?:ne+)dle$", regex=True)["lines"]] == [21]
    assert time.monotonic() - started < 5


def test_search_regex_honours_ignore_case():
    store, scope = _store()
    handle = store.put(scope, "Error\nerror\n", {})
    assert [e["line"] for e in store.search(scope, handle, "^error$", regex=True)["lines"]] == [1, 2]
    assert [e["line"] for e in store.search(scope, handle, "^error$", regex=True, ignore_case=False)["lines"]] == [2]


def test_search_matches_one_line_at_a_time():
    store, scope = _store()
    handle = store.put(scope, "alpha\r\nbeta\rgamma\u2028delta", {})
    assert store.search(scope, handle, "alpha.*beta", regex=True)["lines"] == []
    assert store.search(scope, handle, "a\\s+b", regex=True)["lines"] == []
    assert [entry["line"] for entry in store.search(scope, handle, "^[bg]", regex=True)["lines"]] == [2, 3]
    assert [entry["line"] for entry in store.search(scope, handle, "a$", regex=True)["lines"]] == [1, 2, 3, 4]


def test_search_merges_context_without_duplicates():
    store, scope = _store()
    text = "".join(f"line {number}{' hit' if number in (3, 5, 12) else ''}\n" for number in range(1, 16))
    handle = store.put(scope, text, {"source": "user_message"})
    result = store.search(scope, handle, "hit", context_lines=2)
    assert [(entry["line"], entry["match"]) for entry in result["lines"]] == [
        (1, False),
        (2, False),
        (3, True),
        (4, False),
        (5, True),
        (6, False),
        (7, False),
        (10, False),
        (11, False),
        (12, True),
        (13, False),
        (14, False),
    ]
    assert result["lines"][2] == {"line": 3, "text": "line 3 hit", "match": True, "clipped": False, "column": 8}
    assert result["match_count"] == 3
    assert result["total_lines"] == 15
    assert result["truncated"] is False
    assert result["metadata"] == {"source": "user_message"}


def test_search_reports_truncation_only_when_more_matches_exist():
    store, scope = _store()
    handle = store.put(scope, "hit\nmiss\nhit\nmiss\nhit\n", {})
    exact = store.search(scope, handle, "hit", max_matches=3)
    assert (exact["match_count"], exact["truncated"]) == (3, False)
    limited = store.search(scope, handle, "hit", max_matches=2, context_lines=1)
    assert [(entry["line"], entry["match"]) for entry in limited["lines"]] == [
        (1, True),
        (2, False),
        (3, True),
        (4, False),
    ]
    assert (limited["match_count"], limited["truncated"], limited["total_lines"]) == (2, True, 5)


def test_search_clips_giant_lines_around_the_match():
    store, scope = _store()
    text = '{"items": [' + "1, " * 100_000 + '"NEEDLE"' + ", 2" * 100_000 + "]}"
    handle = store.put(scope, text, {})
    result = store.search(scope, handle, "needle")
    [entry] = result["lines"]
    assert entry["clipped"] is True
    assert len(entry["text"]) == ARTIFACT_SEARCH_MAX_LINE_CHARS
    assert "NEEDLE" in entry["text"]
    assert entry["column"] == text.index("NEEDLE") + 1
    context = store.search(scope, store.put(scope, "x" * 10_000 + "\nhit\n", {}), "hit", context_lines=1)
    assert context["lines"][0] == {
        "line": 1,
        "text": "x" * ARTIFACT_SEARCH_MAX_LINE_CHARS,
        "match": False,
        "clipped": True,
    }


def test_search_output_is_byte_capped():
    store, scope = _store()
    handle = store.put(scope, ("\u20ac" * 400 + " hit\n") * 200, {})
    result = store.search(scope, handle, "hit", max_matches=200)
    assert sum(len(entry["text"].encode("utf-8")) for entry in result["lines"]) <= ARTIFACT_READ_MAX_BYTES
    assert 0 < result["match_count"] < 200
    assert result["truncated"] is True
    assert result["total_lines"] == 200


def test_search_handles_empty_and_non_ascii_artifacts():
    store, scope = _store()
    assert store.search(scope, store.put(scope, "", {}), "a") == {
        "lines": [],
        "match_count": 0,
        "total_lines": 0,
        "truncated": False,
        "partially_searched_lines": 0,
        "metadata": {},
    }
    handle = store.put(scope, "\ufffd\x00binary\nSTRASSE stra\u00dfe\n", {})
    assert [entry["line"] for entry in store.search(scope, handle, "\x00bin")["lines"]] == [1]
    assert store.search(scope, handle, "\u00df")["lines"][0]["column"] == 13


def test_search_output_respects_max_bytes():
    store, scope = _store()
    handle = store.put(scope, "hit one\nhit two\nhit three\n", {})
    result = store.search(scope, handle, "hit", max_bytes=len("hit one") + len("hit two"))
    assert [entry["line"] for entry in result["lines"]] == [1, 2]
    assert result["truncated"] is True
    for max_bytes in (0, ARTIFACT_READ_MAX_BYTES + 1):
        with pytest.raises(ValueError):
            store.search(scope, handle, "hit", max_bytes=max_bytes)


def test_regex_search_only_scans_the_start_of_giant_lines():
    store, scope = _store()
    window = ARTIFACT_SEARCH_REGEX_MAX_LINE_CHARS
    text = "early " + "x" * window + " late\nshort late\n"
    handle = store.put(scope, text, {})
    regex = store.search(scope, handle, "late", regex=True)
    assert [entry["line"] for entry in regex["lines"]] == [2]
    assert regex["partially_searched_lines"] == 1
    assert [entry["line"] for entry in store.search(scope, handle, "early", regex=True)["lines"]] == [1]
    literal = store.search(scope, handle, "late")
    assert [entry["line"] for entry in literal["lines"]] == [1, 2]
    assert literal["partially_searched_lines"] == 0


def test_read_region_reports_numbered_lines_clipping_and_byte_offsets():
    store, scope = _store()
    handle = store.put(scope, "\u00e9a\r\nbb\nccc\n", {})
    region = store.read_region(scope, handle, start_line=2, max_lines=2)
    assert region["lines"] == [
        {"line": 2, "text": "bb", "clipped": False, "start_byte": 5},
        {"line": 3, "text": "ccc", "clipped": False},
    ]
    clipped = store.read_region(scope, handle, max_bytes=7)
    assert clipped["text"] == "\u00e9a\r\nbb"
    assert clipped["lines"] == [
        {"line": 1, "text": "\u00e9a", "clipped": False, "start_byte": 0},
        {"line": 2, "text": "bb", "clipped": False},
    ]
    assert clipped["truncated"] is True
    cut = store.read_region(scope, handle, start_line=3, max_bytes=2)
    assert cut["lines"] == [{"line": 3, "text": "cc", "clipped": True, "start_byte": 8}]
    for max_bytes in (0, ARTIFACT_READ_MAX_BYTES + 1):
        with pytest.raises(ValueError):
            store.read_region(scope, handle, max_bytes=max_bytes)
    assert store.statistics()["failed_reads"] == 0


def test_read_bytes_snaps_to_character_boundaries():
    store, scope = _store()
    text = "a\u00e9\u20ac\U0001f600z"
    handle = store.put(scope, text, {"source": "tool_result"})
    encoded = text.encode("utf-8")
    assert store.read_bytes(scope, handle, max_bytes=5) == {
        "text": "a\u00e9",
        "start_byte": 0,
        "end_byte": 3,
        "total_bytes": len(encoded),
        "truncated": True,
        "metadata": {"source": "tool_result"},
    }
    # A mid-character offset snaps back so nothing is skipped.
    middle = store.read_bytes(scope, handle, start_byte=4, max_bytes=7)
    assert (middle["text"], middle["start_byte"], middle["end_byte"]) == ("\u20ac\U0001f600", 3, 10)
    end = store.read_bytes(scope, handle, start_byte=10)
    assert (end["text"], end["truncated"]) == ("z", False)
    assert store.read_bytes(scope, handle, start_byte=len(encoded))["text"] == ""
    pieces, offset = [], 0
    while offset < len(encoded):
        page = store.read_bytes(scope, handle, start_byte=offset, max_bytes=4)
        pieces.append(page["text"])
        offset = page["end_byte"]
    assert "".join(pieces) == text


def test_read_bytes_spans_chunked_encoding():
    store, scope = _store()
    text = "\u20ac" * (artifacts_module._UTF8_CHUNK_CHARS + 10)
    handle = store.put(scope, text, {})
    page = store.read_bytes(scope, handle, start_byte=3 * artifacts_module._UTF8_CHUNK_CHARS + 1, max_bytes=7)
    assert (page["text"], page["start_byte"]) == ("\u20ac\u20ac", 3 * artifacts_module._UTF8_CHUNK_CHARS)


@pytest.mark.parametrize(
    "kwargs", [{"start_byte": -1}, {"start_byte": 4}, {"max_bytes": 3}, {"max_bytes": ARTIFACT_READ_MAX_BYTES + 1}]
)
def test_read_bytes_rejects_invalid_ranges_without_counting_reads(kwargs):
    store, scope = _store()
    handle = store.put(scope, "abc", {})
    with pytest.raises(ValueError):
        store.read_bytes(scope, handle, **kwargs)
    assert store.statistics()["failed_reads"] == 0


@pytest.mark.parametrize("invalidate", ["delete", "switch", "expire"])
def test_read_bytes_revalidates_after_reading(monkeypatch, invalidate):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    store, scope = _store(max_age_seconds=10)
    handle = store.put(scope, "payload", {})
    char_at_byte = artifacts_module._char_at_byte

    def locate(*args):
        located = char_at_byte(*args)
        if invalidate == "delete":
            store.delete_session("connection")
        elif invalidate == "switch":
            store.activate("connection", "other", "alice")
        else:
            clock[0] = 10
        return located

    monkeypatch.setattr(artifacts_module, "_char_at_byte", locate)
    with pytest.raises(ArtifactError):
        store.read_bytes(scope, handle)
    assert store.statistics()["failed_reads"] == 1


@pytest.mark.parametrize("mutation", ["expire", "thread", "user", "session", "close"])
def test_search_racing_with_expiry_or_deletion_fails_without_partial_result(monkeypatch, mutation):
    clock = [0]
    monkeypatch.setattr(artifacts_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    store, scope = _store(max_age_seconds=10)
    handle = store.put(scope, "first hit\nsecond hit\nthird hit\n", {})
    scanning = Event()
    resume = Event()
    finditer = re.finditer

    def scan(*args):
        for boundary in finditer(*args):
            yield boundary
            scanning.set()
            assert resume.wait(timeout=5), "Search was not released after mutation"

    monkeypatch.setattr(artifacts_module.re, "finditer", scan)
    with ThreadPoolExecutor(max_workers=1) as executor:
        searching = executor.submit(store.search, scope, handle, "hit")
        try:
            assert scanning.wait(timeout=5), "Search never reached the scanning phase"
            # These take the store lock, so they would deadlock if the scan held it.
            if mutation == "expire":
                clock[0] = 10
                store.expire()
            elif mutation == "thread":
                store.delete_thread(scope.thread_id, scope.user_id)
            elif mutation == "user":
                store.delete_user(scope.user_id)
            elif mutation == "session":
                store.delete_session(scope.connection_id)
            else:
                store.close()
        finally:
            resume.set()
        with pytest.raises(ArtifactError, match="unavailable"):
            searching.result(timeout=5)
    assert store.statistics()["failed_reads"] == 1
    assert store.statistics()["expiries"] == int(mutation == "expire")


@pytest.mark.asyncio
@pytest.mark.parametrize("use_fallback", [False, True])
async def test_expiry_during_primary_request_preserves_replacement_handle(monkeypatch, use_fallback):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    store, scope = _store(max_age_seconds=10)
    chat_config = ChatConfig().model_copy(
        update={"context_budget_safety_fraction": 0, "fallback_model": "qwen.qwen3-32b-v1:0"}
    )
    original = "full original request"
    original_handle = store.put(scope, original, {"source": "user_message"})
    original_metadata = {"message_id": "u1", "timestamp": "t1", "artifact_handle": original_handle}
    messages = [{"role": "user", "content": original, "metadata": original_metadata}]
    calls = []

    async def invoke(lc_messages):
        calls.append(lc_messages)
        if len(calls) == 1:
            clock[0] = 10
            raise Exception("input is too long")
        if use_fallback and len(calls) == 2:
            raise Exception("input is too long")
        return AIMessage(content="answer")

    async def truncate(**kwargs):
        return [{**kwargs["messages"][0], "content": "shortened"}]

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=invoke)
    with (
        patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm),
        patch("autolangchat.graph.nodes.llm_call.MessagePreprocessor.preprocess_messages", side_effect=truncate),
    ):
        result = await llm_call_node(
            {"messages": messages},
            {"configurable": {"chat_config": chat_config, "artifact_store": store, "artifact_scope": scope}},
        )
    user = result["messages"][0]
    replacement = user["metadata"]["artifact_handle"]
    assert replacement != original_handle
    assert store.read_region(scope, replacement)["text"] == original
    assert user["content"] == original
    assert user["metadata"] == {**original_metadata, "artifact_handle": replacement}
    assert messages[0]["metadata"] == original_metadata
    assert calls[1][0].content == "shortened"
    if use_fallback:
        assert calls[2][0].content == original


def test_atomic_quota_and_deduplication():
    store, scope = _store(max_bytes_per_session=8, max_bytes=8, max_items_per_session=1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        handles = list(executor.map(lambda _: store.put(scope, "\u00e9" * 4, {}), range(8)))
    assert len(set(handles)) == 1
    assert store.statistics()["puts"] == 1
    with pytest.raises(ArtifactError, match="quota"):
        store.put(scope, "different", {})
    store.delete_thread("thread", "alice")
    with pytest.raises(ArtifactError):
        store.put(scope, "late write", {})
    fresh = store.activate("connection", "thread", "alice")
    assert store.put(fresh, "new", {})


def test_deleting_one_thread_preserves_others_and_user_purge_revokes_inflight_scopes():
    store, first = _store()
    first_handle = store.put(first, "first", {})
    second = store.activate("connection", "second", "alice")
    second_handle = store.put(second, "second", {})
    store.delete_thread("thread", "alice")
    assert store.read_region(second, second_handle)["text"] == "second"
    assert not store.contains(second, first_handle)
    store.delete_user("alice")
    with pytest.raises(ArtifactError):
        store.put(second, "late write", {})
    fresh = store.activate("connection", "second", "alice")
    assert not store.contains(fresh, second_handle)


@pytest.mark.asyncio
async def test_graph_archives_original_before_preprocessing_and_checkpoints_handle():
    store, scope = _store()
    config = ChatConfig().model_copy(update={"context_budget_safety_fraction": 0, "enable_ai_summarization": False})
    original = "Original request " * 100
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="response"))
    runnable = {
        "configurable": {
            "thread_id": "thread",
            "chat_config": config,
            "artifact_store": store,
            "artifact_scope": scope,
        }
    }
    with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
        graph = build_chat_graph(config)
        result = await graph.ainvoke({"user_message": original}, config=runnable)
        checkpoint = await graph.aget_state(runnable)
    user = next(message for message in result["messages"] if message["role"] == "user")
    handle = user["metadata"]["artifact_handle"]
    assert user["content"] == original
    assert store.read_region(scope, handle)["text"] == original
    assert any(
        message.get("metadata", {}).get("artifact_handle") == handle for message in checkpoint.values["messages"]
    )
    assert any(message.content == original for message in llm.ainvoke.call_args.args[0])


@pytest.mark.asyncio
async def test_websocket_preserves_feedback_and_title_inputs_and_passes_scope(caplog):
    caplog.set_level(logging.DEBUG, logger="autolangchat.websocket_handler")
    config = ChatConfig().model_copy(update={"conversation_persistence_enabled": True})
    manager = ChatSessionManager(config)
    ws = MagicMock()
    ws.send_json = AsyncMock()
    await manager.create_session(ws, user_id="alice", is_authenticated=True)
    session = await manager.get_session(ws)
    conversation_store = MagicMock()
    conversation_store.create_conversation = AsyncMock()
    conversation_store.record_turn = AsyncMock()
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={"messages": [{"role": "assistant", "content": "answer"}]})
    handler = WebSocketChatHandler(
        session_manager=manager, config=config, chat_graph=graph, conversation_store=conversation_store
    )
    handler._generate_and_apply_title = AsyncMock()
    original = "the full original input"
    try:
        await handler._handle_chat_message(ws, {"message": original})
        await asyncio.sleep(0)
        assert graph.ainvoke.call_args.args[0]["user_message"] == original
        configurable = graph.ainvoke.call_args.kwargs["config"]["configurable"]
        assert configurable["artifact_store"] is manager.artifact_store
        assert configurable["artifact_scope"].connection_id == session.connection_id
        assert next(iter(session.metadata["feedback_meta"].values()))["query"] == original
        assert handler._generate_and_apply_title.call_args.args[2][0]["content"] == original
        assert f"Received user message: {original}" in caplog.text
    finally:
        await handler.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("threshold,expected", [(0, True), (0.5, False)])
async def test_preprocessing_handles_only_oversized_messages(threshold, expected):
    store, scope = _store()
    config = ChatConfig().model_copy(update={"context_budget_safety_fraction": threshold})
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "request"}]
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="answer"))
    runnable = {"configurable": {"chat_config": config, "artifact_store": store, "artifact_scope": scope}}
    with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
        processed = await preprocess_node({"messages": messages}, runnable)
        result = await llm_call_node(processed, runnable)
    assert ("artifact_handle" in result["messages"][1].get("metadata", {})) is expected
    assert "metadata" not in messages[1]
    assert result["messages"][1]["content"] == "request"


@pytest.mark.asyncio
async def test_manager_removal_and_identity_change_revoke_artifacts():
    manager = ChatSessionManager(ChatConfig())
    ws = MagicMock()
    ws.close = AsyncMock()
    try:
        sid = await manager.create_session(ws, user_id="alice")
        session = await manager.get_session(ws)
        store = manager.artifact_store
        scope = store.activate(session.connection_id, "thread", "alice")
        handle = store.put(scope, "secret", {})
        await manager.update_session_user_id(sid, "bob")
        with pytest.raises(ArtifactError):
            store.put(scope, "late write", {})
        fresh = store.activate(session.connection_id, "thread", "bob")
        assert not store.contains(fresh, handle)
        await manager.remove_session(ws)
        with pytest.raises(ArtifactError):
            store.put(fresh, "late write", {})
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_original_is_archived_before_lossy_preprocessing():
    store, scope = _store()
    config = ChatConfig().model_copy(update={"context_budget_safety_fraction": 0})
    original = "entire original input"
    messages = [{"role": "user", "content": original}]

    async def truncate(*, messages, on_progress):
        return [{**messages[0], "content": "truncated"}]

    with patch("autolangchat.graph.nodes.preprocess.MessagePreprocessor.preprocess_messages", side_effect=truncate):
        result = await preprocess_node(
            {"messages": messages},
            {"configurable": {"chat_config": config, "artifact_store": store, "artifact_scope": scope}},
        )
    assert result["messages"][0]["content"] == "truncated"
    handle = result["messages"][0]["metadata"]["artifact_handle"]
    assert store.read_region(scope, handle)["text"] == original


@pytest.mark.asyncio
async def test_rearchiving_an_expired_handle_does_not_count_as_failed_read(monkeypatch):
    clock = [0]
    monkeypatch.setattr(artifacts_module.time, "monotonic", lambda: clock[0])
    store, scope = _store(max_age_seconds=10)
    config = ChatConfig().model_copy(update={"context_budget_safety_fraction": 0})
    runnable = {"configurable": {"chat_config": config, "artifact_store": store, "artifact_scope": scope}}
    first = await preprocess_node({"messages": [{"role": "user", "content": "original"}]}, runnable)
    expired_handle = first["messages"][0]["metadata"]["artifact_handle"]
    clock[0] = 10
    second = await preprocess_node({"messages": first["messages"]}, runnable)
    assert second["messages"][0]["metadata"]["artifact_handle"] != expired_handle
    stats = store.statistics()
    assert stats["puts"] == 2
    assert stats["expiries"] == 1
    assert stats["failed_reads"] == 0


@pytest.mark.asyncio
async def test_quota_failure_is_explicit_and_prevents_model_invocation():
    store, scope = _store(max_bytes_per_session=1)
    config = ChatConfig().model_copy(update={"context_budget_safety_fraction": 0})
    llm = MagicMock()
    llm.ainvoke = AsyncMock()
    with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
        with pytest.raises(ArtifactError, match="quota"):
            await preprocess_node(
                {"messages": [{"role": "user", "content": "too large to archive"}]},
                {"configurable": {"chat_config": config, "artifact_store": store, "artifact_scope": scope}},
            )
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_artifacts_do_not_store_or_reject_input():
    store, scope = _store(max_bytes_per_session=1)
    config = ChatConfig().model_copy(update={"artifact_enabled": False, "context_budget_safety_fraction": 0})
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="answer"))
    with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
        result = await preprocess_node(
            {"messages": [{"role": "user", "content": "original"}]},
            {"configurable": {"chat_config": config, "artifact_store": store, "artifact_scope": scope}},
        )
    assert result["messages"][0] == {"role": "user", "content": "original"}
    assert store._artifacts == {}


@pytest.mark.asyncio
async def test_tool_results_are_archived_with_structure_and_retries_reuse_handles():
    store, scope = _store()
    config = ChatConfig().model_copy(
        update={"context_budget_safety_fraction": 0, "fallback_model": "qwen.qwen3-32b-v1:0"}
    )
    messages = [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "lookup", "args": {}}]},
        {
            "role": "tool",
            "content": "",
            "tool_results": [{"name": "lookup", "tool_call_id": "c1", "result": {"full": "result"}}],
        },
    ]
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        side_effect=[Exception("input is too long"), Exception("input is too long"), AIMessage(content="answer")]
    )
    with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
        runnable = {"configurable": {"chat_config": config, "artifact_store": store, "artifact_scope": scope}}
        processed = await preprocess_node({"messages": messages}, runnable)
        result = await llm_call_node(
            processed,
            runnable,
        )
    assert len(store._artifacts) == 1
    handle = result["messages"][2]["metadata"]["artifact_handle"]
    assert json.loads(store.read_region(scope, handle)["text"]) == messages[2]
    assert result["messages"][2]["tool_results"] == messages[2]["tool_results"]
    assert "artifact_handle" not in result["messages"][0].get("metadata", {})
    assert result["metadata"]["fallback_model_used"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["clear", "new", "load", "delete", "bulk", "all", "logout", "remove", "shutdown"])
async def test_lifecycle_handlers_revoke_inflight_artifact_scopes(operation):
    config = ChatConfig().model_copy(update={"conversation_persistence_enabled": True})
    manager = ChatSessionManager(config)
    ws = MagicMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    await manager.create_session(ws, user_id="alice", is_authenticated=True)
    session = await manager.get_session(ws)
    session.metadata["conversation_id"] = "thread"
    store = manager.artifact_store
    scope = store.activate(session.connection_id, "thread", "alice")
    handle = store.put(scope, "original", {})
    conversations = MagicMock()
    conversations.get_conversation = AsyncMock(return_value={"user_id": "alice", "message_count": 0})
    conversations.delete_conversation = AsyncMock()
    conversations.delete_conversations = AsyncMock(return_value=["thread"])
    conversations.delete_all_conversations = AsyncMock(return_value=1)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=None)
    graph.aupdate_state = AsyncMock()
    handler = WebSocketChatHandler(
        session_manager=manager, config=config, chat_graph=graph, conversation_store=conversations
    )
    operations = {
        "clear": (handler._handle_clear_history, {}),
        "new": (handler._handle_conversation_new, {}),
        "load": (handler._handle_conversation_load, {"conversation_id": "other"}),
        "delete": (handler._handle_conversation_delete, {"conversation_id": "thread"}),
        "bulk": (handler._handle_conversation_delete_bulk, {"conversation_ids": ["thread"]}),
        "all": (handler._handle_conversation_delete_all, {}),
        "logout": (handler._handle_logout, {}),
    }
    try:
        if operation == "remove":
            await manager.remove_session_by_id(session.session_id)
        elif operation == "shutdown":
            await manager.shutdown()
        else:
            method, data = operations[operation]
            await method(ws, data)
        with pytest.raises(ArtifactError):
            store.put(scope, "late write", {})
        if operation not in ("remove", "shutdown"):
            fresh = store.activate(session.connection_id, "thread", "alice")
            if operation in ("new", "load"):
                assert store.read_region(fresh, handle)["text"] == "original"
            else:
                assert not store.contains(fresh, handle)
    finally:
        await handler.shutdown()


class TestArtifactMarkers:
    HANDLE = "11111111-2222-3333-4444-555555555555"

    def _messages(self):
        meta = {"artifact_handle": self.HANDLE}
        return [
            {"role": "user", "content": "big paste", "metadata": meta},
            {"role": "user", "content": "small"},
            {
                "role": "tool",
                "tool_results": [
                    {"tool_call_id": "a", "name": "t1", "result": "r1"},
                    {"tool_call_id": "b", "name": "t2", "result": "r2"},
                ],
                "metadata": meta,
            },
        ]

    def test_markers_name_handle_and_tools_without_mutating_messages(self):
        from autolangchat.graph.nodes.llm_call import _to_langchain_messages

        messages = self._messages()
        lc = _to_langchain_messages(messages, artifact_markers=True)

        assert lc[0].content.startswith("big paste\n\n[Archived:")
        assert self.HANDLE in lc[0].content
        assert "read_content_range" in lc[0].content and "grep_content" in lc[0].content
        assert lc[1].content == "small"
        assert [m.content.startswith(("r1", "r2")) and self.HANDLE in m.content for m in lc[2:]] == [True, True]
        assert messages == self._messages()

    def test_no_markers_by_default_or_without_handle(self):
        from autolangchat.graph.nodes.llm_call import _to_langchain_messages

        assert [m.content for m in _to_langchain_messages(self._messages())][:2] == [
            "big paste",
            "small",
        ]
        plain = [{"role": "user", "content": "hi", "metadata": {}}]
        assert _to_langchain_messages(plain, artifact_markers=True)[0].content == "hi"

    @pytest.mark.parametrize(
        "enabled,tools,supports,expected",
        [
            (True, ["read_content_range", "grep_content"], True, True),
            (False, ["read_content_range"], True, False),
            (True, ["other"], True, False),
            (True, [], True, False),
            (True, ["read_content_range"], False, False),
        ],
    )
    def test_markers_only_when_artifact_tools_are_bound(self, enabled, tools, supports, expected):
        from autolangchat.graph.nodes import llm_call

        config = SimpleNamespace(
            artifact_enabled=enabled,
            langchain_tools=[SimpleNamespace(name=n) for n in tools],
        )
        with patch.object(llm_call, "supports_tool_calling", return_value=supports):
            assert llm_call._artifact_tools_bound(config, "model") is expected


@pytest.mark.asyncio
async def test_model_reads_archived_paste_using_only_the_handle_marker():
    from autolangchat.graph.tools.artifact_tools import build_artifact_tools
    from autolangchat.graph.tools.manager import ToolManager

    store, scope = _store()
    config = ChatConfig().model_copy(update={"context_budget_safety_fraction": 0, "enable_ai_summarization": False})
    manager = ToolManager(generated_tools={}, config=config, internal_tools=build_artifact_tools())
    paste = "\n".join(f"line {i} " + ("ERROR code=XYZ-42 disk full" if i == 7 else "ok") for i in range(1, 21))
    seen = []

    async def invoke(messages, *args, **kwargs):
        seen.append(messages)
        if len(seen) == 1:
            marker = re.search(r"artifact handle ([0-9a-f-]{36})", messages[-1].content)
            assert marker, "the model was never told the handle"
            call = {"id": "call_1", "name": "grep_content", "args": {"handle": marker.group(1), "pattern": "ERROR"}}
            return AIMessage(content="", tool_calls=[call])
        return AIMessage(content="found it")

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=invoke)
    runnable = {
        "configurable": {
            "thread_id": "thread",
            "chat_config": config,
            "tool_manager": manager,
            "artifact_store": store,
            "artifact_scope": scope,
        }
    }
    with patch("autolangchat.graph.nodes.llm_call._build_llm", return_value=llm):
        result = await build_chat_graph(config, tool_manager=manager).ainvoke({"user_message": paste}, config=runnable)

    assert result["messages"][-1]["content"] == "found it"
    tool_message = next(m for m in result["messages"] if m["role"] == "tool")
    assert "7: line 7 ERROR code=XYZ-42 disk full" in json.dumps(tool_message["tool_results"][0]["result"])
    assert paste in next(m for m in result["messages"] if m["role"] == "user")["content"]
