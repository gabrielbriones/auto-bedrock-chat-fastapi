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
from datetime import datetime, timezone

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
# Postgres store (faked async connection layer)
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Async cursor mapping ``INSERT ... ON CONFLICT DO NOTHING`` against a dict.

    Mirrors real Postgres semantics: inserting an ``id`` that already exists
    in ``rows`` is a no-op (the existing row's values are preserved).
    """

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params):
        assert "%s" in sql
        turn_id = str(params[0])
        if turn_id not in self._rows:
            self._rows[turn_id] = tuple(params)


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
