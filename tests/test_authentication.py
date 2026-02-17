"""Tests for tool call authentication system"""

import base64
from unittest.mock import AsyncMock, Mock

import pytest

from auto_bedrock_chat_fastapi.auth_handler import (
    DEFAULT_SUPPORTED_AUTH_TYPES,
    AuthenticationHandler,
    AuthType,
    Credentials,
)
from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.session_manager import ChatSession


class TestAuthType:
    """Test AuthType enum"""

    def test_auth_type_values(self):
        """Test all authentication type values"""
        assert AuthType.NONE.value == "none"
        assert AuthType.BEARER_TOKEN.value == "bearer_token"
        assert AuthType.BASIC_AUTH.value == "basic_auth"
        assert AuthType.OAUTH2_CLIENT_CREDENTIALS.value == "oauth2_client_credentials"
        assert AuthType.API_KEY.value == "api_key"
        assert AuthType.CUSTOM.value == "custom"

    def test_auth_type_from_string(self):
        """Test creating AuthType from string"""
        auth_type = AuthType("bearer_token")
        assert auth_type == AuthType.BEARER_TOKEN


class TestCredentials:
    """Test Credentials dataclass"""

    def test_credentials_defaults(self):
        """Test default credential values"""
        creds = Credentials()
        assert creds.auth_type == AuthType.NONE
        assert creds.bearer_token is None
        assert creds.username is None
        assert creds.password is None
        assert creds.api_key is None
        assert creds.api_key_header == "X-API-Key"

    def test_credentials_bearer_token(self):
        """Test bearer token credentials"""
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="test-token-123")
        assert creds.auth_type == AuthType.BEARER_TOKEN
        assert creds.bearer_token == "test-token-123"

    def test_credentials_basic_auth(self):
        """Test basic authentication credentials"""
        creds = Credentials(auth_type=AuthType.BASIC_AUTH, username="user@example.com", password="password123")
        assert creds.auth_type == AuthType.BASIC_AUTH
        assert creds.username == "user@example.com"
        assert creds.password == "password123"

    def test_credentials_api_key(self):
        """Test API key credentials"""
        creds = Credentials(auth_type=AuthType.API_KEY, api_key="sk-1234567890", api_key_header="X-Custom-Key")
        assert creds.auth_type == AuthType.API_KEY
        assert creds.api_key == "sk-1234567890"
        assert creds.api_key_header == "X-Custom-Key"

    def test_credentials_oauth2(self):
        """Test OAuth2 credentials"""
        creds = Credentials(
            auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
            client_id="client-id",
            client_secret="client-secret",
            token_url="https://auth.example.com/token",
            scope="api:read api:write",
        )
        assert creds.auth_type == AuthType.OAUTH2_CLIENT_CREDENTIALS
        assert creds.client_id == "client-id"
        assert creds.client_secret == "client-secret"
        assert creds.token_url == "https://auth.example.com/token"
        assert creds.scope == "api:read api:write"

    def test_credentials_to_dict(self):
        """Test converting credentials to dict"""
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token123")
        creds_dict = creds.to_dict()

        assert creds_dict["auth_type"] == "bearer_token"
        assert creds_dict["has_bearer_token"] is True
        assert "bearer_token" not in creds_dict  # Token not included

    def test_credentials_from_dict(self):
        """Test creating credentials from dict"""
        data = {"auth_type": "bearer_token", "bearer_token": "token123"}
        creds = Credentials.from_dict(data)

        assert creds.auth_type == AuthType.BEARER_TOKEN
        assert creds.bearer_token == "token123"

    def test_credentials_oauth2_token_caching(self):
        """Test OAuth2 token caching mechanism"""
        creds = Credentials(
            auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS, client_id="client-id", client_secret="client-secret"
        )

        # Initially no cached token
        assert creds._cached_access_token is None
        assert creds._token_expiry is None

        # Simulate token caching
        creds._cached_access_token = "cached-token"
        creds._token_expiry = 9999999999  # Far in future

        assert creds._cached_access_token == "cached-token"
        assert creds._token_expiry == 9999999999


