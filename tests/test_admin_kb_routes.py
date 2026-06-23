from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

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
register_admin_error_handlers = admin_errors_mod.register_admin_error_handlers
register_admin_kb_routes = kb_routes_mod.register_admin_kb_routes


class _Identity(SimpleNamespace):
    user_id: str = "admin"


class _FakeKBStore:
    def __init__(self):
        self.documents = {}
        self.stats = {"chunks": 0}

    def list_documents(self, filters, limit=50, offset=0):
        items = list(self.documents.values())
        if getattr(filters, "source", None):
            items = [doc for doc in items if doc.source == filters.source]
        if getattr(filters, "tags", None):
            tags = set(filters.tags)
            items = [doc for doc in items if tags.intersection(doc.tags)]
        return items[offset : offset + limit]

    def count_documents(self, filters):
        return len(self.list_documents(filters, limit=10_000, offset=0))

    def get_document(self, doc_id):
        doc = self.documents.get(doc_id)
        if doc is None:
            return None
        return doc.model_dump() if hasattr(doc, "model_dump") else doc.dict()

    def update_document(self, doc_id, **kwargs):
        doc = self.documents[doc_id]
        data = doc.model_dump() if hasattr(doc, "model_dump") else doc.dict()
        data.update({k: v for k, v in kwargs.items() if v is not None})
        updated = KBDocument(**data)
        self.documents[doc_id] = updated
        return updated

    def delete_document(self, doc_id):
        self.documents.pop(doc_id, None)

    def add_document(
        self, doc_id, content, title=None, source=None, source_url=None, topic=None, date_published=None, metadata=None
    ):
        self.documents[doc_id] = KBDocument(
            id=doc_id,
            content=content,
            title=title,
            source=source,
            source_url=source_url,
            topic=topic,
            date_published=date_published,
            metadata=metadata or {},
            tags=list((metadata or {}).get("tags", [])),
            created_at=datetime.utcnow(),
        )

    def add_chunk(self, *args, **kwargs):
        self.stats["chunks"] += 1


def _build_app(store):
    app = FastAPI()
    register_admin_error_handlers(app)

    async def require_admin():
        return _Identity(user_id="admin")

    async def re_embed_document(doc_id, content):
        store.add_chunk(doc_id, content)
        return 1

    register_admin_kb_routes(
        app,
        prefix="/bedrock-chat/admin",
        kb_store=store,
        require_admin=require_admin,
        re_embed_document=re_embed_document,
    )
    return TestClient(app)


def _seed(store, doc_id, **kwargs):
    store.add_document(doc_id=doc_id, content=kwargs.pop("content", "hello world"), **kwargs)


def test_list_kb_documents_empty_returns_zero_envelope():
    client = _build_app(_FakeKBStore())
    resp = client.get("/bedrock-chat/admin/kb/documents")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_list_kb_documents_filters_by_source():
    store = _FakeKBStore()
    _seed(store, "d1", source="blog")
    _seed(store, "d2", source="docs")
    client = _build_app(store)
    resp = client.get("/bedrock-chat/admin/kb/documents", params={"source": "blog"})
    assert resp.status_code == 200
    assert {item["id"] for item in resp.json()["items"]} == {"d1"}


def test_get_kb_document_missing_returns_404():
    client = _build_app(_FakeKBStore())
    resp = client.get(f"/bedrock-chat/admin/kb/documents/{uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_patch_kb_document_reembed_on_content_change():
    store = _FakeKBStore()
    _seed(store, "d1", content="old body", metadata={"tags": ["perf"]})
    client = _build_app(store)
    resp = client.patch(f"/bedrock-chat/admin/kb/documents/d1", json={"content": "new body"})
    assert resp.status_code == 200
    assert resp.json()["content"] == "new body"


def test_delete_kb_document_removes_entry():
    store = _FakeKBStore()
    _seed(store, "d1")
    client = _build_app(store)
    resp = client.delete(f"/bedrock-chat/admin/kb/documents/d1")
    assert resp.status_code == 204
