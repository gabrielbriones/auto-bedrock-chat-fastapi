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
:func:`~autolangchat.skills.models.validate_skill_limits` first. An update
replaces the whole skill content (last write wins); there is no versioning.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..skills.models import Skill, SkillCatalogEntry


class BaseSkillStore(ABC):
    """Async interface for admin-managed skills."""

    @abstractmethod
    async def open(self) -> None:
        """Open connections and (optionally) bootstrap the schema."""

    @abstractmethod
    async def close(self) -> None:
        """Release connections."""

    @abstractmethod
    async def list_skills(self) -> List[Skill]:
        """Return every skill (enabled or not), ordered by name."""

    @abstractmethod
    async def get_skill(self, name: str) -> Optional[Skill]:
        """Return the skill called ``name``, or ``None`` when it does not exist."""

    @abstractmethod
    async def create_skill(self, skill: Skill, updated_by: Optional[str] = None) -> Skill:
        """Insert ``skill``, stamping ``created_at``/``updated_at``/``updated_by``.

        Raises
        ------
        SkillAlreadyExistsError
            If a skill with the same name exists.
        """

    @abstractmethod
    async def replace_skill(self, skill: Skill, updated_by: Optional[str] = None) -> Skill:
        """Overwrite the content of the existing skill ``skill.name``.

        Replaces ``display_name``, ``description``, ``instructions`` and
        ``parameters``. ``enabled`` and ``created_at`` are preserved (use
        :meth:`set_enabled` to toggle); ``updated_at``/``updated_by`` are
        restamped.

        Raises
        ------
        SkillNotFoundError
            If no skill with that name exists.
        """

    @abstractmethod
    async def set_enabled(self, name: str, enabled: bool, updated_by: Optional[str] = None) -> Skill:
        """Enable or disable a skill and return the updated record.

        Raises
        ------
        SkillNotFoundError
            If no skill with that name exists.
        """

    @abstractmethod
    async def delete_skill(self, name: str) -> None:
        """Permanently delete a skill.

        Raises
        ------
        SkillNotFoundError
            If no skill with that name exists.
        """

    @abstractmethod
    async def list_enabled_catalog(self) -> List[SkillCatalogEntry]:
        """Return the user-facing view of enabled skills, ordered by name.

        Implementations must not read or return ``instructions``.
        """