class TestAuthenticationHandler:
    """Test AuthenticationHandler class"""

    def test_handler_initialization(self):
        """Test handler initialization"""
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token")
        handler = AuthenticationHandler(creds)

        assert handler.credentials == creds
        assert handler.http_client is None

    def test_handler_default_credentials(self):
        """Test handler with default credentials"""
        handler = AuthenticationHandler()

        assert handler.credentials.auth_type == AuthType.NONE
        assert handler.http_client is None

    @pytest.mark.asyncio
    async def test_apply_bearer_token(self):
        """Test bearer token application"""
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="test-token-123")
        handler = AuthenticationHandler(creds)

        headers = {}
        result = await handler.apply_auth_to_headers(headers)

        assert result["Authorization"] == "Bearer test-token-123"

    @pytest.mark.asyncio
    async def test_apply_basic_auth(self):
        """Test basic authentication"""
        creds = Credentials(auth_type=AuthType.BASIC_AUTH, username="user", password="pass")
        handler = AuthenticationHandler(creds)

        headers = {}
        result = await handler.apply_auth_to_headers(headers)

        # Verify base64 encoding of "user:pass"
        expected = base64.b64encode(b"user:pass").decode()
        assert result["Authorization"] == f"Basic {expected}"

    @pytest.mark.asyncio
    async def test_apply_api_key_default_header(self):
        """Test API key with default header"""
        creds = Credentials(auth_type=AuthType.API_KEY, api_key="sk-1234567890")
        handler = AuthenticationHandler(creds)

        headers = {}
        result = await handler.apply_auth_to_headers(headers)

        assert result["X-API-Key"] == "sk-1234567890"

    @pytest.mark.asyncio
    async def test_apply_api_key_custom_header(self):
        """Test API key with custom header"""
        creds = Credentials(auth_type=AuthType.API_KEY, api_key="sk-1234567890", api_key_header="X-Custom-Key")
        handler = AuthenticationHandler(creds)

        headers = {}
        result = await handler.apply_auth_to_headers(headers)

        assert result["X-Custom-Key"] == "sk-1234567890"
        assert "X-API-Key" not in result

    @pytest.mark.asyncio
    async def test_apply_custom_auth(self):
        """Test custom authentication with headers"""
        creds = Credentials(auth_type=AuthType.CUSTOM, custom_headers={"X-Custom": "value1", "X-Version": "v2"})
        handler = AuthenticationHandler(creds)

        headers = {}
        result = await handler.apply_auth_to_headers(headers)

        assert result["X-Custom"] == "value1"
        assert result["X-Version"] == "v2"

    @pytest.mark.asyncio
    async def test_apply_auth_preserves_existing_headers(self):
        """Test that applying auth preserves existing headers"""
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token")
        handler = AuthenticationHandler(creds)

        headers = {"User-Agent": "test-agent", "Content-Type": "application/json"}
        result = await handler.apply_auth_to_headers(headers)

        assert result["User-Agent"] == "test-agent"
        assert result["Content-Type"] == "application/json"
        assert result["Authorization"] == "Bearer token"

    @pytest.mark.asyncio
    async def test_apply_no_auth(self):
        """Test applying when no auth type is set"""
        creds = Credentials(auth_type=AuthType.NONE)
        handler = AuthenticationHandler(creds)

        headers = {"Content-Type": "application/json"}
        result = await handler.apply_auth_to_headers(headers)

        # Should return headers unchanged (no auth applied)
        assert result == {"Content-Type": "application/json"}

    @pytest.mark.asyncio
    async def test_oauth2_missing_token_url(self):
        """Test OAuth2 fails without token URL"""
        creds = Credentials(
            auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS, client_id="client-id", client_secret="client-secret"
        )
        handler = AuthenticationHandler(creds)

        headers = {}
        with pytest.raises(ValueError, match="OAuth2 token URL"):
            await handler.apply_auth_to_headers(headers)

    @pytest.mark.asyncio
    async def test_oauth2_missing_http_client(self):
        """Test OAuth2 fails when HTTP client is not configured"""
        creds = Credentials(
            auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
            client_id="client-id",
            client_secret="client-secret",
            token_url="https://auth.example.com/token",
        )
        handler = AuthenticationHandler(creds)

        # Ensure http_client is None (default)
        assert handler.http_client is None

        headers = {}
        with pytest.raises(RuntimeError, match="HTTP client not configured"):
            await handler.apply_auth_to_headers(headers)

    @pytest.mark.asyncio
    async def test_oauth2_with_cached_token(self):
        """Test OAuth2 uses cached token"""
        import time

        creds = Credentials(
            auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
            client_id="client-id",
            client_secret="client-secret",
            token_url="https://auth.example.com/token",
        )

        # Pre-cache a token
        creds._cached_access_token = "cached-token"
        creds._token_expiry = time.time() + 3600  # Valid for 1 hour

        handler = AuthenticationHandler(creds)

        # Mock HTTP client
        handler.http_client = AsyncMock()

        headers = {}
        result = await handler.apply_auth_to_headers(headers)

        # Should use cached token without making request
        assert result["Authorization"] == "Bearer cached-token"
        handler.http_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_oauth2_refreshes_expired_token(self):
        """Test OAuth2 refreshes expired token"""
        import time

        creds = Credentials(
            auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
            client_id="client-id",
            client_secret="client-secret",
            token_url="https://auth.example.com/token",
        )

        # Set expired token
        creds._cached_access_token = "old-token"
        creds._token_expiry = time.time() - 100  # Already expired

        handler = AuthenticationHandler(creds)

        # Mock HTTP client with token response
        mock_response = AsyncMock()
        mock_response.json = Mock(return_value={"access_token": "new-token", "expires_in": 3600})
        mock_response.raise_for_status = Mock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        handler.http_client = mock_client

        headers = {}
        result = await handler.apply_auth_to_headers(headers)

        # Should fetch new token
        assert result["Authorization"] == "Bearer new-token"
        mock_client.post.assert_called_once()

    def test_validate_credentials_bearer_token(self):
        """Test credential validation for bearer token"""
        creds_valid = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token123")
        handler = AuthenticationHandler(creds_valid)
        assert handler.validate_credentials() is True

        creds_invalid = Credentials(auth_type=AuthType.BEARER_TOKEN)
        handler = AuthenticationHandler(creds_invalid)
        assert handler.validate_credentials() is False

    def test_validate_credentials_basic_auth(self):
        """Test credential validation for basic auth"""
        creds_valid = Credentials(auth_type=AuthType.BASIC_AUTH, username="user", password="pass")
        handler = AuthenticationHandler(creds_valid)
        assert handler.validate_credentials() is True

        creds_invalid = Credentials(auth_type=AuthType.BASIC_AUTH, username="user")
        handler = AuthenticationHandler(creds_invalid)
        assert handler.validate_credentials() is False

    def test_validate_credentials_api_key(self):
        """Test credential validation for API key"""
        creds_valid = Credentials(auth_type=AuthType.API_KEY, api_key="sk-123")
        handler = AuthenticationHandler(creds_valid)
        assert handler.validate_credentials() is True

        creds_invalid = Credentials(auth_type=AuthType.API_KEY)
        handler = AuthenticationHandler(creds_invalid)
        assert handler.validate_credentials() is False

    def test_validate_credentials_oauth2(self):
        """Test credential validation for OAuth2"""
        creds_valid = Credentials(auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS, client_id="id", client_secret="secret")
        handler = AuthenticationHandler(creds_valid)
        assert handler.validate_credentials() is True

        creds_invalid = Credentials(auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS, client_id="id")
        handler = AuthenticationHandler(creds_invalid)
        assert handler.validate_credentials() is False

    def test_validate_credentials_custom(self):
        """Test credential validation for custom auth (always valid)"""
        creds = Credentials(auth_type=AuthType.CUSTOM)
        handler = AuthenticationHandler(creds)
        assert handler.validate_credentials() is True


