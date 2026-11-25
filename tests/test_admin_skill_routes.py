"""Tests for the ``/admin/skills*`` routes."""

import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autolangchat.admin.admin_errors import register_admin_error_handlers
from autolangchat.admin.admin_skill_routes import register_admin_skill_routes
from autolangchat.db import SQLiteSkillStore

PREFIX = "/chat/admin"
SECRET_INSTRUCTIONS = "TOP-SECRET-INSTRUCTIONS {{X}}"


def _body(**overrides):
    body = {
        "name": "triage",
        "display_name": "Triage",
        "description": "Triage a job",
        "instructions": SECRET_INSTRUCTIONS,
        "parameters": [{"name": "x", "input_type": "select", "options": ["a", "b"], "required": True}],
    }
    body.update(overrides)
    return body


def _limits(**overrides):
    values = dict(skill_instructions_max_chars=200, skill_description_max_chars=50, skill_max_parameters=2)
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def store(tmp_path):
    return SQLiteSkillStore(str(tmp_path / "skills.db"))


def _make_client(store_ref, config=None, on_change=None):
    app = FastAPI()
    register_admin_error_handlers(app)

    async def require_admin():
        return SimpleNamespace(user_id="admin-1")

    register_admin_skill_routes(
        app,
        prefix=PREFIX,
        get_skill_store=lambda: store_ref(),
        require_admin=require_admin,
        chat_config=config or _limits(),
        on_change=on_change,
    )
    return TestClient(app)


@pytest.fixture
def client(store):
    return _make_client(lambda: store)


def test_create_get_and_list(client):
    created = client.post(f"{PREFIX}/skills", json=_body())
    assert created.status_code == 201
    assert created.json()["name"] == "triage"
    assert created.json()["updated_by"] == "admin-1"
    assert created.json()["enabled"] is True
    assert created.json()["revision"] == 1

    detail = client.get(f"{PREFIX}/skills/triage")
    assert detail.status_code == 200
    assert detail.json()["instructions"] == SECRET_INSTRUCTIONS
    assert detail.json()["revision"] == 1

    listing = client.get(f"{PREFIX}/skills").json()
    assert [item["name"] for item in listing["items"]] == ["triage"]
    assert "instructions" not in listing["items"][0]
    assert listing["items"][0]["revision"] == 1


def test_list_exposes_the_configured_limits(client):
    assert client.get(f"{PREFIX}/skills").json()["limits"] == {
        "skill_instructions_max_chars": 200,
        "skill_description_max_chars": 50,
        "skill_max_parameters": 2,
    }


def test_create_duplicate_name_is_409(client):
    assert client.post(f"{PREFIX}/skills", json=_body()).status_code == 201
    resp = client.post(f"{PREFIX}/skills", json=_body())
    assert resp.status_code == 409
    assert resp.json()["code"] == "skill_exists"


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Bad Name"},
        {"name": "triage\n"},
        {"name": "x" * 65},
        {"instructions": "   "},
        {"parameters": [{"name": "x"}, {"name": "x"}]},
        {"unexpected": 1},
    ],
)
def test_create_rejects_invalid_body(client, overrides):
    assert client.post(f"{PREFIX}/skills", json=_body(**overrides)).status_code == 422


@pytest.mark.parametrize(
    "overrides",
    [
        {"instructions": "x" * 201},
        {"description": "d" * 51},
        {"parameters": [{"name": "a"}, {"name": "b"}, {"name": "c"}]},
    ],
)
def test_create_enforces_configured_limits(client, overrides):
    resp = client.post(f"{PREFIX}/skills", json=_body(**overrides))
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_skill"
    assert client.get(f"{PREFIX}/skills/triage").status_code == 404


def test_put_replaces_content_and_keeps_enabled(client):
    client.post(f"{PREFIX}/skills", json=_body())
    client.post(f"{PREFIX}/skills/triage/disable")

    body = {"display_name": "New", "instructions": "new body", "parameters": []}
    resp = client.put(f"{PREFIX}/skills/triage", json=body)

    assert resp.status_code == 200
    data = resp.json()
    assert (data["display_name"], data["instructions"], data["description"], data["parameters"]) == (
        "New",
        "new body",
        "",
        [],
    )
    assert data["enabled"] is False


