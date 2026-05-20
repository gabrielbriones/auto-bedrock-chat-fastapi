"""Tests for the XMGPLAT-10417 Phase 2 KB admin extensions.

Covers the new ``list_documents`` / ``count_documents`` / ``update_document``
methods on :class:`SQLiteKBStore` (and via the BaseKBStore interface) plus
the :class:`KBDocument` / :class:`KBDocumentListFilters` Pydantic models.

The pgvector implementation mirrors the SQLite one and is exercised in
``test_pgvector_kb_store.py`` when a Postgres instance is available; this
file is the SQLite-only baseline that always runs.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from typing import List

import numpy as np
import pytest

from auto_bedrock_chat_fastapi.db.kb_sqlite import SQLiteKBStore
from auto_bedrock_chat_fastapi.exceptions import KBDocumentNotFoundError
from auto_bedrock_chat_fastapi.models import KBDocument, KBDocumentListFilters

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    s = SQLiteKBStore(db_path)
    try:
        yield s
    finally:
        s.close()
        os.unlink(db_path)


def _embedding() -> List[float]:
    return np.random.rand(1536).astype(np.float32).tolist()


def _seed(store: SQLiteKBStore, doc_id: str, content: str = "hello world", **kw) -> None:
    """Add a doc + one chunk so chunk_count > 0."""
    store.add_document(doc_id=doc_id, content=content, **kw)
    store.add_chunk(
        chunk_id=f"{doc_id}-c0",
        document_id=doc_id,
        content=content,
        embedding=_embedding(),
        chunk_index=0,
    )


# ---------------------------------------------------------------------------
# KBDocumentListFilters validation
# ---------------------------------------------------------------------------


class TestKBDocumentListFilters:
    def test_blank_tags_dropped(self):
        f = KBDocumentListFilters(tags=["", "  ", "a", "b"])
        assert f.tags == ["a", "b"]

    def test_all_blank_tags_become_none(self):
        f = KBDocumentListFilters(tags=["", "   "])
        assert f.tags is None

    def test_date_window_validated(self):
        now = datetime.now(timezone.utc)
        with pytest.raises(ValueError, match="date_to must be after date_from"):
            KBDocumentListFilters(date_from=now, date_to=now)

    def test_source_topic_stripped(self):
        f = KBDocumentListFilters(source="  blog  ", topic="")
        assert f.source == "blog"
        assert f.topic is None


# ---------------------------------------------------------------------------
# KBDocument.tags hygiene
# ---------------------------------------------------------------------------


class TestKBDocumentTagsNormalization:
    def test_strip_and_dedupe(self):
        d = KBDocument(id="x", content="c", tags=["a", " a", "  ", "b"])
        assert d.tags == ["a", "b"]

    def test_empty_default(self):
        d = KBDocument(id="x", content="c")
        assert d.tags == []
        assert d.metadata == {}


# ---------------------------------------------------------------------------
# list_documents / count_documents
# ---------------------------------------------------------------------------


class TestListAndCount:
    def test_empty_store(self, store):
        assert store.count_documents() == 0
        assert store.list_documents() == []

    def test_basic_list_with_chunk_count(self, store):
        _seed(store, "d1", source="blog", topic="ai")
        _seed(store, "d2", source="docs", topic="ai")
        # d3 has zero chunks → COALESCE keeps chunk_count==0
        store.add_document(doc_id="d3", content="lonely", source="blog", topic="ml")

        docs = store.list_documents()
        assert len(docs) == 3
        ids = {d.id for d in docs}
        assert ids == {"d1", "d2", "d3"}

        by_id = {d.id: d for d in docs}
        assert by_id["d1"].chunk_count == 1
        assert by_id["d3"].chunk_count == 0
        assert store.count_documents() == 3

    def test_filter_by_source(self, store):
        _seed(store, "d1", source="blog")
        _seed(store, "d2", source="docs")
        _seed(store, "d3", source="blog")

        f = KBDocumentListFilters(source="blog")
        docs = store.list_documents(filters=f)
        assert {d.id for d in docs} == {"d1", "d3"}
        assert store.count_documents(filters=f) == 2

    def test_filter_by_topic(self, store):
        _seed(store, "d1", topic="ai")
        _seed(store, "d2", topic="ml")
        f = KBDocumentListFilters(topic="ai")
        assert {d.id for d in store.list_documents(filters=f)} == {"d1"}
        assert store.count_documents(filters=f) == 1

    def test_filter_by_tags_overlap(self, store):
        _seed(store, "d1", metadata={"tags": ["red", "blue"]})
        _seed(store, "d2", metadata={"tags": ["green"]})
        _seed(store, "d3", metadata={"tags": ["blue", "yellow"]})
        store.add_document(doc_id="d4", content="no tags", metadata={})

        f = KBDocumentListFilters(tags=["blue"])
        docs = store.list_documents(filters=f)
        assert {d.id for d in docs} == {"d1", "d3"}

        f2 = KBDocumentListFilters(tags=["green", "yellow"])
        assert {d.id for d in store.list_documents(filters=f2)} == {"d2", "d3"}

    def test_filter_by_date_window(self, store):
        store.add_document(doc_id="d1", content="x", date_published="2024-01-01")
        store.add_document(doc_id="d2", content="x", date_published="2024-06-01")
        store.add_document(doc_id="d3", content="x", date_published="2025-01-01")

        f = KBDocumentListFilters(
            date_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
            date_to=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        docs = store.list_documents(filters=f)
        assert {d.id for d in docs} == {"d1", "d2"}  # date_to exclusive

    def test_pagination(self, store):
        for i in range(5):
            store.add_document(doc_id=f"d{i}", content=f"c{i}")

        page1 = store.list_documents(limit=2, offset=0)
        page2 = store.list_documents(limit=2, offset=2)
        page3 = store.list_documents(limit=2, offset=4)

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1
        # Pages must be disjoint
        all_ids = {d.id for d in page1 + page2 + page3}
        assert len(all_ids) == 5

    def test_count_consistent_with_list(self, store):
        for i in range(7):
            _seed(store, f"d{i}", source="blog" if i % 2 == 0 else "docs")

        f = KBDocumentListFilters(source="blog")
        assert store.count_documents(filters=f) == len(store.list_documents(filters=f, limit=100))

    def test_tags_projection_from_metadata(self, store):
        _seed(store, "d1", metadata={"tags": ["a", "b"], "extra": 1})
        docs = store.list_documents()
        assert docs[0].tags == ["a", "b"]
        assert docs[0].metadata == {"tags": ["a", "b"], "extra": 1}


# ---------------------------------------------------------------------------
# update_document
# ---------------------------------------------------------------------------


class TestUpdateDocument:
    def test_missing_doc_raises(self, store):
        with pytest.raises(KBDocumentNotFoundError):
            store.update_document("nope", title="x")

    def test_metadata_only_update_does_not_touch_chunks(self, store):
        _seed(store, "d1", content="hello", metadata={"v": 1})
        before = store.get_stats()["chunks"]

        updated = store.update_document("d1", metadata={"v": 2})

        after = store.get_stats()["chunks"]
        assert before == 1
        assert after == 1  # chunks untouched
        assert updated.metadata == {"v": 2}
        assert updated.chunk_count == 1
        assert updated.content == "hello"  # content unchanged

    def test_content_change_clears_chunks(self, store):
        _seed(store, "d1", content="hello")
        assert store.get_stats()["chunks"] == 1

        updated = store.update_document("d1", content="completely new body")

        assert store.get_stats()["chunks"] == 0
        assert updated.chunk_count == 0
        assert updated.content == "completely new body"

    def test_content_unchanged_keeps_chunks(self, store):
        """Passing content equal to the stored value must NOT clear chunks."""
        _seed(store, "d1", content="same")
        updated = store.update_document("d1", content="same", title="new title")
        assert store.get_stats()["chunks"] == 1
        assert updated.chunk_count == 1
        assert updated.title == "new title"

    def test_tags_only_update_persists_in_metadata(self, store):
        _seed(store, "d1", metadata={"x": 1})
        updated = store.update_document("d1", tags=["alpha", "beta"])
        assert updated.tags == ["alpha", "beta"]
        assert updated.metadata.get("tags") == ["alpha", "beta"]
        assert updated.metadata.get("x") == 1
        # Round-trip via get_document
        doc = store.get_document("d1")
        assert doc["metadata"]["tags"] == ["alpha", "beta"]

    def test_tags_normalized(self, store):
        _seed(store, "d1")
        updated = store.update_document("d1", tags=["  a", "a", "", "b "])
        assert updated.tags == ["a", "b"]

    def test_tags_empty_list_clears(self, store):
        _seed(store, "d1", metadata={"tags": ["x"]})
        updated = store.update_document("d1", tags=[])
        assert updated.tags == []
        assert updated.metadata.get("tags") == []

    def test_none_means_dont_touch(self, store):
        _seed(store, "d1", title="orig", source="blog", metadata={"tags": ["t1"]})
        updated = store.update_document("d1")  # no kwargs
        assert updated.title == "orig"
        assert updated.source == "blog"
        assert updated.tags == ["t1"]

    def test_tags_arg_wins_over_metadata_tags(self, store):
        _seed(store, "d1", metadata={"tags": ["old"]})
        updated = store.update_document(
            "d1",
            metadata={"tags": ["from-metadata"], "other": 1},
            tags=["from-tags-arg"],
        )
        assert updated.tags == ["from-tags-arg"]
        assert updated.metadata["tags"] == ["from-tags-arg"]
        assert updated.metadata["other"] == 1

    def test_partial_field_updates(self, store):
        _seed(
            store,
            "d1",
            title="t",
            source="s",
            source_url="u",
            topic="tp",
            date_published="2024-01-01",
        )
        updated = store.update_document("d1", source="new-source", topic="new-topic")
        assert updated.source == "new-source"
        assert updated.topic == "new-topic"
        assert updated.title == "t"
        assert updated.source_url == "u"
        assert updated.date_published == "2024-01-01"

    def test_returned_doc_matches_subsequent_list(self, store):
        _seed(store, "d1")
        updated = store.update_document("d1", title="renamed", tags=["x"])
        listed = store.list_documents()
        assert len(listed) == 1
        assert listed[0].title == "renamed"
        assert listed[0].tags == ["x"]
        assert listed[0].chunk_count == updated.chunk_count


# ---------------------------------------------------------------------------
# Concurrency (PR review feedback #10)
# ---------------------------------------------------------------------------


class TestConcurrency:
    """SQLiteKBStore shares a single sqlite3.Connection across threads
    (admin routes wrap calls in asyncio.to_thread). Without
    serialization, the multi-statement update_document transaction
    (BEGIN; delete chunks; UPDATE doc; COMMIT) races against
    concurrent admin reads and writes. The ``@_locked`` decorator on
    every public method must prevent that.
    """

    def test_concurrent_reads_and_writes_do_not_corrupt(self, store):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Seed a handful of docs so list_documents has something to
        # return on every read.
        for i in range(5):
            _seed(store, f"d{i}", content=f"original-{i}")

        def reader(_):
            for _ in range(10):
                docs = store.list_documents()
                assert len(docs) == 5
                # ``count_documents`` must agree with ``list_documents``.
                assert store.count_documents() == 5

        def writer(i):
            for j in range(5):
                # Alternate content vs. metadata-only updates so we
                # exercise both the chunk-clearing transaction and the
                # fast-path no-content branch.
                if j % 2 == 0:
                    store.update_document(
                        f"d{i}",
                        content=f"rewritten-{i}-{j}",
                    )
                else:
                    store.update_document(
                        f"d{i}",
                        title=f"t-{i}-{j}",
                    )

        # 5 writer threads (one per doc, no logical conflict beyond
        # the shared connection) + 8 reader threads.
        with ThreadPoolExecutor(max_workers=13) as pool:
            futures = [pool.submit(writer, i) for i in range(5)]
            futures += [pool.submit(reader, i) for i in range(8)]
            for f in as_completed(futures):
                # Surfaces "database is locked" or any race-induced
                # OperationalError as a test failure.
                f.result()

        # After the dust settles every doc must still exist and be in
        # a consistent state (content cleared chunks → re-seed to
        # restore an invariant if your code re-embeds; here we just
        # verify the row count is unchanged).
        assert store.count_documents() == 5
        assert len(store.list_documents()) == 5
