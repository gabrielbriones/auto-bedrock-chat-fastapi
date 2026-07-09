"""Store-level unit tests for the token-usage backend (XMGPLAT-10746).

Covers the SQLite ``TokenUsageStore``:

  (a) ``record_turn`` inserts a row that can be read back exactly;
  (b) a duplicate ``turn_id`` is silently ignored (idempotency) rather than
      raising or double-counting.

And the Postgres ``TokenUsageStore`` (faked async connection layer, no live
server in CI, mirroring the ``test_feedback_store_delete`` approach):

  (c) ``record_turn`` inserts a row via ``INSERT ... ON CONFLICT DO NOTHING``;
  (d) a duplicate ``turn_id`` does not overwrite the existing row.

Mirrors the ``load_module``-based import pattern used by
``tests/test_feedback_store_delete.py`` so these tests don't require the
heavy ``autolangchat`` package (and its ``langchain``/``langchain-aws``
dependencies) to be importable.
"""

import sys
from datetime import datetime, timedelta, timezone

import pytest

from ._autolangchat_imports import install_package_stubs, load_module

token_usage_base_mod = load_module(
    "autolangchat.db.token_usage_base",
    "db/token_usage_base.py",
)
token_usage_sqlite_mod = load_module(
    "autolangchat.db.token_usage_sqlite",
    "db/token_usage_sqlite.py",
    extra_modules={
        "autolangchat.db.token_usage_base": token_usage_base_mod,
    },
)
token_usage_postgres_mod = load_module(
    "autolangchat.db.token_usage_postgres",
    "db/token_usage_postgres.py",
    extra_modules={
        "autolangchat.db.token_usage_base": token_usage_base_mod,
    },
)

SQLiteTokenUsageStore = token_usage_sqlite_mod.SQLiteTokenUsageStore
PostgresTokenUsageStore = token_usage_postgres_mod.PostgresTokenUsageStore


@pytest.fixture(autouse=True)
def _package_stubs():
    """Install lightweight ``autolangchat`` package stubs only while this
    module's tests run.

    The SQLite store resolves its schema DDL at runtime via
    ``importlib.resources.files("autolangchat.db.sql")``, which requires
    ``autolangchat.db`` to be importable. Installing path-only stubs lets
    that lookup resolve the lightweight ``autolangchat.db.sql`` subpackage
    without importing the heavy real ``autolangchat.db`` package.

    Scoping the stubs to test execution (rather than installing them at
    module import time) keeps them out of ``sys.modules`` during
    collection, so they cannot shadow the real package for other test
    modules collected later (see XMGPLAT-10766).
    """
    stubs = install_package_stubs()
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        yield
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


async def _open_sqlite_store():
    store = SQLiteTokenUsageStore(":memory:")
    await store.open()
    return store


def _select_all(store):
    return store._conn.execute(
        "SELECT id, session_id, user_id, model_id, input_tokens, output_tokens, turn_ts FROM token_usage"
    ).fetchall()


async def test_record_turn_inserts_a_row():
    store = await _open_sqlite_store()
    try:
        ts = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
        await store.record_turn(
            turn_id="turn-1",
            session_id="sess-1",
            user_id="alice",
            model_id="us.anthropic.claude-sonnet-4-6",
            input_tokens=120,
            output_tokens=240,
            turn_ts=ts,
        )

        rows = _select_all(store)
        assert rows == [
            (
                "turn-1",
                "sess-1",
                "alice",
                "us.anthropic.claude-sonnet-4-6",
                120,
                240,
                "2026-07-08T12:00:00+00:00",
            )
        ]
    finally:
        await store.close()


async def test_record_turn_allows_null_user_id():
    """``user_id`` is optional (anonymous sessions have none)."""
    store = await _open_sqlite_store()
    try:
        await store.record_turn(
            turn_id="turn-anon",
            session_id="sess-1",
            user_id=None,
            model_id="us.anthropic.claude-sonnet-4-6",
            input_tokens=10,
            output_tokens=20,
            turn_ts=datetime.now(timezone.utc),
        )

        rows = _select_all(store)
        assert len(rows) == 1
        assert rows[0][2] is None
    finally:
        await store.close()