def test_put_enforces_limits_and_requires_existing_skill(client):
    body = {"display_name": "N", "instructions": "i"}
    assert client.put(f"{PREFIX}/skills/missing", json=body).status_code == 404

    client.post(f"{PREFIX}/skills", json=_body())
    too_long = {"display_name": "N", "instructions": "x" * 201}
    assert client.put(f"{PREFIX}/skills/triage", json=too_long).status_code == 400
    assert client.get(f"{PREFIX}/skills/triage").json()["instructions"] == SECRET_INSTRUCTIONS


def test_put_cannot_rename(client):
    client.post(f"{PREFIX}/skills", json=_body())
    resp = client.put(f"{PREFIX}/skills/triage", json={"name": "other", "display_name": "N", "instructions": "i"})
    assert resp.status_code == 422


def test_enable_and_disable(client):
    client.post(f"{PREFIX}/skills", json=_body())
    assert client.post(f"{PREFIX}/skills/triage/disable").json()["enabled"] is False
    assert client.post(f"{PREFIX}/skills/triage/enable").json()["enabled"] is True


# ---------------------------------------------------------------------------
# Revisions, conflicts and rollback
# ---------------------------------------------------------------------------


def _assert_revision_conflict(resp, current_revision, instructions):
    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "revision_conflict"
    assert isinstance(data["detail"], str)
    assert data["current"]["revision"] == current_revision
    assert data["current"]["instructions"] == instructions


def test_put_increments_the_revision_and_checks_expected_revision(client):
    client.post(f"{PREFIX}/skills", json=_body())

    first = client.put(f"{PREFIX}/skills/triage", json={"display_name": "N", "instructions": "second"})
    assert first.json()["revision"] == 2
    matching = {"display_name": "N", "instructions": "third", "expected_revision": 2}
    assert client.put(f"{PREFIX}/skills/triage", json=matching).json()["revision"] == 3

    stale = client.put(f"{PREFIX}/skills/triage", json={**matching, "instructions": "lost"})

    _assert_revision_conflict(stale, 3, "third")
    assert client.get(f"{PREFIX}/skills/triage").json()["instructions"] == "third"


def test_patch_changes_only_the_given_fields(client):
    client.post(f"{PREFIX}/skills", json=_body())
    client.post(f"{PREFIX}/skills/triage/disable")

    resp = client.patch(f"{PREFIX}/skills/triage", json={"expected_revision": 1, "description": "New desc"})

    assert resp.status_code == 200
    data = resp.json()
    assert (data["revision"], data["description"], data["enabled"]) == (2, "New desc", False)
    assert (data["display_name"], data["instructions"]) == ("Triage", SECRET_INSTRUCTIONS)
    assert data["parameters"][0]["options"] == ["a", "b"]


def test_patch_with_stale_revision_is_409_and_changes_nothing(client):
    client.post(f"{PREFIX}/skills", json=_body())
    client.patch(f"{PREFIX}/skills/triage", json={"expected_revision": 1, "instructions": "second"})

    resp = client.patch(f"{PREFIX}/skills/triage", json={"expected_revision": 1, "instructions": "lost"})

    _assert_revision_conflict(resp, 2, "second")
    assert client.get(f"{PREFIX}/skills/triage").json()["revision"] == 2


def test_patch_conflicts_when_a_write_lands_between_read_and_write(store):
    client = _make_client(lambda: store)
    client.post(f"{PREFIX}/skills", json=_body())
    real_replace = store.replace_skill

    async def racing_replace(skill, updated_by=None, expected_revision=None):
        await real_replace(skill.model_copy(update={"instructions": "concurrent"}), updated_by="other")
        return await real_replace(skill, updated_by=updated_by, expected_revision=expected_revision)

    store.replace_skill = racing_replace
    resp = client.patch(f"{PREFIX}/skills/triage", json={"expected_revision": 1, "instructions": "mine"})

    _assert_revision_conflict(resp, 2, "concurrent")


