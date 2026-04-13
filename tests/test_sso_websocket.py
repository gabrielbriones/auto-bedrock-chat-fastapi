"""Unit tests for SSO WebSocket authentication (Subtask 4)"""

import time
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from auto_bedrock_chat_fastapi.auth_handler import AuthType, Credentials
from auto_bedrock_chat_fastapi.sso_session_store import SSOSessionStore

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SESSION_SECRET = "test-sso-secret-xyz"

_DEFAULT_TOKENS = {
    "access_token": "at_abc",
    "refresh_token": "rt_xyz",
    "id_token": "eyJ...",
    "expires_in": 3600,
}
_DEFAULT_USER_INFO = {
    "sub": "user123",
    "name": "Test User",
    "email": "user@example.com",
}


def _make_store(session_ttl=3600) -> SSOSessionStore:
    return SSOSessionStore(session_ttl=session_ttl)


def _make_config(sso_enabled=True, **overrides):
    config = MagicMock()
    config.sso_enabled = sso_enabled
    config.sso_session_secret = _SESSION_SECRET
    config.sso_session_ttl = 3600
    config.chat_endpoint = "/bedrock-chat"
    config.timeout = 30
    config.require_tool_auth = False
    config.auth_verification_endpoint = None
    config.enable_rag = False
    for k, v in overrides.items():
        setattr(config, k, v)
    return config


def _make_handler(config=None, sso_session_store=None):
    from auto_bedrock_chat_fastapi.websocket_handler import WebSocketChatHandler

    if config is None:
        config = _make_config()
    mock_session_manager = AsyncMock()
    mock_chat_manager = Mock()

    handler = WebSocketChatHandler(
        session_manager=mock_session_manager,
        config=config,
        chat_manager=mock_chat_manager,
        sso_session_store=sso_session_store,
    )
    return handler, mock_session_manager


def _make_mock_session(session_id="sess-1", credentials=None):
    from auto_bedrock_chat_fastapi.session_manager import ChatSession

    session = Mock(spec=ChatSession)
    session.session_id = session_id
    session.credentials = credentials
    session.auth_handler = None
    session.metadata = {}
    session.conversation_history = []
    return session


def _seed_sso_session(store: SSOSessionStore) -> tuple[str, str]:
    """Create an SSO session and return (session_id, session_token)."""
    session_id = store.create_session(
        tokens=_DEFAULT_TOKENS,
        user_info=_DEFAULT_USER_INFO,
        id_token_claims={"sub": "user123", "email": "user@example.com"},
    )
    token = store.generate_session_token(session_id, _SESSION_SECRET)
    return session_id, token


# ---------------------------------------------------------------------------
# Tests: AuthType.SSO presence
# ---------------------------------------------------------------------------


class TestAuthTypeSSOEnum:
    """SSO is a valid AuthType enum value."""

    def test_sso_enum_value(self):
        assert AuthType.SSO.value == "sso"

    def test_sso_from_string(self):
        assert AuthType("sso") == AuthType.SSO


# ---------------------------------------------------------------------------
# Tests: Credentials SSO fields
# ---------------------------------------------------------------------------


class TestCredentialsSSOFields:
    """Credentials supports SSO-specific fields."""

    def test_sso_credentials_default_fields_none(self):
        creds = Credentials()
        assert creds.session_token is None
        assert creds.sso_user_info is None

    def test_sso_credentials_creation(self):
        creds = Credentials(
            auth_type=AuthType.SSO,
            bearer_token="at_abc",
            session_token="signed.jwt.token",
            sso_user_info={"email": "u@example.com"},
        )
        assert creds.auth_type == AuthType.SSO
        assert creds.bearer_token == "at_abc"
        assert creds.session_token == "signed.jwt.token"
        assert creds.sso_user_info["email"] == "u@example.com"

    def test_to_dict_includes_sso_user_info(self):
        creds = Credentials(
            auth_type=AuthType.SSO,
            bearer_token="at_abc",
            sso_user_info={"email": "u@example.com"},
        )
        d = creds.to_dict()
        assert d["auth_type"] == "sso"
        assert d["sso_user_info"] == {"email": "u@example.com"}

    def test_to_dict_non_sso_excludes_sso_user_info(self):
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="tok")
        d = creds.to_dict()
        assert "sso_user_info" not in d

    def test_from_dict_parses_sso_fields(self):
        data = {
            "auth_type": "sso",
            "session_token": "my.token",
            "sso_user_info": {"name": "Alice"},
        }
        creds = Credentials.from_dict(data)
        assert creds.auth_type == AuthType.SSO
        assert creds.session_token == "my.token"
        assert creds.sso_user_info == {"name": "Alice"}


