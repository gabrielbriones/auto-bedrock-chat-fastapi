"""Tests for WebSocket authentication message handling"""

from unittest.mock import AsyncMock, Mock

import pytest

from auto_bedrock_chat_fastapi.auth_handler import AuthType, Credentials
from auto_bedrock_chat_fastapi.session_manager import ChatSession


class TestWebSocketAuthMessages:
    """Test WebSocket authentication message handling"""

    @pytest.mark.asyncio
    async def test_handle_bearer_token_auth_message(self):
        """Test handling bearer token authentication message"""
        # Mock the websocket and session manager
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()

        # Create a mock session
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        # Import after mocking
        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        # Create auth message data
        auth_data = {"type": "auth", "auth_type": "bearer_token", "token": "test-token-123"}

        # Handle auth message
        await handler._handle_auth_message(mock_websocket, auth_data)

        # Verify session credentials were updated
        assert mock_session.credentials is not None
        assert mock_session.credentials.auth_type == AuthType.BEARER_TOKEN
        assert mock_session.credentials.bearer_token == "test-token-123"

        # Verify confirmation message was sent
        mock_websocket.send_json.assert_called()
        sent_message = mock_websocket.send_json.call_args[0][0]
        assert sent_message["type"] == "auth_configured"
        assert sent_message["auth_type"] == "bearer_token"

    @pytest.mark.asyncio
    async def test_handle_basic_auth_message(self):
        """Test handling basic auth message"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        auth_data = {
            "type": "auth",
            "auth_type": "basic_auth",
            "username": "user@example.com",
            "password": "password123",
        }

        await handler._handle_auth_message(mock_websocket, auth_data)

        assert mock_session.credentials.auth_type == AuthType.BASIC_AUTH
        assert mock_session.credentials.username == "user@example.com"
        assert mock_session.credentials.password == "password123"

    @pytest.mark.asyncio
    async def test_handle_api_key_auth_message(self):
        """Test handling API key authentication message"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        auth_data = {
            "type": "auth",
            "auth_type": "api_key",
            "api_key": "sk-1234567890",
            "api_key_header": "X-Custom-Key",
        }

        await handler._handle_auth_message(mock_websocket, auth_data)

        assert mock_session.credentials.auth_type == AuthType.API_KEY
        assert mock_session.credentials.api_key == "sk-1234567890"
        assert mock_session.credentials.api_key_header == "X-Custom-Key"

    @pytest.mark.asyncio
    async def test_handle_oauth2_auth_message(self):
        """Test handling OAuth2 authentication message"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        auth_data = {
            "type": "auth",
            "auth_type": "oauth2",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "token_url": "https://auth.example.com/token",
            "scope": "api:read",
        }

        await handler._handle_auth_message(mock_websocket, auth_data)

        assert mock_session.credentials.auth_type == AuthType.OAUTH2_CLIENT_CREDENTIALS
        assert mock_session.credentials.client_id == "client-id"
        assert mock_session.credentials.client_secret == "client-secret"
        assert mock_session.credentials.token_url == "https://auth.example.com/token"

    @pytest.mark.asyncio
    async def test_handle_custom_auth_message(self):
        """Test handling custom authentication message"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        auth_data = {
            "type": "auth",
            "auth_type": "custom",
            "custom_headers": {"X-Custom": "value1", "X-Version": "v2"},
        }

        await handler._handle_auth_message(mock_websocket, auth_data)

        assert mock_session.credentials.auth_type == AuthType.CUSTOM
        assert mock_session.credentials.custom_headers["X-Custom"] == "value1"

    @pytest.mark.asyncio
    async def test_handle_auth_missing_session(self):
        """Test auth handling when session not found"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session_manager.get_session = AsyncMock(return_value=None)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        auth_data = {"type": "auth", "auth_type": "bearer_token", "token": "token"}

        await handler._handle_auth_message(mock_websocket, auth_data)

        # Should send error
        mock_websocket.send_json.assert_called()
        sent_message = mock_websocket.send_json.call_args[0][0]
        assert sent_message["type"] == "error"

    @pytest.mark.asyncio
    async def test_handle_auth_missing_token(self):
        """Test auth handling with missing required token"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        # Missing required token
        auth_data = {"type": "auth", "auth_type": "bearer_token"}

        await handler._handle_auth_message(mock_websocket, auth_data)

        # Should send error
        sent_message = mock_websocket.send_json.call_args[0][0]
        assert sent_message["type"] == "error"
        assert "token" in sent_message["message"].lower()

    @pytest.mark.asyncio
    async def test_handle_auth_invalid_type(self):
        """Test auth handling with invalid auth type"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        auth_data = {"type": "auth", "auth_type": "invalid_auth_type"}

        await handler._handle_auth_message(mock_websocket, auth_data)

        # Should send error
        sent_message = mock_websocket.send_json.call_args[0][0]
        assert sent_message["type"] == "error"


class TestWebSocketAuthVerification:
    """Test WebSocket authentication with verification endpoint"""

    @pytest.mark.asyncio
    async def test_auth_verified_successfully(self):
        """Test auth succeeds when verification endpoint returns 200"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()
        config.auth_verification_endpoint = "https://api.example.com/verify"

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        # Mock the HTTP client to return 200 for verification
        mock_response = AsyncMock()
        mock_response.status_code = 200
        handler.http_client = AsyncMock()
        handler.http_client.get = AsyncMock(return_value=mock_response)

        auth_data = {"type": "auth", "auth_type": "bearer_token", "token": "valid-token"}

        await handler._handle_auth_message(mock_websocket, auth_data)

        # Credentials should be stored
        assert mock_session.credentials is not None
        assert mock_session.credentials.auth_type == AuthType.BEARER_TOKEN

        # Should send auth_configured
        sent_message = mock_websocket.send_json.call_args[0][0]
        assert sent_message["type"] == "auth_configured"

    @pytest.mark.asyncio
    async def test_auth_rejected_by_verification_endpoint(self):
        """Test auth is rejected when verification endpoint returns 401"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()
        config.auth_verification_endpoint = "https://api.example.com/verify"

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        # Mock the HTTP client to return 401 for verification
        mock_response = AsyncMock()
        mock_response.status_code = 401
        mock_response.json = Mock(return_value={"detail": "Invalid token"})
        handler.http_client = AsyncMock()
        handler.http_client.get = AsyncMock(return_value=mock_response)

        auth_data = {"type": "auth", "auth_type": "bearer_token", "token": "bad-token"}

        await handler._handle_auth_message(mock_websocket, auth_data)

        # Credentials should NOT be stored (session attributes unchanged)
        # The mock starts with no credentials attribute set by our code
        sent_message = mock_websocket.send_json.call_args[0][0]
        assert sent_message["type"] == "auth_failed"
        assert "401" in sent_message["message"]

    @pytest.mark.asyncio
    async def test_auth_no_verification_when_endpoint_not_configured(self):
        """Test auth proceeds without verification when endpoint is not set"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        # No verification endpoint configured (default)
        config = ChatConfig()

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        auth_data = {"type": "auth", "auth_type": "bearer_token", "token": "any-token"}

        await handler._handle_auth_message(mock_websocket, auth_data)

        # Should succeed without any HTTP calls for verification
        assert mock_session.credentials is not None
        assert mock_session.credentials.auth_type == AuthType.BEARER_TOKEN
        sent_message = mock_websocket.send_json.call_args[0][0]
        assert sent_message["type"] == "auth_configured"

    @pytest.mark.asyncio
    async def test_auth_verification_timeout_rejects(self):
        """Test auth is rejected when verification endpoint times out"""
        import httpx

        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()
        config.auth_verification_endpoint = "https://api.example.com/verify"

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        handler.http_client = AsyncMock()
        handler.http_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        auth_data = {"type": "auth", "auth_type": "bearer_token", "token": "token"}

        await handler._handle_auth_message(mock_websocket, auth_data)

        sent_message = mock_websocket.send_json.call_args[0][0]
        assert sent_message["type"] == "auth_failed"
        assert "timed out" in sent_message["message"].lower()


