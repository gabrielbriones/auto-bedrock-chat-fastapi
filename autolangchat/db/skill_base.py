"""
Skill storage — abstract interface.

Concrete backends live in:

* :mod:`autolangchat.db.skill_sqlite` (zero-config default)
* :mod:`autolangchat.db.skill_postgres` (production)

Use :func:`autolangchat.db.create_skill_store` to build the backend. There is
deliberately no skill-specific storage-type setting: the backend is inferred
from whichever database the app's sibling stores already use.

Stores persist and return :class:`~autolangchat.skills.models.Skill` records
but do not enforce the configurable size limits; callers validate with
:func:`~autolangchat.skills.models.validate_skill_limits` first.

Versioning: every skill carries an integer ``revision`` (1 on create, +1 on
every content write). Each revision's content is kept in a history table,
so earlier content can be listed, read and restored (as a new revision;
history is never rewritten). Enable/disable does not create a revision.
Content writes accept an ``expected_revision`` and raise
:class:`~autolangchat.exceptions.SkillRevisionConflictError` on mismatch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..skills.models import Skill, SkillCatalogEntry, SkillRevision, SkillRevisionSummary


class BaseSkillStore(ABC):
    """Async interface for admin-managed skills."""

    @abstractmethod
    async def open(self) -> None:
        """Open connections and (optionally) bootstrap the schema."""

    @abstractmethod
    async def close(self) -> None:
        """Release connections."""

    @abstractmethod
    async def list_skills(self) -> list[Skill]:
        """Return every skill (enabled or not), ordered by name."""

    @abstractmethod
    async def get_skill(self, name: str) -> Skill | None:
        """Return the skill called ``name``, or ``None`` when it does not exist."""

    @abstractmethod
    async def create_skill(self, skill: Skill, updated_by: str | None = None) -> Skill:
        """Insert ``skill`` at revision 1, stamping ``created_at``/``updated_at``/``updated_by``.

        Also records revision 1 in the history.

        Raises
        ------
        SkillAlreadyExistsError
            If a skill with the same name exists.
        """

    @abstractmethod
    async def replace_skill(
        self, skill: Skill, updated_by: str | None = None, expected_revision: int | None = None
    ) -> Skill:
        """Overwrite the content of the existing skill ``skill.name`` as a new revision.

        Replaces ``display_name``, ``description``, ``instructions`` and
        ``parameters``, increments ``revision`` and records it in the history.
        ``enabled`` and ``created_at`` are preserved (use :meth:`set_enabled`
        to toggle); ``updated_at``/``updated_by`` are restamped.

        Raises
        ------
        SkillNotFoundError
            If no skill with that name exists.
        SkillRevisionConflictError
            If ``expected_revision`` is set and differs from the stored
            revision; nothing is written.
        """

    @abstractmethod
    async def set_enabled(self, name: str, enabled: bool, updated_by: str | None = None) -> Skill:
        """Enable or disable a skill and return the updated record (``revision`` unchanged).

        Raises
        ------
        SkillNotFoundError
            If no skill with that name exists.
        """

    @abstractmethod
    async def delete_skill(self, name: str) -> int:
        """Permanently delete a skill and its history; return its last revision.

        Raises
        ------
        SkillNotFoundError
            If no skill with that name exists.
        """

    @abstractmethod
    async def list_revisions(self, name: str) -> list[SkillRevisionSummary]:
        """Return the stored revisions of a skill, newest first, without their content.

        Raises
        ------
        SkillNotFoundError
            If no skill with that name exists.
        """

    @abstractmethod
    async def get_revision(self, name: str, revision: int) -> SkillRevision | None:
        """Return the content of one revision, or ``None`` when it is not stored."""

    @abstractmethod
    async def list_enabled_catalog(self) -> list[SkillCatalogEntry]:
        """Return the user-facing view of enabled skills, ordered by name.

        Implementations must not read or return ``instructions``.
        """
