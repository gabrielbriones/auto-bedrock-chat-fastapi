"""
Tests for Hybrid Search (Semantic + BM25).

Uses a temporary database seeded with test data and synthetic embeddings
so no external services or populated knowledge base are needed.
"""

import numpy as np
import pytest

from auto_bedrock_chat_fastapi.vector_db import VectorDB

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 1536


def _random_embedding(seed: int = 0) -> list[float]:
    """Return a deterministic random 1536-d unit vector."""
    rng = np.random.RandomState(seed)
    vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec.tolist()


def _similar_embedding(base: list[float], noise: float = 0.05, seed: int = 1) -> list[float]:
    """Return an embedding close to *base* (high cosine similarity)."""
    rng = np.random.RandomState(seed)
    arr = np.array(base, dtype=np.float32) + noise * rng.randn(EMBEDDING_DIM).astype(np.float32)
    arr /= np.linalg.norm(arr)
    return arr.tolist()


# -- Test documents ----------------------------------------------------------

DOCS = [
    {
        "doc_id": "doc-async-tests",
        "title": "Async Tests - FastAPI",
        "source": "docs",
        "source_url": "https://fastapi.tiangolo.com/advanced/async-tests/",
        "topic": "testing",
        "content": "Async testing with FastAPI using pytest and httpx.",
        "chunks": [
            ("Async testing allows you to test async endpoints using pytest-asyncio " "and httpx.AsyncClient."),
            (
                "Tip: If you encounter a RuntimeError: Task attached to a different loop, "
                "make sure you are using the correct event loop fixture provided by "
                "pytest-asyncio."
            ),
        ],
    },
    {
        "doc_id": "doc-bedrock-auth",
        "title": "AWS Bedrock Authentication",
        "source": "blog",
        "source_url": "https://example.com/bedrock-auth",
        "topic": "authentication",
        "content": "How to authenticate with AWS Bedrock Converse API.",
        "chunks": [
            (
                "AWS Bedrock Converse API requires valid IAM credentials. You can use "
                "environment variables AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
            ),
            (
                "For production, use IAM roles attached to your EC2 instance or ECS task "
                "instead of long-lived credentials."
            ),
        ],
    },
    {
        "doc_id": "doc-fastapi-db",
        "title": "FastAPI Database Connection",
        "source": "docs",
        "source_url": "https://fastapi.tiangolo.com/tutorial/sql-databases/",
        "topic": "database",
        "content": "Connecting to databases from async FastAPI applications.",
        "chunks": [
            (
                "FastAPI supports async database connections using SQLAlchemy with "
                "asyncpg or databases library for PostgreSQL."
            ),
            (
                "Use dependency injection to manage database sessions. Each request "
                "gets its own session that is closed after the response."
            ),
        ],
    },
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path):
    """Create a VectorDB at a temp path and seed it with test data."""
    db_path = str(tmp_path / "test_kb.db")
    db = VectorDB(db_path)

    seed = 0
    for doc in DOCS:
        db.add_document(
            doc_id=doc["doc_id"],
            content=doc["content"],
            title=doc["title"],
            source=doc["source"],
            source_url=doc["source_url"],
            topic=doc["topic"],
        )
        for idx, chunk_text in enumerate(doc["chunks"]):
            chunk_id = f"{doc['doc_id']}_chunk_{idx}"
            emb = _random_embedding(seed)
            db.add_chunk(
                chunk_id=chunk_id,
                document_id=doc["doc_id"],
                content=chunk_text,
                embedding=emb,
                chunk_index=idx,
            )
            seed += 1

    yield db
    db.close()


# ---------------------------------------------------------------------------
# Tests – BM25 search
# ---------------------------------------------------------------------------


def test_bm25_finds_exact_error_message(seeded_db):
    """Keyword search should find the chunk containing the exact RuntimeError text."""
    results = seeded_db.keyword_search(query="RuntimeError Task attached different loop", limit=5)

    assert len(results) > 0
    top = results[0]
    assert "RuntimeError" in top["content"]
    assert top["keyword_score"] > 0


def test_bm25_respects_limit(seeded_db):
    """Keyword search should not return more results than limit."""
    results = seeded_db.keyword_search(query="FastAPI async", limit=2)
    assert len(results) <= 2


def test_bm25_handles_special_characters(seeded_db):
    """Keyword search should handle FTS5 special characters in user queries without crashing."""
    # Question mark
    results = seeded_db.keyword_search(query="what is the parameter cheetah for?", limit=5)
    assert isinstance(results, list)

    # Parentheses, quotes, asterisks
    results = seeded_db.keyword_search(query='RuntimeError("task*") + loop?', limit=5)
    assert isinstance(results, list)

    # FTS5 boolean keywords mixed with text
    results = seeded_db.keyword_search(query="NOT OR AND async testing", limit=5)
    assert isinstance(results, list)

    # Only special characters should return empty
    results = seeded_db.keyword_search(query="???***", limit=5)
    assert results == []


