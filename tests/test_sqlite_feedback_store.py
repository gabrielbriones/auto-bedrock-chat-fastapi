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
from auto_bedrock_chat_fastapi.models import FeedbackEntry, FeedbackListFilters, Rating, ReviewStatus


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
        await store.create(_entry(rating=Rating.NEGATIVE, correction_text="actually 41"))

        stats = await store.stats()
        assert stats.total == 3
        assert stats.by_rating[Rating.POSITIVE] == 2
        assert stats.by_rating[Rating.NEGATIVE] == 1
        assert stats.with_correction == 1
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


# ---------------------------------------------------------------------------
# T2 — list_entries / count_entries / extended stats
# ---------------------------------------------------------------------------


class TestListEntries:
    async def test_empty_filters_returns_all_newest_first(self, store):
        now = datetime.now(timezone.utc)
        a = await store.create(_entry(created_at=now - timedelta(seconds=2)))
        b = await store.create(_entry(created_at=now - timedelta(seconds=1)))
        c = await store.create(_entry(created_at=now))

        rows = await store.list_entries(FeedbackListFilters())
        assert [r.id for r in rows] == [c.id, b.id, a.id]
        assert await store.count_entries(FeedbackListFilters()) == 3

    async def test_status_filter(self, store):
        a = await store.create(_entry())
        b = await store.create(_entry())
        await store.update_review(b.id, ReviewStatus.APPROVED, reviewer_id="bob", tags=[], comment=None)

        pending = await store.list_entries(FeedbackListFilters(status=ReviewStatus.PENDING_REVIEW))
        assert [r.id for r in pending] == [a.id]
        assert await store.count_entries(FeedbackListFilters(status=ReviewStatus.APPROVED)) == 1

    async def test_rating_filter(self, store):
        await store.create(_entry(rating=Rating.POSITIVE))
        n = await store.create(_entry(rating=Rating.NEGATIVE))
        await store.create(_entry(rating=Rating.NEGATIVE, correction_text="actually 41"))

        rows = await store.list_entries(FeedbackListFilters(rating=Rating.NEGATIVE))
        assert {r.id for r in rows} >= {n.id}
        assert len(rows) == 2

    async def test_has_correction_filter(self, store):
        pos = await store.create(_entry(rating=Rating.POSITIVE))
        plain_neg = await store.create(_entry(rating=Rating.NEGATIVE))
        with_text = await store.create(_entry(rating=Rating.NEGATIVE, correction_text="actually 41"))

        rows = await store.list_entries(FeedbackListFilters(has_correction=True))
        assert [r.id for r in rows] == [with_text.id]

        rows = await store.list_entries(FeedbackListFilters(has_correction=False))
        assert {r.id for r in rows} == {pos.id, plain_neg.id}

    async def test_user_id_filter(self, store):
        a = await store.create(_entry(user_id="alice"))
        await store.create(_entry(user_id="bob"))

        rows = await store.list_entries(FeedbackListFilters(user_id="alice"))
        assert [r.id for r in rows] == [a.id]

    async def test_tag_overlap_filter(self, store):
        a = await store.create(_entry())
        b = await store.create(_entry())
        c = await store.create(_entry())
        await store.update_review(a.id, ReviewStatus.APPROVED, "bob", ["perf", "ipc"], None)
        await store.update_review(b.id, ReviewStatus.APPROVED, "bob", ["security"], None)
        await store.update_review(c.id, ReviewStatus.APPROVED, "bob", ["perf"], None)

        rows = await store.list_entries(FeedbackListFilters(tags=["perf"]))
        assert {r.id for r in rows} == {a.id, c.id}

        rows = await store.list_entries(FeedbackListFilters(tags=["perf", "security"]))
        assert {r.id for r in rows} == {a.id, b.id, c.id}

    async def test_blank_only_tags_drop_to_no_constraint(self, store):
        # Filter with only-blank tags is equivalent to "no tag filter",
        # so all rows match (regression guard for the validator behavior).
        await store.create(_entry())
        await store.create(_entry())
        rows = await store.list_entries(FeedbackListFilters(tags=["", "  "]))
        assert len(rows) == 2

    async def test_date_window_filter(self, store):
        now = datetime.now(timezone.utc)
        old = await store.create(_entry(created_at=now - timedelta(hours=2)))
        recent = await store.create(_entry(created_at=now - timedelta(minutes=5)))
        await store.create(_entry(created_at=now + timedelta(hours=1)))

        rows = await store.list_entries(
            FeedbackListFilters(
                date_from=now - timedelta(hours=3),
                date_to=now,
            )
        )
        assert {r.id for r in rows} == {old.id, recent.id}

    async def test_combined_filters_and_semantics(self, store):
        now = datetime.now(timezone.utc)
        a = await store.create(_entry(user_id="alice", created_at=now - timedelta(minutes=10)))
        b = await store.create(_entry(user_id="alice", created_at=now - timedelta(minutes=5)))
        await store.create(_entry(user_id="bob", created_at=now - timedelta(minutes=5)))
        await store.update_review(a.id, ReviewStatus.APPROVED, "rev", ["topicX"], None)
        await store.update_review(b.id, ReviewStatus.REJECTED, "rev", ["topicX"], None)

        # alice + approved + tag overlap -> only ``a``
        rows = await store.list_entries(
            FeedbackListFilters(
                user_id="alice",
                status=ReviewStatus.APPROVED,
                tags=["topicX"],
            )
        )
        assert [r.id for r in rows] == [a.id]

    async def test_pagination_bounds(self, store):
        for i in range(3):
            await store.create(_entry(created_at=datetime.now(timezone.utc) + timedelta(seconds=i)))
        page = await store.list_entries(FeedbackListFilters(), limit=2, offset=1)
        assert len(page) == 2

        with pytest.raises(ValueError):
            await store.list_entries(FeedbackListFilters(), limit=0)
        with pytest.raises(ValueError):
            await store.list_entries(FeedbackListFilters(), offset=-1)

    async def test_empty_result_set(self, store):
        await store.create(_entry(user_id="alice"))
        rows = await store.list_entries(FeedbackListFilters(user_id="ghost"))
        assert rows == []
        assert await store.count_entries(FeedbackListFilters(user_id="ghost")) == 0

    async def test_count_matches_filtered_total(self, store):
        for _ in range(5):
            await store.create(_entry(user_id="alice"))
        for _ in range(3):
            await store.create(_entry(user_id="bob"))
        assert await store.count_entries(FeedbackListFilters()) == 8
        assert await store.count_entries(FeedbackListFilters(user_id="alice")) == 5