async def test_duplicate_turn_id_is_silently_ignored():
    store = await _open_sqlite_store()
    try:
        ts = datetime.now(timezone.utc)
        await store.record_turn(
            turn_id="turn-1",
            session_id="sess-1",
            user_id="alice",
            model_id="model-a",
            input_tokens=10,
            output_tokens=20,
            turn_ts=ts,
        )
        # Same turn_id, different (bogus) counts — must be ignored, not
        # overwritten and not raise a duplicate-key error.
        await store.record_turn(
            turn_id="turn-1",
            session_id="sess-1",
            user_id="alice",
            model_id="model-a",
            input_tokens=999,
            output_tokens=999,
            turn_ts=ts,
        )

        rows = _select_all(store)
        assert len(rows) == 1
        assert rows[0][4] == 10
        assert rows[0][5] == 20
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# SQLite store — query methods (XMGPLAT-10748)
# ---------------------------------------------------------------------------


async def _seed(store):
    ts1 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)
    ts3 = datetime(2026, 7, 2, 9, 0, 0, tzinfo=timezone.utc)
    await store.record_turn("t1", "sess-1", "alice", "model-a", 10, 20, ts1)
    await store.record_turn("t2", "sess-1", "alice", "model-b", 5, 5, ts2)
    await store.record_turn("t3", "sess-2", "bob", "model-a", 100, 200, ts3)
    return ts1, ts2, ts3


async def test_list_by_user_returns_rows_newest_first():
    store = await _open_sqlite_store()
    try:
        await _seed(store)
        rows = await store.list_by_user("alice")
        assert rows == [
            {
                "session_id": "sess-1",
                "model_id": "model-b",
                "input_tokens": 5,
                "output_tokens": 5,
                "turn_ts": "2026-07-01T14:00:00+00:00",
            },
            {
                "session_id": "sess-1",
                "model_id": "model-a",
                "input_tokens": 10,
                "output_tokens": 20,
                "turn_ts": "2026-07-01T10:00:00+00:00",
            },
        ]
    finally:
        await store.close()


async def test_list_by_user_respects_limit_and_offset():
    store = await _open_sqlite_store()
    try:
        await _seed(store)
        first_page = await store.list_by_user("alice", limit=1, offset=0)
        second_page = await store.list_by_user("alice", limit=1, offset=1)
        assert len(first_page) == 1
        assert len(second_page) == 1
        assert first_page[0]["model_id"] == "model-b"
        assert second_page[0]["model_id"] == "model-a"
    finally:
        await store.close()


async def test_list_by_user_rejects_invalid_pagination():
    store = await _open_sqlite_store()
    try:
        with pytest.raises(ValueError):
            await store.list_by_user("alice", limit=0)
        with pytest.raises(ValueError):
            await store.list_by_user("alice", offset=-1)
    finally:
        await store.close()


async def test_list_by_user_returns_empty_for_unknown_user():
    store = await _open_sqlite_store()
    try:
        await _seed(store)
        assert await store.list_by_user("nobody") == []
    finally:
        await store.close()


async def test_aggregate_by_model_sums_and_counts_per_model():
    store = await _open_sqlite_store()
    try:
        await _seed(store)
        rows = await store.aggregate_by_model()
        assert rows == [
            {"model_id": "model-a", "input_tokens": 110, "output_tokens": 220, "turn_count": 2},
            {"model_id": "model-b", "input_tokens": 5, "output_tokens": 5, "turn_count": 1},
        ]
    finally:
        await store.close()


async def test_aggregate_by_day_buckets_by_utc_date():
    store = await _open_sqlite_store()
    try:
        await _seed(store)
        rows = await store.aggregate_by_day(
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 3, tzinfo=timezone.utc),
        )
        assert rows == [
            {"date": "2026-07-01", "input_tokens": 15, "output_tokens": 25, "turn_count": 2},
            {"date": "2026-07-02", "input_tokens": 100, "output_tokens": 200, "turn_count": 1},
        ]
    finally:
        await store.close()


