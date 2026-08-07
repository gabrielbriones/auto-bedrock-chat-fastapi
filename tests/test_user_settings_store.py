"""Unit tests for the per-user settings store (XMGPLAT-11193).

Covers ``SQLiteUserSettingsStore``:

  (a) ``get_or_create_settings`` creates the row from ``defaults`` on first
      call and never overwrites an existing row on subsequent calls;
  (b) ``set_settings`` upserts (creates when missing, replaces wholesale
      when present) and bumps ``updated_at`` while preserving ``created_at``;
  (c) reset-to-defaults semantics (``set_settings`` with the default
      payload) keep the row rather than deleting it;
  (d) user isolation — one user's settings never leak into another's;
  (e) unknown/stale keys in the stored document round-trip untouched, and a
      corrupt (non-JSON / non-object) payload degrades to ``{}`` instead of
      raising;
  (f) idempotent ``delete_settings`` on a missing user.

And the ``create_user_settings_store`` factory
(``autolangchat/db/__init__.py``):

  (g) returns ``None`` when ``user_settings_persistence_enabled=False``;
  (h) infers the backend and database from whichever store the app already
      uses (conversation → feedback → token usage → KB) — there is no
      user-settings-specific backend/URL/path setting;
  (i) returns ``None`` (with a warning, not a crash) when no usable database
      can be resolved.

And ``PostgresUserSettingsStore`` (faked async connection layer — no live
server in CI, mirroring the ``tests/test_conversation_store.py`` approach):

  (j) get-or-create, upsert, reset and delete against the Postgres backend's
      SQL, including ``ON CONFLICT (user_id) DO NOTHING`` not clobbering an
      existing row.
"""

import json
from datetime import datetime, timezone

from autolangchat.config import ChatConfig
from autolangchat.db import SQLiteUserSettingsStore, create_user_settings_store
from autolangchat.db.user_settings_postgres import PostgresUserSettingsStore

DEFAULTS = {"model_id": "us.anthropic.claude-sonnet-5", "max_tokens": 4096}


async def _open_store(**kwargs):
    store = SQLiteUserSettingsStore(db_path=":memory:", **kwargs)
    await store.open()
    return store


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


async def test_get_settings_returns_none_when_missing():
    store = await _open_store()
    try:
        assert await store.get_settings("alice") is None
    finally:
        await store.close()


async def test_get_or_create_creates_row_from_defaults():
    store = await _open_store()
    try:
        row = await store.get_or_create_settings("alice", DEFAULTS)

        assert row["user_id"] == "alice"
        assert row["settings"] == DEFAULTS
        assert row["created_at"] == row["updated_at"]
        # Readable through the plain getter afterwards.
        assert (await store.get_settings("alice"))["settings"] == DEFAULTS
    finally:
        await store.close()


async def test_get_or_create_is_idempotent_and_never_clobbers():
    """Second call must return the stored row, not re-apply ``defaults`` —
    this is what makes opening several tabs at once safe."""
    store = await _open_store()
    try:
        await store.get_or_create_settings("alice", DEFAULTS)
        await store.set_settings("alice", {"model_id": "meta.llama3-3-70b-instruct-v1:0"})

        row = await store.get_or_create_settings("alice", DEFAULTS)

        assert row["settings"] == {"model_id": "meta.llama3-3-70b-instruct-v1:0"}
    finally:
        await store.close()


async def test_get_or_create_without_defaults_stores_empty_document():
    store = await _open_store()
    try:
        row = await store.get_or_create_settings("alice")
        assert row["settings"] == {}
    finally:
        await store.close()


async def test_set_settings_creates_row_when_missing():
    store = await _open_store()
    try:
        row = await store.set_settings("alice", {"temperature": 0.2})
        assert row["settings"] == {"temperature": 0.2}
    finally:
        await store.close()


async def test_set_settings_replaces_document_wholesale():
    """The payload is opaque and replaced, not merged — the caller owns
    merge/validation via ``ChatConfig.validate_overrides()``."""
    store = await _open_store()
    try:
        await store.set_settings("alice", {"temperature": 0.2, "top_p": 0.9})
        row = await store.set_settings("alice", {"max_tokens": 1024})

        assert row["settings"] == {"max_tokens": 1024}
    finally:
        await store.close()


