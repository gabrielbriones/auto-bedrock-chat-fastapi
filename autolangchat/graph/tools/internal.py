"""In-process tools that run alongside the OpenAPI-generated ones.

An ``InternalTool`` exposes the same ``function_desc`` shape that
``ToolsGenerator`` produces (``name``, ``description``, ``parameters``), so it
can be bound to the LLM like any other tool.  Instead of being sent as an HTTP
request, it is dispatched to a Python ``handler(arguments, ctx)`` coroutine.

``ToolContext`` carries the per-turn state a handler needs that does not come
from the model's arguments (artifact store, caller scope, context budget and
chat config).  It is built per request in ``tools_execution_node``; the
``InternalTool`` itself is stateless and shared across sessions.

Handler contract
----------------
Handlers return a JSON-serialisable result (dict or string), or a ``ToolOutput``
to also attach metadata to the tool result entry (never shown to the model).
Expected
failures are reported by returning ``tool_error(code, message)`` rather than
raising, so the model gets a stable ``{"error": code, "message": ...}`` shape.
Unexpected exceptions propagate to ``ToolManager``, which reports them like any
other failed tool call.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

from ...artifacts import ArtifactScope, ArtifactStore
from ...context_budget import ContextBudget, chars_per_token
from ...defaults import CONTEXT_BUDGET_KIND_JSON, CONTEXT_BUDGET_KINDS, CONTEXT_BUDGET_PER_MESSAGE_OVERHEAD_TOKENS

TOOL_UNAVAILABLE = "tool_unavailable"
ARTIFACT_UNAVAILABLE = "artifact_unavailable"
INVALID_ARGUMENTS = "invalid_arguments"
CONTEXT_EXHAUSTED = "context_exhausted"


def tool_error(code: str, message: str) -> Dict[str, str]:
    """Build the error result an internal tool returns to the model."""
    return {"error": code, "message": message}


@dataclass
class BatchClaims:
    """Tokens claimed by the accepted results of one tool batch, shared by its concurrent calls."""

    tokens: int = 0


@dataclass(frozen=True)
class ToolContext:
    """Per-turn state passed to internal tool handlers.

    One context serves one batch of tool calls: results that budget their size claim it from the shared
    ``claims``, so the batch's results fit ``context_budget`` together whatever order they complete in.
    ``ToolManager`` gives each call a copy carrying its ``tool_name`` and ``tool_call_id`` (and the same
    ``claims``), so the name and id the result is sent with are charged too.
    """

    artifact_store: Optional[ArtifactStore]
    artifact_scope: Optional[ArtifactScope]
    context_budget: Optional[ContextBudget]
    chat_config: Any
    user_id: Optional[str] = None
    tool_name: str = ""
    tool_call_id: str = ""
    claims: BatchClaims = field(default_factory=BatchClaims, compare=False, repr=False)

    def free_chars(self) -> Optional[int]:
        """Characters of result text this call may still return, or ``None`` without a budget.

        What the batch's claimed results and this result's framing (message overhead, tool name and call id)
        leave of the budget, at the densest chars/token ratio.
        """
        budget = self.context_budget
        if budget is None:
            return None
        tokens = budget.remaining_tokens - self.claims.tokens - self._framing_tokens()
        return max(0, math.floor(tokens * self._chars_per_token()))

    def claim(self, chars: int) -> bool:
        """Claim ``chars`` of result text plus its framing; ``False`` (nothing claimed) when it does not fit.

        Do not ``await`` between sizing a result with :meth:`free_chars` and claiming it.
        """
        free = self.free_chars()
        if free is None:
            return True
        if chars > free:
            return False
        self.claims.tokens += math.ceil(chars / self._chars_per_token()) + self._framing_tokens()
        return True

    def _framing_tokens(self) -> int:
        name_and_id = self.context_budget.estimate(
            f"{self.tool_name}{self.tool_call_id}", kind=CONTEXT_BUDGET_KIND_JSON
        )
        return CONTEXT_BUDGET_PER_MESSAGE_OVERHEAD_TOKENS + name_and_id

    def _chars_per_token(self) -> float:
        return min(chars_per_token(kind, self.context_budget.provider) for kind in CONTEXT_BUDGET_KINDS)


@dataclass(frozen=True)
class ToolOutput:
    """A handler result plus ``metadata`` stored on its tool result entry."""

    result: Any
    metadata: Dict[str, Any] = field(default_factory=dict)


InternalHandler = Callable[[Dict[str, Any], ToolContext], Awaitable[Any]]


@dataclass(frozen=True)
class InternalTool:
    """A tool executed in-process instead of over HTTP.

    Attributes:
        name: Tool name used for dispatch; must equal ``function_desc["name"]``.
        function_desc: Schema shown to the model, in ``ToolsGenerator`` shape.
        handler: Coroutine called as ``handler(arguments, ctx)``.
        requires_session: The tool needs a live chat session (artifact store and
            scope). Such tools are excluded from MCP and return
            ``tool_unavailable`` when called without one.
        exposed: Whether the schema is advertised to the model. ``False`` for
            aliases that are dispatchable by name but never bound.
        available: Optional predicate checked whenever tools are (re)bound; when
            it returns ``False`` the tool is not advertised (it stays dispatchable).
    """

    name: str
    function_desc: Dict[str, Any]
    handler: InternalHandler
    requires_session: bool = True
    exposed: bool = True
    available: Optional[Callable[[], bool]] = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("InternalTool name must not be empty")
        if self.function_desc.get("name") != self.name:
            raise ValueError(
                f"InternalTool {self.name!r}: function_desc name {self.function_desc.get('name')!r} does not match"
            )
        if not isinstance(self.function_desc.get("parameters"), dict):
            raise ValueError(f"InternalTool {self.name!r}: function_desc must define 'parameters'")

    @property
    def advertised(self) -> bool:
        """Whether the schema is bound to the model right now."""
        return self.exposed and (self.available is None or self.available())

    async def invoke(self, arguments: Dict[str, Any], ctx: ToolContext) -> Any:
        """Run the handler, failing closed when a session-bound tool has no session."""
        if self.requires_session and (ctx.artifact_store is None or ctx.artifact_scope is None):
            return tool_error(TOOL_UNAVAILABLE, f"{self.name} requires an active chat session.")
        return await self.handler(arguments, ctx)
