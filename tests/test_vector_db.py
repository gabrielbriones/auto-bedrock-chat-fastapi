"""Tests for vector database module."""

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

from auto_bedrock_chat_fastapi.vector_db import VectorDB


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    db = VectorDB(db_path)
    yield db

    # Cleanup
    db.close()
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def sample_embedding():
    """Generate a sample embedding vector."""
    return np.random.rand(1536).tolist()


@pytest.fixture
def sample_document_data():
    """Sample document data for testing."""
    return {
        "doc_id": "test-doc-001",
        "content": "This is a comprehensive test document about authentication and security best practices.",
        "title": "Authentication Guide",
        "source": "docs",
        "source_url": "https://example.com/docs/auth",
        "topic": "authentication",
        "date_published": "2026-01-08",
        "metadata": {"author": "Test Team", "version": "1.0"},
    }


class TestVectorDBInitialization:
    """Test database initialization and schema creation."""

    def test_db_creation(self, temp_db):
        """Test that database is created successfully."""
        assert temp_db.conn is not None
        assert Path(temp_db.db_path).exists()

    def test_schema_creation(self, temp_db):
        """Test that all tables are created."""
        cursor = temp_db.conn.cursor()

        # Check documents table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documents'")
        assert cursor.fetchone() is not None

        # Check chunks table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunks'")
        assert cursor.fetchone() is not None

        # Check vec_chunks virtual table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vec_chunks'")
        assert cursor.fetchone() is not None


class TestDocumentOperations:
    """Test document CRUD operations."""

    def test_add_document(self, temp_db, sample_document_data):
        """Test adding a document."""
        temp_db.add_document(**sample_document_data)

        doc = temp_db.get_document(sample_document_data["doc_id"])
        assert doc is not None
        assert doc["id"] == sample_document_data["doc_id"]
        assert doc["title"] == sample_document_data["title"]
        assert doc["source"] == sample_document_data["source"]
        assert doc["topic"] == sample_document_data["topic"]

    def test_get_nonexistent_document(self, temp_db):
        """Test getting a document that doesn't exist."""
        doc = temp_db.get_document("nonexistent-id")
        assert doc is None

    def test_update_document(self, temp_db, sample_document_data):
        """Test updating a document (replace)."""
        # Add original
        temp_db.add_document(**sample_document_data)

        # Update
        updated_data = sample_document_data.copy()
        updated_data["title"] = "Updated Authentication Guide"
        updated_data["content"] = "Updated content"
        temp_db.add_document(**updated_data)

        # Verify update
        doc = temp_db.get_document(sample_document_data["doc_id"])
        assert doc["title"] == "Updated Authentication Guide"
        assert doc["content"] == "Updated content"

    def test_delete_document(self, temp_db, sample_document_data, sample_embedding):
        """Test deleting a document and its chunks."""
        # Add document and chunk
        temp_db.add_document(**sample_document_data)
        temp_db.add_chunk(
            chunk_id="test-chunk-001",
            document_id=sample_document_data["doc_id"],
            content="Test chunk content",
            embedding=sample_embedding,
            chunk_index=0,
        )

        # Delete
        temp_db.delete_document(sample_document_data["doc_id"])

        # Verify deletion
        doc = temp_db.get_document(sample_document_data["doc_id"])
        assert doc is None


class TestChunkOperations:
    """Test chunk operations with embeddings."""

    def test_add_chunk(self, temp_db, sample_document_data, sample_embedding):
        """Test adding a chunk with embedding."""
        temp_db.add_document(**sample_document_data)

        temp_db.add_chunk(
            chunk_id="test-chunk-001",
            document_id=sample_document_data["doc_id"],
            content="Use API keys for authentication.",
            embedding=sample_embedding,
            chunk_index=0,
            start_char=0,
            end_char=35,
            metadata={"section": "intro"},
        )

        # Verify chunk was added
        cursor = temp_db.conn.cursor()
        cursor.execute("SELECT * FROM chunks WHERE id = ?", ("test-chunk-001",))
        chunk = cursor.fetchone()
        assert chunk is not None

    def test_add_multiple_chunks(self, temp_db, sample_document_data):
        """Test adding multiple chunks for one document."""
        temp_db.add_document(**sample_document_data)

        for i in range(3):
            embedding = np.random.rand(1536).tolist()
            temp_db.add_chunk(
                chunk_id=f"chunk-{i}",
                document_id=sample_document_data["doc_id"],
                content=f"Chunk {i} content",
                embedding=embedding,
                chunk_index=i,
            )

        # Verify all chunks
        cursor = temp_db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chunks WHERE document_id = ?", (sample_document_data["doc_id"],))
        count = cursor.fetchone()[0]
        assert count == 3


