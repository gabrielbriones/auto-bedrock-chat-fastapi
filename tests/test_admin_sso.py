import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "autolangchat"


def _install_package_stubs():
    package = types.ModuleType("autolangchat")
    package.__path__ = [str(PACKAGE_ROOT)]
    admin_pkg = types.ModuleType("autolangchat.admin")
    admin_pkg.__path__ = [str(PACKAGE_ROOT / "admin")]
    sso_pkg = types.ModuleType("autolangchat.sso")
    sso_pkg.__path__ = [str(PACKAGE_ROOT / "sso")]
    return {
        "autolangchat": package,
        "autolangchat.admin": admin_pkg,
        "autolangchat.sso": sso_pkg,
    }


def _load_module(module_name: str, relative_path: str, extra_modules=None):
    module_path = PACKAGE_ROOT / relative_path
    installed = _install_package_stubs()
    if extra_modules:
        installed.update(extra_modules)

    original = {name: sys.modules.get(name) for name in installed}
    try:
        sys.modules.update(installed)
        spec = spec_from_file_location(module_name, module_path)
        module = module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in original.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


_sso_store_mod = _load_module("autolangchat.sso.sso_session_store", "sso/sso_session_store.py")

extra = {
    "autolangchat.sso.sso_session_store": _sso_store_mod,
}
_admin_auth_mod = _load_module("autolangchat.admin.admin_auth", "admin/admin_auth.py", extra_modules=extra)

SSOSessionStore = _sso_store_mod.SSOSessionStore
extract_user_id_from_sso_session = _sso_store_mod.extract_user_id_from_sso_session
AdminIdentity = _admin_auth_mod.AdminIdentity
DenyAllAdminAuthorizer = _admin_auth_mod.DenyAllAdminAuthorizer
RemoteAdminAuthorizer = _admin_auth_mod.RemoteAdminAuthorizer
SSOGroupAdminAuthorizer = _admin_auth_mod.SSOGroupAdminAuthorizer
build_admin_authorizer = _admin_auth_mod.build_admin_authorizer
resolve_admin_identity_from_sso_session = _admin_auth_mod.resolve_admin_identity_from_sso_session


class TestSSOSessionStore:
    def test_create_get_update_round_trip(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(
            tokens={"access_token": "at_abc", "refresh_token": "rt_xyz", "id_token": "id_123"},
            user_info={"email": "user@example.com", "sub": "user123"},
            id_token_claims={"sub": "user123", "aud": "client-id"},
        )

        session = store.get_session(sid)
        assert session is not None
        assert session["access_token"] == "at_abc"
        assert session["user_info"]["email"] == "user@example.com"

        assert store.update_tokens(sid, {"access_token": "new_at", "refresh_token": "new_rt"}) is True
        updated = store.get_session(sid)
        assert updated["access_token"] == "new_at"
        assert updated["refresh_token"] == "new_rt"

    def test_generate_and_validate_session_token(self):
        store = SSOSessionStore(session_ttl=3600)
        sid = store.create_session(tokens={"access_token": "at"})
        token = store.generate_session_token(sid, "secret-123")

        assert SSOSessionStore.validate_session_token(token, "secret-123") == sid
        assert SSOSessionStore.validate_session_token("not.a.jwt", "secret-123") is None

    def test_extract_user_id_precedence(self):
        user_id = extract_user_id_from_sso_session(
            {"email": "alice@example.com", "sub": "sub-1"},
            {"email": "claim@example.com", "preferred_username": "alice"},
        )
        assert user_id == "alice@example.com"


class TestAdminAuthorizerHelpers:
    @pytest.mark.asyncio
    async def test_deny_all_authorizer_rejects_everyone(self):
        authz = DenyAllAdminAuthorizer()
        assert await authz.is_admin(AdminIdentity(user_id="alice", groups=["admins"])) is False

    @pytest.mark.asyncio
    async def test_sso_group_authorizer_happy_path(self):
        authz = SSOGroupAdminAuthorizer(required_groups=["bedrock-admins", "platform-admins"])
        identity = AdminIdentity(user_id="alice@example.com", groups=["everyone", "bedrock-admins"])
        assert await authz.is_admin(identity) is True

    def test_resolve_identity_prefers_email_and_groups(self):
        session = {
            "user_info": {"email": "alice@example.com", "groups": ["everyone", "bedrock-admins"], "sub": "abc"},
            "id_token_claims": {"sub": "abc"},
        }
        identity = resolve_admin_identity_from_sso_session(session)
        assert identity is not None
        assert identity.user_id == "alice@example.com"
        assert identity.email == "alice@example.com"
        assert "bedrock-admins" in identity.groups

    def test_build_admin_authorizer_picks_remote_when_endpoint_set(self):
        config = MagicMock()
        config.admin_verification_endpoint = "https://host/admin/check"
        config.admin_required_groups = []
        authz = build_admin_authorizer(config)
        assert isinstance(authz, RemoteAdminAuthorizer)
        assert authz.endpoint_url == "https://host/admin/check"