@pytest.mark.parametrize(
    "body",
    [
        {"description": "no expected revision"},
        {"expected_revision": 1},
        {"expected_revision": 1, "instructions": "  "},
        {"expected_revision": 1, "enabled": False},
    ],
)
def test_patch_rejects_invalid_body(client, body):
    client.post(f"{PREFIX}/skills", json=_body())
    assert client.patch(f"{PREFIX}/skills/triage", json=body).status_code == 422


def test_patch_enforces_limits_and_requires_existing_skill(client):
    assert (
        client.patch(f"{PREFIX}/skills/missing", json={"expected_revision": 1, "description": "d"}).status_code == 404
    )

    client.post(f"{PREFIX}/skills", json=_body())
    resp = client.patch(f"{PREFIX}/skills/triage", json={"expected_revision": 1, "description": "d" * 51})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_skill"
    assert client.get(f"{PREFIX}/skills/triage").json()["revision"] == 1


def test_list_and_get_revisions(client):
    client.post(f"{PREFIX}/skills", json=_body())
    client.patch(f"{PREFIX}/skills/triage", json={"expected_revision": 1, "instructions": "second"})

    listing = client.get(f"{PREFIX}/skills/triage/revisions")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert [(item["revision"], item["updated_by"]) for item in items] == [(2, "admin-1"), (1, "admin-1")]
    assert set(items[0]) == {"revision", "updated_at", "updated_by"}
    assert SECRET_INSTRUCTIONS not in listing.text

    first = client.get(f"{PREFIX}/skills/triage/revisions/1")
    assert first.status_code == 200
    assert (first.json()["revision"], first.json()["instructions"]) == (1, SECRET_INSTRUCTIONS)


