"""Tests for WebSocket authentication message handling"""

import json
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
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler
        from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
        from auto_bedrock_chat_fastapi.tools_generator import ToolsGenerator
        from auto_bedrock_chat_fastapi.config import ChatConfig
        
        config = ChatConfig()
        
        with patch('auto_bedrock_chat_fastapi.websocket_handler.BedrockClient'):
            with patch('auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator'):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(spec=BedrockClient),
                    tools_generator=Mock(spec=ToolsGenerator),
                    config=config,
                )
                
                # Create auth message data
                auth_data = {
                    "type": "auth",
                    "auth_type": "bearer_token",
                    "token": "test-token-123"
                }
                
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
        
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler
        from auto_bedrock_chat_fastapi.config import ChatConfig
        
        config = ChatConfig()
        
        with patch('auto_bedrock_chat_fastapi.websocket_handler.BedrockClient'):
            with patch('auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator'):
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
                    "password": "password123"
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
        
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler
        from auto_bedrock_chat_fastapi.config import ChatConfig
        
        config = ChatConfig()
        
        with patch('auto_bedrock_chat_fastapi.websocket_handler.BedrockClient'):
            with patch('auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator'):
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
                    "api_key_header": "X-Custom-Key"
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
        
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler
        from auto_bedrock_chat_fastapi.config import ChatConfig
        
        config = ChatConfig()
        
        with patch('auto_bedrock_chat_fastapi.websocket_handler.BedrockClient'):
            with patch('auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator'):
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
                    "scope": "api:read"
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
        
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler
        from auto_bedrock_chat_fastapi.config import ChatConfig
        
        config = ChatConfig()
        
        with patch('auto_bedrock_chat_fastapi.websocket_handler.BedrockClient'):
            with patch('auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator'):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
                )
                
                auth_data = {
                    "type": "auth",
                    "auth_type": "custom",
                    "custom_headers": {
                        "X-Custom": "value1",
                        "X-Version": "v2"
                    }
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
        
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler
        from auto_bedrock_chat_fastapi.config import ChatConfig
        
        config = ChatConfig()
        
        with patch('auto_bedrock_chat_fastapi.websocket_handler.BedrockClient'):
            with patch('auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator'):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
                )
                
                auth_data = {
                    "type": "auth",
                    "auth_type": "bearer_token",
                    "token": "token"
                }
                
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
        
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler
        from auto_bedrock_chat_fastapi.config import ChatConfig
        
        config = ChatConfig()
        
        with patch('auto_bedrock_chat_fastapi.websocket_handler.BedrockClient'):
            with patch('auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator'):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
                )
                
                # Missing required token
                auth_data = {
                    "type": "auth",
                    "auth_type": "bearer_token"
                }
                
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
        
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler
        from auto_bedrock_chat_fastapi.config import ChatConfig
        
        config = ChatConfig()
        
        with patch('auto_bedrock_chat_fastapi.websocket_handler.BedrockClient'):
            with patch('auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator'):
                handler = WebSocketChatHandler(
                    session_manager=mock_session_manager,
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
                )
                
                auth_data = {
                    "type": "auth",
                    "auth_type": "invalid_auth_type"
                }
                
                await handler._handle_auth_message(mock_websocket, auth_data)
                
                # Should send error
                sent_message = mock_websocket.send_json.call_args[0][0]
                assert sent_message["type"] == "error"


class TestToolCallAuthentication:
    """Test authentication application in tool calls"""

    @pytest.mark.asyncio
    async def test_execute_tool_call_with_bearer_token(self):
        """Test tool call execution with bearer token auth"""
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler
        from auto_bedrock_chat_fastapi.config import ChatConfig
        
        # Create mock session with bearer token
        mock_session = Mock(spec=ChatSession)
        mock_creds = Credentials(
            auth_type=AuthType.BEARER_TOKEN,
            bearer_token="test-token"
        )
        mock_session.credentials = mock_creds
        mock_session.auth_handler = Mock()
        mock_session.auth_handler.apply_auth_to_headers = AsyncMock(
            return_value={"Authorization": "Bearer test-token"}
        )
        
        config = ChatConfig()
        
        with patch('auto_bedrock_chat_fastapi.websocket_handler.BedrockClient'):
            with patch('auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator'):
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
                    "_metadata": {
                        "authentication": {
                            "type": "bearer_token"
                        }
                    }
                }
                
                # Execute tool call
                result = await handler._execute_single_tool_call(
                    tool_metadata,
                    {},
                    session=mock_session
                )
                
                # Verify auth was applied
                mock_session.auth_handler.apply_auth_to_headers.assert_called_once()
                assert result == {"data": "test"}

    @pytest.mark.asyncio
    async def test_execute_tool_call_without_auth(self):
        """Test tool call execution without authentication"""
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler
        from auto_bedrock_chat_fastapi.config import ChatConfig
        
        config = ChatConfig()
        
        with patch('auto_bedrock_chat_fastapi.websocket_handler.BedrockClient'):
            with patch('auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator'):
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
                
                tool_metadata = {
                    "method": "GET",
                    "path": "/api/public"
                }
                
                # Execute without session (no auth)
                result = await handler._execute_single_tool_call(
                    tool_metadata,
                    {}
                )
                
                assert result == {"data": "test"}

    @pytest.mark.asyncio
    async def test_tool_call_auth_failure(self):
        """Test tool call with authentication failure"""
        from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler
        from auto_bedrock_chat_fastapi.config import ChatConfig
        
        mock_session = Mock(spec=ChatSession)
        mock_session.credentials = Credentials(
            auth_type=AuthType.BEARER_TOKEN,
            bearer_token="invalid-token"
        )
        mock_session.auth_handler = Mock()
        mock_session.auth_handler.apply_auth_to_headers = AsyncMock(
            side_effect=Exception("Auth error")
        )
        
        config = ChatConfig()
        
        with patch('auto_bedrock_chat_fastapi.websocket_handler.BedrockClient'):
            with patch('auto_bedrock_chat_fastapi.websocket_handler.ToolsGenerator'):
                handler = WebSocketChatHandler(
                    session_manager=Mock(),
                    bedrock_client=Mock(),
                    tools_generator=Mock(),
                    config=config,
                )
                
                tool_metadata = {
                    "method": "GET",
                    "path": "/api/secure"
                }
                
                result = await handler._execute_single_tool_call(
                    tool_metadata,
                    {},
                    session=mock_session
                )
                
                # Should return error
                assert "error" in result
                assert "Authentication" in result["error"]
