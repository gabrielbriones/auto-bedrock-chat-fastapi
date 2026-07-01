"""Tag filter case-insensitivity (XMGPLAT-10976).

The Admin Panel's tags filter must match regardless of case (e.g. typing
"Perf" should match a stored tag of "perf"). Covers both the feedback
store's ``reviewer_tags`` filter and the KB store's ``metadata.tags``
filter, each against a real in-memory SQLite database.
"""

import sys

import pytest

from ._autolangchat_imports import install_package_stubs, load_module

exceptions_mod = load_module("autolangchat.exceptions", "exceptions.py")
models_mod = load_module("autolangchat.models", "models.py")
feedback_base_mod = load_module(
    "autolangchat.db.feedback_base",
    "db/feedback_base.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
    },
)
feedback_sqlite_mod = load_module(
    "autolangchat.db.feedback_sqlite",
    "db/feedback_sqlite.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.db.feedback_base": feedback_base_mod,
    },
)
kb_base_mod = load_module(
    "autolangchat.db.kb_base",
    "db/kb_base.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
    },
)
kb_sqlite_mod = load_module(
    "autolangchat.db.kb_sqlite",
    "db/kb_sqlite.py",
    extra_modules={
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.models": models_mod,
        "autolangchat.db.kb_base": kb_base_mod,
    },
)

SQLiteFeedbackStore = feedback_sqlite_mod.SQLiteFeedbackStore
SQLiteKBStore = kb_sqlite_mod.SQLiteKBStore
FeedbackEntry = models_mod.FeedbackEntry
FeedbackListFilters = models_mod.FeedbackListFilters
KBDocumentListFilters = models_mod.KBDocumentListFilters
Rating = models_mod.Rating
ReviewStatus = models_mod.ReviewStatus


@pytest.fixture(autouse=True)
def _package_stubs():
    """Install lightweight ``autolangchat`` package stubs (schema DDL for the
    SQLite feedback store is resolved via ``importlib.resources`` at runtime
    and needs ``autolangchat.db`` to be importable). Mirrors the fixture in
    ``test_feedback_store_delete.py``.
    """
    stubs = install_package_stubs()
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        yield
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _make_entry(**kwargs):
    defaults = dict(
        session_id="sess-1",
        user_id="alice",
        query="what is the answer?",
        ai_response="42",
        rating=Rating.NEGATIVE,
        model_id="anthropic.claude-test",
    )
    defaults.update(kwargs)
    return FeedbackEntry(**defaults)


async def test_feedback_tags_filter_is_case_insensitive():
    store = SQLiteFeedbackStore(":memory:")
    await store.open()
    try:
        entry = await store.create(_make_entry())
        await store.update_review(entry.id, ReviewStatus.APPROVED, reviewer_id="admin", tags=["Perf"], comment=None)

        # Query using a different case than what was stored.
        results = await store.list_entries(FeedbackListFilters(tags=["perf"]))
        assert [r.id for r in results] == [entry.id]

        results = await store.list_entries(FeedbackListFilters(tags=["PERF"]))
        assert [r.id for r in results] == [entry.id]

        # A tag that truly doesn't match (even case-insensitively) still misses.
        results = await store.list_entries(FeedbackListFilters(tags=["other"]))
        assert results == []
    finally:
        await store.close()


def test_kb_tags_filter_is_case_insensitive(tmp_path):
    store = SQLiteKBStore(db_path=str(tmp_path / "test_kb.db"))
    store.add_document("doc-1", content="some content", metadata={"tags": ["Networking"]})

    results = store.list_documents(KBDocumentListFilters(tags=["networking"]))
    assert [d.id for d in results] == ["doc-1"]

    results = store.list_documents(KBDocumentListFilters(tags=["NETWORKING"]))
    assert [d.id for d in results] == ["doc-1"]

    results = store.list_documents(KBDocumentListFilters(tags=["other"]))
    assert results == []
