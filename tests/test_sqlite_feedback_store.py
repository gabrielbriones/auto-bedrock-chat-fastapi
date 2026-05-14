"""Tests for the SQLite-backed FeedbackStore (XMGPLAT-10417).

Mirrors the behavior covered by the Postgres-backed test_feedback_store.py
so the two backends provide functionally equivalent surfaces.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from auto_bedrock_chat_fastapi.db import BaseFeedbackStore, create_feedback_store
from auto_bedrock_chat_fastapi.db.feedback_sqlite import SQLiteFeedbackStore
from auto_bedrock_chat_fastapi.exceptions import FeedbackNotFoundError, InvalidStatusTransitionError
from auto_bedrock_chat_fastapi.models import FeedbackEntry, Rating, ReviewStatus


def _entry(
    *,
    rating: Rating = Rating.POSITIVE,
    user_id: str = "alice",
    correction_text: str | None = None,
    reviewer_tags: list[str] | None = None,
    kb_sources_used: list[dict] | None = None,
    created_at: datetime | None = None,
) -> FeedbackEntry:
    return FeedbackEntry(
        session_id="sess-1",
        user_id=user_id,
        query="what is the answer?",
        ai_response="42",
        rating=rating,
        correction_text=correction_text,
        kb_sources_used=kb_sources_used or [],
        reviewer_tags=reviewer_tags or [],
        model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        created_at=created_at or datetime.now(timezone.utc),
    )


@pytest.fixture
async def store(tmp_path):
    """Open a fresh SQLiteFeedbackStore on a temp file per test."""
    db_path = str(tmp_path / "feedback.db")
    s = SQLiteFeedbackStore(db_path=db_path, init_schema=True)
    await s.open()
    try:
        yield s
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_open_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "a" / "b" / "feedback.db"
        s = SQLiteFeedbackStore(db_path=str(nested))
        await s.open()
        try:
            assert os.path.exists(nested)
        finally:
            await s.close()

    async def test_open_is_idempotent_on_schema(self, tmp_path):
        path = str(tmp_path / "feedback.db")
        s1 = SQLiteFeedbackStore(db_path=path)
        await s1.open()
        await s1.close()
        # Second store on the same file should not error
        s2 = SQLiteFeedbackStore(db_path=path)
        await s2.open()
        await s2.close()

    async def test_async_context_manager(self, tmp_path):
        path = str(tmp_path / "feedback.db")
        async with SQLiteFeedbackStore(db_path=path) as s:
            assert isinstance(s, BaseFeedbackStore)
            stats = await s.stats()
            assert stats.total == 0


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreateAndGet:
    async def test_create_returns_persisted(self, store):
        entry = _entry(kb_sources_used=[{"title": "doc", "score": 0.9}])
        result = await store.create(entry)
        assert result.id == entry.id
        assert result.rating == Rating.POSITIVE
        assert result.kb_sources_used == [{"title": "doc", "score": 0.9}]

    async def test_get_returns_entry(self, store):
        entry = await store.create(_entry())
        fetched = await store.get(entry.id)
        assert fetched is not None
        assert fetched.id == entry.id
        assert fetched.query == "what is the answer?"

    async def test_get_returns_none_for_unknown(self, store):
        assert await store.get(uuid4()) is None

    async def test_create_rejects_duplicate_id(self, store):
        entry = _entry()
        await store.create(entry)
        # SQLite raises IntegrityError on PK conflict, which the store
        # surfaces as ValueError (matching the WS handler's invalid_feedback
        # path). Either is acceptable here — the contract is just "rejected".
        with pytest.raises((ValueError, sqlite3.IntegrityError)):
            await store.create(entry)


# ---------------------------------------------------------------------------
# list_pending
# ---------------------------------------------------------------------------


class TestListPending:
    async def test_returns_pending_in_creation_order(self, store):
        now = datetime.now(timezone.utc)
        a = await store.create(_entry(created_at=now - timedelta(seconds=2)))
        b = await store.create(_entry(created_at=now - timedelta(seconds=1)))
        c = await store.create(_entry(created_at=now))

        rows = await store.list_pending()
        assert [r.id for r in rows] == [a.id, b.id, c.id]

    async def test_respects_limit_offset(self, store):
        for i in range(3):
            await store.create(_entry(created_at=datetime.now(timezone.utc) + timedelta(seconds=i)))
        page = await store.list_pending(limit=2, offset=1)
        assert len(page) == 2

    async def test_rejects_invalid_paging(self, store):
        with pytest.raises(ValueError):
            await store.list_pending(limit=0)
        with pytest.raises(ValueError):
            await store.list_pending(offset=-1)


# ---------------------------------------------------------------------------
# update_review + list_by_tags + list_by_date_range
# ---------------------------------------------------------------------------


class TestUpdateReview:
    async def test_approve_sets_reviewer_fields(self, store):
        entry = await store.create(_entry())
        updated = await store.update_review(
            entry.id,
            ReviewStatus.APPROVED,
            reviewer_id="bob",
            tags=["correct", "kb-hit"],
            comment="LGTM",
        )
        assert updated.review_status == ReviewStatus.APPROVED
        assert updated.reviewer_id == "bob"
        assert updated.reviewer_tags == ["correct", "kb-hit"]
        assert updated.reviewed_at is not None

    async def test_unknown_id_raises_not_found(self, store):
        with pytest.raises(FeedbackNotFoundError):
            await store.update_review(uuid4(), ReviewStatus.APPROVED, reviewer_id="bob", tags=[], comment=None)

    async def test_invalid_transition_rejected(self, store):
        entry = await store.create(_entry())
        await store.update_review(entry.id, ReviewStatus.APPROVED, reviewer_id="bob", tags=[], comment=None)
        # APPROVED → PENDING is disallowed
        with pytest.raises(InvalidStatusTransitionError):
            await store.update_review(entry.id, ReviewStatus.PENDING_REVIEW, reviewer_id="bob", tags=[], comment=None)

    async def test_empty_reviewer_id_raises(self, store):
        entry = await store.create(_entry())
        with pytest.raises(ValueError):
            await store.update_review(entry.id, ReviewStatus.APPROVED, reviewer_id="   ", tags=[], comment=None)

    async def test_dedupes_and_strips_tags(self, store):
        entry = await store.create(_entry())
        updated = await store.update_review(
            entry.id,
            ReviewStatus.APPROVED,
            reviewer_id="bob",
            tags=["x", "  x  ", "y", ""],
            comment=None,
        )
        assert updated.reviewer_tags == ["x", "y"]


class TestListByTags:
    async def test_filters_to_matching_tags(self, store):
        a = await store.create(_entry())
        b = await store.create(_entry())
        c = await store.create(_entry())
        await store.update_review(a.id, ReviewStatus.APPROVED, reviewer_id="bob", tags=["alpha"], comment=None)
        await store.update_review(b.id, ReviewStatus.APPROVED, reviewer_id="bob", tags=["beta"], comment=None)
        await store.update_review(c.id, ReviewStatus.APPROVED, reviewer_id="bob", tags=["alpha", "beta"], comment=None)

        result = await store.list_by_tags(["alpha"])
        ids = {r.id for r in result}
        assert ids == {a.id, c.id}

    async def test_empty_tags_returns_empty(self, store):
        await store.create(_entry())
        assert await store.list_by_tags([]) == []
        assert await store.list_by_tags(["", "  "]) == []


class TestListByDateRange:
    async def test_filters_window_inclusive_start_exclusive_end(self, store):
        now = datetime.now(timezone.utc)
        a = await store.create(_entry(created_at=now - timedelta(hours=2)))
        b = await store.create(_entry(created_at=now - timedelta(minutes=30)))
        await store.create(_entry(created_at=now + timedelta(hours=1)))

        rows = await store.list_by_date_range(
            start=now - timedelta(hours=3),
            end=now,
        )
        ids = {r.id for r in rows}
        assert ids == {a.id, b.id}

    async def test_status_filter(self, store):
        now = datetime.now(timezone.utc)
        a = await store.create(_entry(created_at=now - timedelta(minutes=10)))
        b = await store.create(_entry(created_at=now - timedelta(minutes=5)))
        await store.update_review(b.id, ReviewStatus.APPROVED, reviewer_id="bob", tags=[], comment=None)

        pending = await store.list_by_date_range(
            start=now - timedelta(hours=1),
            end=now + timedelta(minutes=1),
            status=ReviewStatus.PENDING_REVIEW,
        )
        assert [r.id for r in pending] == [a.id]

    async def test_invalid_window(self, store):
        now = datetime.now(timezone.utc)
        with pytest.raises(ValueError):
            await store.list_by_date_range(start=now, end=now)


class TestStats:
    async def test_aggregates_counts(self, store):
        await store.create(_entry(rating=Rating.POSITIVE))
        await store.create(_entry(rating=Rating.POSITIVE))
        await store.create(_entry(rating=Rating.CORRECTION, correction_text="actually 41"))

        stats = await store.stats()
        assert stats.total == 3
        assert stats.by_rating[Rating.POSITIVE] == 2
        assert stats.by_rating[Rating.CORRECTION] == 1
        assert stats.by_status[ReviewStatus.PENDING_REVIEW] == 3


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateFeedbackStore:
    """``create_feedback_store`` chooses the right backend / no-ops correctly."""

    def _config(self, **overrides):
        from auto_bedrock_chat_fastapi.config import load_config

        cfg = load_config(enable_ui=False)
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    def test_returns_none_when_disabled(self):
        cfg = self._config(feedback_enabled=False)
        assert create_feedback_store(cfg) is None

    def test_returns_sqlite_store_by_default(self, tmp_path):
        cfg = self._config(
            feedback_enabled=True,
            feedback_storage_type="sqlite",
            feedback_database_path=str(tmp_path / "fb.db"),
        )
        store = create_feedback_store(cfg)
        try:
            assert isinstance(store, SQLiteFeedbackStore)
        finally:
            # Don't open() — just instantiated, nothing to close.
            pass

    def test_sqlite_falls_back_to_kb_database_path(self, tmp_path):
        cfg = self._config(
            feedback_enabled=True,
            feedback_storage_type="sqlite",
            feedback_database_path=None,
            kb_database_path=str(tmp_path / "kb.db"),
        )
        store = create_feedback_store(cfg)
        assert isinstance(store, SQLiteFeedbackStore)
        # Internal attribute is not part of the public surface but verifying
        # the fallback wired through is the whole point of the test.
        assert store._db_path == str(tmp_path / "kb.db")  # noqa: SLF001

    def test_postgres_without_url_returns_none(self):
        cfg = self._config(
            feedback_enabled=True,
            feedback_storage_type="postgres",
            feedback_postgres_url=None,
            kb_postgres_url=None,
        )
        assert create_feedback_store(cfg) is None

    def test_unknown_storage_type_returns_none(self):
        cfg = self._config(
            feedback_enabled=True,
            feedback_storage_type="mysql",  # not supported
        )
        assert create_feedback_store(cfg) is None
