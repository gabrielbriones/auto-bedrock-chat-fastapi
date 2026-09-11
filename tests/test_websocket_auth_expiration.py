"""Tests for proactive SSO auth-expiration handling in _handle_chat_message,
and delivery of the reactive-mode auth_expired signal after ainvoke().

Covers XMGPLAT-11046 Phase 3.
"""

import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

# Sibling test modules install lightweight ``autolangchat`` package stubs into
# ``sys.modules`` at import time. Drop stub entries so this file always gets
# the genuine package.
for _name in [n for n in list(sys.modules) if n == "autolangchat" or n.startswith("autolangchat.")]:
    if getattr(sys.modules.get(_name), "__spec__", None) is None:
        del sys.modules[_name]

from autolangchat.auth_handler import AuthType, Credentials  # noqa: E402
from autolangchat.config import ChatConfig  # noqa: E402
from autolangchat.session_manager import ChatSession  # noqa: E402
from autolangchat.sso.sso_session_store import SSOSessionStore  # noqa: E402
from autolangchat.websocket_handler import WebSocketChatHandler  # noqa: E402

SSO_SECRET = "test-session-secret-32-bytes-long!!"


def _assistant_graph_state(auth_expired=False):
    metadata = {"auth_expired": True} if auth_expired else {}
    return {
        "messages": [
            {
                "role": "assistant",
                "content": "hi",
                "tool_calls": [],
                "tool_results": [],
                "metadata": {"message_id": "msg-1"},
            }
        ],
        "metadata": metadata,
        "kb_results": [],
    }


def _websocket():
    websocket = MagicMock()
    websocket.send_json = AsyncMock()
    return websocket


def _sent_messages(websocket):
    return [call.args[0] for call in websocket.send_json.call_args_list]


def _make_sso_handler(auth_expiration_behaviour="none", expires_in=3600, graph_state=None):
    """Build a WebSocketChatHandler with a real SSOSessionStore-backed SSO session."""
    config = ChatConfig().model_copy(
        update={
            "sso_session_secret": SSO_SECRET,
            "auth_expiration_behaviour": auth_expiration_behaviour,
            "chat_endpoint": "/chat",
        }
    )

    sso_session_store = SSOSessionStore(session_ttl=3600)
    sid = sso_session_store.create_session(
        tokens={"access_token": "at1", "refresh_token": "rt1", "expires_in": expires_in}
    )
    session_token = sso_session_store.generate_session_token(sid, SSO_SECRET)

    credentials = Credentials(
        auth_type=AuthType.SSO,
        bearer_token="at1",
        session_token=session_token,
        metadata={"sso_session_id": sid},
    )
    websocket = _websocket()
    chat_session = ChatSession(session_id="ws-session-1", websocket=websocket, credentials=credentials)

    session_manager = MagicMock()
    session_manager.get_session = AsyncMock(return_value=chat_session)

    chat_graph = MagicMock()
    chat_graph.ainvoke = AsyncMock(return_value=graph_state or _assistant_graph_state())

    sso_provider = MagicMock()
    sso_provider.refresh_token = AsyncMock(return_value={"access_token": "at2", "refresh_token": "rt2"})

    handler = WebSocketChatHandler(
        session_manager=session_manager,
        config=config,
        chat_graph=chat_graph,
        sso_session_store=sso_session_store,
        sso_provider=sso_provider,
    )
    return handler, chat_session, websocket, sso_session_store, sid, sso_provider


class TestNoneModePreservesLegacyBehavior:
    @pytest.mark.asyncio
    async def test_near_expiry_access_token_is_ignored_in_none_mode(self):
        handler, chat_session, websocket, *_rest, sso_provider = _make_sso_handler(
            auth_expiration_behaviour="none", expires_in=10
        )

        await handler._handle_chat_message(websocket, {"message": "hello"})

        sso_provider.refresh_token.assert_not_called()
        handler.chat_graph.ainvoke.assert_awaited_once()
        assert not any(m.get("type") == "auth_expired" for m in _sent_messages(websocket))


