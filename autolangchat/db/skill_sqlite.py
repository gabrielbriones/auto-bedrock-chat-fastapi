"""
SQLite-backed SkillStore.

Zero-config implementation of the async
:class:`~.skill_base.BaseSkillStore` interface using SQLite. Uses the stdlib
``sqlite3`` module wrapped in :func:`asyncio.to_thread`, mirroring the
lifecycle/concurrency approach of
:class:`~.user_settings_sqlite.SQLiteUserSettingsStore`.

Schema lives at ``autolangchat/db/sql/skill_schema_sqlite.sql``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from importlib import resources

from pydantic import ValidationError

from ..exceptions import SkillAlreadyExistsError, SkillError, SkillNotFoundError, SkillRevisionConflictError
from ..skills.models import Skill, SkillCatalogEntry, SkillRevision, SkillRevisionSummary
from .skill_base import BaseSkillStore

logger = logging.getLogger(__name__)

_COLUMNS = (
    "name, display_name, description, instructions, parameters, enabled, revision, created_at, updated_at, updated_by"
)
_SELECT_ONE_SQL = f"SELECT {_COLUMNS} FROM skills WHERE name = ?"
_SELECT_ALL_SQL = f"SELECT {_COLUMNS} FROM skills ORDER BY name"
_SELECT_CATALOG_SQL = (
    "SELECT name, display_name, description, parameters, revision FROM skills WHERE enabled = 1 ORDER BY name"
)
_REVISION_COLUMNS = "name, revision, display_name, description, instructions, parameters, updated_at, updated_by"
# Copies the current content of skill ``name`` into the history (inside the write transaction).
_RECORD_REVISION_SQL = (
    f"INSERT INTO skill_revisions ({_REVISION_COLUMNS}) SELECT {_REVISION_COLUMNS} FROM skills WHERE name = ?"
)
_BASELINE_REVISIONS_SQL = (
    f"INSERT OR IGNORE INTO skill_revisions ({_REVISION_COLUMNS}) SELECT {_REVISION_COLUMNS} FROM skills"
)
_SELECT_REVISIONS_SQL = (
    "SELECT revision, updated_at, updated_by FROM skill_revisions WHERE name = ? ORDER BY revision DESC"
)
_SELECT_REVISION_SQL = f"SELECT {_REVISION_COLUMNS} FROM skill_revisions WHERE name = ? AND revision = ?"
_REPLACE_SET = (
    "SET display_name = ?, description = ?, instructions = ?, parameters = ?, "
    "revision = revision + 1, updated_at = ?, updated_by = ?"
)


def _now_iso() -> str:
    """Current time as a UTC ISO-8601 string (fixed-width ``+00:00`` suffix)."""
    return datetime.now(timezone.utc).isoformat()


class SQLiteSkillStore(BaseSkillStore):
    """Async SQLite-backed skill store.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database. Parent directories are
        created on :meth:`open`. Use ``":memory:"`` for ephemeral test
        databases.
    init_schema:
        When True (default), apply ``skill_schema_sqlite.sql`` on
        :meth:`open`, then migrate a database created before revisions
        (adds ``skills.revision`` and the baseline history). Both steps are
        idempotent.

    Concurrency
    -----------
    A single :class:`sqlite3.Connection` opened with
    ``check_same_thread=False`` is shared across asyncio worker threads and
    serialized by a :class:`threading.Lock`. All blocking calls run via
    :func:`asyncio.to_thread`. WAL mode is enabled for concurrent reads.
    """

    SCHEMA_RESOURCE = ("autolangchat.db.sql", "skill_schema_sqlite.sql")

    def __init__(self, db_path: str, init_schema: bool = True) -> None:
        self._db_path = db_path
        self._init_schema = init_schema
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        await asyncio.to_thread(self._ensure_open_sync)
        logger.info("SQLiteSkillStore ready (db_path=%s, init_schema=%s)", self._db_path, self._init_schema)

    def _ensure_open_sync(self) -> None:
        """Idempotently open the SQLite connection (also the lazy fallback for every call)."""
        if self._conn is not None:
            return
        with self._lock:
            if self._conn is not None:
                return
            self._open_sync_locked()

    def _open_sync_locked(self) -> None:
        if self._db_path != ":memory:":
            parent = os.path.dirname(self._db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        if self._db_path != ":memory:":
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                # Some filesystems (e.g. NFS) reject WAL; fall back silently.
                pass
        if self._init_schema:
            ddl = resources.files(self.SCHEMA_RESOURCE[0]).joinpath(self.SCHEMA_RESOURCE[1]).read_text(encoding="utf-8")
            conn.executescript(ddl)
            _migrate_revisions(conn)
            conn.commit()
        self._conn = conn

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_skills(self) -> list[Skill]:
        rows = await asyncio.to_thread(self._fetchall, _SELECT_ALL_SQL, ())
        return [skill for skill in (_row_to_skill(row) for row in rows) if skill is not None]

    async def get_skill(self, name: str) -> Skill | None:
        row = await asyncio.to_thread(self._fetchone, _SELECT_ONE_SQL, (name,))
        return _row_to_skill(row) if row is not None else None

    async def list_enabled_catalog(self) -> list[SkillCatalogEntry]:
        rows = await asyncio.to_thread(self._fetchall, _SELECT_CATALOG_SQL, ())
        entries = (_row_to_catalog_entry(row) for row in rows)
        return [entry for entry in entries if entry is not None]

    async def list_revisions(self, name: str) -> list[SkillRevisionSummary]:
        exists = await asyncio.to_thread(self._fetchone, "SELECT 1 FROM skills WHERE name = ?", (name,))
        if exists is None:
            raise SkillNotFoundError(f"Skill {name!r} not found")
        rows = await asyncio.to_thread(self._fetchall, _SELECT_REVISIONS_SQL, (name,))
        return [
            SkillRevisionSummary(
                revision=revision, updated_at=datetime.fromisoformat(updated_at), updated_by=updated_by
            )
            for revision, updated_at, updated_by in rows
        ]

    async def get_revision(self, name: str, revision: int) -> SkillRevision | None:
        row = await asyncio.to_thread(self._fetchone, _SELECT_REVISION_SQL, (name, revision))
        return _row_to_revision(row) if row is not None else None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create_skill(self, skill: Skill, updated_by: str | None = None) -> Skill:
        return await asyncio.to_thread(self._create_sync, skill, updated_by)

    def _create_sync(self, skill: Skill, updated_by: str | None) -> Skill:
        self._ensure_open_sync()
        assert self._conn is not None
        now = _now_iso()
        with self._lock:
            try:
                self._conn.execute(
                    f"INSERT INTO skills ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (
                        skill.name,
                        skill.display_name,
                        skill.description,
                        skill.instructions,
                        _dump_parameters(skill),
                        1 if skill.enabled else 0,
                        now,
                        now,
                        updated_by,
                    ),
                )
                self._conn.execute(_RECORD_REVISION_SQL, (skill.name,))
                # Read back before committing: the open write transaction holds
                # SQLite's write lock, so another worker sharing the file cannot
                # change or delete the row between the write and this read.
                row = self._conn.execute(_SELECT_ONE_SQL, (skill.name,)).fetchone()
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                raise SkillAlreadyExistsError(f"Skill {skill.name!r} already exists") from exc
            except Exception:
                self._conn.rollback()
                raise
        return _require_skill(row, skill.name)

    async def replace_skill(
        self, skill: Skill, updated_by: str | None = None, expected_revision: int | None = None
    ) -> Skill:
        return await asyncio.to_thread(self._replace_sync, skill, updated_by, expected_revision)

    def _replace_sync(self, skill: Skill, updated_by: str | None, expected_revision: int | None) -> Skill:
        self._ensure_open_sync()
        assert self._conn is not None
        params: tuple = (
            skill.display_name,
            skill.description,
            skill.instructions,
            _dump_parameters(skill),
            _now_iso(),
            updated_by,
            skill.name,
        )
        where = "name = ?"
        if expected_revision is not None:
            where += " AND revision = ?"
            params += (expected_revision,)
        with self._lock:
            try:
                cur = self._conn.execute(f"UPDATE skills {_REPLACE_SET} WHERE {where}", params)
                if cur.rowcount == 0:
                    current = self._conn.execute(_SELECT_ONE_SQL, (skill.name,)).fetchone()
                    self._conn.rollback()
                    if current is None:
                        raise SkillNotFoundError(f"Skill {skill.name!r} not found")
                    raise _revision_conflict(_require_skill(current, skill.name), expected_revision)
                self._conn.execute(_RECORD_REVISION_SQL, (skill.name,))
                # Read back before committing, as in _create_sync.
                row = self._conn.execute(_SELECT_ONE_SQL, (skill.name,)).fetchone()
                self._conn.commit()
            except SkillError:
                raise
            except Exception:
                self._conn.rollback()
                raise
        return _require_skill(row, skill.name)

    async def set_enabled(self, name: str, enabled: bool, updated_by: str | None = None) -> Skill:
        return await asyncio.to_thread(
            self._update_sync,
            name,
            "SET enabled = ?, updated_at = ?, updated_by = ?",
            (1 if enabled else 0, _now_iso(), updated_by),
        )

    def _update_sync(self, name: str, set_clause: str, params: tuple) -> Skill:
        self._ensure_open_sync()
        assert self._conn is not None
        with self._lock:
            try:
                cur = self._conn.execute(f"UPDATE skills {set_clause} WHERE name = ?", (*params, name))
                if cur.rowcount == 0:
                    self._conn.rollback()
                    raise SkillNotFoundError(f"Skill {name!r} not found")
                # Read back before committing, as in _create_sync.
                row = self._conn.execute(_SELECT_ONE_SQL, (name,)).fetchone()
                self._conn.commit()
            except SkillNotFoundError:
                raise
            except Exception:
                self._conn.rollback()
                raise
        return _require_skill(row, name)

    async def delete_skill(self, name: str) -> int:
        return await asyncio.to_thread(self._delete_sync, name)

    def _delete_sync(self, name: str) -> int:
        self._ensure_open_sync()
        assert self._conn is not None
        with self._lock:
            try:
                # The first DELETE opens the write transaction, so the revision read is stable.
                self._conn.execute("DELETE FROM skill_revisions WHERE name = ?", (name,))
                row = self._conn.execute("SELECT revision FROM skills WHERE name = ?", (name,)).fetchone()
                if row is None:
                    self._conn.rollback()
                    raise SkillNotFoundError(f"Skill {name!r} not found")
                self._conn.execute("DELETE FROM skills WHERE name = ?", (name,))
                self._conn.commit()
            except SkillNotFoundError:
                raise
            except Exception:
                self._conn.rollback()
                raise
        return row[0]

    # ------------------------------------------------------------------
    # Internal helpers (run inside ``asyncio.to_thread``)
    # ------------------------------------------------------------------

    def _fetchone(self, sql: str, params: tuple) -> tuple | None:
        self._ensure_open_sync()
        assert self._conn is not None
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: tuple) -> list[tuple]:
        self._ensure_open_sync()
        assert self._conn is not None
        with self._lock:
            return self._conn.execute(sql, params).fetchall()


def _migrate_revisions(conn: sqlite3.Connection) -> None:
    """Bring a database created before revisions up to date: revision 1 plus its history entry."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(skills)")}
    if "revision" not in columns:
        try:
            conn.execute("ALTER TABLE skills ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError as exc:
            # Another worker sharing the file migrated it first.
            if "duplicate column" not in str(exc):
                raise
    conn.execute(_BASELINE_REVISIONS_SQL)


def _revision_conflict(current: Skill, expected_revision: int | None) -> SkillRevisionConflictError:
    return SkillRevisionConflictError(
        f"Skill {current.name!r} is at revision {current.revision}, not {expected_revision}", current=current
    )


def _dump_parameters(skill: Skill) -> str:
    return json.dumps([parameter.model_dump() for parameter in skill.parameters])


def _require_skill(row: tuple | None, name: str) -> Skill:
    skill = _row_to_skill(row) if row is not None else None
    if skill is None:  # pragma: no cover - only reachable if the row vanished or was hand-corrupted mid-call
        raise RuntimeError(f"skills row for {name!r} is missing or unreadable after write")
    return skill


def _loads_parameters(name: str, raw: object) -> list:
    """Decode the stored parameter list, degrading to ``[]`` on garbage."""
    try:
        decoded = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        decoded = None
    if not isinstance(decoded, list):
        logger.warning("Ignoring unparseable skill parameters for skill=%s", name)
        return []
    return decoded


def _row_to_skill(row: tuple) -> Skill | None:
    name, display_name, description, instructions, parameters, enabled, revision, created_at, updated_at, updated_by = (
        row
    )
    try:
        return Skill(
            name=name,
            display_name=display_name,
            description=description,
            instructions=instructions,
            parameters=_loads_parameters(name, parameters),
            enabled=bool(enabled),
            revision=revision,
            created_at=datetime.fromisoformat(created_at),
            updated_at=datetime.fromisoformat(updated_at),
            updated_by=updated_by,
        )
    except (ValidationError, ValueError):
        logger.warning("Skipping unreadable skill row skill=%s", name, exc_info=True)
        return None


def _row_to_revision(row: tuple) -> SkillRevision | None:
    name, revision, display_name, description, instructions, parameters, updated_at, updated_by = row
    try:
        return SkillRevision(
            name=name,
            revision=revision,
            display_name=display_name,
            description=description,
            instructions=instructions,
            parameters=_loads_parameters(name, parameters),
            updated_at=datetime.fromisoformat(updated_at),
            updated_by=updated_by,
        )
    except (ValidationError, ValueError):
        logger.warning("Skipping unreadable skill revision row skill=%s revision=%s", name, revision, exc_info=True)
        return None


def _row_to_catalog_entry(row: tuple) -> SkillCatalogEntry | None:
    name, display_name, description, parameters, revision = row
    try:
        return SkillCatalogEntry(
            name=name,
            display_name=display_name,
            description=description,
            parameters=_loads_parameters(name, parameters),
            revision=revision,
        )
    except ValidationError:
        logger.warning("Skipping unreadable skill catalog row skill=%s", name, exc_info=True)
        return None
