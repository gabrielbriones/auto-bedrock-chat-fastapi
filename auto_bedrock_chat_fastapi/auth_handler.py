"""Authentication handler for tool call execution"""

import base64
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Default supported authentication types
DEFAULT_SUPPORTED_AUTH_TYPES: List[str] = [
    "bearer_token",
    "basic_auth",
    "api_key",
    "oauth2",
    "custom",
]


class AuthType(str, Enum):
    """Supported authentication types"""

    NONE = "none"
    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"
    OAUTH2_CLIENT_CREDENTIALS = "oauth2_client_credentials"
    API_KEY = "api_key"
    CUSTOM = "custom"


@dataclass
class Credentials:
    """Holds authentication credentials for a session"""

    auth_type: AuthType = AuthType.NONE
    bearer_token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    api_key: Optional[str] = None
    api_key_header: str = "X-API-Key"  # Default header name for API key
    token_url: Optional[str] = None  # For OAuth2
    scope: Optional[str] = None  # For OAuth2
    custom_headers: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Cached token for OAuth2
    _cached_access_token: Optional[str] = field(default=None, init=False, repr=False)
    _token_expiry: Optional[float] = field(default=None, init=False, repr=False)

    def get_auth_type_string(self) -> str:
        """Get auth_type as a string, handling both enum and string types"""
        if isinstance(self.auth_type, AuthType):
            return self.auth_type.value
        return str(self.auth_type)

    def to_dict(self) -> Dict[str, Any]:
        """Convert credentials to dictionary for serialization (excluding sensitive data)"""
        return {
            "auth_type": self.auth_type.value,
            "has_bearer_token": self.bearer_token is not None,
            "has_credentials": self.username is not None or self.password is not None,
            "has_oauth2": self.client_id is not None,
            "has_api_key": self.api_key is not None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Credentials":
        """Create credentials from dictionary"""
        auth_type_str = data.get("auth_type", "none")
        # Convert string to enum
        auth_type = AuthType(auth_type_str) if isinstance(auth_type_str, str) else auth_type_str

        return cls(
            auth_type=auth_type,
            bearer_token=data.get("bearer_token"),
            username=data.get("username"),
            password=data.get("password"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            api_key=data.get("api_key"),
            api_key_header=data.get("api_key_header", "X-API-Key"),
            token_url=data.get("token_url"),
            scope=data.get("scope"),
            custom_headers=data.get("custom_headers", {}),
            metadata=data.get("metadata", {}),
        )


class AuthenticationHandler:
    """Handles authentication for API tool calls"""

    def __init__(self, credentials: Optional[Credentials] = None):
        """
        Initialize authentication handler

        Args:
            credentials: Credentials object containing auth info
        """
        self.credentials = credentials or Credentials(auth_type=AuthType.NONE)
        self.http_client = None

    def set_http_client(self, http_client: httpx.AsyncClient):
        """Set the HTTP client for OAuth2 token requests"""
        self.http_client = http_client

    async def apply_auth_to_headers(
        self, headers: Dict[str, str], tool_auth_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """
        Apply authentication to request headers

        Args:
            headers: Current request headers
            tool_auth_config: Tool-specific auth configuration from spec

        Returns:
            Updated headers with authentication
        """
        if not self.credentials or self.credentials.auth_type == AuthType.NONE:
            return headers

        try:
            if self.credentials.auth_type == AuthType.BEARER_TOKEN:
                return self._apply_bearer_token(headers)

            elif self.credentials.auth_type == AuthType.BASIC_AUTH:
                return self._apply_basic_auth(headers)

            elif self.credentials.auth_type == AuthType.API_KEY:
                return self._apply_api_key(headers)

            elif self.credentials.auth_type == AuthType.OAUTH2_CLIENT_CREDENTIALS:
                return await self._apply_oauth2(headers, tool_auth_config)

            elif self.credentials.auth_type == AuthType.CUSTOM:
                return self._apply_custom_auth(headers, tool_auth_config)

            else:
                logger.warning(f"Unknown auth type: {self.credentials.auth_type}")
                return headers

        except Exception as e:
            logger.error(f"Error applying authentication: {str(e)}")
            raise

    def _apply_bearer_token(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Apply bearer token authentication"""
        if not self.credentials.bearer_token:
            logger.warning("Bearer token auth requested but no token provided")
            return headers

        headers["Authorization"] = f"Bearer {self.credentials.bearer_token}"
        return headers

    def _apply_basic_auth(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Apply HTTP Basic Authentication"""
        if not self.credentials.username or not self.credentials.password:
            logger.warning("Basic auth requested but credentials not provided")
            return headers

        credentials = f"{self.credentials.username}:{self.credentials.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"
        return headers

    def _apply_api_key(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Apply API key authentication"""
        if not self.credentials.api_key:
            logger.warning("API key auth requested but no key provided")
            return headers

        headers[self.credentials.api_key_header] = self.credentials.api_key
        return headers

    async def _apply_oauth2(
        self, headers: Dict[str, str], tool_auth_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """Apply OAuth2 Client Credentials authentication"""
        if not self.credentials.client_id or not self.credentials.client_secret:
            logger.warning("OAuth2 requested but client credentials not provided")
            return headers

        # Get token URL (from tool config or credentials)
        token_url = tool_auth_config.get("token_url") if tool_auth_config else None
        token_url = token_url or self.credentials.token_url

        if not token_url:
            logger.error("OAuth2 token URL not provided")
            raise ValueError("OAuth2 token URL is required for client credentials flow")

        # Check if we have a cached token
        access_token = await self._get_oauth2_token(token_url)
        headers["Authorization"] = f"Bearer {access_token}"
        return headers

    async def _get_oauth2_token(self, token_url: str) -> str:
        """
        Get OAuth2 access token using client credentials flow

        Args:
            token_url: Token endpoint URL

        Returns:
            Access token
        """

        # Check if cached token is still valid
        if self.credentials._cached_access_token and self.credentials._token_expiry:
            if time.time() < self.credentials._token_expiry:
                logger.debug("Using cached OAuth2 token")
                return self.credentials._cached_access_token

        if not self.http_client:
            raise RuntimeError("HTTP client not configured for OAuth2 token requests")

        try:
            # Request new token
            payload = {
                "grant_type": "client_credentials",
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
            }

            if self.credentials.scope:
                payload["scope"] = self.credentials.scope

            logger.debug(f"Requesting OAuth2 token from {token_url}")

            response = await self.http_client.post(token_url, data=payload)
            response.raise_for_status()

            token_data = response.json()
            access_token = token_data.get("access_token")

            if not access_token:
                raise ValueError("No access_token in OAuth2 response")

            # Cache the token with expiry
            expires_in = token_data.get("expires_in", 3600)

            self.credentials._cached_access_token = access_token
            self.credentials._token_expiry = time.time() + (expires_in * 0.9)  # Refresh at 90%

            logger.debug(f"OAuth2 token obtained, expires in {expires_in}s")
            return access_token

        except httpx.HTTPError as e:
            logger.error(f"OAuth2 token request failed: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Failed to process OAuth2 response: {str(e)}")
            raise

    def _apply_custom_auth(
        self, headers: Dict[str, str], tool_auth_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """Apply custom authentication based on tool config"""
        # Apply custom headers from credentials
        if self.credentials.custom_headers:
            headers.update(self.credentials.custom_headers)

        # Apply custom headers from tool config
        if tool_auth_config and "custom_headers" in tool_auth_config:
            headers.update(tool_auth_config["custom_headers"])

        return headers

    def validate_credentials(self) -> bool:
        """
        Validate that required credentials are set for the auth type

        Returns:
            True if credentials are valid, False otherwise
        """
        if self.credentials.auth_type == AuthType.NONE:
            return True

        if self.credentials.auth_type == AuthType.BEARER_TOKEN:
            return bool(self.credentials.bearer_token)

        if self.credentials.auth_type == AuthType.BASIC_AUTH:
            return bool(self.credentials.username and self.credentials.password)

        if self.credentials.auth_type == AuthType.API_KEY:
            return bool(self.credentials.api_key)

        if self.credentials.auth_type == AuthType.OAUTH2_CLIENT_CREDENTIALS:
            return bool(self.credentials.client_id and self.credentials.client_secret)

        # Custom auth is always valid (handled by tool-specific config)
        return True
