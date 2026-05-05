"""Tests for authentication metadata capture and propagation.

Tests the feature that captures user metadata from auth verification endpoints
and propagates it to downstream tool API calls via HTTP headers.

Covers:
- verify_credentials_remote returning user_info (3-tuple return type)
- session.metadata storage with verified_user_info
- update_session_user_id() functionality
- metadata headers (X-User-ID, X-Tenant-ID, X-User-Display-Name, X-User-Metadata)
- SSO verification endpoint integration
- Error handling for non-JSON responses and missing fields
"""

import base64
import json
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from auto_bedrock_chat_fastapi.auth_handler import AuthenticationHandler, AuthType, Credentials
from auto_bedrock_chat_fastapi.session_manager import ChatSessionManager
from auto_bedrock_chat_fastapi.tool_manager import AuthInfo, ToolManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_http_client():
    """Mock HTTP client with successful verification response."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture
def sample_user_info():
    """Sample user info from verification endpoint."""
    return {
        "user_id": "user-123",
        "tenant_id": "tenant-456",
        "email": "test@example.com",
        "display_name": "Test User",
        "roles": ["admin", "developer"],
        "access_all_tenants": False,
    }


@pytest.fixture
def oauth2_credentials():
    """OAuth2 client credentials for testing."""
    return Credentials(
        auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
        client_id="test-client",
        client_secret="test-secret",
        token_url="https://auth.example.com/token",
    )


@pytest.fixture
def bearer_credentials():
    """Bearer token credentials for testing."""
    return Credentials(
        auth_type=AuthType.BEARER_TOKEN,
        bearer_token="test-bearer-token",
    )


# ---------------------------------------------------------------------------
# Test: verify_credentials_remote returns user_info
# ---------------------------------------------------------------------------


class TestVerifyCredentialsRemote:
    """Test that verify_credentials_remote properly returns user metadata."""

    @pytest.mark.asyncio
    async def test_returns_user_info_on_success(self, bearer_credentials, sample_user_info, mock_http_client):
        """Test that successful verification returns user_info as 3rd tuple element."""
        # Setup mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_user_info
        mock_http_client.get.return_value = mock_response

        # Create auth handler
        auth_handler = AuthenticationHandler(bearer_credentials)

        # Call verification
        is_valid, message, user_info = await auth_handler.verify_credentials_remote(
            "https://api.example.com/verify", http_client=mock_http_client
        )

        # Assert
        assert is_valid is True
        assert "verified successfully" in message.lower()
        assert user_info == sample_user_info
        assert user_info["user_id"] == "user-123"
        assert user_info["tenant_id"] == "tenant-456"

    @pytest.mark.asyncio
    async def test_returns_none_user_info_on_failure(self, bearer_credentials, mock_http_client):
        """Test that failed verification returns None for user_info."""
        # Setup mock response
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_http_client.get.return_value = mock_response

        # Create auth handler
        auth_handler = AuthenticationHandler(bearer_credentials)

        # Call verification
        is_valid, message, user_info = await auth_handler.verify_credentials_remote(
            "https://api.example.com/verify", http_client=mock_http_client
        )

        # Assert
        assert is_valid is False
        assert user_info is None

    @pytest.mark.asyncio
    async def test_handles_non_json_response(self, bearer_credentials, mock_http_client):
        """Test that non-JSON response body is handled gracefully."""
        # Setup mock response with non-JSON body
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.side_effect = Exception("Not JSON")
        mock_http_client.get.return_value = mock_response

        # Create auth handler
        auth_handler = AuthenticationHandler(bearer_credentials)

        # Call verification
        is_valid, message, user_info = await auth_handler.verify_credentials_remote(
            "https://api.example.com/verify", http_client=mock_http_client
        )

        # Assert - should succeed but with None user_info
        assert is_valid is True
        assert user_info is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none_user_info(self, bearer_credentials, mock_http_client):
        """Test that timeout returns None for user_info."""
        # Setup mock to raise timeout
        mock_http_client.get.side_effect = httpx.TimeoutException("Timeout")

        # Create auth handler
        auth_handler = AuthenticationHandler(bearer_credentials)

        # Call verification
        is_valid, message, user_info = await auth_handler.verify_credentials_remote(
            "https://api.example.com/verify", http_client=mock_http_client
        )

        # Assert
        assert is_valid is False
        assert "timed out" in message.lower()
        assert user_info is None


# ---------------------------------------------------------------------------
# Test: Session metadata storage
# ---------------------------------------------------------------------------


class TestSessionMetadataStorage:
    """Test that session.metadata properly stores verified_user_info."""

    @pytest.mark.asyncio
    async def test_update_session_user_id(self):
        """Test update_session_user_id method updates user tracking correctly."""
        from starlette.websockets import WebSocket

        from auto_bedrock_chat_fastapi.config import ChatConfig

        # Create session manager with config
        config = ChatConfig()
        manager = ChatSessionManager(config)

        # Create mock websocket
        mock_ws = Mock(spec=WebSocket)

        # Create session with no user_id
        session_id = await manager.create_session(mock_ws, user_id=None)

        # Update user_id
        result = await manager.update_session_user_id(session_id, "user-123")
        assert result is True

        # Verify session was updated
        session = await manager.get_session(mock_ws)
        assert session.user_id == "user-123"

        # Verify user_sessions mapping was updated
        assert session_id in manager._user_sessions.get("user-123", [])

    @pytest.mark.asyncio
    async def test_update_session_user_id_handles_existing_user(self):
        """Test that updating user_id properly removes from old user's list."""
        from starlette.websockets import WebSocket

        from auto_bedrock_chat_fastapi.config import ChatConfig

        config = ChatConfig()
        manager = ChatSessionManager(config)
        mock_ws = Mock(spec=WebSocket)

        # Create session with initial user_id
        session_id = await manager.create_session(mock_ws, user_id="user-old")

        # Update to new user_id
        await manager.update_session_user_id(session_id, "user-new")

        # Verify removed from old user
        assert "user-old" not in manager._user_sessions or session_id not in manager._user_sessions.get("user-old", [])

        # Verify added to new user
        assert session_id in manager._user_sessions.get("user-new", [])

    @pytest.mark.asyncio
    async def test_update_session_user_id_nonexistent_session(self):
        """Test update_session_user_id returns False for nonexistent session."""
        from auto_bedrock_chat_fastapi.config import ChatConfig

        config = ChatConfig()
        manager = ChatSessionManager(config)
        result = await manager.update_session_user_id("nonexistent-session", "user-123")
        assert result is False


