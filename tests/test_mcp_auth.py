"""Tests for MCP per-request auth extraction.

Verifies ``autolangchat/mcp/auth.py`` maps incoming HTTP headers to the same
``Credentials``/``AuthInfo`` shapes already used by the WebSocket ``auth``
message flow: the four "static" (stateless) auth types, plus
recognizing an SSO session token presented as a Bearer token.
"""

import base64
import time
from unittest.mock import MagicMock

from autolangchat.auth_handler import AuthType
from autolangchat.mcp.auth import build_auth_info_from_headers, build_credentials_from_headers


class TestBuildCredentialsFromHeaders:
    def test_bearer_token(self):
        creds = build_credentials_from_headers({"Authorization": "Bearer tok123"})
        assert creds.auth_type == AuthType.BEARER_TOKEN
        assert creds.bearer_token == "tok123"

    def test_bearer_token_case_insensitive_header_name(self):
        creds = build_credentials_from_headers({"authorization": "Bearer tok123"})
        assert creds.auth_type == AuthType.BEARER_TOKEN
        assert creds.bearer_token == "tok123"

    def test_basic_auth(self):
        encoded = base64.b64encode(b"alice:s3cret").decode()
        creds = build_credentials_from_headers({"Authorization": f"Basic {encoded}"})
        assert creds.auth_type == AuthType.BASIC_AUTH
        assert creds.username == "alice"
        assert creds.password == "s3cret"

    def test_malformed_basic_auth_returns_none(self):
        creds = build_credentials_from_headers({"Authorization": "Basic not-valid-base64!!"})
        assert creds is None

    def test_basic_auth_without_colon_returns_none(self):
        encoded = base64.b64encode(b"no-colon-here").decode()
        creds = build_credentials_from_headers({"Authorization": f"Basic {encoded}"})
        assert creds is None

    def test_oauth2_client_credentials(self):
        creds = build_credentials_from_headers(
            {
                "X-OAuth2-Client-Id": "client-1",
                "X-OAuth2-Client-Secret": "secret-1",
                "X-OAuth2-Token-Url": "https://idp.example.com/token",
                "X-OAuth2-Scope": "read write",
            }
        )
        assert creds.auth_type == AuthType.OAUTH2_CLIENT_CREDENTIALS
        assert creds.client_id == "client-1"
        assert creds.client_secret == "secret-1"
        assert creds.token_url == "https://idp.example.com/token"
        assert creds.scope == "read write"

    def test_oauth2_requires_all_three_fields(self):
        creds = build_credentials_from_headers({"X-OAuth2-Client-Id": "client-1", "X-OAuth2-Client-Secret": "secret-1"})
        assert creds is None

    def test_api_key(self):
        creds = build_credentials_from_headers({"X-API-Key": "key-abc"})
        assert creds.auth_type == AuthType.API_KEY
        assert creds.api_key == "key-abc"
        assert creds.api_key_header == "X-API-Key"

    def test_custom_headers_forwarded_with_prefix_stripped(self):
        creds = build_credentials_from_headers({"X-Forward-Tenant-Id": "acme", "X-Forward-Region": "us-east"})
        assert creds.auth_type == AuthType.CUSTOM
        assert creds.custom_headers == {"Tenant-Id": "acme", "Region": "us-east"}

    def test_no_recognized_headers_returns_none(self):
        creds = build_credentials_from_headers({"Content-Type": "application/json"})
        assert creds is None

    def test_bearer_takes_priority_over_api_key(self):
        creds = build_credentials_from_headers({"Authorization": "Bearer tok123", "X-API-Key": "key-abc"})
        assert creds.auth_type == AuthType.BEARER_TOKEN


class _FakeSSOSessionStore:
    """Minimal stand-in for ``SSOSessionStore`` — real JWT signing, in-memory session dict."""

    _SECRET = "test-sso-session-secret"

    def __init__(self):
        self.sessions = {}
        self.deleted = []

    def create_session(self, session_id, access_token, user_info=None):
        self.sessions[session_id] = {
            "access_token": access_token,
            "user_info": user_info or {},
            "expires_at": time.time() + 3600,
        }

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def delete_session(self, session_id):
        self.deleted.append(session_id)
        self.sessions.pop(session_id, None)

    def generate_session_token(self, session_id):
        import jwt

        return jwt.encode({"session_id": session_id}, self._SECRET, algorithm="HS256")

    def validate_session_token(self, token, secret):
        import jwt
        from jwt.exceptions import PyJWTError

        try:
            return jwt.decode(token, secret, algorithms=["HS256"]).get("session_id")
        except PyJWTError:
            return None