async def test_set_settings_preserves_created_at_and_bumps_updated_at():
    store = await _open_store()
    try:
        created = await store.get_or_create_settings("alice", DEFAULTS)
        updated = await store.set_settings("alice", {"max_tokens": 1024})

        assert updated["created_at"] == created["created_at"]
        assert updated["updated_at"] >= created["updated_at"]
    finally:
        await store.close()


async def test_reset_restores_defaults_without_deleting_the_row():
    store = await _open_store()
    try:
        await store.get_or_create_settings("alice", DEFAULTS)
        await store.set_settings("alice", {"temperature": 1.0})

        await store.set_settings("alice", DEFAULTS)

        row = await store.get_settings("alice")
        assert row is not None
        assert row["settings"] == DEFAULTS
    finally:
        await store.close()


async def test_settings_are_isolated_per_user():
    store = await _open_store()
    try:
        await store.set_settings("alice", {"model_id": "model-a"})
        await store.set_settings("bob", {"model_id": "model-b"})

        assert (await store.get_settings("alice"))["settings"] == {"model_id": "model-a"}
        assert (await store.get_settings("bob"))["settings"] == {"model_id": "model-b"}
    finally:
        await store.close()


async def test_unknown_keys_round_trip_untouched():
    """Stale/renamed parameters must survive a read — the store never
    validates the document, so removing an overridable parameter can't brick
    an existing user's row."""
    store = await _open_store()
    try:
        payload = {"model_id": "model-a", "removed_param": 123, "nested": {"a": [1, 2]}}
        await store.set_settings("alice", payload)

        assert (await store.get_settings("alice"))["settings"] == payload
    finally:
        await store.close()


async def test_corrupt_payload_degrades_to_empty_document():
    store = await _open_store()
    try:
        await store.set_settings("alice", {"model_id": "model-a"})
        # Simulate a hand-edited / corrupted row.
        store._conn.execute("UPDATE user_settings SET settings = ? WHERE user_id = ?", ("not json", "alice"))
        store._conn.commit()

        assert (await store.get_settings("alice"))["settings"] == {}
    finally:
        await store.close()


async def test_non_object_payload_degrades_to_empty_document():
    store = await _open_store()
    try:
        await store.set_settings("alice", {"model_id": "model-a"})
        store._conn.execute(
            "UPDATE user_settings SET settings = ? WHERE user_id = ?",
            (json.dumps([1, 2, 3]), "alice"),
        )
        store._conn.commit()

        assert (await store.get_settings("alice"))["settings"] == {}
    finally:
        await store.close()


async def test_delete_settings_is_idempotent():
    store = await _open_store()
    try:
        await store.set_settings("alice", {"model_id": "model-a"})

        await store.delete_settings("alice")
        assert await store.get_settings("alice") is None

        # Deleting again must not raise.
        await store.delete_settings("alice")
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# create_user_settings_store factory
# ---------------------------------------------------------------------------


def test_create_user_settings_store_returns_none_when_disabled():
    config = ChatConfig(AUTOCHAT_USER_SETTINGS_PERSISTENCE_ENABLED=False)
    assert create_user_settings_store(config) is None


def test_create_user_settings_store_returns_none_without_a_resolvable_path():
    config = ChatConfig(
        AUTOCHAT_USER_SETTINGS_PERSISTENCE_ENABLED=True,
        # kb_database_path has a non-empty default and its env alias is
        # "KB_DATABASE_PATH" (no AUTOCHAT_ prefix) — must be cleared
        # explicitly too for the sqlite fallback chain to resolve to nothing.
        KB_DATABASE_PATH="",
    )
    assert create_user_settings_store(config) is None


def test_create_user_settings_store_uses_the_kb_sqlite_database_by_default():
    config = ChatConfig(
        AUTOCHAT_USER_SETTINGS_PERSISTENCE_ENABLED=True,
        KB_DATABASE_PATH="/tmp/kb.db",
    )
    store = create_user_settings_store(config)
    assert isinstance(store, SQLiteUserSettingsStore)
    assert store._db_path == "/tmp/kb.db"


