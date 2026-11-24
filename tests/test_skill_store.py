"""Tests for the skill models and the skill store backends.

The store contract is written once and run against both backends: the real
``SQLiteSkillStore`` and ``PostgresSkillStore`` over a faked async connection
layer (no live server in CI, mirroring ``tests/test_user_settings_store.py``).
"""

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from autolangchat.config import ChatConfig
from autolangchat.db import SQLiteSkillStore, create_skill_store
from autolangchat.db.skill_postgres import PostgresSkillStore
from autolangchat.exceptions import SkillAlreadyExistsError, SkillNotFoundError, SkillRevisionConflictError
from autolangchat.skills import Skill, SkillCreateRequest, SkillParameter, SkillPatchRequest, validate_skill_limits
from autolangchat.skills.models import CONTENT_FIELDS


def _skill(name="triage", **overrides):
    values = dict(
        name=name,
        display_name=name.title(),
        description="Triage a job",
        instructions="Look at {{JOB}}",
        parameters=[SkillParameter(name="job", label="Job", input_type="select", options=["a", "b"], required=True)],
    )
    values.update(overrides)
    return Skill(**values)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["a", "triage", "a-b_c", "0day", "x" * 64])
def test_valid_skill_names(name):
    assert _skill(name).name == name


@pytest.mark.parametrize("name", ["", "Triage", "-a", "_a", "a b", "a/b", "é", "x" * 65, "a\n", "x" * 64 + "\n"])
def test_invalid_skill_names(name):
    with pytest.raises(ValidationError):
        _skill(name)
    with pytest.raises(ValidationError):
        SkillCreateRequest(name=name, display_name="x", instructions="i")


def test_parameter_names_are_validated_and_unique():
    for bad in ("Bad Name", "a\n"):
        with pytest.raises(ValidationError):
            SkillParameter(name=bad)
    with pytest.raises(ValidationError, match="duplicate parameter names: a"):
        _skill(parameters=[SkillParameter(name="a"), SkillParameter(name="a")])


@pytest.mark.parametrize("instructions", ["", "   \n"])
def test_blank_instructions_are_rejected(instructions):
    with pytest.raises(ValidationError):
        _skill(instructions=instructions)


def test_unknown_input_type_is_rejected():
    with pytest.raises(ValidationError):
        SkillParameter(name="a", input_type="slider")


def test_validate_skill_limits():
    skill = _skill(instructions="x" * 10, description="d" * 5, parameters=[SkillParameter(name="a")])
    limits = dict(instructions_max_chars=10, description_max_chars=5, max_parameters=1)
    validate_skill_limits(skill, **limits)  # exactly at every limit

    for override, message in [
        (dict(instructions_max_chars=9), "instructions exceed 9"),
        (dict(description_max_chars=4), "description exceeds 4"),
        (dict(max_parameters=0), "more than 0 parameters"),
    ]:
        with pytest.raises(ValueError, match=message):
            validate_skill_limits(skill, **{**limits, **override})


def test_patch_request_needs_a_change_and_validates_like_content():
    assert SkillPatchRequest(expected_revision=1, description="").changes() == {"description": ""}
    for body in (
        {"expected_revision": 1},
        {"expected_revision": 1, "instructions": None},
        {"description": "d"},
        {"expected_revision": 0, "description": "d"},
        {"expected_revision": 1, "instructions": "  "},
        {"expected_revision": 1, "display_name": ""},
        {"expected_revision": 1, "parameters": [{"name": "a"}, {"name": "a"}]},
        {"expected_revision": 1, "name": "other", "description": "d"},
    ):
        with pytest.raises(ValidationError):
            SkillPatchRequest(**body)


# ---------------------------------------------------------------------------
# Fake Postgres connection layer
# ---------------------------------------------------------------------------


