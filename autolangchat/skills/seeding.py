"""Default skills seeded from preset prompts.

Each preset becomes a skill named after its ``id`` (the label slug assigned by
the plugin). Seeding is create-only: an existing skill is never replaced or
re-enabled, so admin edits and disables survive restarts, while a deleted
default is created again because its preset still exists.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping

from ..exceptions import SkillAlreadyExistsError
from .models import Skill, SkillParameter, validate_skill_limits

if TYPE_CHECKING:
    from ..db.skill_base import BaseSkillStore

logger = logging.getLogger(__name__)

PRESET_SEED_ACTOR = "preset-seed"

# Same placeholder syntax the preset UI resolves.
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

# Preset variable fields copied onto the skill parameter; the rest (validate, detect_*, default, min/max/step) are dropped.
_VARIABLE_FIELDS = ("label", "placeholder", "input_type", "options")


def _option_value(option: Any) -> str | None:
    """Return the string value of a preset select option (a string or a ``{value, label}`` record), else None."""
    if isinstance(option, str):
        return option
    if isinstance(option, Mapping) and isinstance(option.get("value"), str):
        return option["value"]
    return None


def _parameter(placeholder: str, variable: Mapping[str, Any]) -> SkillParameter:
    hints: Dict[str, Any] = {key: variable[key] for key in _VARIABLE_FIELDS if variable.get(key) is not None}
    if "options" in hints:
        if not isinstance(hints["options"], list):
            raise ValueError("variable options must be a list")
        values = (_option_value(option) for option in hints["options"])
        hints["options"] = [value for value in values if value is not None]
    return SkillParameter(name=placeholder.lower(), required=True, **hints)


def preset_to_skill(preset: Mapping[str, Any], variables: Iterable[Mapping[str, Any]]) -> Skill:
    """Map a preset prompt (with its assigned ``id``) to the default skill it seeds.

    Every ``{{VAR}}`` placeholder in the template becomes a required parameter
    named ``var``, and the placeholders in the instructions are rewritten to match.

    Raises:
        ValueError: the resulting skill is not valid (pydantic ``ValidationError``: name pattern, field limits, ...).
    """
    template = str(preset.get("template") or "")
    by_name = {str(variable.get("name")): variable for variable in variables}
    placeholders = list(dict.fromkeys(_PLACEHOLDER_RE.findall(template)))
    return Skill(
        name=str(preset.get("id") or ""),
        display_name=str(preset.get("label") or ""),
        description=str(preset.get("description") or "").strip(),
        instructions=_PLACEHOLDER_RE.sub(lambda match: "{{" + match.group(1).lower() + "}}", template),
        parameters=[_parameter(placeholder, by_name.get(placeholder, {})) for placeholder in placeholders],
    )


async def seed_preset_skills(
    store: "BaseSkillStore",
    presets: Iterable[Mapping[str, Any]],
    variables: Iterable[Mapping[str, Any]],
    *,
    instructions_max_chars: int,
    description_max_chars: int,
    max_parameters: int,
) -> List[str]:
    """Create the default skill of every preset that has none; return the names created.

    An invalid preset is logged and skipped. A name that already exists,
    including one created concurrently by another process, counts as seeded.
    Store failures other than that conflict propagate.
    """
    variables = list(variables)
    created: List[str] = []
    for preset in presets:
        try:
            skill = preset_to_skill(preset, variables)
            validate_skill_limits(
                skill,
                instructions_max_chars=instructions_max_chars,
                description_max_chars=description_max_chars,
                max_parameters=max_parameters,
            )
        except ValueError as exc:
            logger.warning("Skipping default skill for preset prompt %r: %s", preset.get("label"), exc)
            continue
        try:
            await store.create_skill(skill, updated_by=PRESET_SEED_ACTOR)
        except SkillAlreadyExistsError:
            continue
        created.append(skill.name)
    if created:
        logger.info("Seeded %d default skill(s) from preset prompts: %s", len(created), ", ".join(created))
    return created