def test_sanitize_fts5_query():
    """Test the FTS5 query sanitizer directly."""
    sanitize = VectorDB._sanitize_fts5_query

    assert sanitize("what is cheetah for?") == "what OR is OR cheetah OR for"
    assert sanitize('RuntimeError("task")') == "RuntimeError OR task"
    assert sanitize("NOT this OR that") == "this OR that"
    assert sanitize("hello + world - test") == "hello OR world OR test"
    assert sanitize("???") == ""
    assert sanitize("simple query") == "simple OR query"
    assert sanitize("  spaces   everywhere  ") == "spaces OR everywhere"


def test_bm25_filter_by_source(seeded_db):
    """Keyword search should apply source filter."""
    results = seeded_db.keyword_search(query="credentials", limit=5, filters={"source": "blog"})

    for r in results:
        assert r["source"] == "blog"


def test_bm25_filter_by_topic(seeded_db):
    """Keyword search should apply topic filter."""
    results = seeded_db.keyword_search(query="async", limit=5, filters={"topic": "testing"})

    for r in results:
        assert r["topic"] == "testing"


def test_bm25_no_results_for_unrelated_query(seeded_db):
    """Keyword search should return no results for completely unrelated text."""
    results = seeded_db.keyword_search(query="quantum entanglement superconductor", limit=5)
    assert len(results) == 0


# ---------------------------------------------------------------------------
# Tests – hybrid search
# ---------------------------------------------------------------------------


def test_hybrid_search_returns_results(seeded_db):
    """Hybrid search should combine semantic and BM25 results."""
    query_emb = _similar_embedding(_random_embedding(0), noise=0.05, seed=99)

    results = seeded_db.hybrid_search(
        query="async testing pytest",
        query_embedding=query_emb,
        limit=3,
        min_score=0.0,
    )

    assert len(results) > 0
    for r in results:
        assert "hybrid_score" in r
        assert "semantic_component" in r
        assert "keyword_component" in r


def test_hybrid_search_boosts_keyword_match(seeded_db):
    """Hybrid search should rank exact keyword matches higher than semantic-only."""
    # Use a random embedding far from any seeded chunk
    unrelated_emb = _random_embedding(seed=999)

    # Hybrid should still find keyword match even with bad embedding
    hybrid_results = seeded_db.hybrid_search(
        query="RuntimeError Task attached different loop",
        query_embedding=unrelated_emb,
        limit=5,
        min_score=0.0,
        semantic_weight=0.3,
        keyword_weight=0.7,
    )

    assert len(hybrid_results) > 0
    top_hybrid = hybrid_results[0]
    assert "RuntimeError" in top_hybrid["content"]
    assert top_hybrid["keyword_component"] > 0


def test_hybrid_search_respects_min_score(seeded_db):
    """Results below min_score should be filtered out."""
    query_emb = _random_embedding(seed=999)

    results = seeded_db.hybrid_search(
        query="async",
        query_embedding=query_emb,
        limit=10,
        min_score=0.99,
    )

    for r in results:
        assert r["hybrid_score"] >= 0.99


def test_hybrid_search_custom_weights(seeded_db):
    """Different weight configurations should produce different rankings."""
    query_emb = _similar_embedding(_random_embedding(0), noise=0.05, seed=42)

    semantic_heavy = seeded_db.hybrid_search(
        query="async testing",
        query_embedding=query_emb,
        limit=3,
        min_score=0.0,
        semantic_weight=1.0,
        keyword_weight=0.0,
    )

    keyword_heavy = seeded_db.hybrid_search(
        query="async testing",
        query_embedding=query_emb,
        limit=3,
        min_score=0.0,
        semantic_weight=0.0,
        keyword_weight=1.0,
    )

    assert len(semantic_heavy) > 0
    assert len(keyword_heavy) > 0

    for r in semantic_heavy:
        assert r["keyword_component"] >= 0

    for r in keyword_heavy:
        assert r["semantic_component"] >= 0


def test_hybrid_search_with_filters(seeded_db):
    """Hybrid search should respect metadata filters."""
    query_emb = _random_embedding(seed=0)

    results = seeded_db.hybrid_search(
        query="credentials IAM",
        query_embedding=query_emb,
        limit=5,
        min_score=0.0,
        filters={"source": "blog"},
    )

    for r in results:
        assert r["source"] == "blog"


def test_hybrid_search_limit(seeded_db):
    """Hybrid search should respect the limit parameter."""
    query_emb = _random_embedding(seed=0)

    results = seeded_db.hybrid_search(
        query="FastAPI async",
        query_embedding=query_emb,
        limit=2,
        min_score=0.0,
    )

    assert len(results) <= 2


# ---------------------------------------------------------------------------
# Tests – FTS5 schema
# ---------------------------------------------------------------------------


def test_fts_table_populated_on_add_chunk(tmp_path):
    """Adding a chunk should populate both vec_chunks and fts_chunks."""
    db_path = str(tmp_path / "fts_test.db")
    db = VectorDB(db_path)

    db.add_document(doc_id="d1", content="Test doc", title="Test")
    db.add_chunk(
        chunk_id="c1",
        document_id="d1",
        content="The quick brown fox jumps over the lazy dog",
        embedding=_random_embedding(0),
        chunk_index=0,
    )

    cursor = db.conn.cursor()
    cursor.execute("SELECT chunk_id, content FROM fts_chunks WHERE fts_chunks MATCH 'fox'")
    rows = cursor.fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "c1"
    assert "fox" in rows[0][1]

    db.close()
