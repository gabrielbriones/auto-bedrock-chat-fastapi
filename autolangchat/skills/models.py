"""Data models for admin-managed skills."""

import re
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

SkillInputType = Literal["text", "number", "select", "checkbox"]


class SkillParameter(BaseModel):
    """A named input a skill accepts.

    Every field besides ``name`` and ``required`` is a presentation hint for
    the composer UI. Runtime values are always strings; the server performs
    no type validation against ``input_type`` or ``options``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str = Field(default="", max_length=100)
    help: str = Field(default="", max_length=500)
    required: bool = False
    default: Optional[str] = Field(default=None, max_length=1000)
    input_type: SkillInputType = "text"
    options: List[str] = Field(default_factory=list, max_length=50)
    placeholder: str = Field(default="", max_length=200)
    example: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not SKILL_NAME_PATTERN.fullmatch(value):
            raise ValueError(f"parameter name {value!r} must match {SKILL_NAME_PATTERN.pattern}")
        return value

    @field_validator("options")
    @classmethod
    def _validate_options(cls, value: List[str]) -> List[str]:
        if any(not option or len(option) > 200 for option in value):
            raise ValueError("options must be non-empty strings of at most 200 characters")
        return value


class SkillContent(BaseModel):
    """The admin-editable content of a skill (everything but its identity and bookkeeping)."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=100)
    description: str = ""
    instructions: str = Field(min_length=1)
    parameters: List[SkillParameter] = Field(default_factory=list)

    @field_validator("instructions")
    @classmethod
    def _validate_instructions(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("instructions must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_unique_parameter_names(self) -> "SkillContent":
        names = [parameter.name for parameter in self.parameters]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate parameter names: {', '.join(duplicates)}")
        return self


def _check_skill_name(value: str) -> str:
    if not SKILL_NAME_PATTERN.fullmatch(value):
        raise ValueError(f"skill name {value!r} must match {SKILL_NAME_PATTERN.pattern}")
    return value


class Skill(SkillContent):
    """An on-demand instruction set managed by admins.

    The configurable size limits (``skill_instructions_max_chars``,
    ``skill_description_max_chars``, ``skill_max_parameters``) are enforced
    by :func:`validate_skill_limits`, since they come from ``ChatConfig``.
    """

    name: str
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _check_skill_name(value)


class SkillCreateRequest(SkillContent):
    """Body of ``POST /admin/skills``."""

    name: str
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _check_skill_name(value)


class SkillReplaceRequest(SkillContent):
    """Body of ``PUT /admin/skills/{name}``: the complete new content of the skill."""


class SkillSummary(BaseModel):
    """A skill without its instructions, as listed by ``GET /admin/skills``."""

    name: str
    display_name: str
    description: str = ""
    parameters: List[SkillParameter] = Field(default_factory=list)
    enabled: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


class SkillListResponse(BaseModel):
    """Envelope returned by ``GET /admin/skills``."""

    items: List[SkillSummary]


class SkillCatalogEntry(BaseModel):
    """The user-facing view of an enabled skill.

    Deliberately has no ``instructions`` field so the catalog cannot leak them.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    description: str = ""
    parameters: List[SkillParameter] = Field(default_factory=list)


def validate_skill_limits(
    skill: Skill,
    *,
    instructions_max_chars: int,
    description_max_chars: int,
    max_parameters: int,
) -> None:
    """Raise ``ValueError`` when ``skill`` exceeds the configured size limits."""
    if len(skill.instructions) > instructions_max_chars:
        raise ValueError(f"instructions exceed {instructions_max_chars} characters")
    if len(skill.description) > description_max_chars:
        raise ValueError(f"description exceeds {description_max_chars} characters")
    if len(skill.parameters) > max_parameters:
        raise ValueError(f"skill declares more than {max_parameters} parameters")


class SkillCatalogResponse(BaseModel):
    """Envelope returned by ``GET {chat_endpoint}/skills/catalog``."""

    items: List[SkillCatalogEntry]