class TestExtendedStats:
    async def test_top_tags_ordered_by_count_desc_then_name_asc(self, store):
        a = await store.create(_entry())
        b = await store.create(_entry())
        c = await store.create(_entry())
        d = await store.create(_entry())
        # ``perf`` 3x, ``security`` 2x, ``api`` 1x (api < security alphabetically
        # ensures the secondary sort is exercised on equal counts elsewhere).
        await store.update_review(a.id, ReviewStatus.APPROVED, "bob", ["perf", "api"], None)
        await store.update_review(b.id, ReviewStatus.APPROVED, "bob", ["perf", "security"], None)
        await store.update_review(c.id, ReviewStatus.APPROVED, "bob", ["perf", "security"], None)
        await store.update_review(d.id, ReviewStatus.APPROVED, "bob", [], None)

        stats = await store.stats()
        top = [(t.tag, t.count) for t in stats.top_tags]
        assert top == [("perf", 3), ("security", 2), ("api", 1)]

    async def test_oldest_pending_hours_none_when_nothing_pending(self, store):
        e = await store.create(_entry())
        await store.update_review(e.id, ReviewStatus.APPROVED, "bob", [], None)
        stats = await store.stats()
        assert stats.oldest_pending_hours is None

    async def test_oldest_pending_hours_reflects_oldest_pending(self, store):
        now = datetime.now(timezone.utc)
        # An older, decided entry should NOT influence the result.
        old_decided = await store.create(_entry(created_at=now - timedelta(hours=10)))
        await store.update_review(old_decided.id, ReviewStatus.APPROVED, "bob", [], None)
        # The pending floor is ~2h old.
        await store.create(_entry(created_at=now - timedelta(hours=2)))
        await store.create(_entry(created_at=now - timedelta(minutes=30)))

        stats = await store.stats()
        assert stats.oldest_pending_hours is not None
        # Allow a small wallclock slack — the value is computed off ``now()``
        # inside ``stats()`` so a few seconds may have elapsed.
        assert 1.9 <= stats.oldest_pending_hours <= 2.2

    async def test_oldest_pending_hours_handles_mixed_timezones(self, store):
        # SQLite stores ``created_at`` as a normalized UTC ISO string, so
        # mixing tz-aware datetimes in different offsets must still produce
        # the right age.
        now_utc = datetime.now(timezone.utc)
        plus5 = timezone(timedelta(hours=5))
        # Same instant expressed in two different offsets.
        await store.create(_entry(created_at=now_utc - timedelta(hours=1)))
        await store.create(_entry(created_at=(now_utc - timedelta(hours=3)).astimezone(plus5)))
        stats = await store.stats()
        assert stats.oldest_pending_hours is not None
        assert 2.9 <= stats.oldest_pending_hours <= 3.2


# ---------------------------------------------------------------------------
# FeedbackListFilters model
# ---------------------------------------------------------------------------