class TestSemanticSearch:
    """Test semantic similarity search functionality."""

    def test_basic_search(self, temp_db, sample_document_data, sample_embedding):
        """Test basic semantic search."""
        temp_db.add_document(**sample_document_data)
        temp_db.add_chunk(
            chunk_id="chunk-001",
            document_id=sample_document_data["doc_id"],
            content="Use API keys for authentication.",
            embedding=sample_embedding,
            chunk_index=0,
        )

        # Search with same embedding (should be perfect match)
        results = temp_db.semantic_search(sample_embedding, limit=1)

        assert len(results) == 1
        assert results[0]["chunk_id"] == "chunk-001"
        assert results[0]["similarity_score"] >= 0.99  # Should be very high

    def test_search_with_limit(self, temp_db, sample_document_data):
        """Test search result limiting."""
        temp_db.add_document(**sample_document_data)

        # Add 5 chunks
        for i in range(5):
            embedding = np.random.rand(1536).tolist()
            temp_db.add_chunk(
                chunk_id=f"chunk-{i}",
                document_id=sample_document_data["doc_id"],
                content=f"Content {i}",
                embedding=embedding,
                chunk_index=i,
            )

        # Search with limit=3
        query_embedding = np.random.rand(1536).tolist()
        results = temp_db.semantic_search(query_embedding, limit=3)

        assert len(results) <= 3

    def test_search_with_min_score(self, temp_db, sample_document_data, sample_embedding):
        """Test filtering by minimum similarity score."""
        temp_db.add_document(**sample_document_data)
        temp_db.add_chunk(
            chunk_id="chunk-001",
            document_id=sample_document_data["doc_id"],
            content="Test content",
            embedding=sample_embedding,
            chunk_index=0,
        )

        # Search with very high minimum score (should filter out random embeddings)
        random_embedding = np.random.rand(1536).tolist()
        results = temp_db.semantic_search(random_embedding, limit=10, min_score=0.99)

        # Unlikely to have >0.99 similarity with random embedding
        assert len(results) == 0 or results[0]["similarity_score"] >= 0.99

    def test_search_with_source_filter(self, temp_db):
        """Test filtering by document source."""
        # Add documents from different sources
        for source in ["docs", "blog", "faq"]:
            temp_db.add_document(
                doc_id=f"{source}-doc", content=f"Content from {source}", title=f"{source} title", source=source
            )
            embedding = np.random.rand(1536).tolist()
            temp_db.add_chunk(
                chunk_id=f"{source}-chunk",
                document_id=f"{source}-doc",
                content=f"Chunk from {source}",
                embedding=embedding,
                chunk_index=0,
            )

        # Search with source filter
        query_embedding = np.random.rand(1536).tolist()
        results = temp_db.semantic_search(query_embedding, limit=10, filters={"source": "docs"})

        # All results should be from "docs" source
        for result in results:
            assert result["source"] == "docs"

    def test_search_with_topic_filter(self, temp_db):
        """Test filtering by topic."""
        topics = ["authentication", "api", "deployment"]

        for topic in topics:
            temp_db.add_document(
                doc_id=f"{topic}-doc",
                content=f"Content about {topic}",
                title=f"{topic} guide",
                source="docs",
                topic=topic,
            )
            embedding = np.random.rand(1536).tolist()
            temp_db.add_chunk(
                chunk_id=f"{topic}-chunk",
                document_id=f"{topic}-doc",
                content=f"Details about {topic}",
                embedding=embedding,
                chunk_index=0,
            )

        # Search with topic filter
        query_embedding = np.random.rand(1536).tolist()
        results = temp_db.semantic_search(query_embedding, limit=10, filters={"topic": "authentication"})

        # All results should be about authentication
        for result in results:
            assert result["topic"] == "authentication"

    def test_search_with_date_filters(self, temp_db):
        """Test filtering by date range."""
        dates = ["2025-01-01", "2025-06-01", "2026-01-01"]

        for date in dates:
            temp_db.add_document(
                doc_id=f"doc-{date}",
                content=f"Content from {date}",
                title=f"Document {date}",
                source="docs",
                date_published=date,
            )
            embedding = np.random.rand(1536).tolist()
            temp_db.add_chunk(
                chunk_id=f"chunk-{date}",
                document_id=f"doc-{date}",
                content=f"Content from {date}",
                embedding=embedding,
                chunk_index=0,
            )

        # Search with date_after filter
        query_embedding = np.random.rand(1536).tolist()
        results = temp_db.semantic_search(query_embedding, limit=10, filters={"date_after": "2025-12-01"})

        # Should only return 2026 document
        assert len(results) == 1
        assert results[0]["date_published"] == "2026-01-01"