class _FakeSkillsCursor:
    """Fakes enough Postgres semantics to run ``PostgresSkillStore``'s SQL
    against an in-memory ``{name: row_tuple}`` dict plus a
    ``{(name, revision): history_tuple}`` dict.

    ``row_tuple`` order matches the schema: ``(name, display_name,
    description, instructions, parameters, enabled, revision, created_at,
    updated_at, updated_by)``; ``history_tuple`` is ``(name, revision,
    display_name, description, instructions, parameters, updated_at,
    updated_by)``.
    """

    def __init__(self, rows, history):
        self._rows = rows
        self._history = history
        self._result = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=()):
        now = datetime.now(timezone.utc)
        sql = " ".join(sql.split())
        rows, history = self._rows, self._history

        if sql.startswith("INSERT INTO skills"):
            name, display_name, description, instructions, parameters, enabled, updated_by = params
            if name in rows:
                self._result = []
            else:
                rows[name] = (
                    name,
                    display_name,
                    description,
                    instructions,
                    parameters.obj,
                    enabled,
                    1,
                    now,
                    now,
                    updated_by,
                )
                self._result = [rows[name]]
        elif sql.startswith("INSERT INTO skill_revisions"):
            (name,) = params
            row = rows[name]
            assert (name, row[6]) not in history, "duplicate history primary key"
            history[(name, row[6])] = (name, row[6], row[1], row[2], row[3], row[4], row[8], row[9])
        elif sql.startswith("UPDATE skills SET display_name"):
            display_name, description, instructions, parameters, updated_by, name, *expected = params
            row = rows.get(name)
            self._result = []
            if row and (not expected or row[6] == expected[0]):
                rows[name] = (
                    name,
                    display_name,
                    description,
                    instructions,
                    parameters.obj,
                    row[5],
                    row[6] + 1,
                    row[7],
                    now,
                    updated_by,
                )
                self._result = [rows[name]]
        elif sql.startswith("UPDATE skills SET enabled"):
            enabled, updated_by, name = params
            row = rows.get(name)
            self._result = []
            if row:
                rows[name] = (*row[:5], enabled, row[6], row[7], now, updated_by)
                self._result = [rows[name]]
        elif sql.startswith("DELETE FROM skills"):
            (name,) = params
            row = rows.pop(name, None)
            for key in [key for key in history if key[0] == name]:  # ON DELETE CASCADE
                del history[key]
            self._result = [(row[6],)] if row else []
        elif sql.startswith("SELECT 1 FROM skills"):
            self._result = [(1,)] if params[0] in rows else []
        elif sql.startswith("SELECT name, display_name, description, parameters, revision FROM skills WHERE enabled"):
            self._result = [(r[0], r[1], r[2], r[4], r[6]) for _, r in sorted(rows.items()) if r[5]]
        elif sql.startswith("SELECT revision, updated_at, updated_by FROM skill_revisions"):
            self._result = [(h[1], h[6], h[7]) for k, h in sorted(history.items(), reverse=True) if k[0] == params[0]]
        elif sql.startswith("SELECT") and "FROM skill_revisions" in sql:
            found = history.get((params[0], params[1]))
            self._result = [found] if found else []
        elif sql.startswith("SELECT") and "WHERE name = %s" in sql:
            row = rows.get(params[0])
            self._result = [row] if row else []
        elif sql.startswith("SELECT"):
            self._result = [r for _, r in sorted(rows.items())]
        else:
            raise AssertionError(f"unrecognized query in fake cursor: {sql!r}")

    async def fetchone(self):
        return self._result[0] if self._result else None

    async def fetchall(self):
        return self._result


class _FakeConnection:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeSkillsCursor(self._pool.rows, self._pool.history)

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _FakePool:
    def __init__(self):
        self.rows = {}
        self.history = {}

    def connection(self):
        return _FakeConnection(self)


def _make_postgres_store():
    # Bypass __init__ so no live server is needed; inject the fake pool.
    store = PostgresSkillStore.__new__(PostgresSkillStore)
    store._pool = _FakePool()
    store._Jsonb = Jsonb
    return store