class TestFeedbackListFilters:
    def test_defaults_all_none(self):
        f = FeedbackListFilters()
        assert f.status is None
        assert f.rating is None
        assert f.tags is None
        assert f.date_from is None
        assert f.date_to is None
        assert f.user_id is None

    def test_tags_stripped_and_blanks_dropped(self):
        f = FeedbackListFilters(tags=["  perf ", "", "ipc", "   "])
        assert f.tags == ["perf", "ipc"]

    def test_all_blank_tags_collapse_to_none(self):
        f = FeedbackListFilters(tags=["", "  "])
        assert f.tags is None

    def test_user_id_stripped_to_none_when_blank(self):
        f = FeedbackListFilters(user_id="   ")
        assert f.user_id is None

    def test_invalid_date_window_rejected(self):
        import pydantic

        now = datetime.now(timezone.utc)
        with pytest.raises(pydantic.ValidationError):
            FeedbackListFilters(date_from=now, date_to=now)
        with pytest.raises(pydantic.ValidationError):
            FeedbackListFilters(date_from=now, date_to=now - timedelta(seconds=1))


# ---------------------------------------------------------------------------
# Legacy ``rating='correction'`` migration
# ---------------------------------------------------------------------------


class TestLegacyCorrectionMigration:
    """Pre-Phase-2 schemas allowed ``rating='correction'``. The current
    schema forbids it via CHECK, but existing rows in long-lived dev
    DBs must be migrated in place on store init so downstream code that
    re-inserts / updates those rows doesn't fail. The model-layer
    ``mode="before"`` validator on ``Rating`` also coerces the value
    on read as a belt-and-braces measure.
    """

    def _seed_legacy_db(self, db_path: str) -> str:
        """Create a feedback table with the *old* 3-value CHECK and a
        single ``rating='correction'`` row, returning the row id."""
        legacy_id = str(uuid4())
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE feedback (
                    id              TEXT PRIMARY KEY,
                    session_id      TEXT NOT NULL,
                    user_id         TEXT NOT NULL,
                    query           TEXT NOT NULL,
                    ai_response     TEXT NOT NULL,
                    rating          TEXT NOT NULL
                                    CHECK (rating IN ('positive', 'negative', 'correction')),
                    score           INTEGER,
                    correction_text TEXT,
                    user_comment    TEXT,
                    kb_sources_used TEXT NOT NULL DEFAULT '[]',
                    model_id        TEXT NOT NULL,
                    review_status   TEXT NOT NULL DEFAULT 'pending_review',
                    reviewer_id     TEXT,
                    reviewer_tags   TEXT NOT NULL DEFAULT '[]',
                    reviewer_comment TEXT,
                    reviewed_at     TEXT,
                    created_at      TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO feedback (id, session_id, user_id, query, ai_response, "
                "rating, correction_text, model_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    legacy_id,
                    "sess-legacy",
                    "alice",
                    "what is IPC?",
                    "IPC is cycles per instruction",
                    "correction",
                    "IPC = instructions / cycles",
                    "anthropic.claude-3-5-sonnet-20241022-v2:0",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return legacy_id

    async def test_legacy_correction_rows_rewritten_on_open(self, tmp_path, caplog):
        path = str(tmp_path / "legacy.db")
        legacy_id = self._seed_legacy_db(path)

        # Opening the store should run the idempotent UPDATE and
        # rewrite the row to ``negative`` in place.
        with caplog.at_level("WARNING"):
            s = SQLiteFeedbackStore(db_path=path, init_schema=True)
            await s.open()
            try:
                row = await s.get(legacy_id)
            finally:
                await s.close()

        assert row is not None
        assert row.rating == Rating.NEGATIVE
        assert row.correction_text == "IPC = instructions / cycles"
        # Migration log line surfaces the affected row count.
        assert any(
            "migrated" in r.message and "correction" in r.message and "negative" in r.message for r in caplog.records
        ), "expected a WARNING log line announcing the legacy-correction migration"

    async def test_migration_is_idempotent(self, tmp_path):
        # Opening the store a second time on an already-migrated DB must
        # be a no-op (UPDATE matches zero rows) and not log a warning.
        path = str(tmp_path / "legacy.db")
        self._seed_legacy_db(path)

        s1 = SQLiteFeedbackStore(db_path=path, init_schema=True)
        await s1.open()
        await s1.close()

        # Second open: the rewritten row should still read as ``negative``.
        s2 = SQLiteFeedbackStore(db_path=path, init_schema=True)
        await s2.open()
        try:
            stats = await s2.stats()
        finally:
            await s2.close()
        assert stats.by_rating.get("negative", 0) == 1
        assert stats.by_rating.get("correction", 0) == 0

    def test_pydantic_read_alias(self):
        # Independent of any DB: the ``mode="before"`` validator coerces
        # ``"correction"`` → ``"negative"`` so legacy rows hydrating
        # straight from a raw dict don't explode.
        from datetime import datetime, timezone

        entry = FeedbackEntry(
            session_id="sess-1",
            user_id="alice",
            query="q",
            ai_response="a",
            rating="correction",  # legacy value
            correction_text="fix",
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            created_at=datetime.now(timezone.utc),
        )
        assert entry.rating == Rating.NEGATIVE
        assert entry.correction_text == "fix"