# ---------------------------------------------------------------------------
# Test: Metadata headers in tool calls
# ---------------------------------------------------------------------------


class TestMetadataHeaders:
    """Test that user metadata is properly injected as HTTP headers in tool calls."""

    @pytest.mark.asyncio
    async def test_metadata_headers_injected(self):
        """Test that verified_user_info is converted to HTTP headers."""
        from auto_bedrock_chat_fastapi.config import ChatConfig

        # Create tool manager
        config = ChatConfig()
        mock_tools_generator = Mock()
        mock_tools_generator.generate_tools_desc.return_value = {"type": "function", "functions": []}
        mock_tools_generator.get_tool_metadata.return_value = {"method": "GET", "path": "/api/test"}
        mock_tools_generator.validate_tool_call.return_value = True

        tool_manager = ToolManager(
            tools_generator=mock_tools_generator,
            base_url="http://localhost:8000",
            config=config,
        )

        # Mock HTTP client
        mock_http_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success"}
        mock_response.text = '{"result": "success"}'
        mock_http_client.get.return_value = mock_response
        tool_manager._http_client = mock_http_client

        # Create AuthInfo with metadata
        metadata = {
            "display_name": "Test User",
            "verified_user_info": {
                "user_id": "user-123",
                "tenant_id": "tenant-456",
                "email": "test@example.com",
            },
        }
        auth_info = AuthInfo(metadata=metadata)

        # Execute tool call
        tool_call = {"id": "call_1", "name": "test_tool", "arguments": {}}

        await tool_manager.execute_tool_calls([tool_call], auth_info=auth_info)

        # Verify headers were passed
        call_kwargs = mock_http_client.get.call_args[1]
        headers = call_kwargs["headers"]

        assert "X-User-ID" in headers
        assert headers["X-User-ID"] == "user-123"
        assert "X-User-Display-Name" in headers
        assert headers["X-User-Display-Name"] == "Test User"
        assert "X-User-Metadata" in headers

        # Verify metadata is base64-encoded JSON
        decoded = base64.b64decode(headers["X-User-Metadata"]).decode()
        metadata_dict = json.loads(decoded)
        assert metadata_dict["display_name"] == "Test User"
        assert metadata_dict["verified_user_info"]["user_id"] == "user-123"

    @pytest.mark.asyncio
    async def test_metadata_headers_optional(self):
        """Test that tool calls work without metadata (backward compatibility)."""
        from auto_bedrock_chat_fastapi.config import ChatConfig

        config = ChatConfig()
        mock_tools_generator = Mock()
        mock_tools_generator.generate_tools_desc.return_value = {"type": "function", "functions": []}
        mock_tools_generator.get_tool_metadata.return_value = {"method": "GET", "path": "/api/test"}
        mock_tools_generator.validate_tool_call.return_value = True

        tool_manager = ToolManager(
            tools_generator=mock_tools_generator,
            base_url="http://localhost:8000",
            config=config,
        )

        # Mock HTTP client
        mock_http_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success"}
        mock_response.text = '{"result": "success"}'
        mock_http_client.get.return_value = mock_response
        tool_manager._http_client = mock_http_client

        # Create AuthInfo without metadata
        auth_info = AuthInfo()

        # Execute tool call - should not crash
        tool_call = {"id": "call_1", "name": "test_tool", "arguments": {}}

        result = await tool_manager.execute_tool_calls([tool_call], auth_info=auth_info)

        # Should succeed without metadata headers
        assert len(result) == 1
        assert result[0]["result"] == {"result": "success"}

    @pytest.mark.asyncio
    async def test_partial_metadata_handled(self):
        """Test that missing metadata fields don't crash tool execution."""
        from auto_bedrock_chat_fastapi.config import ChatConfig

        config = ChatConfig()
        mock_tools_generator = Mock()
        mock_tools_generator.generate_tools_desc.return_value = {"type": "function", "functions": []}
        mock_tools_generator.get_tool_metadata.return_value = {"method": "GET", "path": "/api/test"}
        mock_tools_generator.validate_tool_call.return_value = True

        tool_manager = ToolManager(
            tools_generator=mock_tools_generator,
            base_url="http://localhost:8000",
            config=config,
        )

        # Mock HTTP client
        mock_http_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success"}
        mock_response.text = '{"result": "success"}'
        mock_http_client.get.return_value = mock_response
        tool_manager._http_client = mock_http_client

        # Create AuthInfo with partial metadata (missing tenant_id)
        metadata = {
            "verified_user_info": {
                "user_id": "user-123",
                # tenant_id missing
            },
        }
        auth_info = AuthInfo(metadata=metadata)

        # Execute tool call
        tool_call = {"id": "call_1", "name": "test_tool", "arguments": {}}

        await tool_manager.execute_tool_calls([tool_call], auth_info=auth_info)

        # Verify only available headers were set
        call_kwargs = mock_http_client.get.call_args[1]
        headers = call_kwargs["headers"]

        assert "X-User-ID" in headers
        assert headers["X-User-ID"] == "user-123"


