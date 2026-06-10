"""Tests for the Admin KB Management routes."""

from __future__ import annotations

import logging
import os
import tempfile
from typing import List
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auto_bedrock_chat_fastapi.admin_auth import AdminIdentity
from auto_bedrock_chat_fastapi.admin_kb_routes import build_default_re_embed_callback, register_admin_kb_routes
from auto_bedrock_chat_fastapi.db.kb_sqlite import SQLiteKBStore
from auto_bedrock_chat_fastapi.plugin import BedrockChatPlugin
from auto_bedrock_chat_fastapi.sso_session_store import SSOSessionStore

_SESSION_SECRET = "admin-kb-test-secret-1234567890"
_CHAT_PREFIX = "/bedrock-chat"
_ADMIN_PREFIX = f"{_CHAT_PREFIX}/admin"
_KB_PREFIX = f"{_ADMIN_PREFIX}/kb/documents"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _AllowAuthorizer:
    async def is_admin(self, identity: AdminIdentity) -> bool:
        return True


def _embedding() -> List[float]:
    return np.random.rand(1536).astype(np.float32).tolist()


def _seed(store: SQLiteKBStore, doc_id: str, content: str = "hello world", **kw) -> None:
    store.add_document(doc_id=doc_id, content=content, **kw)
    store.add_chunk(
        chunk_id=f"{doc_id}-c0",
        document_id=doc_id,
        content=content,
        embedding=_embedding(),
        chunk_index=0,
    )


@pytest.fixture
def kb_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    s = SQLiteKBStore(db_path)
    try:
        yield s
    finally:
        s.close()
        os.unlink(db_path)


def _make_admin_config() -> MagicMock:
    config = MagicMock()
    config.chat_endpoint = _CHAT_PREFIX
    config.admin_enabled = True
    config.sso_enabled = True
    config.sso_session_secret = _SESSION_SECRET
    config.sso_session_ttl = 3600
    config.auth_verification_endpoint = None
    config.kb_embedding_model = "amazon.titan-embed-text-v1"
    return config


@pytest.fixture
def re_embed_calls():
    """Records (doc_id, content) tuples handed to the re-embed callback."""
    return []


@pytest.fixture
def re_embed_stub(kb_store, re_embed_calls):
    """A callback that re-embeds by inserting a single deterministic chunk."""

    async def _cb(doc_id: str, content: str) -> int:
        re_embed_calls.append((doc_id, content))
        kb_store.add_chunk(
            chunk_id=f"{doc_id}-re-c0",
            document_id=doc_id,
            content=content,
            embedding=_embedding(),
            chunk_index=0,
        )
        return 1

    return _cb


@pytest.fixture
def admin_app(kb_store, re_embed_stub):
    """Build a minimal plugin + FastAPI app with the T5 routes wired.

    We deliberately set ``plugin._kb_store = None`` so that
    ``_setup_admin_routes`` only builds the ``require_admin`` dependency
    and skips the production KB-route wiring. We then call
    ``register_admin_kb_routes`` ourselves with a test stub re-embed
    callback so PATCH content-change tests can observe the calls.
    """
    app = FastAPI()
    plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
    plugin.app = app
    plugin.config = _make_admin_config()
    plugin.sso_session_store = SSOSessionStore(session_ttl=3600)
    plugin._admin_authorizer = _AllowAuthorizer()
    plugin._feedback_store = None
    plugin._kb_store = None  # skip auto-wiring; we register manually below
    plugin.app_base_url = "https://app.example.com"
    plugin.bedrock_client = MagicMock()
    plugin._setup_admin_routes()

    register_admin_kb_routes(
        app,
        prefix=_ADMIN_PREFIX,
        kb_store=kb_store,
        require_admin=plugin._require_admin,
        re_embed_document=re_embed_stub,
    )
    return app, plugin.sso_session_store


