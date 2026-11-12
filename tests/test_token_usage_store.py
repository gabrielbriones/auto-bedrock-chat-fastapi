"""Store-level unit tests for the token-usage backend.

Covers the SQLite ``TokenUsageStore``:

  (a) ``record_turn`` inserts a row that can be read back exactly;
  (b) a duplicate ``turn_id`` is silently ignored (idempotency) rather than
      raising or double-counting;
  (e) ``cache_read_tokens``/``cache_write_tokens`` are persisted (default 0),
      summed by the aggregate queries, and added to a pre-existing table
      that lacks them by the idempotent migration run on ``open()``.

And the Postgres ``TokenUsageStore`` (faked async connection layer, no live
server in CI, mirroring the ``test_feedback_store_delete`` approach):

  (c) ``record_turn`` inserts a row via ``INSERT ... ON CONFLICT DO NOTHING``;
  (d) a duplicate ``turn_id`` does not overwrite the existing row;
  (f) the cache-token columns round-trip through every query, and the
      schema DDL carries ``ADD COLUMN IF NOT EXISTS`` migrations for them.

Mirrors the ``load_module``-based import pattern used by
``tests/test_feedback_store_delete.py`` so these tests don't require the
heavy ``autolangchat`` package (and its ``langchain``/``langchain-aws``
dependencies) to be importable.
"""

import sqlite3
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
    modules collected later.
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
        "SELECT id, session_id, user_id, model_id, input_tokens, output_tokens, turn_ts,"
        " cache_read_tokens, cache_write_tokens FROM token_usage"
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
                0,
                0,
            )
        ]
    finally:
        await store.close()


async def test_record_turn_persists_cache_token_counts():
    store = await _open_sqlite_store()
    try:
        await store.record_turn(
            turn_id="turn-1",
            session_id="sess-1",
            user_id="alice",
            model_id="model-a",
            input_tokens=1000,
            output_tokens=50,
            turn_ts=datetime(2026, 7, 8, tzinfo=timezone.utc),
            cache_read_tokens=800,
            cache_write_tokens=150,
        )

        rows = _select_all(store)
        assert rows[0][7:] == (800, 150)
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
            cache_read_tokens=4,
            cache_write_tokens=2,
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
            cache_read_tokens=999,
            cache_write_tokens=999,
        )

        rows = _select_all(store)
        assert len(rows) == 1
        assert rows[0][4] == 10
        assert rows[0][5] == 20
        assert rows[0][7:] == (4, 2)
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# SQLite store — cache-token column migration
# ---------------------------------------------------------------------------

# ``token_usage`` DDL as shipped before the cache-token columns existed.
_LEGACY_SQLITE_DDL = """
    CREATE TABLE token_usage (
        id            TEXT PRIMARY KEY,
        session_id    TEXT NOT NULL,
        user_id       TEXT,
        model_id      TEXT NOT NULL,
        input_tokens  INTEGER NOT NULL CHECK (input_tokens >= 0),
        output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
        turn_ts       TEXT NOT NULL
    );
    INSERT INTO token_usage VALUES
        ('legacy-1', 'sess-old', 'alice', 'model-a', 70, 30, '2026-06-30T08:00:00+00:00');
"""


def _columns(conn):
    return [row[1] for row in conn.execute("PRAGMA table_info(token_usage)")]


async def _open_legacy_sqlite_store(monkeypatch, init_schema=True):
    """Open a store whose ``sqlite3.connect`` returns an in-memory DB that
    already holds a legacy ``token_usage`` table (no cache-token columns)."""
    legacy_conn = sqlite3.connect(":memory:", check_same_thread=False)
    legacy_conn.executescript(_LEGACY_SQLITE_DDL)
    legacy_conn.commit()
    monkeypatch.setattr(token_usage_sqlite_mod.sqlite3, "connect", lambda *args, **kwargs: legacy_conn)
    store = SQLiteTokenUsageStore(":memory:", init_schema=init_schema)
    await store.open()
    return store


