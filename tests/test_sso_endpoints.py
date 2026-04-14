"""Unit tests for SSO callback endpoints and SSOSessionStore"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_bedrock_chat_fastapi.sso_session_store import SSOSessionStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_SECRET = "test-session-secret-1234"
_DEFAULT_TOKENS = {
    "access_token": "at_abc",
    "refresh_token": "rt_xyz",
    "id_token": "eyJ...",
    "expires_in": 3600,
}
_DEFAULT_USER_INFO = {"sub": "user123", "name": "Test User", "email": "user@example.com"}
_DEFAULT_CLAIMS = {"sub": "user123", "aud": "client-id", "email": "user@example.com"}


# ---------------------------------------------------------------------------
# Tests: SSOSessionStore — session CRUD
# ---------------------------------------------------------------------------


class TestSSOSessionStoreSessions:
    """SSOSessionStore session create / get / delete / update."""

    def test_create_session_returns_id(self):
        store = SSOSessionStore(session_ttl=3600)
        session_id = store.create_session(tokens=_DEFAULT_TOKENS, user_info=_DEFAULT_USER_INFO)
        assert isinstance(session_id, str)
        assert len(session_id) > 0

    def test_get_session_returns_data(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens=_DEFAULT_TOKENS, user_info=_DEFAULT_USER_INFO)
        session = store.get_session(sid)
        assert session is not None
        assert session["access_token"] == "at_abc"
        assert session["refresh_token"] == "rt_xyz"
        assert session["user_info"]["email"] == "user@example.com"

    def test_get_session_returns_none_for_unknown(self):
        store = SSOSessionStore()
        assert store.get_session("nonexistent-id") is None

    def test_get_session_returns_none_for_expired(self):
        store = SSOSessionStore(session_ttl=1)
        sid = store.create_session(tokens=_DEFAULT_TOKENS)
        # Manually expire it
        store._sessions[sid]["expires_at"] = time.time() - 1
        assert store.get_session(sid) is None

    def test_delete_session_removes_it(self):
        store = SSOSessionStore()
        sid = store.create_session(tokens=_DEFAULT_TOKENS)
        store.delete_session(sid)
        assert store.get_session(sid) is None

    def test_delete_session_idempotent(self):
        store = SSOSessionStore()
        store.delete_session("does-not-exist")  # Should not raise

    def test_update_tokens_changes_access_token(self):
        store = SSOSessionStore()
        sid = store.create_session(tokens=_DEFAULT_TOKENS)
        result = store.update_tokens(sid, {"access_token": "new_at", "refresh_token": "new_rt"})
        assert result is True
        session = store.get_session(sid)
        assert session["access_token"] == "new_at"
        assert session["refresh_token"] == "new_rt"

    def test_update_tokens_returns_false_for_unknown(self):
        store = SSOSessionStore()
        assert store.update_tokens("bad-id", {"access_token": "x"}) is False

    def test_cleanup_expired_removes_expired(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens=_DEFAULT_TOKENS)
        store._sessions[sid]["expires_at"] = time.time() - 1
        removed = store.cleanup_expired()
        assert removed == 1
        assert store.get_session(sid) is None

    def test_cleanup_expired_preserves_active(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens=_DEFAULT_TOKENS)
        removed = store.cleanup_expired()
        assert removed == 0
        assert store.get_session(sid) is not None


# ---------------------------------------------------------------------------
# Tests: SSOSessionStore — pending auth store
# ---------------------------------------------------------------------------


class TestSSOSessionStorePending:
    """Pending auth store: state / code_verifier lifecycle."""

    def test_store_and_get_pending(self):
        store = SSOSessionStore()
        store.store_pending("state_abc", "verifier_xyz")
        assert store.get_pending("state_abc") == "verifier_xyz"

    def test_get_pending_returns_none_for_unknown(self):
        store = SSOSessionStore()
        assert store.get_pending("no-such-state") is None

    def test_get_pending_returns_none_after_expiry(self):
        store = SSOSessionStore()
        store.store_pending("state_abc", "verifier_xyz", ttl=1)
        store._pending["state_abc"]["expires_at"] = time.time() - 1
        assert store.get_pending("state_abc") is None

    def test_delete_pending_removes_entry(self):
        store = SSOSessionStore()
        store.store_pending("state_abc", "verifier_xyz")
        store.delete_pending("state_abc")
        assert store.get_pending("state_abc") is None

    def test_delete_pending_idempotent(self):
        store = SSOSessionStore()
        store.delete_pending("does-not-exist")  # Should not raise


# ---------------------------------------------------------------------------
# Tests: SSOSessionStore — session token generation / validation
# ---------------------------------------------------------------------------


class TestSSOSessionToken:
    """generate_session_token / validate_session_token round-trip."""

    def test_round_trip(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens=_DEFAULT_TOKENS)
        token = store.generate_session_token(sid, _SESSION_SECRET)
        recovered = SSOSessionStore.validate_session_token(token, _SESSION_SECRET)
        assert recovered == sid

    def test_invalid_token_returns_none(self):
        assert SSOSessionStore.validate_session_token("not.a.jwt", _SESSION_SECRET) is None

    def test_wrong_secret_returns_none(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens=_DEFAULT_TOKENS)
        token = store.generate_session_token(sid, _SESSION_SECRET)
        assert SSOSessionStore.validate_session_token(token, "wrong-secret") is None

    def test_expired_token_returns_none(self):
        from jose import jwt as jose_jwt

        # Create a token that expired 1 second ago
        claims = {
            "session_id": "some-id",
            "exp": int(time.time()) - 1,
            "iat": int(time.time()) - 3600,
        }
        expired_token = jose_jwt.encode(claims, _SESSION_SECRET, algorithm="HS256")
        assert SSOSessionStore.validate_session_token(expired_token, _SESSION_SECRET) is None


# ---------------------------------------------------------------------------
# Fixtures: FastAPI test client with SSO-enabled plugin
# ---------------------------------------------------------------------------


def _make_sso_config():
    """Build a mock ChatConfig with SSO enabled."""
    config = MagicMock()
    config.sso_enabled = True
    config.sso_client_id = "test-client-id"
    config.sso_client_secret = "test-client-secret"
    config.sso_session_secret = _SESSION_SECRET
    config.sso_session_ttl = 3600
    config.sso_discovery_url = "https://idp.example.com/.well-known/openid-configuration"
    config.sso_authorization_url = None
    config.sso_token_url = None
    config.sso_userinfo_url = None
    config.sso_jwks_url = None
    config.sso_scopes = "openid profile email"
    config.sso_callback_path = "/chat/auth/callback"
    config.sso_provider = None
    config.api_base_url = "https://app.example.com"
    config.ui_endpoint = "/bedrock-chat/ui"
    config.chat_endpoint = "/bedrock-chat"
    return config


def _build_test_app():
    """Create a minimal FastAPI app with only SSO routes registered (no full plugin)."""
    import html as html_mod
    import secrets as secrets_mod

    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

    from auto_bedrock_chat_fastapi.sso_handler import SSOProvider
    from auto_bedrock_chat_fastapi.sso_session_store import SSOSessionStore

    app = FastAPI()
    config = _make_sso_config()
    sso_provider = SSOProvider(config)
    sso_session_store = SSOSessionStore(session_ttl=config.sso_session_ttl)

    @app.get(f"{config.chat_endpoint}/auth/sso/login")
    async def sso_login():
        try:
            await sso_provider.discover()
        except Exception as exc:
            return JSONResponse({"error": "sso_discovery_failed", "detail": str(exc)}, status_code=502)
        state = secrets_mod.token_urlsafe(32)
        auth_url, code_verifier = sso_provider.build_authorization_url(state=state)
        sso_session_store.store_pending(state, code_verifier)
        return RedirectResponse(url=auth_url, status_code=302)

    @app.get(config.sso_callback_path)
    async def sso_callback(
        request: Request, code: str = None, state: str = None, error: str = None, error_description: str = None
    ):
        if error:
            return HTMLResponse(
                content=f"<html><body><h1>SSO Login Failed</h1><p>{html_mod.escape(error)}</p></body></html>",
                status_code=400,
            )
        if not code or not state:
            return HTMLResponse(
                content="<html><body><h1>SSO Error</h1><p>Missing code or state parameter.</p></body></html>",
                status_code=400,
            )
        code_verifier = sso_session_store.get_pending(state)
        if code_verifier is None:
            return HTMLResponse(
                content="<html><body><h1>SSO Error</h1><p>Invalid or expired state parameter.</p></body></html>",
                status_code=400,
            )
        sso_session_store.delete_pending(state)
        try:
            tokens = await sso_provider.exchange_code(code=code, code_verifier=code_verifier)
        except Exception as exc:
            return HTMLResponse(
                content=f"<html><body><h1>SSO Error</h1><p>Token exchange failed: {html_mod.escape(str(exc))}</p></body></html>",
                status_code=502,
            )
        id_token_claims = {}
        if tokens.get("id_token"):
            try:
                id_token_claims = await sso_provider.validate_id_token(tokens["id_token"])
            except Exception:
                return HTMLResponse(
                    content="<html><body><h1>SSO Error</h1><p>ID token validation failed.</p></body></html>",
                    status_code=401,
                )
        if not tokens.get("access_token"):
            return HTMLResponse(
                content="<html><body><h1>SSO Error</h1><p>No access token returned.</p></body></html>",
                status_code=502,
            )
        user_info = {}
        session_id = sso_session_store.create_session(
            tokens=tokens, user_info=user_info, id_token_claims=id_token_claims
        )
        session_token = sso_session_store.generate_session_token(
            session_id=session_id, sso_session_secret=config.sso_session_secret
        )
        redirect_url = config.ui_endpoint
        response = RedirectResponse(url=redirect_url, status_code=302)
        response.set_cookie(
            key="sso_session_token", value=session_token, httponly=True, samesite="lax", max_age=config.sso_session_ttl
        )
        return response

    @app.post(f"{config.chat_endpoint}/auth/sso/refresh")
    async def sso_refresh(request: Request):
        session_token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_token = auth_header[7:]
        else:
            try:
                body = await request.json()
                session_token = body.get("session_token")
            except Exception:
                pass
        if not session_token:
            return JSONResponse({"error": "missing_session_token"}, status_code=401)
        session_id = SSOSessionStore.validate_session_token(session_token, config.sso_session_secret)
        if not session_id:
            return JSONResponse({"error": "invalid_session_token"}, status_code=401)
        session = sso_session_store.get_session(session_id)
        if not session:
            return JSONResponse({"error": "session_not_found"}, status_code=401)
        refresh_tok = session.get("refresh_token")
        if not refresh_tok:
            return JSONResponse({"error": "no_refresh_token"}, status_code=400)
        try:
            new_tokens = await sso_provider.refresh_token(refresh_tok)
        except Exception as exc:
            return JSONResponse({"error": "refresh_failed", "detail": str(exc)}, status_code=502)
        sso_session_store.update_tokens(session_id, new_tokens)
        new_session_token = sso_session_store.generate_session_token(
            session_id=session_id, sso_session_secret=config.sso_session_secret
        )
        updated_session = sso_session_store.get_session(session_id)
        return JSONResponse(
            {
                "session_token": new_session_token,
                "expires_at": updated_session["expires_at"] if updated_session else None,
            }
        )

    @app.post(f"{config.chat_endpoint}/auth/sso/logout")
    async def sso_logout(request: Request):
        session_token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_token = auth_header[7:]
        else:
            try:
                body = await request.json()
                session_token = body.get("session_token")
            except Exception:
                pass
        if not session_token:
            session_token = request.cookies.get("sso_session_token")
        if session_token:
            session_id = SSOSessionStore.validate_session_token(session_token, config.sso_session_secret)
            if session_id:
                sso_session_store.delete_session(session_id)
        response = JSONResponse({"logged_out": True})
        response.delete_cookie(key="sso_session_token")
        return response

    return app, sso_provider, sso_session_store, config


@pytest.fixture
def test_app_bundle():
    """Provide (TestClient, sso_provider, sso_session_store, config)."""
    app, provider, store, config = _build_test_app()
    client = TestClient(app, follow_redirects=False)
    return client, provider, store, config


# ---------------------------------------------------------------------------
# Tests: GET {chat_endpoint}/auth/sso/login
# ---------------------------------------------------------------------------


class TestSSOLoginEndpoint:
    """GET {chat_endpoint}/auth/sso/login — redirects to IdP and stores pending state."""

    def test_login_redirects_302(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        with patch.object(provider, "discover", new_callable=AsyncMock):
            provider._authorization_endpoint = "https://idp.example.com/authorize"
            response = client.get(f"{config.chat_endpoint}/auth/sso/login")
        assert response.status_code == 302

    def test_login_redirect_url_contains_idp_endpoint(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        with patch.object(provider, "discover", new_callable=AsyncMock):
            provider._authorization_endpoint = "https://idp.example.com/authorize"
            response = client.get(f"{config.chat_endpoint}/auth/sso/login")
        location = response.headers["location"]
        assert "idp.example.com/authorize" in location

    def test_login_stores_pending_state(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        with patch.object(provider, "discover", new_callable=AsyncMock):
            provider._authorization_endpoint = "https://idp.example.com/authorize"
            client.get(f"{config.chat_endpoint}/auth/sso/login")
        assert len(store._pending) == 1

    def test_login_returns_502_on_discovery_failure(self, test_app_bundle):
        from auto_bedrock_chat_fastapi.sso_handler import SSODiscoveryError

        client, provider, store, config = test_app_bundle
        with patch.object(provider, "discover", new_callable=AsyncMock, side_effect=SSODiscoveryError("network error")):
            response = client.get(f"{config.chat_endpoint}/auth/sso/login")
        assert response.status_code == 502


# ---------------------------------------------------------------------------
# Tests: GET {sso_callback_path}
# ---------------------------------------------------------------------------


class TestSSOCallbackEndpoint:
    """GET {sso_callback_path} — full happy-path and error cases."""

    def _seed_pending(self, store: SSOSessionStore) -> tuple[str, str]:
        """Seed a pending state and return (state, code_verifier)."""
        state = "test-state-" + "x" * 20
        verifier = "v" * 64
        store.store_pending(state, verifier)
        return state, verifier

    def test_callback_happy_path_creates_session_and_redirects(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        state, verifier = self._seed_pending(store)

        with (
            patch.object(provider, "exchange_code", new_callable=AsyncMock, return_value=_DEFAULT_TOKENS),
            patch.object(provider, "validate_id_token", new_callable=AsyncMock, return_value=_DEFAULT_CLAIMS),
        ):
            response = client.get(f"{config.sso_callback_path}?code=AUTH_CODE&state={state}")

        assert response.status_code == 302
        # Session token should NOT be in the redirect URL (cookie-only flow)
        assert "session_token=" not in response.headers["location"]
        assert response.headers["location"] == config.ui_endpoint

    def test_callback_sets_cookie(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        state, verifier = self._seed_pending(store)

        with (
            patch.object(provider, "exchange_code", new_callable=AsyncMock, return_value=_DEFAULT_TOKENS),
            patch.object(provider, "validate_id_token", new_callable=AsyncMock, return_value=_DEFAULT_CLAIMS),
        ):
            response = client.get(f"{config.sso_callback_path}?code=AUTH_CODE&state={state}")

        assert "sso_session_token" in response.cookies

    def test_callback_pending_state_consumed(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        state, verifier = self._seed_pending(store)

        with (
            patch.object(provider, "exchange_code", new_callable=AsyncMock, return_value=_DEFAULT_TOKENS),
            patch.object(provider, "validate_id_token", new_callable=AsyncMock, return_value=_DEFAULT_CLAIMS),
        ):
            client.get(f"{config.sso_callback_path}?code=AUTH_CODE&state={state}")

        # State must be consumed (one-time use)
        assert store.get_pending(state) is None

    def test_callback_rejects_invalid_state(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        response = client.get(f"{config.sso_callback_path}?code=CODE&state=INVALID_STATE")
        assert response.status_code == 400

    def test_callback_rejects_expired_state(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        state, verifier = self._seed_pending(store)
        # Expire the state
        store._pending[state]["expires_at"] = time.time() - 1
        response = client.get(f"{config.sso_callback_path}?code=CODE&state={state}")
        assert response.status_code == 400

    def test_callback_returns_400_on_idp_error_param(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        response = client.get(f"{config.sso_callback_path}?error=access_denied&error_description=User+denied")
        assert response.status_code == 400
        assert "access_denied" in response.text

    def test_callback_returns_400_on_missing_code(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        state, _ = self._seed_pending(store)
        response = client.get(f"{config.sso_callback_path}?state={state}")
        assert response.status_code == 400

    def test_callback_handles_token_exchange_error(self, test_app_bundle):
        from auto_bedrock_chat_fastapi.sso_handler import SSOTokenError

        client, provider, store, config = test_app_bundle
        state, verifier = self._seed_pending(store)

        with patch.object(
            provider, "exchange_code", new_callable=AsyncMock, side_effect=SSOTokenError("invalid_grant")
        ):
            response = client.get(f"{config.sso_callback_path}?code=BAD_CODE&state={state}")

        assert response.status_code == 502

    def test_callback_rejects_missing_access_token(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        state, verifier = self._seed_pending(store)

        tokens_no_at = {"id_token": "eyJ...", "refresh_token": "rt", "expires_in": 3600}
        with (
            patch.object(provider, "exchange_code", new_callable=AsyncMock, return_value=tokens_no_at),
            patch.object(provider, "validate_id_token", new_callable=AsyncMock, return_value={"sub": "u1"}),
        ):
            response = client.get(f"{config.sso_callback_path}?code=CODE&state={state}")

        assert response.status_code == 502
        assert "access token" in response.text.lower()


# ---------------------------------------------------------------------------
# Tests: POST {chat_endpoint}/auth/sso/logout
# ---------------------------------------------------------------------------


class TestSSOLogoutEndpoint:
    """POST {chat_endpoint}/auth/sso/logout — clears session and cookie."""

    def test_logout_clears_session(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        sid = store.create_session(tokens=_DEFAULT_TOKENS)
        token = store.generate_session_token(sid, _SESSION_SECRET)

        response = client.post(f"{config.chat_endpoint}/auth/sso/logout", json={"session_token": token})
        assert response.status_code == 200
        assert response.json()["logged_out"] is True
        assert store.get_session(sid) is None

    def test_logout_deletes_cookie(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        sid = store.create_session(tokens=_DEFAULT_TOKENS)
        token = store.generate_session_token(sid, _SESSION_SECRET)

        response = client.post(f"{config.chat_endpoint}/auth/sso/logout", json={"session_token": token})
        # Cookie should be cleared (Set-Cookie with empty value or max-age=0)
        set_cookie = response.headers.get("set-cookie", "")
        assert "sso_session_token" in set_cookie

    def test_logout_with_bearer_header(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        sid = store.create_session(tokens=_DEFAULT_TOKENS)
        token = store.generate_session_token(sid, _SESSION_SECRET)

        response = client.post(
            f"{config.chat_endpoint}/auth/sso/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert store.get_session(sid) is None

    def test_logout_with_no_token_still_succeeds(self, test_app_bundle):
        client, provider, store, config = test_app_bundle
        response = client.post(f"{config.chat_endpoint}/auth/sso/logout", json={})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tests: SSO endpoints not present when sso_enabled=False
# ---------------------------------------------------------------------------


class TestSSOEndpointsNotRegisteredWhenDisabled:
    """SSO routes must not exist when sso_enabled=False."""

    def test_setup_sso_routes_not_called_when_disabled(self):
        """_setup_sso_routes should not be called when sso_enabled=False."""
        from unittest.mock import MagicMock, patch

        from auto_bedrock_chat_fastapi.plugin import BedrockChatPlugin

        with patch.object(BedrockChatPlugin, "__init__", return_value=None):
            plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)

        plugin.config = MagicMock()
        plugin.config.sso_enabled = False
        plugin.config.enable_ui = False
        plugin.config.enable_rag = False
        plugin.config.chat_endpoint = "/bedrock-chat"
        plugin.config.websocket_endpoint = "/bedrock-chat/ws"
        plugin.sso_provider = None
        plugin.sso_session_store = None
        plugin.app = FastAPI()

        with patch.object(plugin, "_setup_sso_routes") as mock_sso_setup:
            plugin._setup_routes()
            mock_sso_setup.assert_not_called()
