"""Tests for including authenticated user info in prompts."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from auto_bedrock_chat_fastapi.auth_handler import AuthType, Credentials
from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.models import ChatCompletionResult
from auto_bedrock_chat_fastapi.session_manager import ChatSession
from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler


@pytest.fixture
def mock_config():
    """Create mock config with auth info in prompts enabled"""
    config = ChatConfig()
    config.include_auth_info_in_prompts = True
    config.enable_rag = False  # Disable RAG to isolate auth info feature
    config.auth_verification_endpoint = "https://api.example.com/verify"
    return config


@pytest.fixture
def mock_config_disabled():
    """Create mock config with auth info in prompts disabled"""
    config = ChatConfig()
    config.include_auth_info_in_prompts = False
    config.enable_rag = False
    config.auth_verification_endpoint = "https://api.example.com/verify"
    return config


class TestAuthInfoInPrompts:
    """Test including authenticated user info in system prompts"""

    @pytest.mark.asyncio
    async def test_auth_info_included_when_enabled(self, mock_config):
        """Test that user info is included in system prompt when feature is enabled"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()

        # Create session with verified user info
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session.user_id = "user123"
        mock_session.credentials = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token")
        mock_session.metadata = {
            "verified_user_info": {
                "user_id": "user123",
                "email": "test@example.com",
                "name": "Test User",
                "department": "Engineering",
                "role": "Developer",
            }
        }
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)
        mock_session_manager.add_message = AsyncMock()
        mock_session_manager.get_context_messages = AsyncMock(return_value=[])

        # Mock ChatManager
        mock_chat_manager = AsyncMock()
        mock_chat_manager.chat_completion.return_value = ChatCompletionResult(
            messages=[{"role": "user", "content": "who am I?"}],
            response={"content": "You are Test User from Engineering", "role": "assistant", "tool_calls": []},
            tool_results=[],
            metadata={"tool_call_rounds": 0},
        )

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=mock_config,
            chat_manager=mock_chat_manager,
        )

        # Send a chat message
        chat_data = {"type": "chat", "message": "who am I?"}
        await handler._handle_chat_message(mock_websocket, chat_data)

        # Verify chat_completion was called
        assert mock_chat_manager.chat_completion.called

        # Get the messages argument passed to chat_completion
        call_args = mock_chat_manager.chat_completion.call_args
        messages = call_args.kwargs["messages"]

        # Verify a system message was added
        system_messages = [msg for msg in messages if msg.get("role") == "system"]
        assert len(system_messages) == 1

        # Verify the system message contains user info
        system_content = system_messages[0]["content"]
        assert "AUTHENTICATED USER CONTEXT" in system_content
        assert "user_id: user123" in system_content
        assert "email: test@example.com" in system_content
        assert "name: Test User" in system_content
        assert "department: Engineering" in system_content
        assert "role: Developer" in system_content

    @pytest.mark.asyncio
    async def test_auth_info_not_included_when_disabled(self, mock_config_disabled):
        """Test that user info is NOT included when feature is disabled"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()

        # Create session with verified user info
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session.user_id = "user123"
        mock_session.credentials = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token")
        mock_session.metadata = {
            "verified_user_info": {"user_id": "user123", "email": "test@example.com", "name": "Test User"}
        }
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)
        mock_session_manager.add_message = AsyncMock()
        mock_session_manager.get_context_messages = AsyncMock(return_value=[])

        # Mock ChatManager
        mock_chat_manager = AsyncMock()
        mock_chat_manager.chat_completion.return_value = ChatCompletionResult(
            messages=[{"role": "user", "content": "Hello"}],
            response={"content": "Hi there!", "role": "assistant", "tool_calls": []},
            tool_results=[],
            metadata={"tool_call_rounds": 0},
        )

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=mock_config_disabled,
            chat_manager=mock_chat_manager,
        )

        # Send a chat message
        chat_data = {"type": "chat", "message": "Hello"}
        await handler._handle_chat_message(mock_websocket, chat_data)

        # Verify chat_completion was called
        assert mock_chat_manager.chat_completion.called

        # Get the messages argument passed to chat_completion
        call_args = mock_chat_manager.chat_completion.call_args
        messages = call_args.kwargs["messages"]

        # Verify NO system message was added (or if added, doesn't contain auth info)
        system_messages = [msg for msg in messages if msg.get("role") == "system"]
        for msg in system_messages:
            assert "AUTHENTICATED USER CONTEXT" not in msg["content"]
            assert "user_id: user123" not in msg["content"]

    @pytest.mark.asyncio
    async def test_auth_info_gracefully_handles_missing_user_info(self, mock_config):
        """Test that missing verified_user_info doesn't break the feature"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()

        # Create session WITHOUT verified user info
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session.user_id = "user123"
        mock_session.credentials = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token")
        mock_session.metadata = {}  # No verified_user_info
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)
        mock_session_manager.add_message = AsyncMock()
        mock_session_manager.get_context_messages = AsyncMock(return_value=[])

        # Mock ChatManager
        mock_chat_manager = AsyncMock()
        mock_chat_manager.chat_completion.return_value = ChatCompletionResult(
            messages=[{"role": "user", "content": "Hello"}],
            response={"content": "Hi!", "role": "assistant", "tool_calls": []},
            tool_results=[],
            metadata={"tool_call_rounds": 0},
        )

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=mock_config,
            chat_manager=mock_chat_manager,
        )

        # Should not raise an error
        chat_data = {"type": "chat", "message": "Hello"}
        await handler._handle_chat_message(mock_websocket, chat_data)

        # Verify chat_completion was called successfully
        assert mock_chat_manager.chat_completion.called

    @pytest.mark.asyncio
    async def test_auth_info_with_complex_values(self, mock_config):
        """Test that only simple values are included, complex structures are skipped"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()

        # Create session with mixed types in verified_user_info
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session.user_id = "user123"
        mock_session.credentials = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token")
        mock_session.metadata = {
            "verified_user_info": {
                "user_id": "user123",
                "name": "Test User",
                "age": 30,
                "is_active": True,
                "tags": ["tag1", "tag2"],  # Simple list of strings - should be included
                "nested_object": {"key": "value"},  # Complex object - should be skipped
                "complex_list": [{"a": 1}, {"b": 2}],  # Complex list - should be skipped
            }
        }
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)
        mock_session_manager.add_message = AsyncMock()
        mock_session_manager.get_context_messages = AsyncMock(return_value=[])

        # Mock ChatManager
        mock_chat_manager = AsyncMock()
        mock_chat_manager.chat_completion.return_value = ChatCompletionResult(
            messages=[{"role": "user", "content": "Hello"}],
            response={"content": "Hi!", "role": "assistant", "tool_calls": []},
            tool_results=[],
            metadata={"tool_call_rounds": 0},
        )

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=mock_config,
            chat_manager=mock_chat_manager,
        )

        # Send a chat message
        chat_data = {"type": "chat", "message": "Hello"}
        await handler._handle_chat_message(mock_websocket, chat_data)

        # Verify chat_completion was called
        assert mock_chat_manager.chat_completion.called

        # Get the messages argument passed to chat_completion
        call_args = mock_chat_manager.chat_completion.call_args
        messages = call_args.kwargs["messages"]

        # Verify system message was added
        system_messages = [msg for msg in messages if msg.get("role") == "system"]
        assert len(system_messages) == 1

        # Verify the system message contains simple values
        system_content = system_messages[0]["content"]
        assert "user_id: user123" in system_content
        assert "name: Test User" in system_content
        assert "age: 30" in system_content
        assert "is_active: True" in system_content
        assert "tags: tag1, tag2" in system_content

        # Verify complex values are NOT included
        assert "nested_object" not in system_content
        assert "complex_list" not in system_content

    @pytest.mark.asyncio
    async def test_format_auth_context_empty(self, mock_config):
        """Test _format_auth_context with empty user_info"""
        mock_session_manager = AsyncMock()
        mock_chat_manager = AsyncMock()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=mock_config,
            chat_manager=mock_chat_manager,
        )

        result = handler._format_auth_context({})
        assert result == ""

        result = handler._format_auth_context(None)
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_auth_context_with_data(self, mock_config):
        """Test _format_auth_context formats user info correctly"""
        mock_session_manager = AsyncMock()
        mock_chat_manager = AsyncMock()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=mock_config,
            chat_manager=mock_chat_manager,
        )

        user_info = {
            "user_id": "user123",
            "email": "test@example.com",
            "name": "Test User",
        }

        result = handler._format_auth_context(user_info)

        assert "AUTHENTICATED USER CONTEXT" in result
        assert "user_id: user123" in result
        assert "email: test@example.com" in result
        assert "name: Test User" in result
        assert "INSTRUCTIONS:" in result
        assert "who am I?" in result


class TestAuthInfoWithRAG:
    """Test that auth info works alongside RAG features"""

    @pytest.mark.asyncio
    async def test_auth_info_and_rag_both_enabled(self):
        """Test that both RAG and auth info can be included in the same prompt"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()

        # Create session with verified user info
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session.user_id = "user123"
        mock_session.credentials = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token")
        mock_session.metadata = {"verified_user_info": {"user_id": "user123", "name": "Test User"}}
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)
        mock_session_manager.add_message = AsyncMock()
        mock_session_manager.get_context_messages = AsyncMock(return_value=[])

        # Config with both features enabled
        config = ChatConfig()
        config.include_auth_info_in_prompts = True
        config.enable_rag = True
        config.kb_database_path = ":memory:"

        # Mock ChatManager with embedding support
        mock_chat_manager = AsyncMock()
        mock_chat_manager.chat_completion.return_value = ChatCompletionResult(
            messages=[{"role": "user", "content": "test"}],
            response={"content": "response", "role": "assistant", "tool_calls": []},
            tool_results=[],
            metadata={"tool_call_rounds": 0},
        )

        # Mock KB store
        mock_kb_store = AsyncMock()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=mock_chat_manager,
            kb_store=mock_kb_store,
        )

        # Mock KB retrieval to return empty results
        with patch.object(handler, "_retrieve_kb_context", return_value=None):
            chat_data = {"type": "chat", "message": "test query"}
            await handler._handle_chat_message(mock_websocket, chat_data)

        # Verify chat_completion was called
        assert mock_chat_manager.chat_completion.called

        # Get the messages argument
        call_args = mock_chat_manager.chat_completion.call_args
        messages = call_args.kwargs["messages"]

        # Verify system message contains auth info
        system_messages = [msg for msg in messages if msg.get("role") == "system"]
        if system_messages:
            system_content = system_messages[0]["content"]
            # Auth info should be present
            assert "AUTHENTICATED USER CONTEXT" in system_content or "user_id: user123" in system_content