@pytest.fixture(params=["sqlite", "postgres"])
async def store(request, tmp_path):
    if request.param == "sqlite":
        store = SQLiteSkillStore(str(tmp_path / "skills.db"))
        await store.open()
        yield store
        await store.close()
    else:
        yield _make_postgres_store()


# ---------------------------------------------------------------------------
# Store contract (both backends)
# ---------------------------------------------------------------------------


async def test_create_and_get_round_trip(store):
    created = await store.create_skill(_skill(), updated_by="alice")

    assert created.created_at is not None and created.updated_at is not None
    assert created.updated_by == "alice"
    assert created.enabled is True
    fetched = await store.get_skill("triage")
    assert fetched == created
    assert fetched.parameters[0].options == ["a", "b"]
    assert fetched.parameters[0].required is True


async def test_get_missing_skill_returns_none(store):
    assert await store.get_skill("nope") is None


async def test_create_duplicate_name_raises_and_keeps_the_original(store):
    await store.create_skill(_skill(instructions="original"))

    with pytest.raises(SkillAlreadyExistsError):
        await store.create_skill(_skill(instructions="other"))

    assert (await store.get_skill("triage")).instructions == "original"


async def test_create_honours_disabled_flag(store):
    created = await store.create_skill(_skill(enabled=False))
    assert created.enabled is False


async def test_list_skills_is_ordered_by_name_and_includes_disabled(store):
    await store.create_skill(_skill("b"))
    await store.create_skill(_skill("a", enabled=False))

    skills = await store.list_skills()

    assert [(s.name, s.enabled) for s in skills] == [("a", False), ("b", True)]


async def test_replace_overwrites_content_but_keeps_enabled_and_created_at(store):
    original = await store.create_skill(_skill(enabled=False), updated_by="alice")

    replaced = await store.replace_skill(
        Skill(name="triage", display_name="New", instructions="new body", enabled=True), updated_by="bob"
    )

    assert (replaced.display_name, replaced.description, replaced.instructions, replaced.parameters) == (
        "New",
        "",
        "new body",
        [],
    )
    assert replaced.enabled is False
    assert replaced.created_at == original.created_at
    assert replaced.updated_at >= original.updated_at
    assert replaced.updated_by == "bob"
    assert await store.get_skill("triage") == replaced


async def test_replace_missing_skill_raises(store):
    with pytest.raises(SkillNotFoundError):
        await store.replace_skill(_skill("nope"))
    assert await store.list_skills() == []


async def test_set_enabled_toggles_and_stamps_actor(store):
    await store.create_skill(_skill(), updated_by="alice")

    disabled = await store.set_enabled("triage", False, updated_by="bob")
    assert (disabled.enabled, disabled.updated_by) == (False, "bob")
    assert disabled.instructions == "Look at {{JOB}}"

    assert (await store.set_enabled("triage", True)).enabled is True


async def test_set_enabled_missing_skill_raises(store):
    with pytest.raises(SkillNotFoundError):
        await store.set_enabled("nope", True)


async def test_delete_removes_the_skill_and_frees_the_name(store):
    await store.create_skill(_skill())

    await store.delete_skill("triage")

    assert await store.get_skill("triage") is None
    await store.create_skill(_skill())  # the name is reusable


async def test_delete_missing_skill_raises(store):
    with pytest.raises(SkillNotFoundError):
        await store.delete_skill("nope")


async def test_catalog_has_only_enabled_skills_and_no_instructions(store):
    await store.create_skill(_skill("b"))
    await store.create_skill(_skill("a", enabled=False))
    await store.create_skill(_skill("c"))

    catalog = await store.list_enabled_catalog()

    assert [entry.name for entry in catalog] == ["b", "c"]
    assert catalog[0].parameters[0].name == "job"
    assert set(catalog[0].model_dump()) == {"name", "display_name", "description", "parameters", "revision"}

    await store.set_enabled("a", True)
    await store.set_enabled("b", False)
    assert [entry.name for entry in await store.list_enabled_catalog()] == ["a", "c"]


