"""Integration tests for :class:`FeedbackStore` against a real PostgreSQL.

These tests are **skipped** unless ``TEST_FEEDBACK_PG_URL`` is set::

    export TEST_FEEDBACK_PG_URL="postgresql://feedback:feedback@localhost:5432/feedback_test"
    pytest tests/test_feedback_store_integration.py -v

The store applies its own DDL on startup (idempotent), so the database only
needs to exist and have the ``pgcrypto`` extension installable. Each test
truncates ``feedback`` so ordering / counts are deterministic.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

PG_URL = os.environ.get("TEST_FEEDBACK_PG_URL")

pytestmark = pytest.mark.skipif(
    PG_URL is None,
    reason="TEST_FEEDBACK_PG_URL not set — skipping FeedbackStore integration tests",
)


def _can_import_psycopg() -> bool:
    try:
        import psycopg  # noqa: F401
        from psycopg_pool import AsyncConnectionPool  # noqa: F401

        return True
    except ImportError:
        return False


if not _can_import_psycopg():
    pytestmark = pytest.mark.skip(reason="psycopg / psycopg_pool not installed")


from auto_bedrock_chat_fastapi.db.feedback_postgres import PostgresFeedbackStore as FeedbackStore  # noqa: E402
from auto_bedrock_chat_fastapi.exceptions import FeedbackNotFoundError, InvalidStatusTransitionError  # noqa: E402
from auto_bedrock_chat_fastapi.models import FeedbackEntry, FeedbackListFilters, Rating, ReviewStatus  # noqa: E402


def _entry(**overrides) -> FeedbackEntry:
    base = dict(
        session_id="s-int",
        user_id="u-int",
        query="what is IPC?",
        ai_response="instructions / cycles",
        rating=Rating.POSITIVE,
        model_id="anthropic.claude-test",
    )
    base.update(overrides)
    return FeedbackEntry(**base)


@pytest.fixture
async def store():
    s = FeedbackStore(connection_url=PG_URL, pool_max_size=2, init_schema=True)
    await s.open()
    # Truncate before each test for deterministic counts/ordering.
    async with s._pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("TRUNCATE feedback")
        await conn.commit()
    try:
        yield s
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_get_round_trip(store):
    saved = await store.create(_entry(user_comment="lgtm"))
    fetched = await store.get(saved.id)
    assert fetched is not None
    assert fetched.id == saved.id
    assert fetched.user_comment == "lgtm"
    assert fetched.review_status == ReviewStatus.PENDING_REVIEW
    assert fetched.created_at.tzinfo is not None
    # Defaults applied by the DB
    assert fetched.kb_sources_used == []
    assert fetched.reviewer_tags == []


@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    from uuid import uuid4

    assert await store.get(uuid4()) is None


@pytest.mark.asyncio
async def test_create_persists_jsonb_and_text_array(store):
    sources = [{"title": "Doc A", "url": "https://x"}, {"title": "Doc B"}]
    saved = await store.create(_entry(kb_sources_used=sources))
    fetched = await store.get(saved.id)
    assert fetched.kb_sources_used == sources


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_orders_by_created_at_asc(store):
    a = await store.create(_entry(user_comment="first"))
    b = await store.create(_entry(user_comment="second"))
    pending = await store.list_pending()
    assert [e.id for e in pending] == [a.id, b.id]


@pytest.mark.asyncio
async def test_list_pending_excludes_decided(store):
    pending_e = await store.create(_entry())
    decided_e = await store.create(_entry())
    await store.update_review(decided_e.id, ReviewStatus.APPROVED, "rev-1", ["x"], None)
    pending = await store.list_pending()
    assert [e.id for e in pending] == [pending_e.id]


@pytest.mark.asyncio
async def test_list_by_tags_uses_overlap(store):
    e1 = await store.create(_entry())
    e2 = await store.create(_entry())
    await store.update_review(e1.id, ReviewStatus.APPROVED, "rev", ["perf", "ipc"], None)
    await store.update_review(e2.id, ReviewStatus.APPROVED, "rev", ["security"], None)

    perf_only = await store.list_by_tags(["perf"])
    assert {e.id for e in perf_only} == {e1.id}

    either = await store.list_by_tags(["perf", "security"])
    assert {e.id for e in either} == {e1.id, e2.id}

    # Empty input short-circuits without hitting the DB
    assert await store.list_by_tags([]) == []


@pytest.mark.asyncio
async def test_list_by_date_range_filters_and_orders(store):
    a = await store.create(_entry())
    b = await store.create(_entry())
    now = datetime.now(timezone.utc)
    window = await store.list_by_date_range(now - timedelta(hours=1), now + timedelta(hours=1))
    assert {e.id for e in window} == {a.id, b.id}
    # Newest first
    assert window[0].created_at >= window[-1].created_at

    # Future window matches nothing
    far = await store.list_by_date_range(now + timedelta(days=1), now + timedelta(days=2))
    assert far == []

    with pytest.raises(ValueError):
        await store.list_by_date_range(now, now - timedelta(seconds=1))


@pytest.mark.asyncio
async def test_list_by_date_range_status_filter(store):
    e1 = await store.create(_entry())
    e2 = await store.create(_entry())
    await store.update_review(e1.id, ReviewStatus.APPROVED, "rev", [], None)
    now = datetime.now(timezone.utc)
    only_pending = await store.list_by_date_range(
        now - timedelta(hours=1), now + timedelta(hours=1), status=ReviewStatus.PENDING_REVIEW
    )
    assert [e.id for e in only_pending] == [e2.id]


# ---------------------------------------------------------------------------
# update_review / status transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_review_sets_decision_fields(store):
    saved = await store.create(_entry())
    updated = await store.update_review(saved.id, ReviewStatus.APPROVED, "rev-1", ["topicA"], "looks good")
    assert updated.review_status == ReviewStatus.APPROVED
    assert updated.reviewer_id == "rev-1"
    assert updated.reviewer_tags == ["topicA"]
    assert updated.reviewer_comment == "looks good"
    assert updated.reviewed_at is not None


@pytest.mark.asyncio
async def test_update_review_can_flip_decision(store):
    saved = await store.create(_entry())
    await store.update_review(saved.id, ReviewStatus.APPROVED, "rev", [], None)
    flipped = await store.update_review(saved.id, ReviewStatus.REJECTED, "rev", [], None)
    assert flipped.review_status == ReviewStatus.REJECTED


@pytest.mark.asyncio
async def test_update_review_rejects_pending_target(store):
    saved = await store.create(_entry())
    with pytest.raises(InvalidStatusTransitionError):
        await store.update_review(saved.id, ReviewStatus.PENDING_REVIEW, "rev", [], None)


@pytest.mark.asyncio
async def test_update_review_missing_id(store):
    from uuid import uuid4

    with pytest.raises(FeedbackNotFoundError):
        await store.update_review(uuid4(), ReviewStatus.APPROVED, "rev", [], None)


@pytest.mark.asyncio
async def test_update_review_requires_reviewer_id(store):
    saved = await store.create(_entry())
    with pytest.raises(ValueError):
        await store.update_review(saved.id, ReviewStatus.APPROVED, "", [], None)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_counts_by_status_and_rating(store):
    a = await store.create(_entry(rating=Rating.POSITIVE))
    b = await store.create(_entry(rating=Rating.NEGATIVE, score=2))
    # third entry contributes to the stats; we don't reference it by id.
    await store.create(_entry(rating=Rating.NEGATIVE, correction_text="fix"))
    await store.update_review(a.id, ReviewStatus.APPROVED, "rev", [], None)
    await store.update_review(b.id, ReviewStatus.REJECTED, "rev", [], None)

    stats = await store.stats()
    assert stats.total == 3
    assert stats.by_status[ReviewStatus.PENDING_REVIEW] == 1
    assert stats.by_status[ReviewStatus.APPROVED] == 1
    assert stats.by_status[ReviewStatus.REJECTED] == 1
    assert stats.by_rating[Rating.POSITIVE] == 1
    assert stats.by_rating[Rating.NEGATIVE] == 2
    assert stats.with_correction == 1


# ---------------------------------------------------------------------------
# Schema-level constraints (defense in depth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_check_constraint_rejects_positive_with_correction_text(store):
    """Positive rating may not carry correction_text (DB CHECK)."""
    import psycopg

    async with store._pool.connection() as conn:
        async with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                await cur.execute(
                    """
                    INSERT INTO feedback (
                        session_id, user_id, query, ai_response,
                        rating, correction_text, model_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    ("s", "u", "q", "a", "positive", "should not be allowed", "m"),
                )
        await conn.rollback()