class TestSSOSessionTokenRecognition:
    """Bearer-presented SSO session tokens."""

    def test_valid_sso_session_token_builds_sso_credentials(self):
        store = _FakeSSOSessionStore()
        store.create_session("sess-1", access_token="idp-access-tok", user_info={"email": "alice@example.com"})
        token = store.generate_session_token("sess-1")

        creds = build_credentials_from_headers(
            {"Authorization": f"Bearer {token}"},
            sso_session_store=store,
            sso_session_secret=store._SECRET,
        )

        assert creds.auth_type == AuthType.SSO
        assert creds.bearer_token == "idp-access-tok"
        assert creds.session_token == token
        assert creds.sso_user_info == {"email": "alice@example.com"}
        assert creds.metadata["sso_session_id"] == "sess-1"

    def test_invalid_sso_session_token_falls_back_to_plain_bearer(self):
        store = _FakeSSOSessionStore()

        creds = build_credentials_from_headers(
            {"Authorization": "Bearer not-a-real-session-token"},
            sso_session_store=store,
            sso_session_secret=store._SECRET,
        )

        assert creds.auth_type == AuthType.BEARER_TOKEN
        assert creds.bearer_token == "not-a-real-session-token"

    def test_expired_or_missing_session_falls_back_to_plain_bearer(self):
        store = _FakeSSOSessionStore()
        # Token signed for a session_id that was never created (e.g. evicted).
        token = store.generate_session_token("never-existed")

        creds = build_credentials_from_headers(
            {"Authorization": f"Bearer {token}"},
            sso_session_store=store,
            sso_session_secret=store._SECRET,
        )

        assert creds.auth_type == AuthType.BEARER_TOKEN

    def test_session_missing_access_token_is_deleted_and_falls_back(self):
        store = _FakeSSOSessionStore()
        store.sessions["sess-2"] = {"access_token": None, "user_info": {}, "expires_at": time.time() + 3600}
        token = store.generate_session_token("sess-2")

        creds = build_credentials_from_headers(
            {"Authorization": f"Bearer {token}"},
            sso_session_store=store,
            sso_session_secret=store._SECRET,
        )

        assert creds.auth_type == AuthType.BEARER_TOKEN
        assert "sess-2" in store.deleted

    def test_sso_disabled_treats_token_as_plain_bearer(self):
        """Without sso_session_store/secret wired in, no SSO lookup is attempted at all."""
        store = _FakeSSOSessionStore()
        store.create_session("sess-1", access_token="idp-access-tok")
        token = store.generate_session_token("sess-1")

        creds = build_credentials_from_headers({"Authorization": f"Bearer {token}"})

        assert creds.auth_type == AuthType.BEARER_TOKEN
        assert creds.bearer_token == token

    def test_build_auth_info_from_headers_wires_sso_params_through(self):
        store = _FakeSSOSessionStore()
        store.create_session("sess-1", access_token="idp-access-tok")
        token = store.generate_session_token("sess-1")

        auth_info = build_auth_info_from_headers(
            {"Authorization": f"Bearer {token}"},
            sso_session_store=store,
            sso_session_secret=store._SECRET,
        )

        assert auth_info.credentials.auth_type == AuthType.SSO
        assert auth_info.credentials.bearer_token == "idp-access-tok"


class TestBuildAuthInfoFromHeaders:
    def test_returns_none_when_no_credentials(self):
        assert build_auth_info_from_headers({}) is None

    def test_returns_auth_info_with_handler_for_bearer(self):
        auth_info = build_auth_info_from_headers({"Authorization": "Bearer tok123"})
        assert auth_info is not None
        assert auth_info.is_authenticated is True
        assert auth_info.credentials.bearer_token == "tok123"
        assert auth_info.auth_handler is not None

    def test_http_client_wired_for_oauth2_only(self):
        fake_http_client = MagicMock()

        oauth2_auth_info = build_auth_info_from_headers(
            {
                "X-OAuth2-Client-Id": "client-1",
                "X-OAuth2-Client-Secret": "secret-1",
                "X-OAuth2-Token-Url": "https://idp.example.com/token",
            },
            http_client=fake_http_client,
        )
        assert oauth2_auth_info.auth_handler.http_client is fake_http_client

        bearer_auth_info = build_auth_info_from_headers(
            {"Authorization": "Bearer tok123"}, http_client=fake_http_client
        )
        assert bearer_auth_info.auth_handler.http_client is None
