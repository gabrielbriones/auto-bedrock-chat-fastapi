from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

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
feedback_routes_mod = load_module(
    "autolangchat.admin.admin_feedback_routes",
    "admin/admin_feedback_routes.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.admin.admin_errors": admin_errors_mod,
    },
)

AdminAPIError = exceptions_mod.AdminAPIError
FeedbackEntry = models_mod.FeedbackEntry
Rating = models_mod.Rating
ReviewStatus = models_mod.ReviewStatus
FeedbackStats = models_mod.FeedbackStats
register_admin_error_handlers = admin_errors_mod.register_admin_error_handlers
register_admin_feedback_routes = feedback_routes_mod.register_admin_feedback_routes


class _Identity(SimpleNamespace):
    user_id: str = "admin"


class _FakeFeedbackStore:
    def __init__(self):
        self.entries = []
        self.stats_value = FeedbackStats()

    async def list_entries(self, filters, limit=50, offset=0):
        items = list(self.entries)
        if getattr(filters, "user_id", None):
            items = [e for e in items if e.user_id == filters.user_id]
        if getattr(filters, "status", None):
            items = [e for e in items if e.review_status == filters.status]
        if getattr(filters, "rating", None):
            items = [e for e in items if e.rating == filters.rating]
        if getattr(filters, "tags", None):
            tags = set(filters.tags)
            items = [e for e in items if tags.intersection(e.reviewer_tags)]
        return items[offset : offset + limit]

    async def count_entries(self, filters):
        return len(await self.list_entries(filters, limit=10_000, offset=0))

    async def stats(self):
        return self.stats_value

    async def get(self, entry_id):
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    async def update_review(self, entry_id, review_status, reviewer_id=None, tags=None, comment=None):
        entry = await self.get(entry_id)
        if entry is None:
            raise exceptions_mod.FeedbackNotFoundError("feedback not found")
        data = entry.model_dump()
        data.update(
            {
                "review_status": review_status,
                "reviewer_id": reviewer_id,
                "reviewer_tags": list(tags or []),
                "reviewer_comment": comment,
                "reviewed_at": datetime.now(timezone.utc),
            }
        )
        updated = FeedbackEntry(**data)
        for idx, existing in enumerate(self.entries):
            if existing.id == entry_id:
                self.entries[idx] = updated
                break
        return updated

    async def delete(self, entry_id, expected_status=None):
        for idx, existing in enumerate(self.entries):
            if existing.id == entry_id:
                if expected_status is not None and existing.review_status != expected_status:
                    return False
                del self.entries[idx]
                return True
        return False


def _make_entry(**kwargs):
    defaults = dict(
        session_id="sess-1",
        user_id="alice",
        query="what is the answer?",
        ai_response="42",
        rating=Rating.POSITIVE,
        model_id="anthropic.claude-test",
    )
    defaults.update(kwargs)
    return FeedbackEntry(**defaults)


def _build_app(store):
    app = FastAPI()
    register_admin_error_handlers(app)

    async def require_admin():
        return _Identity(user_id="admin")

    register_admin_feedback_routes(app, prefix="/bedrock-chat/admin", feedback_store=store, require_admin=require_admin)
    return TestClient(app)


def test_list_feedback_empty_returns_zero_envelope():
    client = _build_app(_FakeFeedbackStore())
    resp = client.get("/bedrock-chat/admin/feedback")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_list_feedback_filters_by_user_id():
    store = _FakeFeedbackStore()
    store.entries = [_make_entry(user_id="alice"), _make_entry(user_id="bob")]
    client = _build_app(store)
    resp = client.get("/bedrock-chat/admin/feedback", params={"user_id": "alice"})
    assert resp.status_code == 200
    assert [item["user_id"] for item in resp.json()["items"]] == ["alice"]


def test_feedback_get_missing_returns_404():
    client = _build_app(_FakeFeedbackStore())
    resp = client.get(f"/bedrock-chat/admin/feedback/{uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_feedback_patch_updates_review_fields():
    store = _FakeFeedbackStore()
    entry = _make_entry(rating=Rating.NEGATIVE, correction_text="fix")
    store.entries = [entry]
    client = _build_app(store)

    resp = client.patch(
        f"/bedrock-chat/admin/feedback/{entry.id}",
        json={"review_status": "approved", "reviewer_tags": ["perf"], "reviewer_comment": "looks good"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["review_status"] == "approved"
    assert body["reviewer_tags"] == ["perf"]
    assert body["reviewer_comment"] == "looks good"


def _make_rejected_entry(**kwargs):
    entry = _make_entry(**kwargs)
    return FeedbackEntry(
        **{
            **entry.model_dump(),
            "review_status": ReviewStatus.REJECTED,
            "reviewer_id": "admin",
            "reviewed_at": datetime.now(timezone.utc),
        }
    )


def test_feedback_delete_rejected_returns_204():
    store = _FakeFeedbackStore()
    entry = _make_rejected_entry()
    store.entries = [entry]
    client = _build_app(store)

    resp = client.delete(f"/bedrock-chat/admin/feedback/{entry.id}")
    assert resp.status_code == 204
    assert store.entries == []


def test_feedback_delete_missing_returns_404():
    client = _build_app(_FakeFeedbackStore())
    resp = client.delete(f"/bedrock-chat/admin/feedback/{uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_feedback_delete_non_rejected_returns_409():
    store = _FakeFeedbackStore()
    entry = _make_entry()  # defaults to pending_review
    store.entries = [entry]
    client = _build_app(store)

    resp = client.delete(f"/bedrock-chat/admin/feedback/{entry.id}")
    assert resp.status_code == 409
    assert resp.json()["code"] == "invalid_state"
    # The entry must remain — a non-rejected delete is rejected outright.
    assert store.entries == [entry]


def test_feedback_delete_toctou_status_change_returns_409():
    """The entry is ``rejected`` at snapshot time but flips to ``approved``
    before the atomic DELETE runs. The conditional store delete must refuse to
    remove it and the route must surface a 409 (not a spurious 204/404)."""

    entry = _make_rejected_entry()

    class _RacingStore(_FakeFeedbackStore):
        async def delete(self, entry_id, expected_status=None):
            # Simulate a concurrent admin transitioning the row out of
            # ``rejected`` after ``get()`` but before this DELETE commits.
            for idx, existing in enumerate(self.entries):
                if existing.id == entry_id:
                    self.entries[idx] = FeedbackEntry(
                        **{**existing.model_dump(), "review_status": ReviewStatus.APPROVED}
                    )
            return await super().delete(entry_id, expected_status=expected_status)

    store = _RacingStore()
    store.entries = [entry]
    client = _build_app(store)

    resp = client.delete(f"/bedrock-chat/admin/feedback/{entry.id}")
    assert resp.status_code == 409
    assert resp.json()["code"] == "invalid_state"
    # The row survives because the conditional delete no longer matched.
    assert len(store.entries) == 1
    assert store.entries[0].review_status == ReviewStatus.APPROVED


def test_feedback_delete_toctou_row_vanishes_returns_404():
    """If the row is concurrently removed after the snapshot, the conditional
    delete affects 0 rows and the route re-fetches to return a 404."""

    entry = _make_rejected_entry()

    class _VanishingStore(_FakeFeedbackStore):
        async def delete(self, entry_id, expected_status=None):
            # Concurrent hard-delete: the row is already gone by the time our
            # conditional DELETE runs.
            self.entries = [e for e in self.entries if e.id != entry_id]
            return False

    store = _VanishingStore()
    store.entries = [entry]
    client = _build_app(store)

    resp = client.delete(f"/bedrock-chat/admin/feedback/{entry.id}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"
