"""Tests for the ``ai_response`` metadata assembled by the WebSocket handler.

Verifies that ``WebSocketChatHandler._handle_chat_message`` forwards the
expected metadata keys to the client for both the non-KB and KB code paths
(see XMGPLAT-10766).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from autolangchat.websocket_handler import WebSocketChatHandler


def _make_handler(graph_state):
    """Build a handler with all collaborators mocked, wired to return
    ``graph_state`` from the chat graph."""
    config = MagicMock()
    config.timeout = 30.0
    config.model_id = "us.anthropic.claude-sonnet-4-6"
    config.require_tool_auth = False
    config.include_auth_info_in_prompts = False
    config.feedback_allow_anonymous = False

    session = SimpleNamespace(
        session_id="session-123",
        credentials=None,
        auth_handler=None,
        metadata={},
    )

    session_manager = MagicMock()
    session_manager.get_session = AsyncMock(return_value=session)

    chat_graph = MagicMock()
    chat_graph.ainvoke = AsyncMock(return_value=graph_state)

    handler = WebSocketChatHandler(
        session_manager=session_manager,
        config=config,
        chat_graph=chat_graph,
    )
    return handler


def _run_and_get_ai_response(graph_state):
    """Drive ``_handle_chat_message`` and return the ``ai_response`` payload."""
    handler = _make_handler(graph_state)
    websocket = MagicMock()
    websocket.send_json = AsyncMock()

    asyncio.run(handler._handle_chat_message(websocket, {"message": "hello"}))

    sent = [call.args[0] for call in websocket.send_json.call_args_list]
    ai_responses = [m for m in sent if m.get("type") == "ai_response"]
    assert ai_responses, f"no ai_response sent; got {sent}"
    return ai_responses[-1]


def _assistant_message():
    return {
        "role": "assistant",
        "content": "Here is the answer.",
        "tool_calls": [],
        "tool_results": [],
        "metadata": {
            "message_id": "msg-abc",
            "model_id": "from-llm-response",
            "usage": {"input_tokens": 50, "output_tokens": 80},
            "timestamp": "2026-01-01T10:00:05",
        },
    }


def _graph_metadata():
    return {
        "tool_call_rounds": 1,
        "total_tool_calls": 2,
        "preprocessing_applied": True,
        "input_tokens": 120,
        "output_tokens": 240,
    }


# Keys that are always present in the forwarded metadata.
_ALWAYS_KEYS = {
    "message_id",
    "model_id",
    "usage",
    "timestamp",
    "tool_call_rounds",
    "total_tool_calls",
    "preprocessing_applied",
}
_TOKEN_TOTAL_KEYS = {"input_tokens", "output_tokens"}
_KB_KEYS = {"kb_used", "kb_chunks", "kb_sources"}


def test_metadata_non_kb_path():
    graph_state = {
        "messages": [_assistant_message()],
        "metadata": _graph_metadata(),
        "kb_results": [],
    }

    response = _run_and_get_ai_response(graph_state)
    metadata = response["metadata"]

    # Always-present keys and token totals are forwarded.
    assert _ALWAYS_KEYS.issubset(metadata)
    assert _TOKEN_TOTAL_KEYS.issubset(metadata)

    # No KB query => no KB keys.
    assert not (_KB_KEYS & metadata.keys())

    # model_id is overwritten from server config, not the LLM response.
    assert metadata["model_id"] == "us.anthropic.claude-sonnet-4-6"
    # Nested usage dict reflects the final LLM call.
    assert metadata["usage"] == {"input_tokens": 50, "output_tokens": 80}
    # Top-level totals come from the accumulated graph metadata.
    assert metadata["input_tokens"] == 120
    assert metadata["output_tokens"] == 240


def test_metadata_kb_path():
    graph_state = {
        "messages": [_assistant_message()],
        "metadata": _graph_metadata(),
        "kb_results": [
            {
                "title": "Doc A",
                "source": "kb://a",
                "source_url": "https://example.com/a",
                "similarity_score": 0.91,
            },
            {
                "title": None,
                "source": None,
                "source_url": None,
                "similarity_score": 0.42,
            },
        ],
    }

    response = _run_and_get_ai_response(graph_state)
    metadata = response["metadata"]

    # Always-present keys plus the full KB block.
    assert _ALWAYS_KEYS.issubset(metadata)
    assert _KB_KEYS.issubset(metadata)

    assert metadata["kb_used"] is True
    assert metadata["kb_chunks"] == 2
    assert len(metadata["kb_sources"]) == 2

    first = metadata["kb_sources"][0]
    assert set(first) == {"title", "source", "url", "score"}
    assert first == {
        "title": "Doc A",
        "source": "kb://a",
        "url": "https://example.com/a",
        "score": 0.91,
    }
