"""Tests for the React SPA mount (CONTRACT-002 BC-002, Mechanism A).

Builds a throwaway Vite-shaped ``dist/`` on disk rather than depending on a
real ``npm run build`` so the serving contract is checked in isolation:
fallback routing, cache headers, no shadowing of pre-existing routes, and the
same-origin cookie + WebSocket behaviour a page loaded at ``ui_endpoint``
(``/bedrock-chat/ui`` by default) relies on.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from autolangchat.config import ChatConfig
from autolangchat.spa import IMMUTABLE_CACHE, NO_STORE, dashboard_path, default_dist_dir, mount_spa, resolve_dist_dir

INDEX_HTML = (
    '<!doctype html><html><head><script type="module" src="/bedrock-chat/ui/assets/index-abc123.js"></script></head></html>'
)


@pytest.fixture
def dist_dir(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML)
    (dist / "assets" / "index-abc123.js").write_text("console.log('spa')")
    (dist / "favicon.svg").write_text("<svg/>")
    return dist


def _config(**overrides):
    return ChatConfig().model_copy(update=overrides)


def _app_with_spa(dist_dir, **config_overrides):
    app = FastAPI()

    @app.get("/bedrock-chat/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/jobs")
    async def jobs():
        return {"jobs": []}

    @app.websocket("/bedrock-chat/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"cookie": websocket.cookies.get("sso_session_token")})
        await websocket.close()

    served = mount_spa(app, _config(ui_dist_dir=str(dist_dir), **config_overrides))
    return app, served


def test_resolve_dist_dir_defaults_to_repo_frontend_dist():
    assert resolve_dist_dir(_config()) == default_dist_dir()
    assert default_dist_dir().parts[-2:] == ("frontend", "dist")


def test_resolve_dist_dir_honours_override(tmp_path):
    assert resolve_dist_dir(_config(ui_dist_dir=str(tmp_path))) == tmp_path


def test_dashboard_path_never_uses_admin_segment():
    assert dashboard_path("/bedrock-chat/ui") == "/bedrock-chat/dashboard"
    assert dashboard_path("/custom/ui/") == "/custom/dashboard"
    assert dashboard_path("/app/") == "/app/dashboard"


def test_ui_root_serves_index_without_redirect(dist_dir):
    app, served = _app_with_spa(dist_dir)
    assert served == dist_dir
    client = TestClient(app)

    resp = client.get("/bedrock-chat/ui", follow_redirects=False)

    assert resp.status_code == 200
    assert resp.text == INDEX_HTML
    assert resp.headers["cache-control"] == NO_STORE
    assert client.get("/bedrock-chat/ui/", follow_redirects=False).status_code == 200
    assert client.get("/bedrock-chat/dashboard", follow_redirects=False).text == INDEX_HTML


def test_deep_link_falls_back_to_index(dist_dir):
    app, _ = _app_with_spa(dist_dir)
    client = TestClient(app)

    resp = client.get("/bedrock-chat/dashboard/kb-browser?flagged=true")

    assert resp.status_code == 200
    assert resp.text == INDEX_HTML
    assert resp.headers["cache-control"] == NO_STORE
    assert client.get("/bedrock-chat/ui/c/abc123").text == INDEX_HTML


def test_hashed_assets_are_immutable_cached(dist_dir):
    app, _ = _app_with_spa(dist_dir)
    client = TestClient(app)

    resp = client.get("/bedrock-chat/ui/assets/index-abc123.js")

    assert resp.status_code == 200
    assert resp.text == "console.log('spa')"
    assert resp.headers["cache-control"] == IMMUTABLE_CACHE


def test_non_hashed_public_files_are_not_immutable(dist_dir):
    app, _ = _app_with_spa(dist_dir)
    client = TestClient(app)

    resp = client.get("/bedrock-chat/ui/favicon.svg")

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == NO_STORE


def test_missing_file_like_path_is_404_not_index(dist_dir):
    # A broken asset URL must fail loudly rather than hand HTML to a <script type="module">.
    app, _ = _app_with_spa(dist_dir)
    client = TestClient(app)

    assert client.get("/bedrock-chat/ui/assets/index-stale.js").status_code == 404
    assert client.get("/bedrock-chat/ui/robots.txt").status_code == 404
    assert client.get("/bedrock-chat/dashboard/missing.js").status_code == 404


def test_pre_existing_routes_are_not_shadowed(dist_dir):
    app, _ = _app_with_spa(dist_dir)
    client = TestClient(app)

    assert client.get("/bedrock-chat/health").json() == {"status": "ok"}
    assert client.get("/api/v1/jobs").json() == {"jobs": []}
    # Nothing outside the mount prefix falls back to the SPA.
    assert client.get("/nope").status_code == 404
    assert client.get("/bedrock-chat/uix").status_code == 404


def test_route_table_only_gains_spa_entries(dist_dir):
    app = FastAPI()

    @app.get("/bedrock-chat/health")
    async def health():
        return {"status": "ok"}

    before = [(type(r).__name__, r.path) for r in app.router.routes]
    mount_spa(app, _config(ui_dist_dir=str(dist_dir)))
    after = [(type(r).__name__, r.path) for r in app.router.routes]

    assert after[: len(before)] == before
    assert [p for _, p in after[len(before) :]] == [
        "/bedrock-chat/ui", "/bedrock-chat/ui", "/bedrock-chat/dashboard", "/bedrock-chat/dashboard"
    ]
    assert [t for t, _ in after[len(before) :]] == ["APIRoute", "Mount", "APIRoute", "Mount"]


def test_warns_when_existing_route_lives_under_spa_prefix(dist_dir, caplog):
    app = FastAPI()

    @app.get("/bedrock-chat/ui/legacy")
    async def legacy():
        return {"legacy": True}

    with caplog.at_level(logging.WARNING, logger="autolangchat.spa"):
        mount_spa(app, _config(ui_dist_dir=str(dist_dir)))

    assert "/bedrock-chat/ui/legacy" in caplog.text
    # Registration order wins: the pre-existing route keeps resolving.
    assert TestClient(app).get("/bedrock-chat/ui/legacy").json() == {"legacy": True}


def test_websocket_upgrade_from_ui_page_carries_cookie(dist_dir):
    app, _ = _app_with_spa(dist_dir)
    client = TestClient(app)
    client.cookies.set("sso_session_token", "valid:sess-1")

    assert client.get("/bedrock-chat/ui").status_code == 200
    with client.websocket_connect("/bedrock-chat/ws") as websocket:
        assert websocket.receive_json() == {"cookie": "valid:sess-1"}


def test_custom_ui_endpoint(dist_dir):
    app, _ = _app_with_spa(dist_dir, ui_endpoint="/app/")
    client = TestClient(app)

    assert client.get("/app", follow_redirects=False).status_code == 200
    assert client.get("/app/admin").text == INDEX_HTML
    assert client.get("/app/dashboard").text == INDEX_HTML
    assert client.get("/bedrock-chat/ui").status_code == 404


def test_missing_build_registers_503_placeholder(tmp_path, caplog):
    app = FastAPI()
    with caplog.at_level(logging.WARNING, logger="autolangchat.spa"):
        served = mount_spa(app, _config(ui_dist_dir=str(tmp_path / "nowhere")))
    client = TestClient(app)

    assert served is None
    assert "SPA build not found" in caplog.text
    for path in ("/bedrock-chat/ui", "/bedrock-chat/dashboard/kb-browser"):
        resp = client.get(path)
        assert resp.status_code == 503
        assert "npm run build" in resp.text
        assert resp.headers["cache-control"] == NO_STORE
