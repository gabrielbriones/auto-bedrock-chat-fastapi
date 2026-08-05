"""Edge routing logic for the chat graph."""

from typing import Literal

from .state import ChatState


def should_continue(state: ChatState) -> Literal["tools", "__end__"]:
    """Decide the next node after the LLM call.

    Returns ``"tools"`` when the last assistant message contains tool_calls
    that must be executed, ``"__end__"`` otherwise.
    """
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last = messages[-1]
    if last.get("role") == "assistant" and last.get("tool_calls"):
        return "tools"

    return "__end__"