# ---------------------------------------------------------------------------
# Revisions (both backends)
# ---------------------------------------------------------------------------


async def test_create_starts_at_revision_1_with_a_history_entry(store):
    created = await store.create_skill(_skill(revision=7), updated_by="alice")

    assert created.revision == 1
    [summary] = await store.list_revisions("triage")
    assert (summary.revision, summary.updated_by) == (1, "alice")
    first = await store.get_revision("triage", 1)
    assert (first.instructions, first.parameters) == (created.instructions, created.parameters)


async def test_every_content_write_is_a_new_revision_listed_newest_first(store):
    await store.create_skill(_skill(), updated_by="alice")
    await store.replace_skill(_skill(instructions="second"), updated_by="bob")
    third = await store.replace_skill(_skill(instructions="third"), updated_by="carol")

    assert third.revision == 3
    assert (await store.get_skill("triage")).revision == 3
    summaries = await store.list_revisions("triage")
    assert [(s.revision, s.updated_by) for s in summaries] == [(3, "carol"), (2, "bob"), (1, "alice")]
    assert (await store.get_revision("triage", 2)).instructions == "second"
    assert (await store.get_revision("triage", 1)).instructions == "Look at {{JOB}}"
    assert "instructions" not in summaries[0].model_dump()


async def test_enable_and_disable_keep_the_revision(store):
    await store.create_skill(_skill())
    await store.replace_skill(_skill(instructions="second"))

    assert (await store.set_enabled("triage", False)).revision == 2
    assert (await store.set_enabled("triage", True)).revision == 2
    assert len(await store.list_revisions("triage")) == 2


async def test_replace_with_matching_expected_revision_succeeds(store):
    await store.create_skill(_skill())

    replaced = await store.replace_skill(_skill(instructions="new"), expected_revision=1)

    assert (replaced.revision, replaced.instructions) == (2, "new")


async def test_replace_with_stale_expected_revision_conflicts_and_changes_nothing(store):
    await store.create_skill(_skill())
    current = await store.replace_skill(_skill(instructions="second"), updated_by="bob")

    with pytest.raises(SkillRevisionConflictError) as caught:
        await store.replace_skill(_skill(instructions="stale"), expected_revision=1)

    assert caught.value.current == current
    assert await store.get_skill("triage") == current
    assert [s.revision for s in await store.list_revisions("triage")] == [2, 1]


async def test_replace_missing_skill_with_expected_revision_is_not_found(store):
    with pytest.raises(SkillNotFoundError):
        await store.replace_skill(_skill("nope"), expected_revision=1)


async def test_restoring_an_old_revision_creates_a_new_one_and_keeps_history(store):
    await store.create_skill(_skill())
    await store.replace_skill(_skill(display_name="Second", instructions="second", parameters=[]))

    old = await store.get_revision("triage", 1)
    restored = await store.replace_skill(
        Skill(name="triage", **old.model_dump(include=set(CONTENT_FIELDS))), updated_by="admin", expected_revision=2
    )

    assert restored.revision == 3
    assert (restored.display_name, restored.instructions) == ("Triage", "Look at {{JOB}}")
    assert restored.parameters == old.parameters
    assert [s.revision for s in await store.list_revisions("triage")] == [3, 2, 1]
    assert (await store.get_revision("triage", 2)).instructions == "second"


async def test_revisions_of_a_missing_skill(store):
    with pytest.raises(SkillNotFoundError):
        await store.list_revisions("nope")
    assert await store.get_revision("nope", 1) is None


async def test_get_missing_revision_returns_none(store):
    await store.create_skill(_skill())
    assert await store.get_revision("triage", 2) is None


