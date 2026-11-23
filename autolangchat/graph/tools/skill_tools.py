"""``load_skill``: load an admin-managed skill's instructions into the conversation.

A session-free ``InternalTool`` over :class:`~autolangchat.skills.service.SkillService`.
The schema is fixed (``name`` plus a ``params`` object of strings), so
parameter names can vary per skill without regenerating the tool. The tool is
only advertised while the bound skill snapshot has at least one enabled skill.

The result is the rendered skill text; the entry's ``metadata.skill_ref``
records which skill and revision were loaded. A result that would not fit the
turn's ``ContextBudget`` is refused with ``context_exhausted``: instructions
are never cut short.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ...defaults import CONTEXT_BUDGET_KINDS
from ...skills.prompt import LOAD_SKILL
from ...skills.service import SkillLoadError, SkillService
from .internal import CONTEXT_EXHAUSTED, INVALID_ARGUMENTS, InternalTool, ToolContext, ToolOutput, tool_error

_ARGUMENTS = frozenset({"name", "params"})

LOAD_SKILL_DESC: Dict[str, Any] = {
    "name": LOAD_SKILL,
    "description": (
        "Load the instructions of a skill listed under AVAILABLE SKILLS in the system prompt. Call it when the "
        "user's request matches a skill, then follow the returned instructions. Pass every parameter value as a "
        "string; if a required parameter is unknown, ask the user for it first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name exactly as listed under AVAILABLE SKILLS."},
            "params": {
                "type": "object",
                "description": "Parameter values keyed by parameter name. Every value must be a string.",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["name"],
    },
}


_RESERVED_KEY = "load_skill"


def _reserve(text: str, ctx: ToolContext) -> bool:
    """Whether ``text`` fits what is left of the turn's budget after the batch's earlier loads; reserves it if so."""
    budget = ctx.context_budget
    if budget is None:
        return True
    allowance = min(budget.allowance_for(kind) for kind in CONTEXT_BUDGET_KINDS)
    reserved = ctx.reserved_chars.get(_RESERVED_KEY, 0)
    # Raw length: the result string is what reaches the model. No await between check and update.
    if reserved + len(text) > allowance:
        return False
    ctx.reserved_chars[_RESERVED_KEY] = reserved + len(text)
    return True


def make_load_skill_handler(service: SkillService):
    """Build the ``load_skill`` handler bound to ``service``."""

    async def load_skill(arguments: Dict[str, Any], ctx: ToolContext) -> Any:
        unknown = sorted(set(arguments) - _ARGUMENTS)
        if unknown:
            return tool_error(
                INVALID_ARGUMENTS,
                f"Unknown argument(s): {', '.join(unknown)}. Pass skill parameters inside 'params'.",
            )
        try:
            loaded = await service.load(arguments.get("name"), arguments.get("params"), actor=ctx.user_id)
        except SkillLoadError as error:
            return tool_error(error.code, error.message)
        if not _reserve(loaded.rendered_text, ctx):
            return tool_error(
                CONTEXT_EXHAUSTED,
                "Not enough context is left in this conversation to load this skill. "
                "Ask the user to start a new conversation.",
            )
        return ToolOutput(result=loaded.rendered_text, metadata={"skill_ref": loaded.skill_ref})

    return load_skill


def build_skill_tools(service: SkillService) -> List[InternalTool]:
    """The ``load_skill`` tool, advertised only while ``service``'s snapshot has enabled skills."""
    return [
        InternalTool(
            name=LOAD_SKILL,
            function_desc=LOAD_SKILL_DESC,
            handler=make_load_skill_handler(service),
            requires_session=False,
            available=lambda: bool(service.snapshot.entries),
        )
    ]