class TestProactiveModeRefreshSucceeds:
    @pytest.mark.asyncio
    async def test_expired_access_token_is_silently_refreshed(self):
        handler, chat_session, websocket, sso_session_store, sid, sso_provider = _make_sso_handler(
            auth_expiration_behaviour="proactive", expires_in=-100
        )

        await handler._handle_chat_message(websocket, {"message": "hello"})

        sso_provider.refresh_token.assert_awaited_once_with("rt1")
        # Chat proceeds normally -- no auth_expired, credentials preserved (updated).
        assert not any(m.get("type") == "auth_expired" for m in _sent_messages(websocket))
        handler.chat_graph.ainvoke.assert_awaited_once()
        assert chat_session.credentials is not None
        assert chat_session.credentials.bearer_token == "at2"
        # Store itself reflects the refresh, and the (re)issued session token
        # still resolves to the same session.
        assert sso_session_store.get_session(sid)["access_token"] == "at2"
        assert SSOSessionStore.validate_session_token(chat_session.credentials.session_token, SSO_SECRET) == sid


class TestProactiveModeRefreshFails:
    @pytest.mark.asyncio
    async def test_no_refresh_token_falls_back_to_auth_expired(self):
        handler, chat_session, websocket, sso_session_store, sid, sso_provider = _make_sso_handler(
            auth_expiration_behaviour="proactive", expires_in=-100
        )
        # Simulate an IdP that never returned a refresh_token. Mutate the
        # session dict directly (not via update_tokens(), which would also
        # extend access_token_expires_at and mask the expiry under test).
        sso_session_store._sessions[sid]["refresh_token"] = None

        await handler._handle_chat_message(websocket, {"message": "hello"})

        sso_provider.refresh_token.assert_not_called()
        sent = _sent_messages(websocket)
        assert any(m.get("type") == "auth_expired" for m in sent)
        assert chat_session.credentials is None
        handler.chat_graph.ainvoke.assert_not_called()


class TestReactiveAuthExpiredDeliveredAfterAinvoke:
    @pytest.mark.asyncio
    async def test_auth_expired_metadata_from_graph_sends_message_and_clears_credentials(self):
        handler, chat_session, websocket, *_rest, sso_provider = _make_sso_handler(
            auth_expiration_behaviour="reactive",
            expires_in=3600,  # fresh -- proactive check must not itself trigger
            graph_state=_assistant_graph_state(auth_expired=True),
        )

        await handler._handle_chat_message(websocket, {"message": "hello"})

        sent = _sent_messages(websocket)
        # Both the normal reply and the auth_expired signal are delivered.
        assert any(m.get("type") == "ai_response" for m in sent)
        assert any(m.get("type") == "auth_expired" for m in sent)
        assert chat_session.credentials is None


class TestIntegrationTokenExpiryMidSession:
    @pytest.mark.asyncio
    async def test_session_survives_token_expiry_between_two_turns(self):
        """End-to-end (within the WebSocketChatHandler boundary): a session
        chats successfully, goes idle long enough for the IdP access token to
        expire, and the next message recovers via a silent proactive refresh
        instead of forcing re-login -- the real-world scenario this ticket
        was written to fix.
        """
        handler, chat_session, websocket, sso_session_store, sid, sso_provider = _make_sso_handler(
            auth_expiration_behaviour="both", expires_in=3600
        )

        await handler._handle_chat_message(websocket, {"message": "hello"})

        sso_provider.refresh_token.assert_not_called()
        assert handler.chat_graph.ainvoke.await_count == 1
        assert not any(m.get("type") == "auth_expired" for m in _sent_messages(websocket))

        # Simulate waiting well past the access token's real expiry, with no
        # message sent in between (the exact scenario that used to kill the
        # session outright).
        sso_session_store._sessions[sid]["access_token_expires_at"] = time.time() - 300

        await handler._handle_chat_message(websocket, {"message": "what do you know about me?"})

        sso_provider.refresh_token.assert_awaited_once_with("rt1")
        assert handler.chat_graph.ainvoke.await_count == 2
        assert not any(m.get("type") == "auth_expired" for m in _sent_messages(websocket))
        assert chat_session.credentials is not None
        assert chat_session.credentials.bearer_token == "at2"
        assert sso_session_store.get_session(sid)["access_token"] == "at2"