@pytest.mark.asyncio
async def test_db_check_constraint_rejects_whitespace_correction_text(store):
    """C4: whitespace-only correction_text must be rejected at the DB layer."""
    import psycopg

    async with store._pool.connection() as conn:
        async with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                await cur.execute(
                    """
                    INSERT INTO feedback (
                        session_id, user_id, query, ai_response,
                        rating, correction_text, model_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    ("s", "u", "q", "a", "negative", "   ", "m"),
                )
        await conn.rollback()


@pytest.mark.asyncio
async def test_db_check_constraint_rejects_whitespace_reviewer_id(store):
    """C4: whitespace-only reviewer_id with a decided status must be rejected."""
    from datetime import datetime, timezone

    import psycopg

    async with store._pool.connection() as conn:
        async with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                await cur.execute(
                    """
                    INSERT INTO feedback (
                        session_id, user_id, query, ai_response,
                        rating, model_id,
                        review_status, reviewer_id, reviewed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "s",
                        "u",
                        "q",
                        "a",
                        "positive",
                        "m",
                        "approved",
                        "   ",
                        datetime.now(timezone.utc),
                    ),
                )
        await conn.rollback()


# ---------------------------------------------------------------------------
# T2 — list_entries / count_entries / extended stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_entries_no_filters_orders_newest_first(store):
    a = await store.create(_entry())
    b = await store.create(_entry())
    rows = await store.list_entries(FeedbackListFilters())
    assert [r.id for r in rows] == [b.id, a.id]
    assert await store.count_entries(FeedbackListFilters()) == 2