async def test_aggregate_by_day_excludes_rows_outside_range():
    store = await _open_sqlite_store()
    try:
        await _seed(store)
        rows = await store.aggregate_by_day(
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 2, tzinfo=timezone.utc),
        )
        assert rows == [{"date": "2026-07-01", "input_tokens": 15, "output_tokens": 25, "turn_count": 2}]
    finally:
        await store.close()


async def test_aggregate_by_day_rejects_end_not_after_start():
    store = await _open_sqlite_store()
    try:
        ts = datetime(2026, 7, 1, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            await store.aggregate_by_day(ts, ts)
    finally:
        await store.close()


async def test_aggregate_by_user_ranks_by_combined_tokens_desc():
    store = await _open_sqlite_store()
    try:
        await _seed(store)
        # alice: 15 input + 25 output = 40 combined; bob: 100 + 200 = 300 combined.
        rows = await store.aggregate_by_user()
        assert rows == [
            {"user_id": "bob", "input_tokens": 100, "output_tokens": 200},
            {"user_id": "alice", "input_tokens": 15, "output_tokens": 25},
        ]
    finally:
        await store.close()


async def test_aggregate_by_user_respects_limit():
    store = await _open_sqlite_store()
    try:
        await _seed(store)
        rows = await store.aggregate_by_user(limit=1)
        assert rows == [{"user_id": "bob", "input_tokens": 100, "output_tokens": 200}]
    finally:
        await store.close()


async def test_aggregate_by_user_excludes_anonymous_rows():
    store = await _open_sqlite_store()
    try:
        await store.record_turn(
            "t-anon", "sess-3", None, "model-a", 500, 500, datetime(2026, 7, 3, tzinfo=timezone.utc)
        )
        await _seed(store)
        rows = await store.aggregate_by_user()
        assert all(r["user_id"] is not None for r in rows)
        assert {r["user_id"] for r in rows} == {"alice", "bob"}
    finally:
        await store.close()


async def test_aggregate_by_user_rejects_non_positive_limit():
    store = await _open_sqlite_store()
    try:
        with pytest.raises(ValueError):
            await store.aggregate_by_user(limit=0)
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Postgres store (faked async connection layer)
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Async cursor faking enough Postgres semantics to exercise the store's
    SQL against an in-memory ``{turn_id: full_row_tuple}`` dict.

    ``full_row_tuple`` order matches the schema:
    ``(id, session_id, user_id, model_id, input_tokens, output_tokens, turn_ts)``.

    Supports:

    * ``INSERT ... ON CONFLICT DO NOTHING`` — a no-op when ``id`` already
      exists in ``rows`` (mirrors real Postgres semantics).
    * The four read-only queries used by ``list_by_user``,
      ``aggregate_by_model``, ``aggregate_by_day``, and ``aggregate_by_user``,
      distinguished by a short substring unique to each query in the store's
      SQL text (rather than a real SQL parser).
    """

    def __init__(self, rows):
        self._rows = rows
        self._result = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        if "INSERT INTO token_usage" in sql:
            turn_id = str(params[0])
            if turn_id not in self._rows:
                self._rows[turn_id] = tuple(params)
            return

        values = list(self._rows.values())

        if "WHERE user_id = %s" in sql:
            user_id, limit, offset = params
            matched = [v for v in values if v[2] == user_id]
            # Emulate ORDER BY turn_ts DESC, id ASC: sort by the ascending
            # secondary key first, then stable-sort by the descending
            # primary key so ties on turn_ts keep id ascending (Python's
            # sort is stable, so the first sort's order is preserved for
            # ties in the second).
            matched.sort(key=lambda v: v[0])
            matched.sort(key=lambda v: v[6], reverse=True)
            page = matched[offset : offset + limit]
            self._result = [(v[1], v[3], v[4], v[5], v[6]) for v in page]
            return

        if "GROUP BY model_id" in sql:
            by_model = {}
            for v in values:
                model_id, input_tokens, output_tokens = v[3], v[4], v[5]
                agg = by_model.setdefault(model_id, [0, 0, 0])
                agg[0] += input_tokens
                agg[1] += output_tokens
                agg[2] += 1
            self._result = [(model_id, *agg) for model_id, agg in sorted(by_model.items())]
            return

        if "GROUP BY day" in sql:
            start, end = params
            by_day = {}
            for v in values:
                turn_ts, input_tokens, output_tokens = v[6], v[4], v[5]
                if not (start <= turn_ts < end):
                    continue
                day = turn_ts.date() if hasattr(turn_ts, "date") else turn_ts
                agg = by_day.setdefault(day, [0, 0, 0])
                agg[0] += input_tokens
                agg[1] += output_tokens
                agg[2] += 1
            self._result = [(day, *agg) for day, agg in sorted(by_day.items())]
            return

        if "GROUP BY user_id" in sql:
            (limit,) = params
            by_user = {}
            for v in values:
                user_id, input_tokens, output_tokens = v[2], v[4], v[5]
                if user_id is None:
                    continue
                agg = by_user.setdefault(user_id, [0, 0])
                agg[0] += input_tokens
                agg[1] += output_tokens
            ranked = sorted(by_user.items(), key=lambda kv: kv[1][0] + kv[1][1], reverse=True)
            self._result = [(user_id, *agg) for user_id, agg in ranked[:limit]]
            return

        raise AssertionError(f"unrecognized query in fake cursor: {sql!r}")

    async def fetchall(self):
        return self._result


class _FakeConnection:
    def __init__(self, rows):
        self._rows = rows
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._rows)

    async def commit(self):
        self.commits += 1


class _FakePool:
    """Stateful stand-in for ``psycopg_pool.AsyncConnectionPool``."""

    def __init__(self):
        self.rows = {}
        self.last_connection = None

    def connection(self):
        conn = _FakeConnection(self.rows)
        self.last_connection = conn
        return conn


def _make_postgres_store():
    # Bypass __init__ so we don't require psycopg or a live server; inject
    # the fake pool that ``record_turn`` interacts with directly.
    store = PostgresTokenUsageStore.__new__(PostgresTokenUsageStore)
    store._pool = _FakePool()
    return store


async def test_postgres_record_turn_inserts_a_row():
    store = _make_postgres_store()
    ts = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)

    await store.record_turn(
        turn_id="turn-1",
        session_id="sess-1",
        user_id="alice",
        model_id="us.anthropic.claude-sonnet-4-6",
        input_tokens=120,
        output_tokens=240,
        turn_ts=ts,
    )

    assert store._pool.rows["turn-1"] == (
        "turn-1",
        "sess-1",
        "alice",
        "us.anthropic.claude-sonnet-4-6",
        120,
        240,
        ts,
    )
    assert store._pool.last_connection.commits == 1


async def test_postgres_duplicate_turn_id_does_not_overwrite():
    store = _make_postgres_store()
    ts = datetime.now(timezone.utc)

    await store.record_turn(
        turn_id="turn-1",
        session_id="sess-1",
        user_id="alice",
        model_id="model-a",
        input_tokens=10,
        output_tokens=20,
        turn_ts=ts,
    )
    # Same turn_id, different (bogus) counts — ON CONFLICT DO NOTHING must
    # leave the original row untouched.
    await store.record_turn(
        turn_id="turn-1",
        session_id="sess-1",
        user_id="alice",
        model_id="model-a",
        input_tokens=999,
        output_tokens=999,
        turn_ts=ts,
    )

    row = store._pool.rows["turn-1"]
    assert row[4] == 10
    assert row[5] == 20


# ---------------------------------------------------------------------------
# Postgres store — query methods (XMGPLAT-10748)
# ---------------------------------------------------------------------------


async def _seed_postgres(store):
    ts1 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)
    ts3 = datetime(2026, 7, 2, 9, 0, 0, tzinfo=timezone.utc)
    await store.record_turn("t1", "sess-1", "alice", "model-a", 10, 20, ts1)
    await store.record_turn("t2", "sess-1", "alice", "model-b", 5, 5, ts2)
    await store.record_turn("t3", "sess-2", "bob", "model-a", 100, 200, ts3)


async def test_postgres_list_by_user_returns_rows_newest_first():
    store = _make_postgres_store()
    await _seed_postgres(store)

    rows = await store.list_by_user("alice")

    assert rows == [
        {
            "session_id": "sess-1",
            "model_id": "model-b",
            "input_tokens": 5,
            "output_tokens": 5,
            "turn_ts": "2026-07-01T14:00:00+00:00",
        },
        {
            "session_id": "sess-1",
            "model_id": "model-a",
            "input_tokens": 10,
            "output_tokens": 20,
            "turn_ts": "2026-07-01T10:00:00+00:00",
        },
    ]


async def test_postgres_list_by_user_rejects_invalid_pagination():
    store = _make_postgres_store()
    with pytest.raises(ValueError):
        await store.list_by_user("alice", limit=0)
    with pytest.raises(ValueError):
        await store.list_by_user("alice", offset=-1)


async def test_postgres_list_by_user_normalizes_turn_ts_to_utc():
    """``turn_ts`` must always be reported in UTC regardless of the
    timezone offset the driver returns it in — the underlying instant is
    the same, but a non-UTC offset would break parity with the SQLite
    backend (always UTC TEXT) and the documented UTC-consistency contract."""
    store = _make_postgres_store()
    non_utc_tz = timezone(timedelta(hours=2))
    await store.record_turn(
        turn_id="t-tz",
        session_id="sess-1",
        user_id="alice",
        model_id="model-a",
        input_tokens=1,
        output_tokens=2,
        turn_ts=datetime(2026, 7, 1, 12, 0, 0, tzinfo=non_utc_tz),
    )

    rows = await store.list_by_user("alice")

    assert rows == [
        {
            "session_id": "sess-1",
            "model_id": "model-a",
            "input_tokens": 1,
            "output_tokens": 2,
            "turn_ts": "2026-07-01T10:00:00+00:00",
        }
    ]


async def test_postgres_aggregate_by_model_sums_and_counts_per_model():
    store = _make_postgres_store()
    await _seed_postgres(store)

    rows = await store.aggregate_by_model()

    assert rows == [
        {"model_id": "model-a", "input_tokens": 110, "output_tokens": 220, "turn_count": 2},
        {"model_id": "model-b", "input_tokens": 5, "output_tokens": 5, "turn_count": 1},
    ]


async def test_postgres_aggregate_by_day_buckets_by_date():
    store = _make_postgres_store()
    await _seed_postgres(store)

    rows = await store.aggregate_by_day(
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    assert rows == [
        {"date": "2026-07-01", "input_tokens": 15, "output_tokens": 25, "turn_count": 2},
        {"date": "2026-07-02", "input_tokens": 100, "output_tokens": 200, "turn_count": 1},
    ]


async def test_postgres_aggregate_by_day_rejects_end_not_after_start():
    store = _make_postgres_store()
    ts = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        await store.aggregate_by_day(ts, ts)


async def test_postgres_aggregate_by_user_ranks_by_combined_tokens_desc():
    store = _make_postgres_store()
    await _seed_postgres(store)

    rows = await store.aggregate_by_user()

    assert rows == [
        {"user_id": "bob", "input_tokens": 100, "output_tokens": 200},
        {"user_id": "alice", "input_tokens": 15, "output_tokens": 25},
    ]


async def test_postgres_aggregate_by_user_respects_limit():
    store = _make_postgres_store()
    await _seed_postgres(store)

    rows = await store.aggregate_by_user(limit=1)

    assert rows == [{"user_id": "bob", "input_tokens": 100, "output_tokens": 200}]


async def test_postgres_aggregate_by_user_rejects_non_positive_limit():
    store = _make_postgres_store()
    with pytest.raises(ValueError):
        await store.aggregate_by_user(limit=0)