# ---------------------------------------------------------------------------
# Tests: SSO auth message handling
# ---------------------------------------------------------------------------


class TestSSOAuthMessage:
    """_handle_auth_message handles auth_type='sso' correctly."""

    @pytest.mark.asyncio
    async def test_sso_auth_message_success(self):
        store = _make_store()
        sso_session_id, token = _seed_sso_session(store)
        handler, sm = _make_handler(sso_session_store=store)

        mock_ws = AsyncMock()
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_auth_message(mock_ws, {"type": "auth", "auth_type": "sso", "session_token": token})

        assert mock_session.credentials is not None
        assert mock_session.credentials.auth_type == AuthType.SSO
        assert mock_session.credentials.bearer_token == "at_abc"
        assert mock_session.credentials.session_token == token

        # auth_configured must have been sent
        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "auth_configured" in sent_types

    @pytest.mark.asyncio
    async def test_sso_auth_message_auth_type_is_sso(self):
        store = _make_store()
        _, token = _seed_sso_session(store)
        handler, sm = _make_handler(sso_session_store=store)

        mock_ws = AsyncMock()
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_auth_message(mock_ws, {"type": "auth", "auth_type": "sso", "session_token": token})

        sent = mock_ws.send_json.call_args_list
        auth_configured = next(c for c in sent if c.args[0]["type"] == "auth_configured")
        assert auth_configured.args[0]["auth_type"] == "sso"

    @pytest.mark.asyncio
    async def test_sso_auth_message_user_info_in_session_metadata(self):
        store = _make_store()
        _, token = _seed_sso_session(store)
        handler, sm = _make_handler(sso_session_store=store)

        mock_ws = AsyncMock()
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_auth_message(mock_ws, {"type": "auth", "auth_type": "sso", "session_token": token})

        assert "sso_user_info" in mock_session.metadata
        assert mock_session.metadata["sso_user_info"]["email"] == "user@example.com"

    @pytest.mark.asyncio
    async def test_sso_auth_invalid_token_rejected(self):
        store = _make_store()
        handler, sm = _make_handler(sso_session_store=store)

        mock_ws = AsyncMock()
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_auth_message(
            mock_ws, {"type": "auth", "auth_type": "sso", "session_token": "invalid.token.here"}
        )

        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "auth_failed" in sent_types
        assert mock_session.credentials is None

    @pytest.mark.asyncio
    async def test_sso_auth_expired_session_rejected(self):
        store = _make_store()
        sso_session_id, token = _seed_sso_session(store)
        # Expire the session
        store._sessions[sso_session_id]["expires_at"] = time.time() - 1
        handler, sm = _make_handler(sso_session_store=store)

        mock_ws = AsyncMock()
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_auth_message(mock_ws, {"type": "auth", "auth_type": "sso", "session_token": token})

        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "auth_failed" in sent_types

    @pytest.mark.asyncio
    async def test_sso_auth_missing_token_rejected(self):
        store = _make_store()
        handler, sm = _make_handler(sso_session_store=store)

        mock_ws = AsyncMock()
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_auth_message(mock_ws, {"type": "auth", "auth_type": "sso"})

        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "error" in sent_types

    @pytest.mark.asyncio
    async def test_sso_not_enabled_returns_error(self):
        config = _make_config(sso_enabled=False)
        handler, sm = _make_handler(config=config, sso_session_store=None)

        mock_ws = AsyncMock()
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_auth_message(mock_ws, {"type": "auth", "auth_type": "sso", "session_token": "tok"})

        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "error" in sent_types