def test_create_user_settings_store_prefers_the_conversation_database():
    config = ChatConfig(
        AUTOCHAT_USER_SETTINGS_PERSISTENCE_ENABLED=True,
        AUTOCHAT_CONVERSATION_DB_PATH="/tmp/shared.db",
        AUTOCHAT_FEEDBACK_DATABASE_PATH="/tmp/feedback.db",
    )
    store = create_user_settings_store(config)
    assert isinstance(store, SQLiteUserSettingsStore)
    assert store._db_path == "/tmp/shared.db"


def test_create_user_settings_store_falls_back_to_feedback_database_path():
    config = ChatConfig(
        AUTOCHAT_USER_SETTINGS_PERSISTENCE_ENABLED=True,
        AUTOCHAT_FEEDBACK_DATABASE_PATH="/tmp/feedback.db",
    )
    store = create_user_settings_store(config)
    assert isinstance(store, SQLiteUserSettingsStore)
    assert store._db_path == "/tmp/feedback.db"


def test_create_user_settings_store_follows_conversations_onto_postgres():
    """No user-settings backend setting exists: when the conversation store
    runs on Postgres, so does this one — on the same database."""
    config = ChatConfig(
        AUTOCHAT_USER_SETTINGS_PERSISTENCE_ENABLED=True,
        AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED=True,
        AUTOCHAT_CONVERSATION_STORAGE_TYPE="postgres",
        AUTOCHAT_CONVERSATION_POSTGRES_URL="postgresql://user:pass@localhost:5432/chat",
    )
    store = create_user_settings_store(config)
    assert isinstance(store, PostgresUserSettingsStore)


def test_create_user_settings_store_follows_the_kb_onto_pgvector():
    config = ChatConfig(
        AUTOCHAT_USER_SETTINGS_PERSISTENCE_ENABLED=True,
        AUTOCHAT_KB_STORAGE_TYPE="pgvector",
        AUTOCHAT_KB_POSTGRES_URL="postgresql://user:pass@localhost:5432/kb",
    )
    store = create_user_settings_store(config)
    assert isinstance(store, PostgresUserSettingsStore)


def test_create_user_settings_store_ignores_disabled_postgres_siblings():
    """A postgres storage_type on a *disabled* store must not drag the
    user-settings store onto a database nobody is using."""
    config = ChatConfig(
        AUTOCHAT_USER_SETTINGS_PERSISTENCE_ENABLED=True,
        AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED=False,
        AUTOCHAT_CONVERSATION_STORAGE_TYPE="postgres",
        KB_DATABASE_PATH="/tmp/kb.db",
    )
    store = create_user_settings_store(config)
    assert isinstance(store, SQLiteUserSettingsStore)


def test_create_user_settings_store_returns_none_without_a_postgres_url():
    config = ChatConfig(
        AUTOCHAT_USER_SETTINGS_PERSISTENCE_ENABLED=True,
        AUTOCHAT_FEEDBACK_ENABLED=True,
        AUTOCHAT_FEEDBACK_STORAGE_TYPE="postgres",
    )
    assert (
        create_user_settings_store(config) is None
    )  # ---------------------------------------------------------------------------


# Postgres store (faked async connection layer — no live server in CI,
# mirroring the tests/test_conversation_store.py approach)
# ---------------------------------------------------------------------------


