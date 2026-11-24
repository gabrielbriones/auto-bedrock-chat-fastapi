"""
PostgreSQL-backed SkillStore.

Production backend for :class:`~.skill_base.BaseSkillStore`.
Schema: ``autolangchat/db/sql/skill_schema.sql``.

Requires the optional ``[postgres]`` extra::

    pip install autolangchat[postgres]
"""

from __future__ import annotations

import logging
from importlib import resources
from typing import Any

from pydantic import ValidationError

from ..exceptions import SkillAlreadyExistsError, SkillNotFoundError, SkillRevisionConflictError
from ..skills.models import Skill, SkillCatalogEntry, SkillRevision, SkillRevisionSummary
from .skill_base import BaseSkillStore

logger = logging.getLogger(__name__)

_COLUMNS = (
    "name, display_name, description, instructions, parameters, enabled, revision, created_at, updated_at, updated_by"
)
_SELECT_ONE_SQL = f"SELECT {_COLUMNS} FROM skills WHERE name = %s"
_SELECT_ALL_SQL = f"SELECT {_COLUMNS} FROM skills ORDER BY name"
_SELECT_CATALOG_SQL = (
    "SELECT name, display_name, description, parameters, revision FROM skills WHERE enabled ORDER BY name"
)
_REVISION_COLUMNS = "name, revision, display_name, description, instructions, parameters, updated_at, updated_by"
# Copies the current content of skill ``name`` into the history (inside the write transaction).
_RECORD_REVISION_SQL = (
    f"INSERT INTO skill_revisions ({_REVISION_COLUMNS}) SELECT {_REVISION_COLUMNS} FROM skills WHERE name = %s"
)
_SELECT_REVISIONS_SQL = (
    "SELECT revision, updated_at, updated_by FROM skill_revisions WHERE name = %s ORDER BY revision DESC"
)
_SELECT_REVISION_SQL = f"SELECT {_REVISION_COLUMNS} FROM skill_revisions WHERE name = %s AND revision = %s"

_MISSING_DEPS_MSG = (
    "PostgresSkillStore requires the optional PostgreSQL packages. "
    "Install them with:  pip install autolangchat[postgres]"
)


def _import_psycopg_async() -> tuple[Any, Any, Any]:
    """Return ``(psycopg, AsyncConnectionPool, Jsonb)`` or raise ``ImportError``."""
    try:
        import psycopg  # noqa: F401
        from psycopg.types.json import Jsonb
        from psycopg_pool import AsyncConnectionPool

        return psycopg, AsyncConnectionPool, Jsonb
    except ImportError as exc:  # pragma: no cover - exercised in env-less CI
        raise ImportError(_MISSING_DEPS_MSG) from exc