class TestMetadataOperations:
    """Test metadata and listing operations."""

    def test_list_sources(self, temp_db):
        """Test listing all sources with counts."""
        # Add documents from different sources
        sources = {"docs": 3, "blog": 2, "faq": 1}

        for source, count in sources.items():
            for i in range(count):
                temp_db.add_document(doc_id=f"{source}-{i}", content=f"Content {i}", title=f"Title {i}", source=source)

        # Get sources list
        source_list = temp_db.list_sources()

        # Verify counts
        source_dict = {s["source"]: s["count"] for s in source_list}
        assert source_dict["docs"] == 3
        assert source_dict["blog"] == 2
        assert source_dict["faq"] == 1

    def test_list_topics(self, temp_db):
        """Test listing all topics with counts."""
        topics = {"authentication": 2, "api": 3, "deployment": 1}

        for topic, count in topics.items():
            for i in range(count):
                temp_db.add_document(
                    doc_id=f"{topic}-{i}", content=f"Content {i}", title=f"Title {i}", source="docs", topic=topic
                )

        # Get topics list
        topic_list = temp_db.list_topics()

        # Verify counts
        topic_dict = {t["topic"]: t["count"] for t in topic_list}
        assert topic_dict["authentication"] == 2
        assert topic_dict["api"] == 3
        assert topic_dict["deployment"] == 1


class TestDatabaseStats:
    """Test database statistics functionality."""

    def test_get_stats_empty(self, temp_db):
        """Test stats on empty database."""
        stats = temp_db.get_stats()

        assert stats["documents"] == 0
        assert stats["chunks"] == 0
        assert stats["vectors"] == 0
        assert stats["db_size_bytes"] > 0  # File exists even if empty

    def test_get_stats_with_data(self, temp_db, sample_document_data):
        """Test stats with data."""
        # Add data
        temp_db.add_document(**sample_document_data)

        for i in range(3):
            embedding = np.random.rand(1536).tolist()
            temp_db.add_chunk(
                chunk_id=f"chunk-{i}",
                document_id=sample_document_data["doc_id"],
                content=f"Content {i}",
                embedding=embedding,
                chunk_index=i,
            )

        stats = temp_db.get_stats()

        assert stats["documents"] == 1
        assert stats["chunks"] == 3
        assert stats["vectors"] == 3
        assert stats["db_size_bytes"] > 0


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_search(self, temp_db):
        """Test search on empty database."""
        embedding = np.random.rand(1536).tolist()
        results = temp_db.semantic_search(embedding, limit=10)

        assert results == []

    def test_document_with_no_chunks(self, temp_db, sample_document_data):
        """Test document without any chunks."""
        temp_db.add_document(**sample_document_data)

        doc = temp_db.get_document(sample_document_data["doc_id"])
        assert doc is not None

        # Stats should show document but no chunks
        stats = temp_db.get_stats()
        assert stats["documents"] == 1
        assert stats["chunks"] == 0

    def test_special_characters_in_content(self, temp_db, sample_embedding):
        """Test handling of special characters."""
        temp_db.add_document(
            doc_id="special-doc",
            content="Content with 'quotes', \"double quotes\", and\nnewlines\ttabs",
            title="Special Characters Test",
            source="test",
        )

        temp_db.add_chunk(
            chunk_id="special-chunk",
            document_id="special-doc",
            content="Content with 'special' \"chars\"",
            embedding=sample_embedding,
            chunk_index=0,
        )

        doc = temp_db.get_document("special-doc")
        assert doc is not None
        assert "quotes" in doc["content"]