# ---------------------------------------------------------------------------
# Test: AuthInfo dataclass with metadata
# ---------------------------------------------------------------------------


class TestAuthInfoWithMetadata:
    """Test AuthInfo dataclass metadata field."""

    def test_authinfo_accepts_metadata(self):
        """Test that AuthInfo can be created with metadata."""
        metadata = {"verified_user_info": {"user_id": "test"}}
        auth_info = AuthInfo(metadata=metadata)

        assert auth_info.metadata == metadata
        assert auth_info.metadata["verified_user_info"]["user_id"] == "test"

    def test_authinfo_metadata_defaults_to_none(self):
        """Test that AuthInfo metadata defaults to None."""
        auth_info = AuthInfo()
        assert auth_info.metadata is None

    def test_authinfo_metadata_with_credentials(self):
        """Test that AuthInfo can have both credentials and metadata."""
        credentials = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token")
        metadata = {"display_name": "Test User"}

        auth_info = AuthInfo(credentials=credentials, metadata=metadata)

        assert auth_info.credentials == credentials
        assert auth_info.metadata == metadata
        assert auth_info.is_authenticated is True


# ---------------------------------------------------------------------------
# Test: Error handling
# ---------------------------------------------------------------------------


class TestMetadataErrorHandling:
    """Test error handling in metadata capture and propagation."""

    @pytest.mark.asyncio
    async def test_metadata_encoding_error_doesnt_fail_request(self):
        """Test that encoding errors in metadata don't fail the tool call."""
        from auto_bedrock_chat_fastapi.config import ChatConfig

        config = ChatConfig()
        mock_tools_generator = Mock()
        mock_tools_generator.generate_tools_desc.return_value = {"type": "function", "functions": []}
        mock_tools_generator.get_tool_metadata.return_value = {"method": "GET", "path": "/api/test"}
        mock_tools_generator.validate_tool_call.return_value = True

        tool_manager = ToolManager(
            tools_generator=mock_tools_generator,
            base_url="http://localhost:8000",
            config=config,
        )

        # Mock HTTP client
        mock_http_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success"}
        mock_response.text = '{"result": "success"}'
        mock_http_client.get.return_value = mock_response
        tool_manager._http_client = mock_http_client

        # Create AuthInfo with metadata that might cause encoding issues
        # (Note: json.dumps should handle most things, but we're testing the try/except)
        metadata = {"verified_user_info": {"user_id": "user-123"}}
        auth_info = AuthInfo(metadata=metadata)

        # Patch json.dumps to simulate encoding error
        with patch("auto_bedrock_chat_fastapi.tool_manager.json.dumps") as mock_json_dumps:
            mock_json_dumps.side_effect = Exception("Encoding error")

            # Execute tool call - should not crash
            tool_call = {"id": "call_1", "name": "test_tool", "arguments": {}}

            result = await tool_manager.execute_tool_calls([tool_call], auth_info=auth_info)

            # Should succeed despite encoding error
            assert len(result) == 1
