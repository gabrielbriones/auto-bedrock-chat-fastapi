"""Chat graph state definition."""

from typing import Any, Dict, List, Optional

from typing_extensions import TypedDict


class InputState(TypedDict, total=False):
    """Input-only schema for ``ainvoke``.  Only ``user_message`` lives here;
    it is consumed by ``init_turn_node`` and never returned to the caller."""

    user_message: str


class OutputState(TypedDict, total=False):
    """Output schema returned by ``ainvoke``.  Excludes ``user_message``."""

    messages: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    kb_results: List[Dict[str, Any]]


class ChatState(TypedDict, total=False):
    """State carried through the LangGraph StateGraph for a single chat turn.

    Uses ``total=False`` so nodes only need to return the keys they update;
    all other keys pass through unchanged.

    Fields
    ------
    user_message:
        The raw user message for the current turn.  The websocket handler
        passes only this field (plus ``metadata: {}``) to ``ainvoke``; the
        ``init_turn`` node consumes it, appends it to ``messages``, and
        clears this field.  Because it is not present in the ``ainvoke``
        input for subsequent graph calls, ``messages`` passes through from
        the LangGraph checkpoint automatically — no manual ``aget_state``
        needed in the handler.
    messages:
        Full conversation as a list of plain dicts (``{"role": ..., "content":
        ...}``) — the same format used by MessagePreprocessor and ToolManager,
        so no conversion is needed at the node boundaries.  The last element
        after the graph completes is the final assistant message.
    metadata:
        Accumulated statistics for the current turn:
        ``tool_call_rounds``, ``total_tool_calls``,
        ``preprocessing_applied``, ``context_window_retries``.
    kb_results:
        KB chunks retrieved by the RAG node for the current turn.  Replaced
        on every invocation (no reducer).  Empty list when RAG is disabled or
        produced no results.  Stored in the checkpoint so each turn's
        retrieval is auditable.

    Note: ``auth_info`` and ``on_progress`` are intentionally NOT in the
    state — they are non-serializable runtime objects that must be passed
    via ``config["configurable"]`` at ``ainvoke`` time so the checkpointer
    can serialise the state without errors.
    """

    user_message: Optional[str]  # input-only; cleared by init_turn_node
    messages: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    kb_results: List[Dict[str, Any]]