@pytest.mark.parametrize("init_schema", [True, False])
async def test_open_migrates_legacy_table_missing_cache_columns(monkeypatch, init_schema):
    store = await _open_legacy_sqlite_store(monkeypatch, init_schema=init_schema)
    try:
        assert {"cache_read_tokens", "cache_write_tokens"} <= set(_columns(store._conn))
        # Pre-existing rows pick up the column default.
        assert _select_all(store) == [
            ("legacy-1", "sess-old", "alice", "model-a", 70, 30, "2026-06-30T08:00:00+00:00", 0, 0)
        ]

        await store.record_turn(
            "t-new",
            "sess-new",
            "alice",
            "model-a",
            100,
            10,
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            cache_read_tokens=60,
            cache_write_tokens=20,
        )

        assert await store.aggregate_by_model() == [
            {
                "model_id": "model-a",
                "input_tokens": 170,
                "output_tokens": 40,
                "cache_read_tokens": 60,
                "cache_write_tokens": 20,
                "turn_count": 2,
            }
        ]
        rows = await store.list_by_user("alice")
        assert [(r["cache_read_tokens"], r["cache_write_tokens"]) for r in rows] == [(60, 20), (0, 0)]
    finally:
        await store.close()


async def test_column_migration_is_idempotent(monkeypatch):
    store = await _open_legacy_sqlite_store(monkeypatch)
    try:
        before = _columns(store._conn)
        # Re-running against an already-migrated table must be a no-op
        # rather than raising "duplicate column name".
        SQLiteTokenUsageStore._migrate_columns_locked(store._conn)
        assert _columns(store._conn) == before
        assert before.count("cache_read_tokens") == 1
        assert before.count("cache_write_tokens") == 1
    finally:
        await store.close()


async def test_column_migration_skips_missing_table():
    """``init_schema=False`` on a fresh DB: no table yet, nothing to migrate."""
    store = SQLiteTokenUsageStore(":memory:", init_schema=False)
    await store.open()
    try:
        assert _columns(store._conn) == []
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# SQLite store — query methods
# ---------------------------------------------------------------------------


async def _seed(store):
    ts1 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)
    ts3 = datetime(2026, 7, 2, 9, 0, 0, tzinfo=timezone.utc)
    await store.record_turn("t1", "sess-1", "alice", "model-a", 10, 20, ts1, cache_read_tokens=4, cache_write_tokens=2)
    # No cache counts supplied — both default to 0.
    await store.record_turn("t2", "sess-1", "alice", "model-b", 5, 5, ts2)
    await store.record_turn(
        "t3", "sess-2", "bob", "model-a", 100, 200, ts3, cache_read_tokens=60, cache_write_tokens=30
    )
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
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "turn_ts": "2026-07-01T14:00:00+00:00",
            },
            {
                "session_id": "sess-1",
                "model_id": "model-a",
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_tokens": 4,
                "cache_write_tokens": 2,
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
            {
                "model_id": "model-a",
                "input_tokens": 110,
                "output_tokens": 220,
                "cache_read_tokens": 64,
                "cache_write_tokens": 32,
                "turn_count": 2,
            },
            {
                "model_id": "model-b",
                "input_tokens": 5,
                "output_tokens": 5,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "turn_count": 1,
            },
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
            {
                "date": "2026-07-01",
                "input_tokens": 15,
                "output_tokens": 25,
                "cache_read_tokens": 4,
                "cache_write_tokens": 2,
                "turn_count": 2,
            },
            {
                "date": "2026-07-02",
                "input_tokens": 100,
                "output_tokens": 200,
                "cache_read_tokens": 60,
                "cache_write_tokens": 30,
                "turn_count": 1,
            },
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
        assert rows == [
            {
                "date": "2026-07-01",
                "input_tokens": 15,
                "output_tokens": 25,
                "cache_read_tokens": 4,
                "cache_write_tokens": 2,
                "turn_count": 2,
            }
        ]
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
            {
                "user_id": "bob",
                "input_tokens": 100,
                "output_tokens": 200,
                "cache_read_tokens": 60,
                "cache_write_tokens": 30,
            },
            {
                "user_id": "alice",
                "input_tokens": 15,
                "output_tokens": 25,
                "cache_read_tokens": 4,
                "cache_write_tokens": 2,
            },
        ]
    finally:
        await store.close()


