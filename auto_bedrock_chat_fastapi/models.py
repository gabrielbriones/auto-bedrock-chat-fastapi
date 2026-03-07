"""Shared data models for auto-bedrock-chat-fastapi"""

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ChatCompletionResult:
    """Result from ChatManager.chat_completion().

    Encapsulates everything that happened during a single chat_completion call,
    including the full updated message history, the final AI response, all tool
    results collected across recursive tool call rounds, and metadata/stats.

    Attributes:
        messages: Full updated conversation history (all messages including
            system, user, assistant, and tool messages after processing).
        response: The final assistant response dict (the last assistant message).
        tool_results: All tool results collected across all tool call rounds.
            Each entry is a dict with keys like 'tool_use_id', 'name', 'content'.
        metadata: Stats and diagnostics from the completion run, e.g.:
            - 'tool_call_rounds': number of recursive tool call rounds executed
            - 'total_tool_calls': total number of individual tool calls made
            - 'preprocessing_applied': whether message preprocessing was triggered
            - 'context_window_retries': number of context-window error recoveries
            - 'fallback_model_used': whether the fallback model was used
    """

    messages: List[Dict[str, Any]]
    response: Dict[str, Any]
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