async def test_delete_returns_the_revision_and_removes_history(store):
    await store.create_skill(_skill())
    await store.replace_skill(_skill(instructions="second"))

    assert await store.delete_skill("triage") == 2

    assert await store.get_revision("triage", 1) is None
    recreated = await store.create_skill(_skill())
    assert recreated.revision == 1
    assert [s.revision for s in await store.list_revisions("triage")] == [1]


async def test_catalog_reports_the_revision(store):
    await store.create_skill(_skill())
    await store.replace_skill(_skill(instructions="second"))

    [entry] = await store.list_enabled_catalog()

    assert entry.revision == 2


# ---------------------------------------------------------------------------
# Unreadable rows degrade instead of breaking the store
# ---------------------------------------------------------------------------


async def test_sqlite_garbage_parameters_degrade_to_empty_list(tmp_path):
    path = str(tmp_path / "skills.db")
    store = SQLiteSkillStore(path)
    await store.create_skill(_skill())
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE skills SET parameters = 'not json'")

    assert (await store.get_skill("triage")).parameters == []
    assert (await store.list_enabled_catalog())[0].parameters == []
    await store.close()


async def test_sqlite_unreadable_row_is_skipped_from_listings(tmp_path):
    path = str(tmp_path / "skills.db")
    store = SQLiteSkillStore(path)
    await store.create_skill(_skill("good"))
    await store.create_skill(_skill("bad"))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE skills SET parameters = ? WHERE name = 'bad'", (json.dumps([{"name": "Bad Name"}]),))

    assert [s.name for s in await store.list_skills()] == ["good"]
    assert [e.name for e in await store.list_enabled_catalog()] == ["good"]
    assert await store.get_skill("bad") is None
    await store.close()


async def test_postgres_non_array_parameters_degrade_to_empty_list():
    store = _make_postgres_store()
    await store.create_skill(_skill())
    name, *rest = store._pool.rows["triage"]
    store._pool.rows["triage"] = (name, rest[0], rest[1], rest[2], {"not": "a list"}, *rest[4:])

    assert (await store.get_skill("triage")).parameters == []


# ---------------------------------------------------------------------------
# SQLite specifics
# ---------------------------------------------------------------------------


async def test_sqlite_skills_survive_reopening_the_database(tmp_path):
    path = str(tmp_path / "nested" / "skills.db")
    first = SQLiteSkillStore(path)
    await first.open()
    await first.create_skill(_skill())
    await first.close()

    second = SQLiteSkillStore(path)
    await second.open()
    assert (await second.get_skill("triage")).display_name == "Triage"
    await second.close()


async def test_sqlite_writes_read_the_row_back_before_committing(tmp_path):
    # Another worker sharing the file could change the row between COMMIT and a
    # later SELECT; reading inside the write transaction (which holds SQLite's
    # write lock) returns exactly what this call wrote.
    store = SQLiteSkillStore(str(tmp_path / "skills.db"))
    await store.open()
    statements = []
    store._conn.set_trace_callback(statements.append)

    operations = [
        lambda: store.create_skill(_skill()),
        lambda: store.replace_skill(_skill(display_name="New")),
        lambda: store.set_enabled("triage", False),
    ]
    for operation in operations:
        statements.clear()
        await operation()
        verbs = [statement.split()[0].upper() for statement in statements]
        assert verbs.index("SELECT") < verbs.index("COMMIT"), statements
    await store.close()


async def test_sqlite_opens_lazily_on_first_use(tmp_path):
    store = SQLiteSkillStore(str(tmp_path / "skills.db"))
    assert await store.list_skills() == []
    await store.close()


_PRE_REVISION_SQLITE_DDL = """
CREATE TABLE skills (
    name TEXT PRIMARY KEY, display_name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL, parameters TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT
);
"""


