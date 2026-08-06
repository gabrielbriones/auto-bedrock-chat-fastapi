"""Tests for ``AutoLangChatPlugin._try_silent_external_idp_cookie_auth`` and
``AutoLangChatPlugin._resolve_sso_session``.

Uses the same bare-plugin construction pattern as
``test_admin_capabilities.py``'s ``_make_bare_plugin`` (``object.__new__``)
so these tests exercise the real methods without needing a fully wired-up
plugin (FastAPI app, tool manager, chat graph, etc.).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from autolangchat.plugin import AutoLangChatPlugin


class _FakeRequest:
    def __init__(self, cookies):
        self.cookies = cookies


class _FakeSessionStore:
    """Minimal test double for the parts of ``SSOSessionStore`` that
    ``_resolve_sso_session`` touches, without pulling in real JWT encode/
    decode. A token is only ever "valid" here if it follows the test-only
    ``"valid:<session_id>"`` convention.
    """

    def __init__(self, sessions=None):
        self._sessions = sessions or {}

    @staticmethod
    def validate_session_token(token, secret):
        if isinstance(token, str) and token.startswith("valid:"):
            return token.split(":", 1)[1]
        return None

    def get_session(self, session_id):
        return self._sessions.get(session_id)


def _make_config(**overrides):
    defaults = dict(
        sso_enabled=True,
        sso_trust_external_idp_cookies=True,
        sso_client_id="client123",
        sso_session_secret="s3cr3t",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_bare_plugin(config, sso_provider=None, sso_session_store=None):
    plugin = object.__new__(AutoLangChatPlugin)
    plugin.config = config
    plugin.sso_provider = sso_provider
    plugin.sso_session_store = sso_session_store
    return plugin


COOKIE_PREFIX = "CognitoIdentityServiceProvider.client123"


def _cognito_cookies(
    username="Intel-Azure-AD-B2C_abc123", id_token="id-tok", access_token="acc-tok", refresh_token="ref-tok"
):
    return {
        f"{COOKIE_PREFIX}.LastAuthUser": username,
        f"{COOKIE_PREFIX}.{username}.idToken": id_token,
        f"{COOKIE_PREFIX}.{username}.accessToken": access_token,
        f"{COOKIE_PREFIX}.{username}.refreshToken": refresh_token,
    }


@pytest.mark.asyncio
async def test_disabled_by_default_returns_none():
    config = _make_config(sso_trust_external_idp_cookies=False)
    plugin = _make_bare_plugin(config)
    request = _FakeRequest(_cognito_cookies())

    result = await AutoLangChatPlugin._try_silent_external_idp_cookie_auth(plugin, request)

    assert result is None


@pytest.mark.asyncio
async def test_missing_last_auth_user_cookie_returns_none():
    config = _make_config()
    sso_provider = MagicMock()
    plugin = _make_bare_plugin(config, sso_provider=sso_provider)
    request = _FakeRequest({})

    result = await AutoLangChatPlugin._try_silent_external_idp_cookie_auth(plugin, request)

    assert result is None
    sso_provider.discover.assert_not_called()


@pytest.mark.asyncio
async def test_missing_id_token_cookie_returns_none():
    config = _make_config()
    sso_provider = MagicMock()
    plugin = _make_bare_plugin(config, sso_provider=sso_provider)
    request = _FakeRequest({f"{COOKIE_PREFIX}.LastAuthUser": "someuser"})

    result = await AutoLangChatPlugin._try_silent_external_idp_cookie_auth(plugin, request)

    assert result is None
    sso_provider.discover.assert_not_called()


@pytest.mark.asyncio
async def test_missing_access_token_cookie_returns_none():
    # Without an access_token, validate_id_token's at_hash (access-token
    # binding) check is silently skipped -- require it here so the
    # silent-cookie path never validates more weakly than the normal
    # callback flow (which always has one from the token exchange).
    config = _make_config()
    sso_provider = MagicMock()
    plugin = _make_bare_plugin(config, sso_provider=sso_provider)
    request = _FakeRequest(
        {
            f"{COOKIE_PREFIX}.LastAuthUser": "someuser",
            f"{COOKIE_PREFIX}.someuser.idToken": "id-tok",
        }
    )

    result = await AutoLangChatPlugin._try_silent_external_idp_cookie_auth(plugin, request)

    assert result is None
    sso_provider.discover.assert_not_called()


@pytest.mark.asyncio
async def test_valid_cognito_cookie_mints_session_token():
    config = _make_config()
    sso_provider = MagicMock()
    sso_provider.discover = AsyncMock()
    sso_provider.validate_id_token = AsyncMock(return_value={"sub": "abc123", "email": "user@example.com"})
    sso_session_store = MagicMock()
    sso_session_store.create_session.return_value = "session-id-1"
    sso_session_store.generate_session_token.return_value = "minted-session-token"
    minted_session = {
        "access_token": "acc-tok",
        "refresh_token": "ref-tok",
        "id_token": "id-tok",
        "id_token_claims": {"sub": "abc123", "email": "user@example.com"},
        "user_info": {},
    }
    sso_session_store.get_session.return_value = minted_session
    plugin = _make_bare_plugin(config, sso_provider=sso_provider, sso_session_store=sso_session_store)
    request = _FakeRequest(_cognito_cookies())

    result = await AutoLangChatPlugin._try_silent_external_idp_cookie_auth(plugin, request)

    assert result == ("minted-session-token", minted_session)
    sso_provider.validate_id_token.assert_awaited_once_with("id-tok", access_token="acc-tok")
    sso_session_store.create_session.assert_called_once_with(
        tokens={"id_token": "id-tok", "access_token": "acc-tok", "refresh_token": "ref-tok"},
        user_info={},
        id_token_claims={"sub": "abc123", "email": "user@example.com"},
    )
    sso_session_store.generate_session_token.assert_called_once_with(
        session_id="session-id-1",
        sso_session_secret="s3cr3t",
    )
    sso_session_store.get_session.assert_called_once_with("session-id-1")


@pytest.mark.asyncio
async def test_invalid_token_falls_back_to_none():
    from autolangchat.sso.sso_handler import SSOValidationError

    config = _make_config()
    sso_provider = MagicMock()
    sso_provider.discover = AsyncMock()
    sso_provider.validate_id_token = AsyncMock(side_effect=SSOValidationError("bad audience"))
    sso_session_store = MagicMock()
    plugin = _make_bare_plugin(config, sso_provider=sso_provider, sso_session_store=sso_session_store)
    request = _FakeRequest(_cognito_cookies())

    result = await AutoLangChatPlugin._try_silent_external_idp_cookie_auth(plugin, request)

    assert result is None
    sso_session_store.create_session.assert_not_called()


@pytest.mark.asyncio
async def test_discovery_failure_falls_back_to_none():
    from autolangchat.sso.sso_handler import SSODiscoveryError

    config = _make_config()
    sso_provider = MagicMock()
    sso_provider.discover = AsyncMock(side_effect=SSODiscoveryError("unreachable"))
    sso_session_store = MagicMock()
    plugin = _make_bare_plugin(config, sso_provider=sso_provider, sso_session_store=sso_session_store)
    request = _FakeRequest(_cognito_cookies())

    result = await AutoLangChatPlugin._try_silent_external_idp_cookie_auth(plugin, request)

    assert result is None
    sso_session_store.create_session.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_sso_session — integration of "own cookie" + "silent external
# cookie" fallback, as wired into the chat_ui route.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_sso_session_disabled_returns_none():
    config = _make_config(sso_enabled=False)
    plugin = _make_bare_plugin(config)
    request = _FakeRequest({"sso_session_token": "valid:sess-1"})

    session, new_token = await AutoLangChatPlugin._resolve_sso_session(plugin, request)

    assert session is None
    assert new_token is None


@pytest.mark.asyncio
async def test_resolve_sso_session_own_cookie_short_circuits_silent_auth():
    store = _FakeSessionStore(sessions={"sess-1": {"user_info": {"email": "a@b.com"}}})
    config = _make_config()
    plugin = _make_bare_plugin(config, sso_session_store=store)
    plugin._try_silent_external_idp_cookie_auth = AsyncMock()
    request = _FakeRequest({"sso_session_token": "valid:sess-1"})

    session, new_token = await AutoLangChatPlugin._resolve_sso_session(plugin, request)

    assert session == {"user_info": {"email": "a@b.com"}}
    assert new_token is None
    plugin._try_silent_external_idp_cookie_auth.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_sso_session_no_own_cookie_trust_disabled_returns_none():
    store = _FakeSessionStore()
    config = _make_config(sso_trust_external_idp_cookies=False)
    plugin = _make_bare_plugin(config, sso_session_store=store)
    request = _FakeRequest({})

    session, new_token = await AutoLangChatPlugin._resolve_sso_session(plugin, request)

    assert (session, new_token) == (None, None)


@pytest.mark.asyncio
async def test_resolve_sso_session_falls_back_to_silent_cookie_auth():
    store = _FakeSessionStore()
    config = _make_config(sso_trust_external_idp_cookies=True)
    plugin = _make_bare_plugin(config, sso_session_store=store)
    plugin._try_silent_external_idp_cookie_auth = AsyncMock(return_value=("new-token", {"sub": "x"}))
    request = _FakeRequest({})

    session, new_token = await AutoLangChatPlugin._resolve_sso_session(plugin, request)

    assert session == {"sub": "x"}
    assert new_token == "new-token"
    plugin._try_silent_external_idp_cookie_auth.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_resolve_sso_session_silent_auth_declines_returns_none():
    store = _FakeSessionStore()
    config = _make_config(sso_trust_external_idp_cookies=True)
    plugin = _make_bare_plugin(config, sso_session_store=store)
    plugin._try_silent_external_idp_cookie_auth = AsyncMock(return_value=None)
    request = _FakeRequest({})

    session, new_token = await AutoLangChatPlugin._resolve_sso_session(plugin, request)

    assert (session, new_token) == (None, None)


@pytest.mark.asyncio
async def test_resolve_sso_session_expired_own_session_falls_back():
    # Token decodes fine (well-formed "valid:<id>"), but the session it
    # references is no longer in the store (evicted/expired) — this must
    # NOT be treated as authenticated, and should fall through exactly like
    # having no cookie at all.
    store = _FakeSessionStore(sessions={})
    config = _make_config(sso_trust_external_idp_cookies=True)
    plugin = _make_bare_plugin(config, sso_session_store=store)
    plugin._try_silent_external_idp_cookie_auth = AsyncMock(return_value=None)
    request = _FakeRequest({"sso_session_token": "valid:sess-gone"})

    session, new_token = await AutoLangChatPlugin._resolve_sso_session(plugin, request)

    assert (session, new_token) == (None, None)
    plugin._try_silent_external_idp_cookie_auth.assert_awaited_once_with(request)


# ---------------------------------------------------------------------------
# _safe_return_to — open-redirect / UI-subtree-escape guard for the SSO
# `next` param.
# ---------------------------------------------------------------------------


def _make_plugin_for_return_to(ui_endpoint="/chat/ui"):
    return _make_bare_plugin(_make_config(ui_endpoint=ui_endpoint))


def test_safe_return_to_none_when_missing():
    plugin = _make_plugin_for_return_to()
    assert AutoLangChatPlugin._safe_return_to(plugin, None) is None
    assert AutoLangChatPlugin._safe_return_to(plugin, "") is None


def test_safe_return_to_accepts_exact_ui_endpoint():
    plugin = _make_plugin_for_return_to()
    assert AutoLangChatPlugin._safe_return_to(plugin, "/chat/ui") == "/chat/ui"


def test_safe_return_to_accepts_ui_subpath_with_query():
    plugin = _make_plugin_for_return_to()
    value = "/chat/ui?prompt=health-check&JOB_ID=abc123"
    assert AutoLangChatPlugin._safe_return_to(plugin, value) == value


def test_safe_return_to_rejects_absolute_url():
    plugin = _make_plugin_for_return_to()
    assert AutoLangChatPlugin._safe_return_to(plugin, "https://evil.com/chat/ui") is None


def test_safe_return_to_rejects_protocol_relative_url():
    plugin = _make_plugin_for_return_to()
    assert AutoLangChatPlugin._safe_return_to(plugin, "//evil.com/chat/ui") is None


def test_safe_return_to_rejects_backslash_prefix():
    plugin = _make_plugin_for_return_to()
    assert AutoLangChatPlugin._safe_return_to(plugin, "\\\\evil.com/chat/ui") is None


def test_safe_return_to_rejects_other_paths():
    plugin = _make_plugin_for_return_to()
    assert AutoLangChatPlugin._safe_return_to(plugin, "/admin/dashboard") is None


def test_safe_return_to_rejects_dot_segment_traversal_out_of_ui_subtree():
    # Raw string starts with the UI prefix, but normalizes (as a browser
    # would resolve the redirected Location) to a path outside it.
    plugin = _make_plugin_for_return_to()
    assert AutoLangChatPlugin._safe_return_to(plugin, "/chat/ui/../../admin") is None
    assert AutoLangChatPlugin._safe_return_to(plugin, "/chat/ui/../../../evil") is None


def test_safe_return_to_accepts_dot_segments_that_stay_within_ui_subtree():
    plugin = _make_plugin_for_return_to()
    # "/chat/ui/foo/../bar" normalizes to "/chat/ui/bar" -- still in-subtree.
    assert AutoLangChatPlugin._safe_return_to(plugin, "/chat/ui/foo/../bar") == "/chat/ui/foo/../bar"
