"""Runtime access to skills: the enabled catalog and ``load_skill``.

:class:`SkillService` sits between the skill store and the chat runtime:

* :meth:`SkillService.load` validates a model's ``load_skill`` call and
  renders the skill (see :func:`~autolangchat.skills.prompt.render_skill`).
  It always reads the store, so a load reflects the latest content and
  enabled state. It never calls an LLM.
* :meth:`SkillService.refresh` returns a cached snapshot of the enabled
  catalog, re-read only after :meth:`SkillService.mark_changed` bumped the
  change counter. The admin skill routes call ``mark_changed`` after every
  write; the counter is per process, so a write made by another process is
  only seen after a restart or a local write.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

from ..defaults import DEFAULT_SKILL_PARAM_VALUE_MAX_CHARS
from .models import SKILL_NAME_PATTERN, SkillCatalogEntry
from .prompt import render_skill, skill_revision

if TYPE_CHECKING:
    from ..db.skill_base import BaseSkillStore

logger = logging.getLogger(__name__)

UNKNOWN_SKILL = "unknown_skill"
SKILL_DISABLED = "skill_disabled"
SKILLS_UNAVAILABLE = "skills_unavailable"
INVALID_SKILL_ARGUMENTS = "invalid_arguments"


class SkillLoadError(Exception):
    """A ``load_skill`` call that cannot be served; ``code`` is reported to the model."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LoadedSkill:
    """A rendered skill and the reference recorded on its tool result."""

    rendered_text: str
    skill_ref: Dict[str, str]


@dataclass(frozen=True)
class SkillCatalogSnapshot:
    """The enabled skills as of change-counter ``version`` (``-1``: never loaded)."""

    version: int
    entries: Tuple[SkillCatalogEntry, ...] = field(default_factory=tuple)


_NOT_LOADED = SkillCatalogSnapshot(version=-1)


def _reject_duplicate_keys(pairs: list) -> Dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise SkillLoadError(INVALID_SKILL_ARGUMENTS, f"Duplicate parameter name(s): {', '.join(duplicates)}")
    return dict(pairs)


def _coerce_params(params: Any) -> Dict[str, Any]:
    """``params`` as a dict. A JSON-encoded object (some models send one) is decoded, rejecting duplicate keys."""
    if params is None:
        return {}
    if isinstance(params, str):
        if not params.strip():
            return {}
        try:
            params = json.loads(params, object_pairs_hook=_reject_duplicate_keys)
        except ValueError as error:
            raise SkillLoadError(INVALID_SKILL_ARGUMENTS, "params must be an object of strings") from error
    if not isinstance(params, dict):
        raise SkillLoadError(INVALID_SKILL_ARGUMENTS, "params must be an object of strings")
    return params


class SkillService:
    """Loads skills for the model and tracks catalog changes.

    Args:
        get_store: Returns the active skill store, or ``None`` when skills are
            unavailable (resolved per call because the store is opened, and may
            be dropped, after the service is built).
        param_value_max_chars: Maximum length of one parameter value.
    """

    def __init__(
        self,
        get_store: Callable[[], Optional["BaseSkillStore"]],
        *,
        param_value_max_chars: int = DEFAULT_SKILL_PARAM_VALUE_MAX_CHARS,
    ):
        self._get_store = get_store
        self._param_value_max_chars = param_value_max_chars
        self._version = 0
        self._snapshot = _NOT_LOADED

    @property
    def version(self) -> int:
        """The change counter; bumped by :meth:`mark_changed`."""
        return self._version

    @property
    def snapshot(self) -> SkillCatalogSnapshot:
        """The last catalog read by :meth:`refresh` (version ``-1`` before the first one)."""
        return self._snapshot

    def mark_changed(self) -> None:
        """Record that a skill was created, replaced, enabled, disabled or deleted."""
        self._version += 1

    async def refresh(self) -> SkillCatalogSnapshot:
        """The enabled catalog for the current change counter, read from the store only when it changed.

        A store failure keeps the previous snapshot, so the next call retries.
        """
        version = self._version
        if self._snapshot.version == version:
            return self._snapshot
        store = self._get_store()
        if store is None:
            entries: Tuple[SkillCatalogEntry, ...] = ()
        else:
            try:
                entries = tuple(await store.list_enabled_catalog())
            except Exception:
                logger.warning("Could not read the skill catalog; keeping the previous one", exc_info=True)
                return self._snapshot
        # The version read before the store call: a write made meanwhile triggers another read.
        self._snapshot = SkillCatalogSnapshot(version=version, entries=entries)
        return self._snapshot

    async def load(self, name: Any, params: Any = None, actor: Optional[str] = None) -> LoadedSkill:
        """Validate a ``load_skill`` call and render the skill.

        Raises:
            SkillLoadError: unknown or disabled skill, unavailable store, or
                invalid parameters (unknown or duplicate names, non-string or
                oversized values, missing required ones).
        """
        if not isinstance(name, str) or not SKILL_NAME_PATTERN.fullmatch(name):
            raise SkillLoadError(UNKNOWN_SKILL, f"Unknown skill: {name!r}")
        values = _coerce_params(params)

        store = self._get_store()
        if store is None:
            raise SkillLoadError(SKILLS_UNAVAILABLE, "Skills are not available right now.")
        skill = await store.get_skill(name)
        if skill is None:
            raise SkillLoadError(UNKNOWN_SKILL, f"Unknown skill: {name!r}")
        if not skill.enabled:
            raise SkillLoadError(SKILL_DISABLED, f"Skill {name!r} is disabled.")

        declared = {parameter.name: parameter for parameter in skill.parameters}
        unknown = sorted(set(values) - set(declared))
        if unknown:
            valid = ", ".join(declared) or "none"
            raise SkillLoadError(
                INVALID_SKILL_ARGUMENTS,
                f"Unknown parameter(s) for skill {name!r}: {', '.join(unknown)}. Valid parameters: {valid}.",
            )
        for key, value in values.items():
            if not isinstance(value, str):
                raise SkillLoadError(INVALID_SKILL_ARGUMENTS, f"Parameter {key!r} must be a string.")
            if len(value) > self._param_value_max_chars:
                raise SkillLoadError(
                    INVALID_SKILL_ARGUMENTS,
                    f"Parameter {key!r} exceeds {self._param_value_max_chars} characters.",
                )

        resolved: Dict[str, Optional[str]] = {}
        missing = []
        for parameter in skill.parameters:
            value = values.get(parameter.name)
            if value is None or not value.strip():
                value = parameter.default
            if parameter.required and (value is None or not value.strip()):
                missing.append(parameter.name)
            resolved[parameter.name] = value
        if missing:
            raise SkillLoadError(
                INVALID_SKILL_ARGUMENTS,
                f"Missing required parameter(s) for skill {name!r}: {', '.join(missing)}. "
                "Ask the user for them, then call load_skill again.",
            )

        revision = skill_revision(skill)
        logger.info("skill.load name=%s revision=%s actor=%s", name, revision, actor or "anonymous")
        return LoadedSkill(
            rendered_text=render_skill(skill, resolved),
            skill_ref={"name": skill.name, "revision": revision},
        )
