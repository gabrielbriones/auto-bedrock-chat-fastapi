"""Store-level unit tests for ``delete`` (XMGPLAT-10684, AC-12).

Covers both the SQLite and Postgres feedback stores:

  (a) deleting an existing entry returns ``True``;
  (b) deleting a non-existent id returns ``False``;
  (c) subsequent deletes report not-found (and SQLite ``get()`` returns ``None``).

The SQLite cases run against a real in-memory database. The Postgres
store has no live server in CI, so its cursor/connection layer is faked
with an async, stateful in-memory id set that mirrors the ``rowcount``
contract the real driver provides.
"""

import sys
from uuid import uuid4

import pytest

from ._autolangchat_imports import install_package_stubs, load_module

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
ReviewStatus = models_mod.ReviewStatus


@pytest.fixture(autouse=True)
def _package_stubs():
    """Install lightweight ``autolangchat`` package stubs only while this
    module's tests run.

    The SQLite store resolves its schema DDL at runtime via
    ``importlib.resources.files("autolangchat.db.sql")``, which requires
    ``autolangchat.db`` to be importable. Installing path-only stubs lets that
    lookup resolve the lightweight ``autolangchat.db.sql`` subpackage without
    importing the heavy real ``autolangchat.db`` package.

    Scoping the stubs to test execution (rather than installing them at module
    import time) keeps them out of ``sys.modules`` during collection, so they
    cannot shadow the real package for other test modules collected later
    (e.g. ``test_websocket_response_metadata`` importing
    ``AuthenticatedUserAuthorizer`` from ``autolangchat.db``).
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


async def test_sqlite_conditional_delete_matching_status_returns_true():
    store = await _open_sqlite_store()
    try:
        entry = await store.create(_make_entry())
        await store.update_review(entry.id, ReviewStatus.REJECTED, reviewer_id="admin", tags=[], comment=None)

        assert await store.delete(entry.id, expected_status=ReviewStatus.REJECTED) is True
        assert await store.get(entry.id) is None
    finally:
        await store.close()


async def test_sqlite_conditional_delete_mismatched_status_keeps_row():
    store = await _open_sqlite_store()
    try:
        # Entries default to ``pending_review`` — a conditional delete that
        # requires ``rejected`` must not remove the row (the TOCTOU guard).
        entry = await store.create(_make_entry())

        assert await store.delete(entry.id, expected_status=ReviewStatus.REJECTED) is False
        assert await store.get(entry.id) is not None
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Postgres store (faked async connection layer)
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Async cursor that mutates a shared in-memory id->status map on DELETE."""

    def __init__(self, rows):
        self._rows = rows
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
        # Conditional deletes append ``AND review_status = %s``; honour the
        # extra status predicate so the TOCTOU guard is exercised end-to-end.
        if "review_status" in sql:
            expected_status = params[1]
            if self._rows.get(target) == expected_status:
                del self._rows[target]
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif target in self._rows:
            del self._rows[target]
            self.rowcount = 1
        else:
            self.rowcount = 0


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

    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.last_connection = None

    def connection(self):
        conn = _FakeConnection(self.rows)
        self.last_connection = conn
        return conn


def _make_postgres_store(ids=None, rows=None):
    # Bypass __init__ so we don't require psycopg or a live server; inject the
    # fake pool that ``delete`` interacts with directly.
    store = PostgresFeedbackStore.__new__(PostgresFeedbackStore)
    if rows is None:
        rows = {str(i): None for i in (ids or [])}
    store._pool = _FakePool(rows=rows)
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
    assert str(target) not in store._pool.rows
    assert await store.delete(target) is False


async def test_postgres_conditional_delete_matching_status_returns_true():
    target = uuid4()
    store = _make_postgres_store(rows={str(target): ReviewStatus.REJECTED.value})
    assert await store.delete(target, expected_status=ReviewStatus.REJECTED) is True
    assert str(target) not in store._pool.rows


async def test_postgres_conditional_delete_mismatched_status_keeps_row():
    target = uuid4()
    store = _make_postgres_store(rows={str(target): ReviewStatus.PENDING_REVIEW.value})
    assert await store.delete(target, expected_status=ReviewStatus.REJECTED) is False
    assert str(target) in store._pool.rows