async def test_aggregate_by_user_respects_limit():
    store = await _open_sqlite_store()
    try:
        await _seed(store)
        rows = await store.aggregate_by_user(limit=1)
        assert rows == [
            {
                "user_id": "bob",
                "input_tokens": 100,
                "output_tokens": 200,
                "cache_read_tokens": 60,
                "cache_write_tokens": 30,
            }
        ]
    finally:
        await store.close()


async def test_aggregate_by_user_breaks_ties_by_user_id_ascending():
    """When two users have the same combined token total, ordering must be
    deterministic (user_id ASC) rather than left to the DB engine's
    unspecified tie behavior."""
    store = await _open_sqlite_store()
    try:
        ts = datetime(2026, 7, 1, tzinfo=timezone.utc)
        await store.record_turn("t1", "sess-1", "carol", "model-a", 5, 5, ts)
        await store.record_turn("t2", "sess-2", "alice", "model-a", 5, 5, ts)
        await store.record_turn("t3", "sess-3", "bob", "model-a", 5, 5, ts)

        rows = await store.aggregate_by_user()

        assert [r["user_id"] for r in rows] == ["alice", "bob", "carol"]
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

    ``full_row_tuple`` order matches the store's INSERT column list:
    ``(id, session_id, user_id, model_id, input_tokens, output_tokens, turn_ts,
    cache_read_tokens, cache_write_tokens)``.

    Supports:

    * ``INSERT ... ON CONFLICT DO NOTHING`` — a no-op when ``id`` already
      exists in ``rows`` (mirrors real Postgres semantics).
    * The four read-only queries used by ``list_by_user``,
      ``aggregate_by_model``, ``aggregate_by_day``, and ``aggregate_by_user``,
      distinguished by a short substring unique to each query in the store's
      SQL text (rather than a real SQL parser).
    """

    def __init__(self, rows, executed_ddl=None):
        self._rows = rows
        self._executed_ddl = executed_ddl if executed_ddl is not None else []
        self._result = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        if "CREATE TABLE IF NOT EXISTS token_usage" in sql:
            self._executed_ddl.append(sql)
            return

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
            self._result = [(v[1], v[3], v[4], v[5], v[7], v[8], v[6]) for v in page]
            return

        if "GROUP BY model_id" in sql:
            by_model = {}
            for v in values:
                model_id = v[3]
                agg = by_model.setdefault(model_id, [0, 0, 0, 0, 0])
                agg[0] += v[4]
                agg[1] += v[5]
                agg[2] += v[7]
                agg[3] += v[8]
                agg[4] += 1
            self._result = [(model_id, *agg) for model_id, agg in sorted(by_model.items())]
            return

        if "GROUP BY day" in sql:
            start, end = params
            by_day = {}
            for v in values:
                turn_ts = v[6]
                if not (start <= turn_ts < end):
                    continue
                # Force UTC before bucketing, matching the real SQL's
                # ``turn_ts AT TIME ZONE 'UTC'`` — otherwise a non-UTC-tz
                # turn_ts would bucket by the wrong calendar day here while
                # the real store still buckets correctly, masking a bug.
                day = turn_ts.astimezone(timezone.utc).date() if hasattr(turn_ts, "astimezone") else turn_ts
                agg = by_day.setdefault(day, [0, 0, 0, 0, 0])
                agg[0] += v[4]
                agg[1] += v[5]
                agg[2] += v[7]
                agg[3] += v[8]
                agg[4] += 1
            self._result = [(day, *agg) for day, agg in sorted(by_day.items())]
            return

        if "GROUP BY user_id" in sql:
            (limit,) = params
            by_user = {}
            for v in values:
                user_id = v[2]
                if user_id is None:
                    continue
                agg = by_user.setdefault(user_id, [0, 0, 0, 0])
                agg[0] += v[4]
                agg[1] += v[5]
                agg[2] += v[7]
                agg[3] += v[8]
            # Emulate ORDER BY (combined) DESC, user_id ASC: sort by the
            # ascending tie-breaker first, then stable-sort by the
            # descending primary key.
            ranked = sorted(by_user.items(), key=lambda kv: kv[0])
            ranked.sort(key=lambda kv: kv[1][0] + kv[1][1], reverse=True)
            self._result = [(user_id, *agg) for user_id, agg in ranked[:limit]]
            return

        raise AssertionError(f"unrecognized query in fake cursor: {sql!r}")

    async def fetchall(self):
        return self._result


class _FakeConnection:
    def __init__(self, rows, executed_ddl):
        self._rows = rows
        self._executed_ddl = executed_ddl
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._rows, self._executed_ddl)

    async def commit(self):
        self.commits += 1


class _FakePool:
    """Stateful stand-in for ``psycopg_pool.AsyncConnectionPool``."""

    def __init__(self):
        self.rows = {}
        self.executed_ddl = []
        self.last_connection = None

    def connection(self):
        conn = _FakeConnection(self.rows, self.executed_ddl)
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
        0,
        0,
    )
    assert store._pool.last_connection.commits == 1


async def test_postgres_record_turn_persists_cache_token_counts():
    store = _make_postgres_store()
    ts = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)

    await store.record_turn(
        turn_id="turn-1",
        session_id="sess-1",
        user_id="alice",
        model_id="model-a",
        input_tokens=1000,
        output_tokens=50,
        turn_ts=ts,
        cache_read_tokens=800,
        cache_write_tokens=150,
    )

    assert store._pool.rows["turn-1"][7:] == (800, 150)


async def test_postgres_apply_schema_migrates_cache_token_columns():
    """Existing deployments already have ``token_usage``; the DDL applied on
    ``open()`` must idempotently add the cache-token columns to it."""
    store = _make_postgres_store()

    await store._apply_schema()

    (ddl,) = store._pool.executed_ddl
    normalized = " ".join(ddl.split())
    for column in ("cache_read_tokens", "cache_write_tokens"):
        assert f"ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS {column} INTEGER NOT NULL DEFAULT 0" in normalized
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
        cache_read_tokens=4,
        cache_write_tokens=2,
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
        cache_read_tokens=999,
        cache_write_tokens=999,
    )

    row = store._pool.rows["turn-1"]
    assert row[4] == 10
    assert row[5] == 20
    assert row[7:] == (4, 2)


# ---------------------------------------------------------------------------
# Postgres store — query methods
# ---------------------------------------------------------------------------


async def _seed_postgres(store):
    ts1 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)
    ts3 = datetime(2026, 7, 2, 9, 0, 0, tzinfo=timezone.utc)
    await store.record_turn("t1", "sess-1", "alice", "model-a", 10, 20, ts1, cache_read_tokens=4, cache_write_tokens=2)
    # No cache counts supplied — both default to 0.
    await store.record_turn("t2", "sess-1", "alice", "model-b", 5, 5, ts2)
    await store.record_turn(
        "t3", "sess-2", "bob", "model-a", 100, 200, ts3, cache_read_tokens=60, cache_write_tokens=30
    )


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
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "turn_ts": "2026-07-01T14:00:00+00:00",
        },
        {
            "session_id": "sess-1",
            "model_id": "model-a",
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_tokens": 4,
            "cache_write_tokens": 2,
            "turn_ts": "2026-07-01T10:00:00+00:00",
        },
    ]


async def test_postgres_list_by_user_rejects_invalid_pagination():
    store = _make_postgres_store()
    with pytest.raises(ValueError):
        await store.list_by_user("alice", limit=0)
    with pytest.raises(ValueError):
        await store.list_by_user("alice", offset=-1)


async def test_postgres_list_by_user_respects_limit_and_offset():
    store = _make_postgres_store()
    await _seed_postgres(store)

    first_page = await store.list_by_user("alice", limit=1, offset=0)
    second_page = await store.list_by_user("alice", limit=1, offset=1)

    assert len(first_page) == 1
    assert len(second_page) == 1
    assert first_page[0]["model_id"] == "model-b"
    assert second_page[0]["model_id"] == "model-a"


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
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "turn_ts": "2026-07-01T10:00:00+00:00",
        }
    ]


async def test_postgres_aggregate_by_model_sums_and_counts_per_model():
    store = _make_postgres_store()
    await _seed_postgres(store)

    rows = await store.aggregate_by_model()

    assert rows == [
        {
            "model_id": "model-a",
            "input_tokens": 110,
            "output_tokens": 220,
            "cache_read_tokens": 64,
            "cache_write_tokens": 32,
            "turn_count": 2,
        },
        {
            "model_id": "model-b",
            "input_tokens": 5,
            "output_tokens": 5,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "turn_count": 1,
        },
    ]


async def test_postgres_aggregate_by_day_buckets_by_date():
    store = _make_postgres_store()
    await _seed_postgres(store)

    rows = await store.aggregate_by_day(
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    assert rows == [
        {
            "date": "2026-07-01",
            "input_tokens": 15,
            "output_tokens": 25,
            "cache_read_tokens": 4,
            "cache_write_tokens": 2,
            "turn_count": 2,
        },
        {
            "date": "2026-07-02",
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_tokens": 60,
            "cache_write_tokens": 30,
            "turn_count": 1,
        },
    ]


async def test_postgres_aggregate_by_day_rejects_end_not_after_start():
    store = _make_postgres_store()
    ts = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        await store.aggregate_by_day(ts, ts)


async def test_postgres_aggregate_by_day_excludes_rows_outside_range():
    store = _make_postgres_store()
    await _seed_postgres(store)

    rows = await store.aggregate_by_day(
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    assert rows == [
        {
            "date": "2026-07-01",
            "input_tokens": 15,
            "output_tokens": 25,
            "cache_read_tokens": 4,
            "cache_write_tokens": 2,
            "turn_count": 2,
        }
    ]


async def test_postgres_aggregate_by_day_buckets_by_utc_date_not_local():
    """A turn_ts whose *local* calendar date differs from its *UTC*
    calendar date must bucket under the UTC date, matching the real SQL's
    ``turn_ts AT TIME ZONE 'UTC'`` — not the tz the datetime happens to
    carry."""
    store = _make_postgres_store()
    # 2026-07-02T01:00:00+02:00 == 2026-07-01T23:00:00 UTC: local date is
    # July 2nd, UTC date is July 1st.
    non_utc_tz = timezone(timedelta(hours=2))
    await store.record_turn(
        "t1", "sess-1", "alice", "model-a", 10, 20, datetime(2026, 7, 2, 1, 0, 0, tzinfo=non_utc_tz)
    )

    rows = await store.aggregate_by_day(
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    assert rows == [
        {
            "date": "2026-07-01",
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "turn_count": 1,
        }
    ]


async def test_postgres_aggregate_by_user_ranks_by_combined_tokens_desc():
    store = _make_postgres_store()
    await _seed_postgres(store)

    rows = await store.aggregate_by_user()

    assert rows == [
        {
            "user_id": "bob",
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_tokens": 60,
            "cache_write_tokens": 30,
        },
        {"user_id": "alice", "input_tokens": 15, "output_tokens": 25, "cache_read_tokens": 4, "cache_write_tokens": 2},
    ]


async def test_postgres_aggregate_by_user_respects_limit():
    store = _make_postgres_store()
    await _seed_postgres(store)

    rows = await store.aggregate_by_user(limit=1)

    assert rows == [
        {"user_id": "bob", "input_tokens": 100, "output_tokens": 200, "cache_read_tokens": 60, "cache_write_tokens": 30}
    ]


async def test_postgres_aggregate_by_user_breaks_ties_by_user_id_ascending():
    store = _make_postgres_store()
    ts = datetime(2026, 7, 1, tzinfo=timezone.utc)
    await store.record_turn("t1", "sess-1", "carol", "model-a", 5, 5, ts)
    await store.record_turn("t2", "sess-2", "alice", "model-a", 5, 5, ts)
    await store.record_turn("t3", "sess-3", "bob", "model-a", 5, 5, ts)

    rows = await store.aggregate_by_user()

    assert [r["user_id"] for r in rows] == ["alice", "bob", "carol"]


async def test_postgres_aggregate_by_user_rejects_non_positive_limit():
    store = _make_postgres_store()
    with pytest.raises(ValueError):
        await store.aggregate_by_user(limit=0)


async def test_postgres_aggregate_by_user_excludes_anonymous_rows():
    store = _make_postgres_store()
    await store.record_turn("t-anon", "sess-3", None, "model-a", 500, 500, datetime(2026, 7, 3, tzinfo=timezone.utc))
    await _seed_postgres(store)

    rows = await store.aggregate_by_user()

    assert all(r["user_id"] is not None for r in rows)
    assert {r["user_id"] for r in rows} == {"alice", "bob"}