@pytest.mark.asyncio
async def test_list_entries_filters_compose_with_and(store):
    a = await store.create(_entry(user_id="alice"))
    b = await store.create(_entry(user_id="alice"))
    await store.create(_entry(user_id="bob"))
    await store.update_review(a.id, ReviewStatus.APPROVED, "rev", ["perf"], None)
    await store.update_review(b.id, ReviewStatus.REJECTED, "rev", ["perf"], None)

    rows = await store.list_entries(
        FeedbackListFilters(
            user_id="alice",
            status=ReviewStatus.APPROVED,
            tags=["perf"],
        )
    )
    assert [r.id for r in rows] == [a.id]


@pytest.mark.asyncio
async def test_list_entries_tag_overlap(store):
    a = await store.create(_entry())
    b = await store.create(_entry())
    await store.update_review(a.id, ReviewStatus.APPROVED, "rev", ["perf", "ipc"], None)
    await store.update_review(b.id, ReviewStatus.APPROVED, "rev", ["security"], None)

    rows = await store.list_entries(FeedbackListFilters(tags=["perf", "security"]))
    assert {r.id for r in rows} == {a.id, b.id}


@pytest.mark.asyncio
async def test_list_entries_pagination_bounds(store):
    for _ in range(3):
        await store.create(_entry())
    page = await store.list_entries(FeedbackListFilters(), limit=2, offset=1)
    assert len(page) == 2
    with pytest.raises(ValueError):
        await store.list_entries(FeedbackListFilters(), limit=0)
    with pytest.raises(ValueError):
        await store.list_entries(FeedbackListFilters(), offset=-1)


@pytest.mark.asyncio
async def test_stats_top_tags_and_oldest_pending(store):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    # Decided entry — does not influence oldest_pending_hours.
    old_decided = await store.create(_entry(created_at=now - timedelta(hours=8)))
    await store.update_review(old_decided.id, ReviewStatus.APPROVED, "rev", ["perf", "perf"], None)
    # Pending floor ~3h.
    await store.create(_entry(created_at=now - timedelta(hours=3)))
    a = await store.create(_entry(created_at=now - timedelta(minutes=10)))
    b = await store.create(_entry(created_at=now - timedelta(minutes=5)))
    await store.update_review(a.id, ReviewStatus.APPROVED, "rev", ["perf"], None)
    await store.update_review(b.id, ReviewStatus.REJECTED, "rev", ["security"], None)

    stats = await store.stats()
    # ``perf`` 2x (a + old_decided dedup keeps one per row), ``security`` 1x.
    top = {t.tag: t.count for t in stats.top_tags}
    assert top.get("perf") == 2
    assert top.get("security") == 1
    assert stats.oldest_pending_hours is not None
    assert 2.9 <= stats.oldest_pending_hours <= 3.2


# ---------------------------------------------------------------------------
# Legacy ``rating='correction'`` migration (PR review feedback #3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_correction_rows_rewritten_on_open():
    """Seed a row with the legacy ``'correction'`` value via raw SQL,
    re-open the store, and assert the row reads as ``negative``.

    The Postgres ``feedback_rating`` enum still contains the
    ``'correction'`` value on upgraded deployments (``IF NOT EXISTS``
    guards the type creation), so we can insert directly; the
    ``_migrate_legacy_correction_rows`` step on ``open()`` rewrites it.
    """
    s = FeedbackStore(connection_url=PG_URL, pool_max_size=2, init_schema=True)
    await s.open()
    try:
        # Truncate then seed a legacy row via raw SQL. We bypass the
        # FeedbackEntry model entirely because the new Pydantic layer
        # would coerce the value away before it ever hit the DB.
        async with s._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("TRUNCATE feedback")
                # Ensure the legacy enum value exists; on a fresh test
                # DB the new 2-value enum is created, so add the
                # legacy value if missing (idempotent).
                await cur.execute("ALTER TYPE feedback_rating ADD VALUE IF NOT EXISTS 'correction'")
            await conn.commit()
        # The ALTER must commit before we can use the new value.
        async with s._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO feedback (
                        id, session_id, user_id, query, ai_response,
                        rating, correction_text, model_id, created_at
                    ) VALUES (
                        gen_random_uuid(), 'sess-legacy', 'alice',
                        'q', 'a', 'correction', 'fix',
                        'anthropic.claude-3-5-sonnet-20241022-v2:0',
                        NOW()
                    )
                    RETURNING id
                    """
                )
                row = await cur.fetchone()
                legacy_id = row[0]
            await conn.commit()
    finally:
        await s.close()

    # Re-open: the migration step runs as part of ``open()``.
    s2 = FeedbackStore(connection_url=PG_URL, pool_max_size=2, init_schema=False)
    await s2.open()
    try:
        fetched = await s2.get(legacy_id)
    finally:
        await s2.close()

    assert fetched is not None
    assert fetched.rating == Rating.NEGATIVE
    assert fetched.correction_text == "fix"
