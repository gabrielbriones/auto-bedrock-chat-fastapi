"""Tests for WebSocketChatHandler → ChatManager delegation.

Verifies that _handle_chat_message correctly delegates to
chat_manager.chat_completion() and syncs results back to the session.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.models import ChatCompletionResult
from auto_bedrock_chat_fastapi.session_manager import ChatMessage, ChatSessionManager
from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    """Minimal config for tests."""
    return ChatConfig()


@pytest.fixture
def mock_session():
    """A mock session with basic attributes."""
    session = MagicMock()
    session.session_id = "test-session-123"
    session.credentials = None
    session.auth_handler = None
    session.conversation_history = []
    return session


@pytest.fixture
def mock_session_manager(mock_session):
    """Session manager that returns a deterministic session."""
    mgr = AsyncMock(spec=ChatSessionManager)
    mgr.get_session.return_value = mock_session
    mgr.get_context_messages.return_value = [
        ChatMessage(role="user", content="hello"),
    ]
    mgr.add_message = AsyncMock()
    return mgr


@pytest.fixture
def mock_chat_manager():
    """A mock ChatManager with a default simple response."""
    cm = AsyncMock()
    cm.chat_completion.return_value = ChatCompletionResult(
        messages=[{"role": "user", "content": "hello"}],
        response={
            "content": "Hi there!",
            "role": "assistant",
            "tool_calls": [],
            "metadata": {},
        },
        tool_results=[],
        metadata={"tool_call_rounds": 0},
    )
    return cm


@pytest.fixture
def mock_websocket():
    """Mock WebSocket that records sent messages."""
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def handler(config, mock_session_manager, mock_chat_manager):
    """WebSocketChatHandler wired with mock dependencies."""
    with patch("auto_bedrock_chat_fastapi.websocket_handler.httpx.AsyncClient"):
        h = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=mock_chat_manager,
        )
    return h


# ---------------------------------------------------------------------------
# Tests: basic delegation
# ---------------------------------------------------------------------------


class TestChatManagerDelegation:
    """Verify _handle_chat_message delegates to chat_manager.chat_completion."""

    async def test_simple_response_delegates_to_chat_manager(
        self, handler, mock_chat_manager, mock_websocket, mock_session_manager
    ):
        """A simple chat message should call chat_manager.chat_completion."""
        await handler._handle_chat_message(mock_websocket, {"message": "hello"})

        mock_chat_manager.chat_completion.assert_awaited_once()
        call_kwargs = mock_chat_manager.chat_completion.call_args
        # messages should be a list of dicts
        assert isinstance(call_kwargs.kwargs["messages"], list)

    async def test_response_sent_to_websocket(self, handler, mock_chat_manager, mock_websocket):
        """The AI response should be sent to the WebSocket client."""
        await handler._handle_chat_message(mock_websocket, {"message": "hello"})

        # Find the ai_response message among all send_json calls
        ai_responses = [
            call.args[0]
            for call in mock_websocket.send_json.call_args_list
            if call.args[0].get("type") == "ai_response"
        ]
        assert len(ai_responses) == 1
        assert ai_responses[0]["message"] == "Hi there!"

    async def test_final_assistant_message_added_to_session(self, handler, mock_session_manager, mock_websocket):
        """The final AI response should be added to session history."""
        await handler._handle_chat_message(mock_websocket, {"message": "hello"})

        # add_message called at least twice: user msg + assistant msg
        add_calls = mock_session_manager.add_message.call_args_list
        assert len(add_calls) >= 2

        # Last add_message should be the assistant response
        last_msg = add_calls[-1].args[1]  # ChatMessage object
        assert last_msg.role == "assistant"
        assert last_msg.content == "Hi there!"

    async def test_auth_info_and_on_progress_passed(self, handler, mock_chat_manager, mock_websocket):
        """chat_completion should receive auth_info and a callable on_progress."""
        await handler._handle_chat_message(mock_websocket, {"message": "hello"})

        call_kwargs = mock_chat_manager.chat_completion.call_args.kwargs
        # auth_info should be an AuthInfo instance
        from auto_bedrock_chat_fastapi.tool_manager import AuthInfo

        assert isinstance(call_kwargs["auth_info"], AuthInfo)
        assert callable(call_kwargs["on_progress"])

    async def test_empty_message_rejected(self, handler, mock_websocket):
        """Empty messages should be rejected without calling chat_manager."""
        await handler._handle_chat_message(mock_websocket, {"message": "  "})

        # Should send an error, not call chat_manager
        error_msgs = [
            call.args[0] for call in mock_websocket.send_json.call_args_list if call.args[0].get("type") == "error"
        ]
        assert len(error_msgs) == 1
        assert "Empty" in error_msgs[0]["message"]


# ---------------------------------------------------------------------------
# Tests: tool call round-trip
# ---------------------------------------------------------------------------


class TestToolLoopSessionSync:
    """Verify intermediate tool-loop messages are synced back to session."""

    async def test_tool_rounds_synced_to_session(
        self, handler, mock_chat_manager, mock_session_manager, mock_websocket
    ):
        """After a tool call round, intermediate messages should be added to session."""
        # Configure chat_manager to return a result with 1 tool round
        mock_chat_manager.chat_completion.return_value = ChatCompletionResult(
            messages=[
                {"role": "user", "content": "hello"},
                # Intermediate: assistant with tool_calls
                {
                    "role": "assistant",
                    "content": "Let me look that up.",
                    "tool_calls": [{"id": "tc1", "name": "search", "arguments": {}}],
                },
                # Intermediate: tool result
                {
                    "role": "tool",
                    "content": "Tool results (round 1)",
                    "tool_calls": [{"id": "tc1", "name": "search", "arguments": {}}],
                    "tool_results": [{"tool_call_id": "tc1", "name": "search", "result": {"data": "found"}}],
                },
            ],
            response={
                "content": "I found the answer.",
                "role": "assistant",
                "tool_calls": [],
                "metadata": {},
            },
            tool_results=[{"tool_call_id": "tc1", "name": "search", "result": {"data": "found"}}],
            metadata={"tool_call_rounds": 1},
        )

        await handler._handle_chat_message(mock_websocket, {"message": "hello"})

        # add_message calls: user_msg + assistant_intermediate + tool_intermediate + final_assistant = 4
        add_calls = mock_session_manager.add_message.call_args_list
        assert len(add_calls) == 4

        # Check intermediate assistant message
        intermediate_assistant = add_calls[1].args[1]
        assert intermediate_assistant.role == "assistant"
        assert intermediate_assistant.tool_calls == [{"id": "tc1", "name": "search", "arguments": {}}]

        # Check intermediate tool message
        intermediate_tool = add_calls[2].args[1]
        assert intermediate_tool.role == "tool"

        # Check final assistant message
        final_assistant = add_calls[3].args[1]
        assert final_assistant.role == "assistant"
        assert final_assistant.content == "I found the answer."
        assert final_assistant.tool_calls == []

    async def test_no_tool_rounds_no_intermediate_sync(self, handler, mock_session_manager, mock_websocket):
        """With 0 tool rounds, only user + final assistant messages added."""
        await handler._handle_chat_message(mock_websocket, {"message": "hello"})

        # 2 calls: user_msg + final_assistant
        add_calls = mock_session_manager.add_message.call_args_list
        assert len(add_calls) == 2

    async def test_multiple_tool_rounds_synced(self, handler, mock_chat_manager, mock_session_manager, mock_websocket):
        """Multiple tool rounds should all be synced to session."""
        mock_chat_manager.chat_completion.return_value = ChatCompletionResult(
            messages=[
                {"role": "user", "content": "complex query"},
                {"role": "assistant", "content": "Step 1", "tool_calls": [{"id": "t1"}]},
                {"role": "tool", "content": "Result 1", "tool_results": [{"tool_call_id": "t1"}]},
                {"role": "assistant", "content": "Step 2", "tool_calls": [{"id": "t2"}]},
                {"role": "tool", "content": "Result 2", "tool_results": [{"tool_call_id": "t2"}]},
            ],
            response={"content": "Done!", "role": "assistant", "tool_calls": [], "metadata": {}},
            tool_results=[{"tool_call_id": "t1"}, {"tool_call_id": "t2"}],
            metadata={"tool_call_rounds": 2},
        )

        await handler._handle_chat_message(mock_websocket, {"message": "complex query"})

        # user + 4 intermediate (2 rounds × 2) + final = 6
        add_calls = mock_session_manager.add_message.call_args_list
        assert len(add_calls) == 6

    async def test_tool_results_in_websocket_response(self, handler, mock_chat_manager, mock_websocket):
        """Tool results should be included in the WebSocket response."""
        tool_results = [{"tool_call_id": "tc1", "name": "search", "result": "data"}]
        mock_chat_manager.chat_completion.return_value = ChatCompletionResult(
            messages=[{"role": "user", "content": "hello"}],
            response={"content": "Found it.", "role": "assistant", "tool_calls": [], "metadata": {}},
            tool_results=tool_results,
            metadata={"tool_call_rounds": 0},
        )

        await handler._handle_chat_message(mock_websocket, {"message": "hello"})

        ai_responses = [
            call.args[0]
            for call in mock_websocket.send_json.call_args_list
            if call.args[0].get("type") == "ai_response"
        ]
        assert ai_responses[0]["tool_results"] == tool_results


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------


class TestDelegationErrorHandling:
    """Verify error handling when chat_manager raises."""

    async def test_chat_manager_error_sends_error_response(self, handler, mock_chat_manager, mock_websocket):
        """If chat_manager raises, an error response should be sent."""
        mock_chat_manager.chat_completion.side_effect = Exception("LLM unavailable")

        await handler._handle_chat_message(mock_websocket, {"message": "hello"})

        ai_responses = [
            call.args[0]
            for call in mock_websocket.send_json.call_args_list
            if call.args[0].get("type") == "ai_response" and call.args[0].get("error")
        ]
        assert len(ai_responses) == 1
        assert "error" in ai_responses[0]["message"].lower() or "LLM" in ai_responses[0]["message"]
