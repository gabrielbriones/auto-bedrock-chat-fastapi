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
Handlers return a JSON-serialisable result (dict or string).  Expected
failures are reported by returning ``tool_error(code, message)`` rather than
raising, so the model gets a stable ``{"error": code, "message": ...}`` shape.
Unexpected exceptions propagate to ``ToolManager``, which reports them like any
other failed tool call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from ...artifacts import ArtifactScope, ArtifactStore
from ...context_budget import ContextBudget

TOOL_UNAVAILABLE = "tool_unavailable"
ARTIFACT_UNAVAILABLE = "artifact_unavailable"
INVALID_ARGUMENTS = "invalid_arguments"
CONTEXT_EXHAUSTED = "context_exhausted"


def tool_error(code: str, message: str) -> Dict[str, str]:
    """Build the error result an internal tool returns to the model."""
    return {"error": code, "message": message}


@dataclass(frozen=True)
class ToolContext:
    """Per-turn state passed to internal tool handlers."""

    artifact_store: Optional[ArtifactStore]
    artifact_scope: Optional[ArtifactScope]
    context_budget: Optional[ContextBudget]
    chat_config: Any


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
    """

    name: str
    function_desc: Dict[str, Any]
    handler: InternalHandler
    requires_session: bool = True
    exposed: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("InternalTool name must not be empty")
        if self.function_desc.get("name") != self.name:
            raise ValueError(
                f"InternalTool {self.name!r}: function_desc name {self.function_desc.get('name')!r} does not match"
            )
        if not isinstance(self.function_desc.get("parameters"), dict):
            raise ValueError(f"InternalTool {self.name!r}: function_desc must define 'parameters'")

    async def invoke(self, arguments: Dict[str, Any], ctx: ToolContext) -> Any:
        """Run the handler, failing closed when a session-bound tool has no session."""
        if self.requires_session and (ctx.artifact_store is None or ctx.artifact_scope is None):
            return tool_error(TOOL_UNAVAILABLE, f"{self.name} requires an active chat session.")
        return await self.handler(arguments, ctx)