@pytest.mark.parametrize(
    "path", ["/skills/missing/revisions", "/skills/missing/revisions/1", "/skills/triage/revisions/9"]
)
def test_missing_skill_or_revision_is_404(client, path):
    client.post(f"{PREFIX}/skills", json=_body())
    resp = client.get(f"{PREFIX}{path}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_rollback_restores_old_content_as_a_new_revision(client):
    client.post(f"{PREFIX}/skills", json=_body())
    client.put(f"{PREFIX}/skills/triage", json={"display_name": "N", "instructions": "second"})

    resp = client.post(f"{PREFIX}/skills/triage/rollback", json={"revision": 1, "expected_revision": 2})

    assert resp.status_code == 200
    data = resp.json()
    assert (data["revision"], data["display_name"], data["instructions"]) == (3, "Triage", SECRET_INSTRUCTIONS)
    revisions = client.get(f"{PREFIX}/skills/triage/revisions").json()["items"]
    assert [item["revision"] for item in revisions] == [3, 2, 1]
    assert client.get(f"{PREFIX}/skills/triage/revisions/2").json()["instructions"] == "second"


def test_rollback_with_stale_revision_is_409(client):
    client.post(f"{PREFIX}/skills", json=_body())
    client.put(f"{PREFIX}/skills/triage", json={"display_name": "N", "instructions": "second"})

    resp = client.post(f"{PREFIX}/skills/triage/rollback", json={"revision": 1, "expected_revision": 1})

    _assert_revision_conflict(resp, 2, "second")


def test_rollback_to_missing_revision_or_skill_is_404(client):
    client.post(f"{PREFIX}/skills", json=_body())
    assert client.post(f"{PREFIX}/skills/triage/rollback", json={"revision": 5}).status_code == 404
    assert client.post(f"{PREFIX}/skills/missing/rollback", json={"revision": 1}).status_code == 404
    assert client.post(f"{PREFIX}/skills/triage/rollback", json={"revision": 0}).status_code == 422


def test_rollback_enforces_the_current_limits(store):
    _make_client(lambda: store).post(f"{PREFIX}/skills", json=_body(instructions="x" * 150))
    _make_client(lambda: store).put(f"{PREFIX}/skills/triage", json={"display_name": "N", "instructions": "short"})
    stricter = _make_client(lambda: store, config=_limits(skill_instructions_max_chars=100))

    resp = stricter.post(f"{PREFIX}/skills/triage/rollback", json={"revision": 1})

    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_skill"


def test_enable_and_disable_keep_the_revision(client):
    client.post(f"{PREFIX}/skills", json=_body())
    client.put(f"{PREFIX}/skills/triage", json={"display_name": "N", "instructions": "second"})
    assert client.post(f"{PREFIX}/skills/triage/disable").json()["revision"] == 2
    assert client.post(f"{PREFIX}/skills/triage/enable").json()["revision"] == 2


def test_delete(client):
    client.post(f"{PREFIX}/skills", json=_body())
    assert client.delete(f"{PREFIX}/skills/triage").status_code == 204
    assert client.get(f"{PREFIX}/skills/triage").status_code == 404
    assert client.delete(f"{PREFIX}/skills/triage").status_code == 404


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/skills/missing"),
        ("get", "/skills/Not A Valid Name"),
        ("get", "/skills/triage%0A"),
        ("post", "/skills/missing/enable"),
        ("post", "/skills/missing/disable"),
        ("delete", "/skills/missing"),
    ],
)
def test_unknown_skill_is_404_with_envelope(client, method, path):
    resp = getattr(client, method)(f"{PREFIX}{path}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_every_route_is_503_when_store_unavailable():
    client = _make_client(lambda: None)
    body = {"display_name": "N", "instructions": "i"}
    responses = [
        client.get(f"{PREFIX}/skills"),
        client.post(f"{PREFIX}/skills", json=_body()),
        client.get(f"{PREFIX}/skills/x"),
        client.put(f"{PREFIX}/skills/x", json=body),
        client.patch(f"{PREFIX}/skills/x", json={**body, "expected_revision": 1}),
        client.get(f"{PREFIX}/skills/x/revisions"),
        client.get(f"{PREFIX}/skills/x/revisions/1"),
        client.post(f"{PREFIX}/skills/x/rollback", json={"revision": 1}),
        client.delete(f"{PREFIX}/skills/x"),
        client.post(f"{PREFIX}/skills/x/enable"),
    ]
    assert [r.status_code for r in responses] == [503] * 10
    assert {r.json()["code"] for r in responses} == {"skills_unavailable"}


def test_writes_are_audited_with_revision_and_without_instructions(client, caplog):
    with caplog.at_level(logging.INFO, logger="bedrock.audit"):
        client.post(f"{PREFIX}/skills", json=_body())
        client.put(f"{PREFIX}/skills/triage", json={"display_name": "N", "instructions": SECRET_INSTRUCTIONS})
        client.patch(f"{PREFIX}/skills/triage", json={"expected_revision": 2, "instructions": SECRET_INSTRUCTIONS})
        client.post(f"{PREFIX}/skills/triage/rollback", json={"revision": 1})
        client.post(f"{PREFIX}/skills/triage/disable")
        client.post(f"{PREFIX}/skills/triage/enable")
        client.delete(f"{PREFIX}/skills/triage")
        client.get(f"{PREFIX}/skills")

    records = [r for r in caplog.records if r.name == "bedrock.audit"]
    assert [(r.action, r.revision) for r in records] == [
        ("skill.create", 1),
        ("skill.replace", 2),
        ("skill.patch", 3),
        ("skill.rollback", 4),
        ("skill.disable", 4),
        ("skill.enable", 4),
        ("skill.delete", 4),
    ]
    assert records[3].restored_revision == 1
    assert {r.actor_user_id for r in records} == {"admin-1"}
    assert {r.target_id for r in records} == {"triage"}
    assert not any(SECRET_INSTRUCTIONS in str(vars(r)) for r in records)


def test_failed_writes_are_not_audited(client, caplog):
    client.post(f"{PREFIX}/skills", json=_body())
    with caplog.at_level(logging.INFO, logger="bedrock.audit"):
        client.post(f"{PREFIX}/skills", json=_body(name="other", instructions="x" * 201))
        client.delete(f"{PREFIX}/skills/missing")
        client.patch(f"{PREFIX}/skills/triage", json={"expected_revision": 9, "description": "d"})
        client.post(f"{PREFIX}/skills/triage/rollback", json={"revision": 1, "expected_revision": 9})
    assert [r for r in caplog.records if r.name == "bedrock.audit"] == []


def test_on_change_runs_after_every_successful_write_only(store):
    changes = []
    client = _make_client(lambda: store, on_change=lambda: changes.append(1))

    client.post(f"{PREFIX}/skills", json=_body())
    client.put(f"{PREFIX}/skills/triage", json={"display_name": "N", "instructions": "i"})
    client.patch(f"{PREFIX}/skills/triage", json={"expected_revision": 2, "description": "d"})
    client.post(f"{PREFIX}/skills/triage/rollback", json={"revision": 1})
    client.post(f"{PREFIX}/skills/triage/disable")
    client.post(f"{PREFIX}/skills/triage/enable")
    client.delete(f"{PREFIX}/skills/triage")
    assert len(changes) == 7

    client.get(f"{PREFIX}/skills")
    client.post(f"{PREFIX}/skills", json=_body(instructions="x" * 201))
    client.delete(f"{PREFIX}/skills/missing")
    assert len(changes) == 7


# ---------------------------------------------------------------------------
# Plugin wiring: the real require_admin dependency
# ---------------------------------------------------------------------------


def _make_bare_plugin(skill_store, require_tool_auth):
    from autolangchat.admin.admin_auth import build_admin_authorizer
    from autolangchat.plugin import AutoLangChatPlugin

    config = SimpleNamespace(
        chat_endpoint="/chat",
        admin_enabled=True,
        admin_verification_endpoint=None,
        admin_authorized_users=[],
        admin_allow_anonymous=False,
        sso_enabled=False,
        sso_session_secret=None,
        auth_verification_endpoint=None,
        supported_auth_types=[],
        require_tool_auth=require_tool_auth,
        enable_ui=False,
        kb_embedding_model="m",
        feedback_synthesis_system_prompt=None,
        timeout=30.0,
        **vars(_limits()),
    )
    plugin = object.__new__(AutoLangChatPlugin)
    plugin.app = FastAPI()
    plugin.app_base_url = "http://localhost:8000"
    plugin.config = config
    plugin.sso_session_store = None
    plugin.embedding_client = None
    plugin._feedback_store = None
    plugin._kb_store = None
    plugin._admin_authorizer_override = None
    plugin._token_usage_store = None
    plugin._skill_store = skill_store
    plugin._admin_authorizer = build_admin_authorizer(config, app_base_url=plugin.app_base_url)
    plugin._setup_admin_routes()
    return plugin


def test_plugin_registers_admin_skill_routes_only_with_a_store(store):
    without = TestClient(_make_bare_plugin(None, require_tool_auth=False).app)
    assert without.get(f"{PREFIX}/skills").status_code == 404

    with_store = TestClient(_make_bare_plugin(store, require_tool_auth=False).app)
    assert with_store.get(f"{PREFIX}/skills").status_code == 200


def test_plugin_anonymous_admin_can_manage_skills_without_tool_auth(store):
    client = TestClient(_make_bare_plugin(store, require_tool_auth=False).app)
    resp = client.post(f"{PREFIX}/skills", json=_body())
    assert resp.status_code == 201
    assert resp.json()["updated_by"] == "anonymous"


def test_plugin_skill_routes_require_an_admin_identity_with_tool_auth(store):
    client = TestClient(_make_bare_plugin(store, require_tool_auth=True).app)
    assert client.get(f"{PREFIX}/skills").status_code == 401
    assert client.post(f"{PREFIX}/skills", json=_body()).status_code == 401


def test_plugin_routes_follow_the_store_being_disabled_after_registration(store):
    plugin = _make_bare_plugin(store, require_tool_auth=False)
    client = TestClient(plugin.app)
    plugin._skill_store = None  # e.g. startup() failed to open it
    resp = client.get(f"{PREFIX}/skills")
    assert resp.status_code == 503
    assert resp.json()["code"] == "skills_unavailable"
