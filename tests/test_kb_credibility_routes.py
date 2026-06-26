"""Tests for the credibility-related admin KB routes (XMGPLAT-10933).

Covers:
- GET /admin/kb/documents?removal_flagged=true/false filter
- POST /admin/kb/documents/reset-credibility/{id} happy path
- POST /admin/kb/documents/reset-credibility/{id} returns 404 for unknown id
"""

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._autolangchat_imports import load_module

exceptions_mod = load_module("autolangchat.exceptions", "exceptions.py")
models_mod = load_module("autolangchat.models", "models.py")
admin_errors_mod = load_module(
    "autolangchat.admin.admin_errors",
    "admin/admin_errors.py",
    extra_modules={"autolangchat.exceptions": exceptions_mod, "autolangchat.models": models_mod},
)
kb_routes_mod = load_module(
    "autolangchat.admin.admin_kb_routes",
    "admin/admin_kb_routes.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.admin.admin_errors": admin_errors_mod,
    },
)

KBDocument = models_mod.KBDocument
KBDocumentNotFoundError = exceptions_mod.KBDocumentNotFoundError
register_admin_error_handlers = admin_errors_mod.register_admin_error_handlers
register_admin_kb_routes = kb_routes_mod.register_admin_kb_routes


# ---------------------------------------------------------------------------
# Fake store
# ---------------------------------------------------------------------------


class _Identity(SimpleNamespace):
    user_id: str = "admin"


class _FakeKBStore:
    def __init__(self):
        self.documents: dict[str, KBDocument] = {}

    def _seed(self, doc_id, *, content="hello", source=None, credibility_score=1.0, removal_flagged=False):
        self.documents[doc_id] = KBDocument(
            id=doc_id,
            content=content,
            source=source,
            created_at=datetime.utcnow(),
            credibility_score=credibility_score,
            removal_flagged=removal_flagged,
        )

    def list_documents(self, filters, limit=50, offset=0):
        items = list(self.documents.values())
        if getattr(filters, "source", None):
            items = [d for d in items if d.source == filters.source]
        if getattr(filters, "removal_flagged", None) is not None:
            items = [d for d in items if d.removal_flagged == filters.removal_flagged]
        return items[offset : offset + limit]

    def count_documents(self, filters):
        return len(self.list_documents(filters, limit=10_000, offset=0))

    def get_document(self, doc_id):
        doc = self.documents.get(doc_id)
        return doc.model_dump() if doc else None

    def update_document(self, doc_id, **kwargs):
        doc = self.documents[doc_id]
        data = doc.model_dump()
        data.update({k: v for k, v in kwargs.items() if v is not None})
        self.documents[doc_id] = KBDocument(**data)
        return self.documents[doc_id]

    def delete_document(self, doc_id):
        self.documents.pop(doc_id, None)

    def reset_credibility(self, doc_id):
        if doc_id not in self.documents:
            raise KBDocumentNotFoundError(f"kb document {doc_id} not found")
        doc = self.documents[doc_id]
        data = doc.model_dump()
        data["credibility_score"] = 1.0
        data["removal_flagged"] = False
        self.documents[doc_id] = KBDocument(**data)
        return self.documents[doc_id]

    def add_chunk(self, *a, **kw):
        pass


def _build_app(store):
    app = FastAPI()
    register_admin_error_handlers(app)

    async def require_admin():
        return _Identity(user_id="admin")

    async def re_embed(doc_id, content):
        return 1

    register_admin_kb_routes(
        app,
        prefix="/admin",
        kb_store=store,
        require_admin=require_admin,
        re_embed_document=re_embed,
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /admin/kb/documents?removal_flagged=...
# ---------------------------------------------------------------------------


def test_list_filter_removal_flagged_true():
    store = _FakeKBStore()
    store._seed("d1", removal_flagged=True)
    store._seed("d2", removal_flagged=False)
    store._seed("d3", removal_flagged=True)
    client = _build_app(store)
    resp = client.get("/admin/kb/documents", params={"removal_flagged": "true"})
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {"d1", "d3"}


def test_list_filter_removal_flagged_false():
    store = _FakeKBStore()
    store._seed("d1", removal_flagged=True)
    store._seed("d2", removal_flagged=False)
    client = _build_app(store)
    resp = client.get("/admin/kb/documents", params={"removal_flagged": "false"})
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {"d2"}


def test_list_no_removal_flagged_filter_returns_all():
    store = _FakeKBStore()
    store._seed("d1", removal_flagged=True)
    store._seed("d2", removal_flagged=False)
    client = _build_app(store)
    resp = client.get("/admin/kb/documents")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


# ---------------------------------------------------------------------------
# POST /admin/kb/documents/reset-credibility/{id}
# ---------------------------------------------------------------------------


def test_reset_credibility_returns_200_with_updated_doc():
    store = _FakeKBStore()
    store._seed("d1", credibility_score=0.2, removal_flagged=True)
    client = _build_app(store)
    resp = client.post("/admin/kb/documents/reset-credibility/d1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["credibility_score"] == 1.0
    assert body["removal_flagged"] is False
    assert body["id"] == "d1"


def test_reset_credibility_404_for_unknown_document():
    store = _FakeKBStore()
    client = _build_app(store)
    resp = client.post(f"/admin/kb/documents/reset-credibility/{uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_reset_credibility_unflagged_doc_stays_at_1():
    """Resetting an already-healthy document is a no-op (idempotent)."""
    store = _FakeKBStore()
    store._seed("d1", credibility_score=1.0, removal_flagged=False)
    client = _build_app(store)
    resp = client.post("/admin/kb/documents/reset-credibility/d1")
    assert resp.status_code == 200
    assert resp.json()["credibility_score"] == 1.0
    assert resp.json()["removal_flagged"] is False