# ---------------------------------------------------------------------------
# Tests: Auto-auth via HttpOnly cookie
# ---------------------------------------------------------------------------


class TestSSOAutoAuth:
    """Auto-authentication via sso_session_token cookie on WS connect."""

    @pytest.mark.asyncio
    async def test_auto_auth_success_sends_auth_configured(self):
        store = _make_store()
        sso_session_id, token = _seed_sso_session(store)
        handler, sm = _make_handler(sso_session_store=store)

        mock_ws = AsyncMock()
        mock_ws.cookies = {"sso_session_token": token}
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        result = await handler._try_sso_auto_auth(mock_ws, token)

        assert result is True
        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "auth_configured" in sent_types

    @pytest.mark.asyncio
    async def test_auto_auth_sends_connection_established_first(self):
        store = _make_store()
        _, token = _seed_sso_session(store)
        handler, sm = _make_handler(sso_session_store=store)

        mock_ws = AsyncMock()
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._try_sso_auto_auth(mock_ws, token)

        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert sent_types[0] == "connection_established"
        assert "auth_configured" in sent_types

    @pytest.mark.asyncio
    async def test_auto_auth_invalid_token_returns_false(self):
        store = _make_store()
        handler, sm = _make_handler(sso_session_store=store)

        mock_ws = AsyncMock()
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        result = await handler._try_sso_auto_auth(mock_ws, "bad.token")

        assert result is False
        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "auth_failed" in sent_types

    @pytest.mark.asyncio
    async def test_auto_auth_sets_credentials_correctly(self):
        store = _make_store()
        sso_session_id, token = _seed_sso_session(store)
        handler, sm = _make_handler(sso_session_store=store)

        mock_ws = AsyncMock()
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._try_sso_auto_auth(mock_ws, token)

        assert mock_session.credentials.auth_type == AuthType.SSO
        assert mock_session.credentials.bearer_token == "at_abc"
        assert mock_session.credentials.session_token == token
        assert mock_session.metadata["display_name"] == "Test User"


# ---------------------------------------------------------------------------
# Tests: SSO session expiry mid-conversation
# ---------------------------------------------------------------------------


class TestSSOSessionExpiry:
    """auth_expired is sent when SSO session expires mid-conversation."""

    @pytest.mark.asyncio
    async def test_expired_session_sends_auth_expired(self):
        store = _make_store()
        sso_session_id, token = _seed_sso_session(store)
        # Expire the session
        store._sessions[sso_session_id]["expires_at"] = time.time() - 1

        handler, sm = _make_handler(sso_session_store=store)
        mock_ws = AsyncMock()

        # Build a session with SSO credentials
        creds = Credentials(
            auth_type=AuthType.SSO,
            bearer_token="at_abc",
            session_token=token,
        )
        mock_session = _make_mock_session(credentials=creds)
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_chat_message(mock_ws, {"type": "chat", "message": "hello"})

        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "auth_expired" in sent_types

    @pytest.mark.asyncio
    async def test_expired_session_clears_credentials(self):
        store = _make_store()
        sso_session_id, token = _seed_sso_session(store)
        store._sessions[sso_session_id]["expires_at"] = time.time() - 1

        handler, sm = _make_handler(sso_session_store=store)
        mock_ws = AsyncMock()

        creds = Credentials(auth_type=AuthType.SSO, bearer_token="at_abc", session_token=token)
        mock_session = _make_mock_session(credentials=creds)
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_chat_message(mock_ws, {"type": "chat", "message": "hello"})

        assert mock_session.credentials is None

    @pytest.mark.asyncio
    async def test_valid_session_does_not_send_auth_expired(self):
        store = _make_store()
        sso_session_id, token = _seed_sso_session(store)

        handler, sm = _make_handler(sso_session_store=store)
        mock_ws = AsyncMock()

        creds = Credentials(auth_type=AuthType.SSO, bearer_token="at_abc", session_token=token)
        mock_session = _make_mock_session(credentials=creds)
        # Give the session an empty message so chat manager isn't called
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_chat_message(mock_ws, {"type": "chat", "message": ""})  # empty triggers early error

        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "auth_expired" not in sent_types


