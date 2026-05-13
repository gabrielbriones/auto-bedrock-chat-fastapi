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
from auto_bedrock_chat_fastapi.models import FeedbackEntry, Rating, ReviewStatus  # noqa: E402


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
    await store.create(_entry(rating=Rating.CORRECTION, correction_text="fix"))
    await store.update_review(a.id, ReviewStatus.APPROVED, "rev", [], None)
    await store.update_review(b.id, ReviewStatus.REJECTED, "rev", [], None)

    stats = await store.stats()
    assert stats.total == 3
    assert stats.by_status[ReviewStatus.PENDING_REVIEW] == 1
    assert stats.by_status[ReviewStatus.APPROVED] == 1
    assert stats.by_status[ReviewStatus.REJECTED] == 1
    assert stats.by_rating[Rating.POSITIVE] == 1
    assert stats.by_rating[Rating.NEGATIVE] == 1
    assert stats.by_rating[Rating.CORRECTION] == 1


# ---------------------------------------------------------------------------
# Schema-level constraints (defense in depth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_check_constraint_rejects_correction_without_text(store):
    """Bypass Pydantic and confirm the DB CHECK constraint fires."""
    import psycopg

    async with store._pool.connection() as conn:
        async with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                await cur.execute(
                    """
                    INSERT INTO feedback (
                        session_id, user_id, query, ai_response,
                        rating, model_id
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    ("s", "u", "q", "a", "correction", "m"),
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
                    ("s", "u", "q", "a", "correction", "   ", "m"),
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
