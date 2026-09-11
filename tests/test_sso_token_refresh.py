"""Tests for SSO access-token expiration tracking and the shared refresh helper.

Covers XMGPLAT-11046 Phase 1 pieces:
  - SSOSessionStore.create_session()/update_tokens() tracking access_token_expires_at
    separately from the app-session expires_at TTL
  - SSOSessionStore.get_refresh_lock() (lazy creation, removal on delete/expiry)
  - refresh_sso_session_if_needed() (success, no-session, no-refresh-token, IdP
    failure, and concurrent-callers-share-one-refresh-call scenarios)
  - auth_handler.can_refresh()
"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from ._autolangchat_imports import load_module

_sso_store_mod = load_module("autolangchat.sso.sso_session_store", "sso/sso_session_store.py")
SSOSessionStore = _sso_store_mod.SSOSessionStore
refresh_sso_session_if_needed = _sso_store_mod.refresh_sso_session_if_needed

from autolangchat.auth_handler import AuthType, Credentials, can_refresh  # noqa: E402

# Imported normally (not via load_module) so these are the SAME class objects
# that refresh_sso_session_if_needed's own lazy `from .sso_handler import ...`
# resolves to at call time -- load_module would create a second, distinct
# module (and therefore non-matching exception classes for except-clause
# identity checks).
from autolangchat.sso.sso_handler import SSODiscoveryError, SSOTokenError  # noqa: E402


class TestAccessTokenExpiresAt:
    def test_create_session_stores_access_token_expiry_from_expires_in(self):
        store = SSOSessionStore(session_ttl=3600)
        before = time.time()
        sid = store.create_session(tokens={"access_token": "at", "refresh_token": "rt", "expires_in": 60})
        session = store.get_session(sid)

        assert session["access_token_expires_at"] >= before + 60
        assert session["access_token_expires_at"] <= time.time() + 60

    def test_create_session_falls_back_to_session_ttl_when_no_expires_in(self):
        store = SSOSessionStore(session_ttl=100)
        sid = store.create_session(tokens={"access_token": "at"})
        session = store.get_session(sid)

        assert session["access_token_expires_at"] == pytest.approx(time.time() + 100, abs=2)

    def test_update_tokens_refreshes_access_token_expiry(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens={"access_token": "at", "refresh_token": "rt", "expires_in": 60})

        store.update_tokens(sid, {"access_token": "new_at", "expires_in": 7200})
        session = store.get_session(sid)

        assert session["access_token"] == "new_at"
        assert session["access_token_expires_at"] >= time.time() + 7100


class TestRefreshLock:
    def test_same_session_id_returns_same_lock(self):
        store = SSOSessionStore(session_ttl=3600)
        lock_a = store.get_refresh_lock("sess-1")
        lock_b = store.get_refresh_lock("sess-1")
        assert lock_a is lock_b

    def test_different_session_ids_get_different_locks(self):
        store = SSOSessionStore(session_ttl=3600)
        assert store.get_refresh_lock("sess-1") is not store.get_refresh_lock("sess-2")

    def test_delete_session_removes_its_lock(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens={"access_token": "at"})
        lock_before = store.get_refresh_lock(sid)

        store.delete_session(sid)

        lock_after = store.get_refresh_lock(sid)
        assert lock_after is not lock_before


class TestRefreshSsoSessionIfNeeded:
    @pytest.mark.asyncio
    async def test_successful_refresh_updates_session_and_returns_it(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens={"access_token": "old_at", "refresh_token": "old_rt", "expires_in": 60})

        provider = AsyncMock()
        provider.refresh_token = AsyncMock(
            return_value={"access_token": "new_at", "refresh_token": "new_rt", "expires_in": 3600}
        )

        result = await refresh_sso_session_if_needed(store, provider, sid)

        provider.refresh_token.assert_awaited_once_with("old_rt")
        assert result["access_token"] == "new_at"
        assert result["refresh_token"] == "new_rt"
        assert store.get_session(sid)["access_token"] == "new_at"

    @pytest.mark.asyncio
    async def test_missing_session_returns_none(self):
        store = SSOSessionStore(session_ttl=3600)
        provider = AsyncMock()

        result = await refresh_sso_session_if_needed(store, provider, "does-not-exist")

        assert result is None
        provider.refresh_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_refresh_token_returns_none(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens={"access_token": "at"})  # no refresh_token
        provider = AsyncMock()

        result = await refresh_sso_session_if_needed(store, provider, sid)

        assert result is None
        provider.refresh_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_idp_failure_returns_none_and_leaves_session_untouched(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens={"access_token": "old_at", "refresh_token": "old_rt"})
        provider = AsyncMock()
        provider.refresh_token = AsyncMock(side_effect=SSOTokenError("refresh rejected by IdP"))

        result = await refresh_sso_session_if_needed(store, provider, sid)

        assert result is None
        assert store.get_session(sid)["access_token"] == "old_at"

    @pytest.mark.asyncio
    async def test_idp_discovery_failure_is_also_handled(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens={"access_token": "old_at", "refresh_token": "old_rt"})
        provider = AsyncMock()
        provider.refresh_token = AsyncMock(side_effect=SSODiscoveryError("discovery unavailable"))

        result = await refresh_sso_session_if_needed(store, provider, sid)

        assert result is None

    @pytest.mark.asyncio
    async def test_concurrent_callers_only_trigger_one_idp_refresh_call(self):
        """Two overlapping refresh attempts for the same session must not both
        hit the IdP's refresh_token grant -- the loser should just observe the
        winner's already-refreshed session once it gets the lock."""
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens={"access_token": "old_at", "refresh_token": "old_rt"})

        call_count = 0

        async def _slow_refresh(refresh_token):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return {"access_token": f"new_at_{call_count}", "refresh_token": "old_rt", "expires_in": 3600}

        provider = AsyncMock()
        provider.refresh_token = _slow_refresh

        results = await asyncio.gather(
            refresh_sso_session_if_needed(store, provider, sid),
            refresh_sso_session_if_needed(store, provider, sid),
        )

        assert call_count == 1
        assert all(r is not None for r in results)
        assert store.get_session(sid)["access_token"] == "new_at_1"


class TestCanRefresh:
    def test_false_for_none_credentials(self):
        assert can_refresh(None, {"refresh_token": "rt"}) is False

    def test_false_for_non_sso_auth_type(self):
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="tok")
        assert can_refresh(creds, {"refresh_token": "rt"}) is False

    def test_true_for_sso_with_refresh_token(self):
        creds = Credentials(auth_type=AuthType.SSO, bearer_token="at")
        assert can_refresh(creds, {"refresh_token": "rt"}) is True

    def test_false_for_sso_without_session(self):
        creds = Credentials(auth_type=AuthType.SSO, bearer_token="at")
        assert can_refresh(creds, None) is False

    def test_false_for_sso_session_without_refresh_token(self):
        creds = Credentials(auth_type=AuthType.SSO, bearer_token="at")
        assert can_refresh(creds, {"access_token": "at"}) is False