# ---------------------------------------------------------------------------
# Tests: Logout clears SSO session
# ---------------------------------------------------------------------------


class TestSSOLogoutWebSocket:
    """Logout via WebSocket clears the server-side SSO session."""

    @pytest.mark.asyncio
    async def test_logout_deletes_sso_session(self):
        store = _make_store()
        sso_session_id, token = _seed_sso_session(store)

        handler, sm = _make_handler(sso_session_store=store)
        mock_ws = AsyncMock()

        creds = Credentials(auth_type=AuthType.SSO, bearer_token="at_abc", session_token=token)
        mock_session = _make_mock_session(credentials=creds)
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_logout(mock_ws, {})

        # Server-side SSO session must be gone
        assert store.get_session(sso_session_id) is None

    @pytest.mark.asyncio
    async def test_logout_clears_credentials(self):
        store = _make_store()
        _, token = _seed_sso_session(store)

        handler, sm = _make_handler(sso_session_store=store)
        mock_ws = AsyncMock()

        creds = Credentials(auth_type=AuthType.SSO, bearer_token="at_abc", session_token=token)
        mock_session = _make_mock_session(credentials=creds)
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_logout(mock_ws, {})

        assert mock_session.credentials is None

    @pytest.mark.asyncio
    async def test_logout_sends_logout_success(self):
        store = _make_store()
        _, token = _seed_sso_session(store)

        handler, sm = _make_handler(sso_session_store=store)
        mock_ws = AsyncMock()

        creds = Credentials(auth_type=AuthType.SSO, bearer_token="at_abc", session_token=token)
        mock_session = _make_mock_session(credentials=creds)
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_logout(mock_ws, {})

        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "logout_success" in sent_types

    @pytest.mark.asyncio
    async def test_non_sso_logout_does_not_crash(self):
        store = _make_store()
        handler, sm = _make_handler(sso_session_store=store)
        mock_ws = AsyncMock()

        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="tok")
        mock_session = _make_mock_session(credentials=creds)
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_logout(mock_ws, {})

        assert mock_session.credentials is None
        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "logout_success" in sent_types


# ---------------------------------------------------------------------------
# Tests: Missing access_token guard (Comment 5)
# ---------------------------------------------------------------------------


class TestSSOAuthMissingAccessToken:
    """SSO auth must fail if the session has no access_token."""

    @pytest.mark.asyncio
    async def test_sso_auth_rejects_session_without_access_token(self):
        store = _make_store()
        tokens_no_at = {"refresh_token": "rt", "id_token": "eyJ...", "expires_in": 3600}
        session_id = store.create_session(tokens=tokens_no_at, user_info=_DEFAULT_USER_INFO)
        token = store.generate_session_token(session_id, _SESSION_SECRET)

        handler, sm = _make_handler(sso_session_store=store)
        mock_ws = AsyncMock()
        mock_session = _make_mock_session()
        sm.get_session = AsyncMock(return_value=mock_session)

        await handler._handle_auth_message(mock_ws, {"type": "auth", "auth_type": "sso", "session_token": token})

        # Should send auth_failed
        sent_types = [call.args[0]["type"] for call in mock_ws.send_json.call_args_list]
        assert "auth_failed" in sent_types
        # Credentials should NOT be set
        assert mock_session.credentials is None
        # Broken session should be cleaned up
        assert store.get_session(session_id) is None