async def test_sqlite_migrates_a_pre_revision_database_without_data_loss(tmp_path):
    path = str(tmp_path / "skills.db")
    stamp = "2026-11-01T10:00:00+00:00"
    with sqlite3.connect(path) as conn:
        conn.executescript(_PRE_REVISION_SQLITE_DDL)
        conn.execute(
            "INSERT INTO skills VALUES ('legacy', 'Legacy', 'old', 'old body', ?, 0, ?, ?, 'alice')",
            (json.dumps([{"name": "job"}]), stamp, stamp),
        )
    conn.close()

    for _ in range(2):  # the migration is idempotent
        store = SQLiteSkillStore(path)
        await store.open()
        legacy = await store.get_skill("legacy")
        assert (legacy.revision, legacy.enabled, legacy.instructions, legacy.updated_by) == (
            1,
            False,
            "old body",
            "alice",
        )
        assert legacy.parameters[0].name == "job"
        [summary] = await store.list_revisions("legacy")
        assert (summary.revision, summary.updated_at.isoformat(), summary.updated_by) == (1, stamp, "alice")
        assert (await store.get_revision("legacy", 1)).instructions == "old body"
        await store.close()

    store = SQLiteSkillStore(path)
    updated = await store.replace_skill(Skill(name="legacy", display_name="L", instructions="new"), expected_revision=1)
    assert updated.revision == 2
    assert [s.revision for s in await store.list_revisions("legacy")] == [2, 1]
    await store.close()


def test_postgres_schema_migrates_pre_revision_databases():
    from importlib import resources

    ddl = " ".join(resources.files("autolangchat.db.sql").joinpath("skill_schema.sql").read_text().split())
    assert "ALTER TABLE skills ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;" in ddl
    assert "REFERENCES skills (name) ON DELETE CASCADE" in ddl
    assert "FROM skills ON CONFLICT (name, revision) DO NOTHING;" in ddl


# ---------------------------------------------------------------------------
# create_skill_store factory
# ---------------------------------------------------------------------------


def test_factory_returns_none_when_disabled():
    assert create_skill_store(ChatConfig(AUTOCHAT_SKILLS_PERSISTENCE_ENABLED=False)) is None


def test_factory_is_enabled_by_default_and_uses_the_kb_sqlite_database():
    store = create_skill_store(ChatConfig(KB_DATABASE_PATH="/tmp/kb.db"))
    assert isinstance(store, SQLiteSkillStore)
    assert store._db_path == "/tmp/kb.db"


def test_factory_prefers_the_conversation_database():
    store = create_skill_store(
        ChatConfig(AUTOCHAT_CONVERSATION_DB_PATH="/tmp/shared.db", AUTOCHAT_FEEDBACK_DATABASE_PATH="/tmp/feedback.db")
    )
    assert store._db_path == "/tmp/shared.db"


def test_factory_returns_none_without_a_resolvable_path():
    assert create_skill_store(ChatConfig(KB_DATABASE_PATH="")) is None


def test_factory_follows_the_other_stores_onto_postgres():
    config = ChatConfig(
        AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED=True,
        AUTOCHAT_CONVERSATION_STORAGE_TYPE="postgres",
        AUTOCHAT_CONVERSATION_POSTGRES_URL="postgresql://user:pass@localhost:5432/chat",
    )
    assert isinstance(create_skill_store(config), PostgresSkillStore)


def test_factory_returns_none_on_postgres_without_a_url():
    config = ChatConfig(
        AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED=True,
        AUTOCHAT_CONVERSATION_STORAGE_TYPE="postgres",
    )
    assert create_skill_store(config) is None


def test_factory_ignores_disabled_postgres_siblings():
    config = ChatConfig(
        AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED=False,
        AUTOCHAT_CONVERSATION_STORAGE_TYPE="postgres",
        AUTOCHAT_CONVERSATION_POSTGRES_URL="postgresql://user:pass@localhost:5432/chat",
        KB_DATABASE_PATH="/tmp/kb.db",
    )
    assert isinstance(create_skill_store(config), SQLiteSkillStore)
