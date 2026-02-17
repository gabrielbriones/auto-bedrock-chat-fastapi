"""Tests for WebSocket authentication message handling"""

from unittest.mock import AsyncMock, Mock, patch

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
        from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.tools_generator import ToolsGenerator
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(spec=BedrockClient),
                    tools_generator=Mock(spec=ToolsGenerator),
                    config=config,
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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
                )

                auth_data = {"type": "auth", "auth_type": "invalid_auth_type"}

                await handler._handle_auth_message(mock_websocket, auth_data)

                # Should send error
                sent_message = mock_websocket.send_json.call_args[0][0]
                assert sent_message["type"] == "error"


class TestToolCallAuthentication:
    """Test authentication application in tool calls"""

    @pytest.mark.asyncio
    async def test_execute_tool_call_with_bearer_token(self):
        """Test tool call execution with bearer token auth"""
        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        # Create mock session with bearer token
        mock_session = Mock(spec=ChatSession)
        mock_creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="test-token")
        mock_session.credentials = mock_creds
        mock_session.auth_handler = Mock()
        mock_session.auth_handler.apply_auth_to_headers = AsyncMock(return_value={"Authorization": "Bearer test-token"})

        config = ChatConfig()

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=Mock(),
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
                )

                # Mock HTTP client
                handler.http_client = AsyncMock()
                mock_response = AsyncMock()
                mock_response.status_code = 200
                mock_response.json = Mock(return_value={"data": "test"})
                handler.http_client.get = AsyncMock(return_value=mock_response)

                # Tool metadata
                tool_metadata = {
                    "method": "GET",
                    "path": "/api/users",
                    "_metadata": {"authentication": {"type": "bearer_token"}},
                }

                # Execute tool call
                result = await handler._execute_single_tool_call(tool_metadata, {}, session=mock_session)

                # Verify auth was applied
                mock_session.auth_handler.apply_auth_to_headers.assert_called_once()
                assert result == {"data": "test"}

                # Verify HTTP client was called with the correct headers
                handler.http_client.get.assert_called_once()
                call_kwargs = handler.http_client.get.call_args[1]
                assert "headers" in call_kwargs
                assert call_kwargs["headers"] == {"Authorization": "Bearer test-token"}

    @pytest.mark.asyncio
    async def test_execute_tool_call_without_auth(self):
        """Test tool call execution without authentication"""
        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=Mock(),
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
                )

                # Mock HTTP client
                handler.http_client = AsyncMock()
                mock_response = AsyncMock()
                mock_response.status_code = 200
                mock_response.json = Mock(return_value={"data": "test"})
                handler.http_client.get = AsyncMock(return_value=mock_response)

                tool_metadata = {"method": "GET", "path": "/api/public"}

                # Execute without session (no auth)
                result = await handler._execute_single_tool_call(tool_metadata, {})

                assert result == {"data": "test"}

                # Verify HTTP client was called without auth headers
                handler.http_client.get.assert_called_once()
                call_kwargs = handler.http_client.get.call_args[1]
                # Either no headers or headers don't contain auth
                if "headers" in call_kwargs:
                    assert "Authorization" not in call_kwargs["headers"]

    @pytest.mark.asyncio
    async def test_tool_call_auth_failure(self):
        """Test tool call with authentication failure"""
        from auto_bedrock_chat_fastapi.config import ChatConfig
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        mock_session = Mock(spec=ChatSession)
        mock_session.credentials = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="invalid-token")
        mock_session.auth_handler = Mock()
        mock_session.auth_handler.apply_auth_to_headers = AsyncMock(side_effect=Exception("Auth error"))

        config = ChatConfig()

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=Mock(),
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
                )

                tool_metadata = {"method": "GET", "path": "/api/secure"}

                result = await handler._execute_single_tool_call(tool_metadata, {}, session=mock_session)

                # Should return error
                assert "error" in result
                assert "Authentication" in result["error"]

                # Verify auth handler was attempted
                mock_session.auth_handler.apply_auth_to_headers.assert_called_once()


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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
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

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
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
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()
        config.require_tool_auth = True

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
                )
                # Mock the full chat pipeline
                mock_session_manager.get_context_messages = AsyncMock(return_value=[])
                handler.bedrock_client = Mock()
                handler.bedrock_client.format_messages_for_bedrock = Mock(return_value=[])
                handler.bedrock_client.chat_completion = AsyncMock(return_value={"content": "Hello!", "tool_calls": []})
                handler.tools_generator = Mock()
                handler.tools_generator.generate_tools_desc = Mock(return_value=[])
                handler._handle_tool_calls_recursively = AsyncMock(return_value=({"content": "Hello!"}, []))

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
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

        config = ChatConfig()
        config.require_tool_auth = False

        with patch("auto_bedrock_chat_fastapi.websocket_handler.BedrockClient"):
            with patch("auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator"):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
                )
                # Mock the full chat pipeline
                mock_session_manager.get_context_messages = AsyncMock(return_value=[])
                handler.bedrock_client = Mock()
                handler.bedrock_client.format_messages_for_bedrock = Mock(return_value=[])
                handler.bedrock_client.chat_completion = AsyncMock(return_value={"content": "Hello!", "tool_calls": []})
                handler.tools_generator = Mock()
                handler.tools_generator.generate_tools_desc = Mock(return_value=[])
                handler._handle_tool_calls_recursively = AsyncMock(return_value=({"content": "Hello!"}, []))

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
