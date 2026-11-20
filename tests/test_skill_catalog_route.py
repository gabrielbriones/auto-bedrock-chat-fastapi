"""Tests for ``GET {chat_endpoint}/skills/catalog``."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autolangchat.db import SQLiteSkillStore
from autolangchat.skill_catalog_routes import register_skill_catalog_route
from autolangchat.skills import Skill, SkillParameter

URL = "/chat/skills/catalog"
SECRET = "TOP-SECRET-INSTRUCTIONS"


@pytest.fixture
async def store(tmp_path):
    store = SQLiteSkillStore(str(tmp_path / "skills.db"))
    await store.open()
    await store.create_skill(
        Skill(
            name="visible",
            display_name="Visible",
            description="desc",
            instructions=SECRET,
            parameters=[SkillParameter(name="job", label="Job", input_type="select", options=["a", "b"])],
        )
    )
    await store.create_skill(Skill(name="hidden", display_name="Hidden", instructions=SECRET, enabled=False))
    yield store
    await store.close()


def _make_bare_plugin(*, require_tool_auth, auth_verification_endpoint=None):
    from autolangchat.plugin import AutoLangChatPlugin

    plugin = object.__new__(AutoLangChatPlugin)
    plugin.app_base_url = "http://localhost:8000"
    plugin.sso_session_store = None
    plugin.config = SimpleNamespace(
        require_tool_auth=require_tool_auth,
        sso_enabled=False,
        sso_session_secret=None,
        auth_verification_endpoint=auth_verification_endpoint,
    )
    return plugin


def _make_client(store_ref, plugin):
    app = FastAPI()
    register_skill_catalog_route(
        app,
        prefix="/chat",
        get_skill_store=lambda: store_ref(),
        require_catalog_user=plugin._build_require_skill_catalog_user(),
    )
    return TestClient(app)


async def test_catalog_lists_enabled_skills_without_instructions(store):
    client = _make_client(lambda: store, _make_bare_plugin(require_tool_auth=False))

    resp = client.get(URL)

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [item["name"] for item in items] == ["visible"]
    assert set(items[0]) == {"name", "display_name", "description", "parameters"}
    assert items[0]["parameters"][0]["options"] == ["a", "b"]
    assert SECRET not in resp.text


async def test_catalog_follows_enable_and_disable(store):
    client = _make_client(lambda: store, _make_bare_plugin(require_tool_auth=False))

    await store.set_enabled("hidden", True)
    await store.set_enabled("visible", False)

    assert [item["name"] for item in client.get(URL).json()["items"]] == ["hidden"]


async def test_catalog_needs_no_identity_when_tool_auth_is_off(store):
    client = _make_client(lambda: store, _make_bare_plugin(require_tool_auth=False))
    assert client.get(URL).status_code == 200


async def test_catalog_requires_identity_when_tool_auth_is_on(store):
    client = _make_client(lambda: store, _make_bare_plugin(require_tool_auth=True))
    resp = client.get(URL)
    assert resp.status_code == 401
    assert SECRET not in resp.text


async def test_catalog_accepts_a_resolved_identity_when_tool_auth_is_on(store, monkeypatch):
    import autolangchat.admin.admin_auth as admin_auth

    seen = {}

    async def fake_resolve(request, endpoint_url):
        seen["endpoint"] = endpoint_url
        if request.headers.get("authorization") == "Bearer good":
            return admin_auth.AdminIdentity(user_id="u1", claims={})
        return None

    monkeypatch.setattr(admin_auth, "resolve_admin_identity_from_auth_endpoint", fake_resolve)
    plugin = _make_bare_plugin(require_tool_auth=True, auth_verification_endpoint="/verify")
    client = _make_client(lambda: store, plugin)

    assert client.get(URL, headers={"Authorization": "Bearer good"}).status_code == 200
    assert client.get(URL, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert seen["endpoint"] == "http://localhost:8000/verify"


def test_catalog_is_503_when_store_unavailable():
    client = _make_client(lambda: None, _make_bare_plugin(require_tool_auth=False))
    resp = client.get(URL)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "skills_unavailable"
