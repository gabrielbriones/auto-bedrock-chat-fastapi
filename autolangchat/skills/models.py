"""Data models for admin-managed skills."""

import re
from datetime import datetime
from typing import Literal

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
    default: str | None = Field(default=None, max_length=1000)
    input_type: SkillInputType = "text"
    options: list[str] = Field(default_factory=list, max_length=50)
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
    def _validate_options(cls, value: list[str]) -> list[str]:
        if any(not option or len(option) > 200 for option in value):
            raise ValueError("options must be non-empty strings of at most 200 characters")
        return value


def _check_instructions(value: str) -> str:
    if not value.strip():
        raise ValueError("instructions must not be blank")
    return value


def _check_unique_parameter_names(parameters: list[SkillParameter]) -> None:
    names = [parameter.name for parameter in parameters]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate parameter names: {', '.join(duplicates)}")


CONTENT_FIELDS = frozenset({"display_name", "description", "instructions", "parameters"})


class SkillContent(BaseModel):
    """The admin-editable content of a skill (everything but its identity and bookkeeping)."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=100)
    description: str = ""
    instructions: str = Field(min_length=1)
    parameters: list[SkillParameter] = Field(default_factory=list)

    @field_validator("instructions")
    @classmethod
    def _validate_instructions(cls, value: str) -> str:
        return _check_instructions(value)

    @model_validator(mode="after")
    def _validate_unique_parameter_names(self) -> "SkillContent":
        _check_unique_parameter_names(self.parameters)
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
    # Starts at 1 and grows by one on every content change; enable/disable keeps it.
    revision: int = Field(default=1, ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None

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
    """Body of ``PUT /admin/skills/{name}``: the complete new content of the skill.

    When ``expected_revision`` is set, the write is refused unless it is the stored revision.
    """

    expected_revision: int | None = Field(default=None, ge=1)


class SkillPatchRequest(BaseModel):
    """Body of ``PATCH /admin/skills/{name}``: the content fields to change, guarded by ``expected_revision``."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    instructions: str | None = Field(default=None, min_length=1)
    parameters: list[SkillParameter] | None = None

    @field_validator("instructions")
    @classmethod
    def _validate_instructions(cls, value: str | None) -> str | None:
        return value if value is None else _check_instructions(value)

    @model_validator(mode="after")
    def _validate_changes(self) -> "SkillPatchRequest":
        if self.parameters is not None:
            _check_unique_parameter_names(self.parameters)
        if not self.changes():
            raise ValueError(f"at least one of {', '.join(sorted(CONTENT_FIELDS))} is required")
        return self

    def changes(self) -> dict:
        """The content fields this patch sets (``null`` counts as not set)."""
        return self.model_dump(include=set(CONTENT_FIELDS), exclude_none=True)


class SkillRollbackRequest(BaseModel):
    """Body of ``POST /admin/skills/{name}/rollback``."""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1, description="The revision whose content is restored as a new revision")
    expected_revision: int | None = Field(default=None, ge=1)


class SkillSummary(BaseModel):
    """A skill without its instructions, as listed by ``GET /admin/skills``."""

    name: str
    display_name: str
    description: str = ""
    parameters: list[SkillParameter] = Field(default_factory=list)
    enabled: bool
    revision: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


class SkillLimits(BaseModel):
    """The configured skill size limits, so the dashboard can validate before saving."""

    skill_instructions_max_chars: int
    skill_description_max_chars: int
    skill_max_parameters: int


class SkillListResponse(BaseModel):
    """Envelope returned by ``GET /admin/skills``."""

    items: list[SkillSummary]
    limits: SkillLimits


class SkillRevision(SkillContent):
    """The full content of one stored revision of a skill."""

    name: str
    revision: int
    updated_at: datetime | None = None
    updated_by: str | None = None


class SkillRevisionSummary(BaseModel):
    """A revision without its content, as listed by ``GET /admin/skills/{name}/revisions``."""

    revision: int
    updated_at: datetime | None = None
    updated_by: str | None = None


class SkillRevisionListResponse(BaseModel):
    """Envelope returned by ``GET /admin/skills/{name}/revisions`` (newest first)."""

    items: list[SkillRevisionSummary]


class SkillCatalogEntry(BaseModel):
    """The user-facing view of an enabled skill.

    Deliberately has no ``instructions`` field so the catalog cannot leak them.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    description: str = ""
    parameters: list[SkillParameter] = Field(default_factory=list)
    revision: int = 1


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

    items: list[SkillCatalogEntry]
