"""Store-level unit tests for ``delete`` (XMGPLAT-10684, AC-12).

Covers both the SQLite and Postgres feedback stores:

  (a) deleting an existing entry returns ``True``;
  (b) deleting a non-existent id returns ``False``;
  (c) the row is no longer retrievable via ``get()`` after deletion.

The SQLite cases run against a real in-memory database. The Postgres
store has no live server in CI, so its cursor/connection layer is faked
with an async, stateful in-memory id set that mirrors the ``rowcount``
contract the real driver provides.
"""

import sys
from uuid import uuid4

import pytest

from ._autolangchat_imports import install_package_stubs, load_module

# Keep the package stubs installed for the lifetime of this test module so
# ``importlib.resources`` can resolve ``autolangchat.db.sql`` when the SQLite
# store loads its schema DDL at runtime (load_module restores them otherwise).
sys.modules.update(install_package_stubs())

exceptions_mod = load_module("autolangchat.exceptions", "exceptions.py")
models_mod = load_module("autolangchat.models", "models.py")
feedback_base_mod = load_module(
    "autolangchat.db.feedback_base",
    "db/feedback_base.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
    },
)
feedback_sqlite_mod = load_module(
    "autolangchat.db.feedback_sqlite",
    "db/feedback_sqlite.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.db.feedback_base": feedback_base_mod,
    },
)
feedback_postgres_mod = load_module(
    "autolangchat.db.feedback_postgres",
    "db/feedback_postgres.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.db.feedback_base": feedback_base_mod,
    },
)

SQLiteFeedbackStore = feedback_sqlite_mod.SQLiteFeedbackStore
PostgresFeedbackStore = feedback_postgres_mod.PostgresFeedbackStore
FeedbackEntry = models_mod.FeedbackEntry
Rating = models_mod.Rating


def _make_entry(**kwargs):
    defaults = dict(
        session_id="sess-1",
        user_id="alice",
        query="what is the answer?",
        ai_response="42",
        rating=Rating.NEGATIVE,
        model_id="anthropic.claude-test",
    )
    defaults.update(kwargs)
    return FeedbackEntry(**defaults)


# ---------------------------------------------------------------------------
# SQLite store (real in-memory database)
# ---------------------------------------------------------------------------


async def _open_sqlite_store():
    store = SQLiteFeedbackStore(":memory:")
    await store.open()
    return store


async def test_sqlite_delete_existing_returns_true():
    store = await _open_sqlite_store()
    try:
        entry = await store.create(_make_entry())
        assert await store.delete(entry.id) is True
    finally:
        await store.close()


async def test_sqlite_delete_missing_returns_false():
    store = await _open_sqlite_store()
    try:
        assert await store.delete(uuid4()) is False
    finally:
        await store.close()


async def test_sqlite_delete_removes_row_from_get():
    store = await _open_sqlite_store()
    try:
        entry = await store.create(_make_entry())
        assert await store.get(entry.id) is not None

        assert await store.delete(entry.id) is True
        assert await store.get(entry.id) is None
        # A second delete of the now-absent row reports not-found.
        assert await store.delete(entry.id) is False
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Postgres store (faked async connection layer)
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Async cursor that mutates a shared in-memory id set on DELETE."""

    def __init__(self, ids):
        self._ids = ids
        self.rowcount = -1
        self.executed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params):
        self.executed.append((sql, params))
        # Mirror psycopg's ``%s`` placeholder + numeric ``rowcount`` contract.
        assert "%s" in sql
        target = str(params[0])
        if target in self._ids:
            self._ids.discard(target)
            self.rowcount = 1
        else:
            self.rowcount = 0


class _FakeConnection:
    def __init__(self, ids):
        self._ids = ids
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._ids)

    async def commit(self):
        self.commits += 1


class _FakePool:
    """Stateful stand-in for ``psycopg_pool.AsyncConnectionPool``."""

    def __init__(self, ids=None):
        self.ids = set(ids or [])
        self.last_connection = None

    def connection(self):
        conn = _FakeConnection(self.ids)
        self.last_connection = conn
        return conn


def _make_postgres_store(ids=None):
    # Bypass __init__ so we don't require psycopg or a live server; inject the
    # fake pool that ``delete`` interacts with directly.
    store = PostgresFeedbackStore.__new__(PostgresFeedbackStore)
    store._pool = _FakePool(ids=ids)
    return store


async def test_postgres_delete_existing_returns_true():
    target = uuid4()
    store = _make_postgres_store(ids=[str(target)])
    assert await store.delete(target) is True
    assert store._pool.last_connection.commits == 1


async def test_postgres_delete_missing_returns_false():
    store = _make_postgres_store(ids=[])
    assert await store.delete(uuid4()) is False


async def test_postgres_delete_removes_row():
    target = uuid4()
    store = _make_postgres_store(ids=[str(target)])

    assert await store.delete(target) is True
    # Row is gone: the id set no longer contains it and a repeat delete
    # reports not-found.
    assert str(target) not in store._pool.ids
    assert await store.delete(target) is False