class _FakeUserSettingsCursor:
    """Async cursor faking enough Postgres semantics to exercise
    ``PostgresUserSettingsStore``'s SQL against an in-memory
    ``{user_id: row_tuple}`` dict.

    ``row_tuple`` order matches the schema:
    ``(user_id, settings, created_at, updated_at)``.
    """

    def __init__(self, rows):
        self._rows = rows
        self._result = []
        self.rowcount = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        now = datetime.now(timezone.utc)

        if "DO NOTHING" in sql:
            user_id, settings_jsonb = params
            if user_id not in self._rows:
                self._rows[user_id] = (user_id, settings_jsonb.obj, now, now)
                self.rowcount = 1
            else:
                self.rowcount = 0
            return

        if "DO UPDATE" in sql:
            user_id, settings_jsonb = params
            created_at = self._rows[user_id][2] if user_id in self._rows else now
            self._rows[user_id] = (user_id, settings_jsonb.obj, created_at, now)
            self.rowcount = 1
            self._result = [self._rows[user_id]]
            return

        if sql.strip().startswith("DELETE FROM user_settings"):
            (user_id,) = params
            self.rowcount = 1 if self._rows.pop(user_id, None) is not None else 0
            return

        if sql.strip().startswith("SELECT"):
            (user_id,) = params
            row = self._rows.get(user_id)
            self._result = [row] if row else []
            return

        raise AssertionError(f"unrecognized query in fake cursor: {sql!r}")

    async def fetchone(self):
        return self._result[0] if self._result else None

    async def fetchall(self):
        return self._result


class _FakeUserSettingsConnection:
    def __init__(self, rows):
        self._rows = rows
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeUserSettingsCursor(self._rows)

    async def commit(self):
        self.commits += 1


class _FakeUserSettingsPool:
    """Stateful stand-in for ``psycopg_pool.AsyncConnectionPool``."""

    def __init__(self):
        self.rows = {}

    def connection(self):
        return _FakeUserSettingsConnection(self.rows)


def _make_postgres_store():
    from psycopg.types.json import Jsonb

    # Bypass __init__ so we don't require a live server; inject the fake
    # pool that every store method interacts with directly.
    store = PostgresUserSettingsStore.__new__(PostgresUserSettingsStore)
    store._pool = _FakeUserSettingsPool()
    store._Jsonb = Jsonb
    return store


async def test_postgres_get_settings_returns_none_when_missing():
    store = _make_postgres_store()
    assert await store.get_settings("alice") is None


async def test_postgres_get_or_create_creates_row_from_defaults():
    store = _make_postgres_store()

    row = await store.get_or_create_settings("alice", DEFAULTS)

    assert row["user_id"] == "alice"
    assert row["settings"] == DEFAULTS


async def test_postgres_get_or_create_does_not_clobber_existing_row():
    store = _make_postgres_store()
    await store.set_settings("alice", {"model_id": "model-a"})

    row = await store.get_or_create_settings("alice", DEFAULTS)

    assert row["settings"] == {"model_id": "model-a"}


async def test_postgres_set_settings_upserts_and_returns_row():
    store = _make_postgres_store()

    created = await store.set_settings("alice", {"temperature": 0.2})
    assert created["settings"] == {"temperature": 0.2}

    replaced = await store.set_settings("alice", {"max_tokens": 1024})
    assert replaced["settings"] == {"max_tokens": 1024}
    assert replaced["created_at"] == created["created_at"]


async def test_postgres_reset_restores_defaults_without_deleting_the_row():
    store = _make_postgres_store()
    await store.get_or_create_settings("alice", DEFAULTS)
    await store.set_settings("alice", {"temperature": 1.0})

    await store.set_settings("alice", DEFAULTS)

    row = await store.get_settings("alice")
    assert row is not None
    assert row["settings"] == DEFAULTS


async def test_postgres_settings_are_isolated_per_user():
    store = _make_postgres_store()
    await store.set_settings("alice", {"model_id": "model-a"})
    await store.set_settings("bob", {"model_id": "model-b"})

    assert (await store.get_settings("alice"))["settings"] == {"model_id": "model-a"}
    assert (await store.get_settings("bob"))["settings"] == {"model_id": "model-b"}


async def test_postgres_delete_settings_is_idempotent():
    store = _make_postgres_store()
    await store.set_settings("alice", {"model_id": "model-a"})

    await store.delete_settings("alice")
    assert await store.get_settings("alice") is None

    await store.delete_settings("alice")


async def test_postgres_non_object_payload_degrades_to_empty_document():
    store = _make_postgres_store()
    store._pool.rows["alice"] = ("alice", [1, 2, 3], datetime.now(timezone.utc), datetime.now(timezone.utc))

    assert (await store.get_settings("alice"))["settings"] == {}
