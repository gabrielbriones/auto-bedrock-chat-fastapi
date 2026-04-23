"""Integration tests for PgVectorKBStore against a real PostgreSQL instance.

These tests are **skipped** unless a PostgreSQL server with pgvector is
available.  Set the environment variable ``TEST_PG_URL`` to a valid
connection URL to run them::

    export TEST_PG_URL="postgresql://kb_user:kb_password@localhost:5432/knowledge_base"
    pytest tests/test_pgvector_integration.py -v
"""

import os

import numpy as np
import pytest

PG_URL = os.environ.get("TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    PG_URL is None,
    reason="TEST_PG_URL not set — skipping pgvector integration tests",
)


def _can_import_psycopg() -> bool:
    try:
        import pgvector.psycopg  # noqa: F401
        import psycopg  # noqa: F401
        from psycopg_pool import ConnectionPool  # noqa: F401

        return True
    except ImportError:
        return False


if not _can_import_psycopg():
    pytestmark = pytest.mark.skip(reason="psycopg / pgvector not installed")


@pytest.fixture(scope="module")
def pg_store():
    """Create a PgVectorKBStore for integration testing, clean up afterwards."""
    from auto_bedrock_chat_fastapi.pgvector_kb_store import PgVectorKBStore

    store = PgVectorKBStore(
        connection_url=PG_URL,
        pool_size=2,
        embedding_dimensions=1536,
    )
    yield store

    # Cleanup: drop all rows (keep tables for next run)
    import psycopg

    with psycopg.connect(PG_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunks")
            cur.execute("DELETE FROM documents")
        conn.commit()

    store.close()


@pytest.fixture
def sample_embedding():
    return np.random.rand(1536).tolist()


@pytest.fixture
def sample_doc():
    return {
        "doc_id": "integ-doc-001",
        "content": "Integration test document about authentication and API security.",
        "title": "Auth Guide",
        "source": "docs",
        "source_url": "https://example.com/auth",
        "topic": "authentication",
        "date_published": "2026-01-15",
        "metadata": {"author": "test"},
    }


class TestDocumentLifecycle:
    def test_add_and_get(self, pg_store, sample_doc):
        pg_store.add_document(**sample_doc)
        doc = pg_store.get_document(sample_doc["doc_id"])
        assert doc is not None
        assert doc["id"] == sample_doc["doc_id"]
        assert doc["title"] == "Auth Guide"

    def test_upsert(self, pg_store, sample_doc):
        pg_store.add_document(**sample_doc)
        updated = {**sample_doc, "title": "Updated Auth Guide"}
        pg_store.add_document(**updated)
        doc = pg_store.get_document(sample_doc["doc_id"])
        assert doc["title"] == "Updated Auth Guide"

    def test_get_nonexistent(self, pg_store):
        assert pg_store.get_document("nonexistent-xyz") is None

    def test_delete(self, pg_store, sample_doc, sample_embedding):
        pg_store.add_document(**sample_doc)
        pg_store.add_chunk(
            chunk_id="del-chunk-1",
            document_id=sample_doc["doc_id"],
            content="chunk text",
            embedding=sample_embedding,
            chunk_index=0,
        )
        pg_store.delete_document(sample_doc["doc_id"])
        assert pg_store.get_document(sample_doc["doc_id"]) is None


class TestChunkAndSearch:
    def test_semantic_search(self, pg_store, sample_doc, sample_embedding):
        pg_store.add_document(**sample_doc)
        pg_store.add_chunk(
            chunk_id="sem-chunk-1",
            document_id=sample_doc["doc_id"],
            content="Use API keys for secure authentication.",
            embedding=sample_embedding,
            chunk_index=0,
        )

        results = pg_store.semantic_search(sample_embedding, limit=1)
        assert len(results) >= 1
        assert results[0]["chunk_id"] == "sem-chunk-1"
        assert results[0]["similarity_score"] >= 0.99

    def test_keyword_search(self, pg_store, sample_doc, sample_embedding):
        pg_store.add_document(**sample_doc)
        pg_store.add_chunk(
            chunk_id="kw-chunk-1",
            document_id=sample_doc["doc_id"],
            content="OAuth2 tokens and API key rotation best practices.",
            embedding=sample_embedding,
            chunk_index=0,
        )

        results = pg_store.keyword_search("OAuth2 tokens", limit=3)
        assert len(results) >= 1
        assert any(r["chunk_id"] == "kw-chunk-1" for r in results)

    def test_hybrid_search(self, pg_store, sample_doc, sample_embedding):
        pg_store.add_document(**sample_doc)
        pg_store.add_chunk(
            chunk_id="hyb-chunk-1",
            document_id=sample_doc["doc_id"],
            content="JWT token validation and authentication flow.",
            embedding=sample_embedding,
            chunk_index=0,
        )

        results = pg_store.hybrid_search(
            query="JWT authentication",
            query_embedding=sample_embedding,
            limit=5,
        )
        assert len(results) >= 1

    def test_source_filter(self, pg_store, sample_embedding):
        for src in ("docs", "blog"):
            pg_store.add_document(
                doc_id=f"filter-{src}",
                content=f"Content from {src}",
                source=src,
            )
            pg_store.add_chunk(
                chunk_id=f"fc-{src}",
                document_id=f"filter-{src}",
                content=f"Chunk from {src}",
                embedding=sample_embedding,
                chunk_index=0,
            )

        results = pg_store.semantic_search(sample_embedding, limit=10, filters={"source": "docs"})
        for r in results:
            assert r["source"] == "docs"


class TestMetadataAndStats:
    def test_list_sources(self, pg_store):
        pg_store.add_document(doc_id="ls-1", content="a", source="web")
        pg_store.add_document(doc_id="ls-2", content="b", source="web")
        pg_store.add_document(doc_id="ls-3", content="c", source="docs")

        sources = pg_store.list_sources()
        src_dict = {s["source"]: s["count"] for s in sources}
        assert src_dict.get("web", 0) >= 2

    def test_list_topics(self, pg_store):
        pg_store.add_document(doc_id="lt-1", content="a", source="w", topic="auth")
        pg_store.add_document(doc_id="lt-2", content="b", source="w", topic="auth")

        topics = pg_store.list_topics()
        topic_dict = {t["topic"]: t["count"] for t in topics}
        assert topic_dict.get("auth", 0) >= 2

    def test_get_stats(self, pg_store, sample_embedding):
        pg_store.add_document(doc_id="stats-d1", content="x")
        pg_store.add_chunk(
            chunk_id="stats-c1",
            document_id="stats-d1",
            content="y",
            embedding=sample_embedding,
            chunk_index=0,
        )

        stats = pg_store.get_stats()
        assert stats["documents"] >= 1
        assert stats["chunks"] >= 1
        assert stats["vectors"] >= 1