def _login(sso_store: SSOSessionStore, email: str = "admin@example.com") -> str:
    sid = sso_store.create_session(
        tokens={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
        user_info={"email": email, "sub": "abc-123"},
        id_token_claims={"sub": "abc-123", "email": email},
    )
    return sso_store.generate_session_token(sid, _SESSION_SECRET)


def _client(app: FastAPI, sso_store: SSOSessionStore, email: str = "admin@example.com") -> TestClient:
    token = _login(sso_store, email=email)
    client = TestClient(app)
    client.cookies.set("sso_session_token", token)
    return client


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


class TestAuthGating:
    def test_list_401(self, admin_app):
        app, _ = admin_app
        assert TestClient(app).get(_KB_PREFIX).status_code == 401

    def test_get_401(self, admin_app):
        app, _ = admin_app
        assert TestClient(app).get(f"{_KB_PREFIX}/x").status_code == 401

    def test_patch_401(self, admin_app):
        app, _ = admin_app
        resp = TestClient(app).patch(f"{_KB_PREFIX}/x", json={"title": "y"})
        assert resp.status_code == 401

    def test_delete_401(self, admin_app):
        app, _ = admin_app
        assert TestClient(app).delete(f"{_KB_PREFIX}/x").status_code == 401


# ---------------------------------------------------------------------------
# GET /admin/kb/documents
# ---------------------------------------------------------------------------


class TestList:
    def test_empty(self, admin_app):
        app, sso = admin_app
        resp = _client(app, sso).get(_KB_PREFIX)
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}

    def test_lists_with_chunk_count(self, admin_app, kb_store):
        _seed(kb_store, "d1", source="blog")
        _seed(kb_store, "d2", source="docs")

        app, sso = admin_app
        resp = _client(app, sso).get(_KB_PREFIX)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        for item in body["items"]:
            assert item["chunk_count"] == 1

    def test_filter_by_source(self, admin_app, kb_store):
        _seed(kb_store, "d1", source="blog")
        _seed(kb_store, "d2", source="docs")
        app, sso = admin_app
        resp = _client(app, sso).get(_KB_PREFIX, params={"source": "blog"})
        ids = {i["id"] for i in resp.json()["items"]}
        assert ids == {"d1"}

    def test_filter_by_tags_csv(self, admin_app, kb_store):
        _seed(kb_store, "d1", metadata={"tags": ["a", "b"]})
        _seed(kb_store, "d2", metadata={"tags": ["c"]})
        _seed(kb_store, "d3", metadata={"tags": ["b"]})
        app, sso = admin_app
        resp = _client(app, sso).get(_KB_PREFIX, params={"tags": "b"})
        ids = {i["id"] for i in resp.json()["items"]}
        assert ids == {"d1", "d3"}

    def test_invalid_date_window_400(self, admin_app):
        app, sso = admin_app
        resp = _client(app, sso).get(
            _KB_PREFIX,
            params={"date_from": "2024-06-01T00:00:00Z", "date_to": "2024-01-01T00:00:00Z"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "invalid_filters"

    def test_limit_cap_enforced(self, admin_app):
        app, sso = admin_app
        resp = _client(app, sso).get(_KB_PREFIX, params={"limit": 9999})
        assert resp.status_code == 422  # FastAPI Query(le=200)

    def test_pagination(self, admin_app, kb_store):
        for i in range(5):
            kb_store.add_document(doc_id=f"d{i}", content=f"c{i}")
        app, sso = admin_app
        resp = _client(app, sso).get(_KB_PREFIX, params={"limit": 2, "offset": 2})
        body = resp.json()
        assert body["total"] == 5
        assert body["limit"] == 2
        assert body["offset"] == 2
        assert len(body["items"]) == 2


# ---------------------------------------------------------------------------
# GET /admin/kb/documents/{id}
# ---------------------------------------------------------------------------


class TestGetOne:
    def test_404_missing(self, admin_app):
        app, sso = admin_app
        resp = _client(app, sso).get(f"{_KB_PREFIX}/nope")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_returns_document(self, admin_app, kb_store):
        _seed(kb_store, "d1", title="t", source="blog", metadata={"tags": ["x"], "v": 1})
        app, sso = admin_app
        resp = _client(app, sso).get(f"{_KB_PREFIX}/d1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "d1"
        assert body["title"] == "t"
        assert body["source"] == "blog"
        assert body["tags"] == ["x"]
        assert body["metadata"] == {"tags": ["x"], "v": 1}
        # chunk_count is intentionally not populated on the single-doc path
        assert body["chunk_count"] is None

    def test_url_shaped_id_with_encoded_slashes(self, admin_app, kb_store):
        # Web-crawled docs use the source URL as the document id. The
        # client percent-encodes slashes (``%2F``) which uvicorn decodes
        # before routing, so the route must use the ``:path`` converter
        # to capture the full id. Regression test for the original
        # 404 bug.
        url_id = "https://fastapi.tiangolo.com/reference/templating/"
        _seed(kb_store, url_id, title="t")
        app, sso = admin_app
        from urllib.parse import quote

        resp = _client(app, sso).get(f"{_KB_PREFIX}/{quote(url_id, safe='')}")
        assert resp.status_code == 200
        assert resp.json()["id"] == url_id


# ---------------------------------------------------------------------------
# PATCH /admin/kb/documents/{id}
# ---------------------------------------------------------------------------


class TestPatch:
    def test_404_missing(self, admin_app):
        app, sso = admin_app
        resp = _client(app, sso).patch(f"{_KB_PREFIX}/nope", json={"title": "x"})
        assert resp.status_code == 404

    def test_metadata_only_skips_re_embed(self, admin_app, kb_store, re_embed_calls):
        _seed(kb_store, "d1", content="orig")
        app, sso = admin_app
        resp = _client(app, sso).patch(f"{_KB_PREFIX}/d1", json={"title": "new"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "new"
        assert re_embed_calls == []  # no content change
        # Original chunk untouched
        assert kb_store.get_stats()["chunks"] == 1

    def test_content_change_invokes_re_embed(self, admin_app, kb_store, re_embed_calls):
        _seed(kb_store, "d1", content="orig")
        app, sso = admin_app
        resp = _client(app, sso).patch(f"{_KB_PREFIX}/d1", json={"content": "brand new body"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "brand new body"
        assert body["chunk_count"] == 1
        assert re_embed_calls == [("d1", "brand new body")]
        # Old chunk replaced; new one inserted by the stub
        assert kb_store.get_stats()["chunks"] == 1

    def test_content_equal_does_not_re_embed(self, admin_app, kb_store, re_embed_calls):
        _seed(kb_store, "d1", content="same")
        app, sso = admin_app
        resp = _client(app, sso).patch(f"{_KB_PREFIX}/d1", json={"content": "same", "title": "renamed"})
        assert resp.status_code == 200
        assert re_embed_calls == []
        assert kb_store.get_stats()["chunks"] == 1  # original chunk preserved

    def test_tags_update(self, admin_app, kb_store):
        _seed(kb_store, "d1", metadata={"v": 1})
        app, sso = admin_app
        resp = _client(app, sso).patch(f"{_KB_PREFIX}/d1", json={"tags": ["alpha", "beta"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["tags"] == ["alpha", "beta"]
        assert body["metadata"]["tags"] == ["alpha", "beta"]

    def test_blank_only_tags_collapse_to_cleared(self, admin_app, kb_store):
        # PR review feedback #5: the ``tags`` validator advertises
        # "collapse all-blank list to []". A list of just whitespace
        # must reach the store as ``[]`` (i.e. clear the tags), not
        # as ``["", ""]`` that depends on downstream normalization.
        _seed(kb_store, "d1", metadata={"tags": ["keep", "me"]})
        app, sso = admin_app
        resp = _client(app, sso).patch(f"{_KB_PREFIX}/d1", json={"tags": ["", "   "]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["tags"] == []
        assert body["metadata"].get("tags", []) == []

    def test_explicit_empty_tags_clears(self, admin_app, kb_store):
        _seed(kb_store, "d1", metadata={"tags": ["keep", "me"]})
        app, sso = admin_app
        resp = _client(app, sso).patch(f"{_KB_PREFIX}/d1", json={"tags": []})
        assert resp.status_code == 200
        body = resp.json()
        assert body["tags"] == []

    def test_unknown_field_rejected_422(self, admin_app, kb_store):
        _seed(kb_store, "d1")
        app, sso = admin_app
        resp = _client(app, sso).patch(f"{_KB_PREFIX}/d1", json={"id": "hacked"})
        assert resp.status_code == 422

    def test_audit_log_emitted(self, admin_app, kb_store, caplog):
        _seed(kb_store, "d1", content="orig", title="t-old")
        app, sso = admin_app

        caplog.set_level(logging.INFO, logger="bedrock.audit")
        resp = _client(app, sso).patch(f"{_KB_PREFIX}/d1", json={"content": "new content", "title": "t-new"})
        assert resp.status_code == 200

        audit_records = [
            r
            for r in caplog.records
            if r.name == "bedrock.audit" and getattr(r, "action", None) == "kb.document.update"
        ]
        assert len(audit_records) == 1
        rec = audit_records[0]
        assert rec.target_id == "d1"
        assert rec.actor_user_id == "admin@example.com"
        assert rec.content_changed is True
        assert rec.before["title"] == "t-old"
        assert rec.after["title"] == "t-new"
        # Hashes must be present and differ
        assert rec.before["content_hash"].startswith("sha256:")
        assert rec.after["content_hash"].startswith("sha256:")
        assert rec.before["content_hash"] != rec.after["content_hash"]

    def test_re_embed_failure_leaves_doc_with_zero_chunks(self, admin_app, kb_store, monkeypatch):
        """If re-embedding throws, the document is left empty (logged)."""
        _seed(kb_store, "d1", content="orig")

        # Replace the route's callback with a raising one by re-registering.
        app2 = FastAPI()
        plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
        plugin.app = app2
        plugin.config = _make_admin_config()
        plugin.sso_session_store = SSOSessionStore(session_ttl=3600)
        plugin._admin_authorizer = _AllowAuthorizer()
        plugin._feedback_store = None
        plugin._kb_store = None
        plugin.app_base_url = "https://app.example.com"
        plugin.bedrock_client = MagicMock()
        plugin._setup_admin_routes()

        async def _raises(doc_id, content):
            raise RuntimeError("embed boom")

        register_admin_kb_routes(
            app2,
            prefix=_ADMIN_PREFIX,
            kb_store=kb_store,
            require_admin=plugin._require_admin,
            re_embed_document=_raises,
        )

        resp = _client(app2, plugin.sso_session_store).patch(f"{_KB_PREFIX}/d1", json={"content": "new"})
        # Update still succeeds — embedding is best-effort.
        assert resp.status_code == 200
        assert resp.json()["chunk_count"] == 0
        # Old chunk was cleared by the store; new one was never written.
        assert kb_store.get_stats()["chunks"] == 0


# ---------------------------------------------------------------------------
# DELETE /admin/kb/documents/{id}
# ---------------------------------------------------------------------------


class TestDelete:
    def test_404_missing(self, admin_app):
        app, sso = admin_app
        resp = _client(app, sso).delete(f"{_KB_PREFIX}/nope")
        assert resp.status_code == 404

    def test_deletes_doc_and_chunks(self, admin_app, kb_store):
        _seed(kb_store, "d1")
        assert kb_store.get_stats()["documents"] == 1
        assert kb_store.get_stats()["chunks"] == 1

        app, sso = admin_app
        resp = _client(app, sso).delete(f"{_KB_PREFIX}/d1")
        assert resp.status_code == 204
        assert kb_store.get_stats()["documents"] == 0
        assert kb_store.get_stats()["chunks"] == 0

    def test_audit_log_emitted(self, admin_app, kb_store, caplog):
        _seed(kb_store, "d1", content="orig", title="t1")
        app, sso = admin_app

        caplog.set_level(logging.INFO, logger="bedrock.audit")
        resp = _client(app, sso).delete(f"{_KB_PREFIX}/d1")
        assert resp.status_code == 204

        audit_records = [
            r
            for r in caplog.records
            if r.name == "bedrock.audit" and getattr(r, "action", None) == "kb.document.delete"
        ]
        assert len(audit_records) == 1
        rec = audit_records[0]
        assert rec.target_id == "d1"
        assert rec.actor_user_id == "admin@example.com"
        assert rec.before["title"] == "t1"
        assert rec.before["content_hash"].startswith("sha256:")


# ---------------------------------------------------------------------------
# build_default_re_embed_callback (factory contract)
# ---------------------------------------------------------------------------


class TestDefaultReEmbedCallback:
    @pytest.mark.asyncio
    async def test_calls_bedrock_and_writes_chunks(self, kb_store):
        kb_store.add_document(
            doc_id="d1",
            content="paragraph one with content. " * 50,
            title="t",
            source="blog",
        )

        bedrock_client = MagicMock()
        bedrock_client.generate_embeddings_batch = AsyncMock(return_value=[_embedding() for _ in range(10)])

        cb = build_default_re_embed_callback(
            kb_store=kb_store,
            bedrock_client=bedrock_client,
            embedding_model="amazon.titan-embed-text-v1",
        )

        n = await cb("d1", "paragraph one with content. " * 50)
        assert n >= 1
        bedrock_client.generate_embeddings_batch.assert_awaited_once()
        # The number of texts handed to the embedder must match the number
        # of chunks written.
        called_texts = bedrock_client.generate_embeddings_batch.await_args.kwargs["texts"]
        assert len(called_texts) == n
        assert kb_store.get_stats()["chunks"] == n

    @pytest.mark.asyncio
    async def test_missing_doc_returns_zero(self, kb_store):
        bedrock_client = MagicMock()
        bedrock_client.generate_embeddings_batch = AsyncMock(return_value=[])
        cb = build_default_re_embed_callback(
            kb_store=kb_store,
            bedrock_client=bedrock_client,
            embedding_model="amazon.titan-embed-text-v1",
        )
        n = await cb("missing", "anything")
        assert n == 0
        bedrock_client.generate_embeddings_batch.assert_not_awaited()


# ---------------------------------------------------------------------------
# Wiring: plugin registers kb routes when kb_store is present
# ---------------------------------------------------------------------------


class TestPluginWiring:
    def test_routes_skipped_when_kb_store_missing(self, kb_store):
        app = FastAPI()
        plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
        plugin.app = app
        plugin.config = _make_admin_config()
        plugin.sso_session_store = SSOSessionStore(session_ttl=3600)
        plugin._admin_authorizer = _AllowAuthorizer()
        plugin._feedback_store = None
        plugin._kb_store = None
        plugin.app_base_url = "https://app.example.com"
        plugin.bedrock_client = MagicMock()
        plugin._setup_admin_routes()

        # None of the KB routes should be registered.
        paths = {r.path for r in app.routes}
        assert not any(p.startswith(_KB_PREFIX) for p in paths)

    def test_routes_registered_when_kb_store_present(self, kb_store):
        app = FastAPI()
        plugin = BedrockChatPlugin.__new__(BedrockChatPlugin)
        plugin.app = app
        plugin.config = _make_admin_config()
        plugin.sso_session_store = SSOSessionStore(session_ttl=3600)
        plugin._admin_authorizer = _AllowAuthorizer()
        plugin._feedback_store = None
        plugin._kb_store = kb_store
        plugin.app_base_url = "https://app.example.com"
        plugin.bedrock_client = MagicMock()
        plugin._setup_admin_routes()

        paths = {r.path for r in app.routes}
        assert f"{_KB_PREFIX}" in paths
        # ``:path`` converter is required so doc_ids containing
        # slashes (e.g. URL-shaped IDs from web-crawled docs) match.
        assert f"{_KB_PREFIX}/{{doc_id:path}}" in paths
