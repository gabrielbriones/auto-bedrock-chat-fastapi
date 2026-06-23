"""Edge routing logic for the chat graph."""

from typing import Literal

from .state import ChatState


def should_continue(state: ChatState) -> Literal["tools", "__end__"]:
    """Decide the next node after the LLM call.

    Returns ``"tools"`` when the last assistant message contains tool_calls
    that must be executed, ``"__end__"`` otherwise.

    Phase 1 note: tool execution is stubbed out.  This function is wired but
    the ``tools`` node always routes back to ``__end__``.  Full ToolNode
    integration is Phase 2.
    """
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last = messages[-1]
    if last.get("role") == "assistant" and last.get("tool_calls"):
        return "tools"

    return "__end__"
