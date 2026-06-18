"""Chat graph state definition."""

from typing import Any, Dict, List

from typing_extensions import TypedDict


class ChatState(TypedDict, total=False):
    """State carried through the LangGraph StateGraph for a single chat turn.

    Uses ``total=False`` so nodes only need to return the keys they update;
    all other keys pass through unchanged.

    Fields
    ------
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

    messages: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    kb_results: List[Dict[str, Any]]