class TestChatSessionWithAuth:
    """Test ChatSession with authentication"""

    def test_session_credentials_default(self):
        """Test session has default credentials"""
        from fastapi import WebSocket

        websocket = Mock(spec=WebSocket)
        session = ChatSession(session_id="test-session", websocket=websocket)

        assert session.credentials.auth_type == AuthType.NONE
        assert session.auth_handler is not None

    def test_session_credentials_initialization(self):
        """Test session credentials are initialized"""
        from fastapi import WebSocket

        websocket = Mock(spec=WebSocket)
        session = ChatSession(session_id="test-session", websocket=websocket)

        # Auth handler should be created in __post_init__
        assert isinstance(session.auth_handler, AuthenticationHandler)
        assert session.auth_handler.credentials == session.credentials

    def test_session_update_credentials(self):
        """Test updating session credentials"""
        from fastapi import WebSocket

        websocket = Mock(spec=WebSocket)
        session = ChatSession(session_id="test-session", websocket=websocket)

        # Update credentials
        new_creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="new-token")
        session.credentials = new_creds
        session.auth_handler = AuthenticationHandler(new_creds)

        assert session.credentials.auth_type == AuthType.BEARER_TOKEN
        assert session.auth_handler.credentials.bearer_token == "new-token"


class TestAuthenticationConfiguration:
    """Test authentication configuration"""

    def test_auth_config_defaults(self):
        """Test default auth configuration"""
        config = ChatConfig()

        assert config.enable_tool_auth is True
        assert config.supported_auth_types == DEFAULT_SUPPORTED_AUTH_TYPES
        assert config.require_tool_auth is False
        assert config.auth_token_cache_ttl == 3600

    def test_auth_config_custom_types(self):
        """Test custom supported auth types"""
        # Test defaults - all auth types should be supported
        config = ChatConfig()

        assert "bearer_token" in config.supported_auth_types
        assert "oauth2" in config.supported_auth_types
        assert "basic_auth" in config.supported_auth_types
        assert "api_key" in config.supported_auth_types
        assert "custom" in config.supported_auth_types

    def test_auth_disabled(self):
        """Test authentication enabled by default"""
        config = ChatConfig()

        # Default should be enabled (True)
        assert config.enable_tool_auth is True

    def test_require_auth(self):
        """Test require authentication default"""
        config = ChatConfig()

        # Default should be not required (False)
        assert config.require_tool_auth is False


