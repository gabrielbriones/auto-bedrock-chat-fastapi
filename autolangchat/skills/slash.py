"""Slash commands: a chat message ``/<skill> key=value ...`` loads a skill directly.

The grammar is owned by the server so every client (the SPA, scripts) behaves
the same::

    /<name> key=value key2="value with spaces" [free text]

* ``name`` matches the skill name pattern and is followed by whitespace or the
  end of the message. Anything else after the leading ``/`` (whitespace, an
  upper-case letter, ``/usr/bin``, ...) is plain text, not a command.
* A message starting with ``//`` is plain text too, sent with one slash removed.
* Keys are case-insensitive (folded to lower case, the case of skill parameter
  names). A value is either bare (up to the next whitespace, possibly empty) or
  double-quoted; inside quotes a backslash makes the next character literal.
* Pairs come first. From the first token that is not ``key=...`` on, the rest
  of the message is free text, passed as the skill's ``input`` parameter (a
  skill without one rejects it).

A command is loaded with :func:`load_slash_command`, which reuses
:meth:`SkillService.load` (no LLM call) and builds the messages a
model-initiated ``load_skill`` round would have produced.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .models import SKILL_NAME_PATTERN
from .prompt import LOAD_SKILL
from .service import INPUT_PARAMETER

INVALID_COMMAND = "invalid_command"
TOOLS_UNSUPPORTED = "tools_unsupported"

_NAME_RE = re.compile(r"/(" + SKILL_NAME_PATTERN.pattern.strip("^$") + r")(?=\s|$)")
_KEY_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9_-]*)=")


class SlashCommandError(Exception):
    """A message that is a slash command but cannot be parsed."""

    code = INVALID_COMMAND

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class SlashCommand:
    """A parsed ``/<name> ...`` message."""

    name: str
    params: dict[str, str] = field(default_factory=dict)
    # Everything after the pairs, stripped; "" when there is none.
    text: str = ""


@dataclass(frozen=True)
class DirectSkillLoad:
    """A skill loaded by a slash command, as the messages that follow the user's message."""

    name: str
    revision: int
    message_id: str
    messages: list[dict[str, Any]]


def is_literal_slash(text: str) -> bool:
    """Whether ``text`` escapes a leading slash (``//...``): plain text, sent without the first slash."""
    return text.startswith("//")


def _quoted_value(text: str, start: int) -> tuple[str, int]:
    """The value of the double-quoted string opening at ``text[start]`` and the index after its closing quote."""
    chars: list[str] = []
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            if index + 1 == len(text):
                break
            chars.append(text[index + 1])
            index += 2
        elif char == '"':
            return "".join(chars), index + 1
        else:
            chars.append(char)
            index += 1
    raise SlashCommandError("Unterminated quoted value.")


def parse_slash_command(text: str) -> SlashCommand | None:
    """Parse ``text`` as a slash command; ``None`` when it is plain text.

    Raises:
        SlashCommandError: a command with a duplicate key, an unterminated
            quote, or a closing quote not followed by whitespace.
    """
    if not isinstance(text, str) or is_literal_slash(text):
        return None
    match = _NAME_RE.match(text)
    if match is None:
        return None

    params: dict[str, str] = {}
    index = match.end()
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            return SlashCommand(name=match.group(1), params=params)
        key_match = _KEY_RE.match(text, index)
        if key_match is None:
            return SlashCommand(name=match.group(1), params=params, text=text[index:].strip())
        key = key_match.group(1).lower()
        if key in params:
            raise SlashCommandError(f"Duplicate parameter: {key}.")
        index = key_match.end()
        if index < len(text) and text[index] == '"':
            value, index = _quoted_value(text, index)
            if index < len(text) and not text[index].isspace():
                raise SlashCommandError(f"Expected whitespace after the quoted value of {key}.")
        else:
            end = index
            while end < len(text) and not text[end].isspace():
                end += 1
            value, index = text[index:end], end
        params[key] = value


def direct_load_messages(
    name: str, params: dict[str, str], rendered_text: str, skill_ref: dict[str, str]
) -> list[dict[str, Any]]:
    """The assistant ``load_skill`` call and its tool result, shaped like a model-initiated round.

    Both are flagged ``metadata.direct`` (the tool result entry too). The
    assistant message carries no usage: no model produced it.
    """
    call_id = f"direct_{uuid.uuid4().hex}"
    now = datetime.now().isoformat()
    call = {"name": LOAD_SKILL, "args": {"name": name, "params": dict(params)}, "id": call_id, "type": "tool_call"}
    entry = {
        "tool_call_id": call_id,
        "name": LOAD_SKILL,
        "result": rendered_text,
        "metadata": {"skill_ref": dict(skill_ref), "direct": True},
    }
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [call],
            "metadata": {"message_id": str(uuid.uuid4()), "timestamp": now, "direct": True},
        },
        {
            "role": "tool",
            "content": json.dumps([rendered_text]),
            "tool_results": [entry],
            "metadata": {"timestamp": now, "direct": True},
        },
    ]


async def load_slash_command(service: Any, command: SlashCommand, actor: str | None = None) -> DirectSkillLoad:
    """Load ``command``'s skill through ``service`` (a ``SkillService``) without calling a model.

    Raises:
        SkillLoadError: the skill is unknown, disabled or unavailable, or the
            parameters (free text included) are invalid.
    """
    loaded = await service.load(command.name, command.params, actor=actor, free_text=command.text or None)
    params = dict(command.params)
    if command.text:
        params[INPUT_PARAMETER] = command.text
    messages = direct_load_messages(command.name, params, loaded.rendered_text, loaded.skill_ref)
    return DirectSkillLoad(
        name=command.name,
        revision=int(loaded.skill_ref["revision"]),
        message_id=messages[0]["metadata"]["message_id"],
        messages=messages,
    )