class TestRequireToolAuth:
    """Test that require_tool_auth blocks unauthenticated chat messages"""

    @pytest.mark.asyncio
    async def test_chat_blocked_when_require_auth_and_no_credentials(self):
        """Test that chat messages are rejected when require_tool_auth is True and user has not authenticated"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        # Default credentials with auth_type=NONE (as created by ChatSession dataclass)
        mock_session.credentials = Credentials()
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()
        config.require_tool_auth = True

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=Mock(),
        )

        chat_data = {"type": "chat", "message": "Hello"}
        await handler._handle_chat_message(mock_websocket, chat_data)

        # Should send error about authentication required
        sent_message = mock_websocket.send_json.call_args[0][0]
        assert sent_message["type"] == "error"
        assert "authentication" in sent_message["message"].lower()

    @pytest.mark.asyncio
    async def test_chat_allowed_when_require_auth_and_authenticated(self):
        """Test that chat messages are allowed when require_tool_auth is True and user has authenticated"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        # Authenticated credentials
        mock_session.credentials = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="valid-token")
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)
        mock_session_manager.add_message = AsyncMock()

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.models import ChatCompletionResult
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()
        config.require_tool_auth = True

        # Mock ChatManager to return a simple response
        mock_chat_manager = AsyncMock()
        mock_chat_manager.chat_completion.return_value = ChatCompletionResult(
            messages=[{"role": "user", "content": "Hello"}],
            response={"content": "Hello!", "role": "assistant", "tool_calls": []},
            tool_results=[],
            metadata={"tool_call_rounds": 0},
        )

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=mock_chat_manager,
        )
        # Mock session context and tools
        mock_session_manager.get_context_messages = AsyncMock(return_value=[])

        chat_data = {"type": "chat", "message": "Hello"}
        await handler._handle_chat_message(mock_websocket, chat_data)

        # Should NOT have sent an auth error — message was accepted
        calls = mock_websocket.send_json.call_args_list
        error_calls = [
            c
            for c in calls
            if c[0][0].get("type") == "error" and "authentication" in c[0][0].get("message", "").lower()
        ]
        assert len(error_calls) == 0
        # Verify chat_manager was actually called (chat went through)
        mock_chat_manager.chat_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_allowed_when_require_auth_false(self):
        """Test that chat messages work without auth when require_tool_auth is False"""
        mock_websocket = AsyncMock()
        mock_session_manager = AsyncMock()
        mock_session = Mock(spec=ChatSession)
        mock_session.session_id = "test-session"
        mock_session.credentials = Credentials()  # Default NONE credentials
        mock_session_manager.get_session = AsyncMock(return_value=mock_session)
        mock_session_manager.add_message = AsyncMock()

        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.models import ChatCompletionResult
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()
        config.require_tool_auth = False

        # Mock ChatManager to return a simple response
        mock_chat_manager = AsyncMock()
        mock_chat_manager.chat_completion.return_value = ChatCompletionResult(
            messages=[{"role": "user", "content": "Hello"}],
            response={"content": "Hello!", "role": "assistant", "tool_calls": []},
            tool_results=[],
            metadata={"tool_call_rounds": 0},
        )

        handler = WebSocketChatHandler(
            session_manager=mock_session_manager,
            config=config,
            chat_manager=mock_chat_manager,
        )
        # Mock session context and tools
        mock_session_manager.get_context_messages = AsyncMock(return_value=[])

        chat_data = {"type": "chat", "message": "Hello"}
        await handler._handle_chat_message(mock_websocket, chat_data)

        # Should NOT have sent an auth error
        calls = mock_websocket.send_json.call_args_list
        error_calls = [
            c
            for c in calls
            if c[0][0].get("type") == "error" and "authentication" in c[0][0].get("message", "").lower()
        ]
        assert len(error_calls) == 0
        # Verify chat_manager was actually called (chat went through)
        mock_chat_manager.chat_completion.assert_called_once()