class PostgresSkillStore(BaseSkillStore):
    """Async PostgreSQL-backed store for admin-managed skills.

    Parameters
    ----------
    connection_url:
        ``libpq``-style PostgreSQL connection URL.
    pool_min_size, pool_max_size:
        Bounds for the underlying :class:`psycopg_pool.AsyncConnectionPool`.
    init_schema:
        When ``True`` (default), execute ``sql/skill_schema.sql`` against the
        database on :meth:`open`. Set ``False`` if a separate provisioning
        task owns the DDL lifecycle.
    """

    SCHEMA_RESOURCE = ("autolangchat.db.sql", "skill_schema.sql")

    def __init__(
        self,
        connection_url: str,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
        init_schema: bool = True,
    ) -> None:
        psycopg, AsyncConnectionPool, Jsonb = _import_psycopg_async()
        self._psycopg = psycopg
        self._Jsonb = Jsonb
        self._connection_url = connection_url
        self._init_schema = init_schema
        self._pool: Any = AsyncConnectionPool(
            conninfo=connection_url,
            min_size=pool_min_size,
            max_size=pool_max_size,
            open=False,
            kwargs={"autocommit": False},
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Open the connection pool and (optionally) bootstrap the schema."""
        await self._pool.open()
        if self._init_schema:
            await self._apply_schema()
        logger.info("PostgresSkillStore ready (init_schema=%s)", self._init_schema)

    async def close(self) -> None:
        """Close the connection pool."""
        await self._pool.close(timeout=5)

    async def _apply_schema(self) -> None:
        package, filename = self.SCHEMA_RESOURCE
        ddl = resources.files(package).joinpath(filename).read_text(encoding="utf-8")
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(ddl)
            await conn.commit()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_skills(self) -> list[Skill]:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(_SELECT_ALL_SQL)
            rows = await cur.fetchall()
        return [skill for skill in (_row_to_skill(row) for row in rows) if skill is not None]

    async def get_skill(self, name: str) -> Skill | None:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(_SELECT_ONE_SQL, (name,))
            row = await cur.fetchone()
        return _row_to_skill(row) if row else None

    async def list_enabled_catalog(self) -> list[SkillCatalogEntry]:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(_SELECT_CATALOG_SQL)
            rows = await cur.fetchall()
        entries = (_row_to_catalog_entry(row) for row in rows)
        return [entry for entry in entries if entry is not None]

    async def list_revisions(self, name: str) -> list[SkillRevisionSummary]:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM skills WHERE name = %s", (name,))
            exists = await cur.fetchone()
            await cur.execute(_SELECT_REVISIONS_SQL, (name,))
            rows = await cur.fetchall()
        if exists is None:
            raise SkillNotFoundError(f"Skill {name!r} not found")
        return [
            SkillRevisionSummary(revision=revision, updated_at=updated_at, updated_by=updated_by)
            for revision, updated_at, updated_by in rows
        ]

    async def get_revision(self, name: str, revision: int) -> SkillRevision | None:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(_SELECT_REVISION_SQL, (name, revision))
            row = await cur.fetchone()
        return _row_to_revision(row) if row else None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create_skill(self, skill: Skill, updated_by: str | None = None) -> Skill:
        sql = f"""
            INSERT INTO skills ({_COLUMNS})
                 VALUES (%s, %s, %s, %s, %s, %s, 1, now(), now(), %s)
            ON CONFLICT (name) DO NOTHING
              RETURNING {_COLUMNS}
        """
        params = (
            skill.name,
            skill.display_name,
            skill.description,
            skill.instructions,
            self._parameters_json(skill),
            skill.enabled,
            updated_by,
        )
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            row = await cur.fetchone()
            if row is not None:
                await cur.execute(_RECORD_REVISION_SQL, (skill.name,))
            await conn.commit()
        if row is None:
            raise SkillAlreadyExistsError(f"Skill {skill.name!r} already exists")
        return _require_skill(row, skill.name)

    async def replace_skill(
        self, skill: Skill, updated_by: str | None = None, expected_revision: int | None = None
    ) -> Skill:
        params: tuple = (
            skill.display_name,
            skill.description,
            skill.instructions,
            self._parameters_json(skill),
            updated_by,
            skill.name,
        )
        where = "name = %s"
        if expected_revision is not None:
            where += " AND revision = %s"
            params += (expected_revision,)
        sql = f"""
            UPDATE skills
               SET display_name = %s, description = %s, instructions = %s, parameters = %s,
                   revision = revision + 1, updated_at = now(), updated_by = %s
             WHERE {where}
         RETURNING {_COLUMNS}
        """
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            row = await cur.fetchone()
            if row is None:
                await cur.execute(_SELECT_ONE_SQL, (skill.name,))
                current = await cur.fetchone()
                await conn.rollback()
                if current is None:
                    raise SkillNotFoundError(f"Skill {skill.name!r} not found")
                current_skill = _require_skill(current, skill.name)
                raise SkillRevisionConflictError(
                    f"Skill {skill.name!r} is at revision {current_skill.revision}, not {expected_revision}",
                    current=current_skill,
                )
            await cur.execute(_RECORD_REVISION_SQL, (skill.name,))
            await conn.commit()
        return _require_skill(row, skill.name)

    async def set_enabled(self, name: str, enabled: bool, updated_by: str | None = None) -> Skill:
        sql = f"""
            UPDATE skills
               SET enabled = %s, updated_at = now(), updated_by = %s
             WHERE name = %s
         RETURNING {_COLUMNS}
        """
        return await self._update_returning(sql, (enabled, updated_by, name), name)

    async def delete_skill(self, name: str) -> int:
        # skill_revisions rows go with it (ON DELETE CASCADE).
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("DELETE FROM skills WHERE name = %s RETURNING revision", (name,))
            row = await cur.fetchone()
            await conn.commit()
        if row is None:
            raise SkillNotFoundError(f"Skill {name!r} not found")
        return row[0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _update_returning(self, sql: str, params: tuple, name: str) -> Skill:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            row = await cur.fetchone()
            await conn.commit()
        if row is None:
            raise SkillNotFoundError(f"Skill {name!r} not found")
        return _require_skill(row, name)

    def _parameters_json(self, skill: Skill) -> Any:
        return self._Jsonb([parameter.model_dump() for parameter in skill.parameters])


def _require_skill(row: tuple | None, name: str) -> Skill:
    skill = _row_to_skill(row) if row is not None else None
    if skill is None:  # pragma: no cover - only reachable if the row was hand-corrupted mid-call
        raise RuntimeError(f"skills row for {name!r} is unreadable after write")
    return skill


def _parameters_list(name: str, raw: Any) -> list:
    """psycopg decodes JSONB; guard against a non-array document from a hand-edited row."""
    if not isinstance(raw, list):
        logger.warning("Ignoring non-array skill parameters for skill=%s", name)
        return []
    return raw


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
            parameters=_parameters_list(name, parameters),
            enabled=bool(enabled),
            revision=revision,
            created_at=created_at,
            updated_at=updated_at,
            updated_by=updated_by,
        )
    except ValidationError:
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
            parameters=_parameters_list(name, parameters),
            updated_at=updated_at,
            updated_by=updated_by,
        )
    except ValidationError:
        logger.warning("Skipping unreadable skill revision row skill=%s revision=%s", name, revision, exc_info=True)
        return None


def _row_to_catalog_entry(row: tuple) -> SkillCatalogEntry | None:
    name, display_name, description, parameters, revision = row
    try:
        return SkillCatalogEntry(
            name=name,
            display_name=display_name,
            description=description,
            parameters=_parameters_list(name, parameters),
            revision=revision,
        )
    except ValidationError:
        logger.warning("Skipping unreadable skill catalog row skill=%s", name, exc_info=True)
        return None