class TestAuthenticationIntegration:
    """Integration tests for authentication"""

    @pytest.mark.asyncio
    async def test_bearer_token_flow(self):
        """Test complete bearer token authentication flow"""
        # Create credentials
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="test-token-123")

        # Create handler
        handler = AuthenticationHandler(creds)

        # Validate credentials
        assert handler.validate_credentials() is True

        # Apply to headers
        headers = {"Content-Type": "application/json"}
        result = await handler.apply_auth_to_headers(headers)

        assert result["Authorization"] == "Bearer test-token-123"
        assert result["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_basic_auth_flow(self):
        """Test complete basic auth flow"""
        creds = Credentials(auth_type=AuthType.BASIC_AUTH, username="user@example.com", password="secret")

        handler = AuthenticationHandler(creds)
        assert handler.validate_credentials() is True

        headers = {}
        result = await handler.apply_auth_to_headers(headers)

        # Decode and verify
        auth_value = result["Authorization"].replace("Basic ", "")
        decoded = base64.b64decode(auth_value).decode()
        assert decoded == "user@example.com:secret"

    @pytest.mark.asyncio
    async def test_api_key_flow(self):
        """Test complete API key authentication flow"""
        creds = Credentials(auth_type=AuthType.API_KEY, api_key="sk-test-key", api_key_header="X-API-Key")

        handler = AuthenticationHandler(creds)
        assert handler.validate_credentials() is True

        headers = {}
        result = await handler.apply_auth_to_headers(headers)

        assert result["X-API-Key"] == "sk-test-key"

    @pytest.mark.asyncio
    async def test_multiple_sessions_isolated(self):
        """Test credentials isolated per session"""
        from fastapi import WebSocket

        websocket1 = Mock(spec=WebSocket)
        websocket2 = Mock(spec=WebSocket)

        session1 = ChatSession(session_id="session1", websocket=websocket1)
        session2 = ChatSession(session_id="session2", websocket=websocket2)

        # Set different credentials
        creds1 = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token1")
        creds2 = Credentials(auth_type=AuthType.API_KEY, api_key="key2")

        session1.credentials = creds1
        session1.auth_handler = AuthenticationHandler(creds1)

        session2.credentials = creds2
        session2.auth_handler = AuthenticationHandler(creds2)

        # Verify isolation
        assert session1.credentials.bearer_token == "token1"
        assert session2.credentials.api_key == "key2"
        assert session1.credentials != session2.credentials


class TestVerifyCredentialsRemote:
    """Test remote credential verification"""

    @pytest.mark.asyncio
    async def test_verify_success_returns_true(self):
        """Test that a 2XX response from the verification endpoint returns True"""
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="valid-token")
        handler = AuthenticationHandler(creds)

        mock_response = AsyncMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        is_valid, message = await handler.verify_credentials_remote(
            "https://api.example.com/verify", http_client=mock_client
        )

        assert is_valid is True
        assert "200" in message
        mock_client.get.assert_called_once()
        # Verify auth headers were sent
        call_kwargs = mock_client.get.call_args
        assert "Authorization" in call_kwargs[1]["headers"]
        assert call_kwargs[1]["headers"]["Authorization"] == "Bearer valid-token"

    @pytest.mark.asyncio
    async def test_verify_success_201(self):
        """Test that a 201 response also counts as valid"""
        creds = Credentials(auth_type=AuthType.API_KEY, api_key="my-key")
        handler = AuthenticationHandler(creds)

        mock_response = AsyncMock()
        mock_response.status_code = 201

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        is_valid, _ = await handler.verify_credentials_remote("https://api.example.com/verify", http_client=mock_client)

        assert is_valid is True

    @pytest.mark.asyncio
    async def test_verify_failure_401(self):
        """Test that a 401 response returns False with message"""
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="bad-token")
        handler = AuthenticationHandler(creds)

        mock_response = AsyncMock()
        mock_response.status_code = 401
        mock_response.json = Mock(return_value={"detail": "Invalid token"})

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        is_valid, message = await handler.verify_credentials_remote(
            "https://api.example.com/verify", http_client=mock_client
        )

        assert is_valid is False
        assert "401" in message
        assert "Invalid token" in message

    @pytest.mark.asyncio
    async def test_verify_failure_403(self):
        """Test that a 403 response returns False"""
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="forbidden-token")
        handler = AuthenticationHandler(creds)

        mock_response = AsyncMock()
        mock_response.status_code = 403
        mock_response.json = Mock(side_effect=Exception("not json"))
        mock_response.text = "Forbidden"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        is_valid, message = await handler.verify_credentials_remote(
            "https://api.example.com/verify", http_client=mock_client
        )

        assert is_valid is False
        assert "403" in message

    @pytest.mark.asyncio
    async def test_verify_timeout(self):
        """Test that a timeout returns False with descriptive message"""
        import httpx

        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token")
        handler = AuthenticationHandler(creds)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        is_valid, message = await handler.verify_credentials_remote(
            "https://api.example.com/verify", http_client=mock_client
        )

        assert is_valid is False
        assert "timed out" in message.lower()

    @pytest.mark.asyncio
    async def test_verify_connection_error(self):
        """Test that a connection error returns False"""
        import httpx

        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token")
        handler = AuthenticationHandler(creds)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        is_valid, message = await handler.verify_credentials_remote(
            "https://api.example.com/verify", http_client=mock_client
        )

        assert is_valid is False
        assert "connect" in message.lower()

    @pytest.mark.asyncio
    async def test_verify_sends_basic_auth_headers(self):
        """Test that basic auth credentials are sent in the verification request"""
        import base64

        creds = Credentials(auth_type=AuthType.BASIC_AUTH, username="user", password="pass")
        handler = AuthenticationHandler(creds)

        mock_response = AsyncMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        is_valid, _ = await handler.verify_credentials_remote("https://api.example.com/verify", http_client=mock_client)

        assert is_valid is True
        call_kwargs = mock_client.get.call_args
        expected = base64.b64encode(b"user:pass").decode()
        assert call_kwargs[1]["headers"]["Authorization"] == f"Basic {expected}"

    @pytest.mark.asyncio
    async def test_verify_creates_temp_client_when_none_provided(self):
        """Test that a temporary httpx client is created when none is passed"""
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="token")
        handler = AuthenticationHandler(creds)

        # Patch httpx.AsyncClient to track creation
        from unittest.mock import patch

        mock_response = AsyncMock()
        mock_response.status_code = 200

        mock_temp_client = AsyncMock()
        mock_temp_client.get = AsyncMock(return_value=mock_response)
        mock_temp_client.aclose = AsyncMock()

        with patch("auto_bedrock_chat_fastapi.auth_handler.httpx.AsyncClient", return_value=mock_temp_client):
            is_valid, _ = await handler.verify_credentials_remote("https://api.example.com/verify")

        assert is_valid is True
        mock_temp_client.aclose.assert_called_once()


class TestAuthVerificationEndpointConfig:
    """Test auth_verification_endpoint configuration"""

    def test_default_is_none(self):
        """Test that auth_verification_endpoint defaults to None"""
        config = ChatConfig()
        assert config.auth_verification_endpoint is None

    def test_set_via_attribute_assignment(self):
        """Test setting auth_verification_endpoint after construction"""
        config = ChatConfig()
        config.auth_verification_endpoint = "https://api.example.com/verify"
        assert config.auth_verification_endpoint == "https://api.example.com/verify"
