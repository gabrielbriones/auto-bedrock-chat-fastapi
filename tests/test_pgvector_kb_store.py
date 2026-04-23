"""Tests for PgVectorKBStore — unit tests with mocked PostgreSQL."""

import json
from unittest.mock import MagicMock, patch

import pytest

from auto_bedrock_chat_fastapi.kb_store_base import BaseKBStore, create_kb_store

# ---------------------------------------------------------------------------
# Helper: build a fake PgVectorKBStore that skips real DB connections
# ---------------------------------------------------------------------------


def _make_store(embedding_dimensions=1536):
    """Instantiate PgVectorKBStore with a fully-mocked connection pool."""
    with patch("auto_bedrock_chat_fastapi.pgvector_kb_store._import_psycopg") as mock_import:
        mock_psycopg = MagicMock()
        mock_pool_cls = MagicMock()
        mock_register = MagicMock()
        mock_import.return_value = (mock_psycopg, mock_pool_cls, mock_register)

        # Make the pool context-manager return a mock connection
        mock_pool = MagicMock()
        mock_pool_cls.return_value = mock_pool

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        from auto_bedrock_chat_fastapi.pgvector_kb_store import PgVectorKBStore

        store = PgVectorKBStore(
            connection_url="postgresql://test:test@localhost/test",
            pool_size=2,
            embedding_dimensions=embedding_dimensions,
        )

    return store, mock_pool, mock_conn, mock_cursor


# ---------------------------------------------------------------------------
# Test: class conforms to BaseKBStore
# ---------------------------------------------------------------------------


class TestPgVectorConformance:
    def test_isinstance(self):
        store, *_ = _make_store()
        assert isinstance(store, BaseKBStore)

    def test_has_all_abstract_methods(self):
        from auto_bedrock_chat_fastapi.pgvector_kb_store import PgVectorKBStore

        for name in (
            "add_document",
            "add_chunk",
            "semantic_search",
            "keyword_search",
            "hybrid_search",
            "get_document",
            "delete_document",
            "list_sources",
            "list_topics",
            "get_stats",
            "close",
        ):
            assert hasattr(PgVectorKBStore, name), f"Missing method: {name}"


# ---------------------------------------------------------------------------
# Test: schema bootstrap calls CREATE EXTENSION & CREATE TABLE
# ---------------------------------------------------------------------------


class TestSchemaInit:
    def test_creates_extension_and_tables(self):
        store, mock_pool, mock_conn, mock_cursor = _make_store()

        # Collect all SQL executed during __init__._init_schema
        executed_sqls = [c.args[0].strip() for c in mock_cursor.execute.call_args_list if c.args]
        sql_blob = " ".join(executed_sqls).lower()

        assert "create extension if not exists vector" in sql_blob
        assert "create table if not exists documents" in sql_blob
        assert "create table if not exists chunks" in sql_blob
        assert "create index" in sql_blob

    def test_custom_dimensions(self):
        store, _, _, mock_cursor = _make_store(embedding_dimensions=384)
        executed_sqls = [c.args[0] for c in mock_cursor.execute.call_args_list if c.args]
        combined = " ".join(executed_sqls)
        assert "vector(384)" in combined


# ---------------------------------------------------------------------------
# Test: add_document upserts
# ---------------------------------------------------------------------------


class TestAddDocument:
    def test_insert_on_conflict(self):
        store, mock_pool, mock_conn, mock_cursor = _make_store()
        mock_cursor.reset_mock()

        store.add_document(
            doc_id="doc-1",
            content="Hello world",
            title="Test",
            source="web",
            source_url="https://example.com",
            topic="testing",
            date_published="2026-01-01",
            metadata={"key": "value"},
        )

        sql = mock_cursor.execute.call_args[0][0].lower()
        assert "insert into documents" in sql
        assert "on conflict" in sql

        params = mock_cursor.execute.call_args[0][1]
        assert params[0] == "doc-1"
        assert params[1] == "Hello world"
        # metadata should be JSON-serialized
        assert json.loads(params[7]) == {"key": "value"}


# ---------------------------------------------------------------------------
# Test: add_chunk upserts with vector cast
# ---------------------------------------------------------------------------


class TestAddChunk:
    def test_insert_with_vector_cast(self):
        store, mock_pool, mock_conn, mock_cursor = _make_store()
        mock_cursor.reset_mock()

        embedding = [0.1, 0.2, 0.3]
        store.add_chunk(
            chunk_id="chunk-1",
            document_id="doc-1",
            content="chunk text",
            embedding=embedding,
            chunk_index=0,
            start_char=0,
            end_char=10,
            metadata={"section": "intro"},
        )

        sql = mock_cursor.execute.call_args[0][0].lower()
        assert "insert into chunks" in sql
        assert "on conflict" in sql
        assert "::vector" in sql

        params = mock_cursor.execute.call_args[0][1]
        assert params[0] == "chunk-1"
        # Last param is the embedding string
        assert params[-1] == "[0.1,0.2,0.3]"


# ---------------------------------------------------------------------------
# Test: get_document
# ---------------------------------------------------------------------------


class TestGetDocument:
    def test_found(self):
        store, _, mock_conn, mock_cursor = _make_store()
        mock_cursor.reset_mock()
        mock_cursor.fetchone.return_value = (
            "doc-1",
            "content",
            "Title",
            "web",
            "https://x.com",
            "topic",
            "2026-01-01",
            '{"k":"v"}',
            "2026-01-01T00:00:00+00:00",
        )

        doc = store.get_document("doc-1")
        assert doc is not None
        assert doc["id"] == "doc-1"
        assert doc["metadata"] == {"k": "v"}

    def test_not_found(self):
        store, _, mock_conn, mock_cursor = _make_store()
        mock_cursor.reset_mock()
        mock_cursor.fetchone.return_value = None

        doc = store.get_document("missing")
        assert doc is None


# ---------------------------------------------------------------------------
# Test: delete_document cascades
# ---------------------------------------------------------------------------


class TestDeleteDocument:
    def test_deletes_chunks_then_document(self):
        store, _, mock_conn, mock_cursor = _make_store()
        mock_cursor.reset_mock()

        store.delete_document("doc-1")

        calls = [c.args[0].strip().lower() for c in mock_cursor.execute.call_args_list if c.args]
        # Should delete chunks first, then document
        assert any("delete from chunks" in s for s in calls)
        assert any("delete from documents" in s for s in calls)


# ---------------------------------------------------------------------------
# Test: semantic_search formats results
# ---------------------------------------------------------------------------


class TestSemanticSearch:
    def test_returns_formatted_results(self):
        store, _, mock_conn, mock_cursor = _make_store()
        mock_cursor.reset_mock()
        mock_cursor.fetchall.return_value = [
            (
                "chunk-1",  # chunk_id
                "text here",  # content
                0,  # chunk_index
                "doc-1",  # document_id
                "Title",  # title
                "web",  # source
                "https://x.com",  # source_url
                "topic1",  # topic
                "2026-01-01",  # date_published
                None,  # doc_metadata
                0.15,  # distance
            )
        ]

        results = store.semantic_search(
            query_embedding=[0.1, 0.2, 0.3],
            limit=5,
            min_score=0.0,
        )

        assert len(results) == 1
        r = results[0]
        assert r["chunk_id"] == "chunk-1"
        assert r["similarity_score"] == round(1.0 - 0.15, 4)

    def test_min_score_filtering(self):
        store, _, mock_conn, mock_cursor = _make_store()
        mock_cursor.reset_mock()
        # distance = 0.95 => similarity = 0.05 (below min_score=0.5)
        mock_cursor.fetchall.return_value = [("c1", "t", 0, "d1", None, None, None, None, None, None, 0.95)]

        results = store.semantic_search([0.1], limit=5, min_score=0.5)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Test: keyword_search
# ---------------------------------------------------------------------------


class TestKeywordSearch:
    def test_returns_formatted_results(self):
        store, _, mock_conn, mock_cursor = _make_store()
        mock_cursor.reset_mock()
        mock_cursor.fetchall.return_value = [
            ("c1", "text", 0, "d1", "T", "web", "https://x.com", "t", "2026-01-01", None, 5.0)
        ]

        results = store.keyword_search(query="text", limit=3)
        assert len(results) == 1
        assert results[0]["keyword_score"] == round(min(1.0, 5.0 / 10.0), 4)


# ---------------------------------------------------------------------------
# Test: hybrid_search combines both
# ---------------------------------------------------------------------------


class TestHybridSearch:
    def test_combines_results(self):
        store, _, mock_conn, mock_cursor = _make_store()
        mock_cursor.reset_mock()

        # First call = semantic_search fetchall, second = keyword_search fetchall
        mock_cursor.fetchall.side_effect = [
            # semantic results
            [("c1", "text", 0, "d1", "T", "web", None, None, None, None, 0.2)],
            # keyword results
            [("c1", "text", 0, "d1", "T", "web", None, None, None, None, 5.0)],
        ]

        results = store.hybrid_search(
            query="test",
            query_embedding=[0.1, 0.2],
            limit=5,
            semantic_weight=0.7,
            keyword_weight=0.3,
        )

        assert len(results) == 1
        r = results[0]
        expected_sem = 1.0 - 0.2  # 0.8
        expected_kw = min(1.0, 5.0 / 10.0)  # 0.5
        expected_hybrid = 0.7 * expected_sem + 0.3 * expected_kw
        assert r["hybrid_score"] == round(expected_hybrid, 4)


# ---------------------------------------------------------------------------
# Test: get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_returns_counts(self):
        store, _, mock_conn, mock_cursor = _make_store()
        mock_cursor.reset_mock()
        mock_cursor.fetchone.side_effect = [(10,), (50,), (48,)]

        stats = store.get_stats()
        assert stats == {"documents": 10, "chunks": 50, "vectors": 48}


# ---------------------------------------------------------------------------
# Test: close
# ---------------------------------------------------------------------------


class TestClose:
    def test_closes_pool(self):
        store, mock_pool, _, _ = _make_store()
        store.close()
        mock_pool.close.assert_called_once()

    def test_close_idempotent(self):
        store, mock_pool, _, _ = _make_store()
        store.close()
        store.close()  # should not raise


# ---------------------------------------------------------------------------
# Test: list_sources / list_topics
# ---------------------------------------------------------------------------


class TestListOperations:
    def test_list_sources(self):
        store, _, _, mock_cursor = _make_store()
        mock_cursor.reset_mock()
        mock_cursor.fetchall.return_value = [("web", 5), ("docs", 3)]

        sources = store.list_sources()
        assert sources == [{"source": "web", "count": 5}, {"source": "docs", "count": 3}]

    def test_list_topics(self):
        store, _, _, mock_cursor = _make_store()
        mock_cursor.reset_mock()
        mock_cursor.fetchall.return_value = [("auth", 4), ("api", 2)]

        topics = store.list_topics()
        assert topics == [{"topic": "auth", "count": 4}, {"topic": "api", "count": 2}]


# ---------------------------------------------------------------------------
# Test: factory creates pgvector store
# ---------------------------------------------------------------------------


class TestFactory:
    def _make_config(self, **overrides):
        from auto_bedrock_chat_fastapi.config import ChatConfig

        defaults = {
            "BEDROCK_MODEL_ID": "anthropic.claude-sonnet-4-5-20250929-v1:0",
            "AWS_REGION": "us-east-1",
            "BEDROCK_KB_STORAGE_TYPE": "pgvector",
            "BEDROCK_KB_POSTGRES_URL": "postgresql://test:test@localhost/testdb",
        }
        defaults.update(overrides)
        return ChatConfig(**defaults)

    def test_factory_creates_pgvector(self):
        config = self._make_config()

        with patch("auto_bedrock_chat_fastapi.pgvector_kb_store._import_psycopg") as mock_import:
            mock_psycopg = MagicMock()
            mock_pool_cls = MagicMock()
            mock_register = MagicMock()
            mock_import.return_value = (mock_psycopg, mock_pool_cls, mock_register)

            mock_pool = MagicMock()
            mock_pool_cls.return_value = mock_pool
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

            store = create_kb_store(config)

            from auto_bedrock_chat_fastapi.pgvector_kb_store import PgVectorKBStore

            assert isinstance(store, PgVectorKBStore)
            assert isinstance(store, BaseKBStore)
            store.close()

    def test_factory_pgvector_missing_url_raises(self):
        config = self._make_config(BEDROCK_KB_POSTGRES_URL=None)
        with pytest.raises(ValueError, match="BEDROCK_KB_POSTGRES_URL"):
            create_kb_store(config)

    def test_factory_passes_pool_size(self):
        config = self._make_config(BEDROCK_KB_POSTGRES_POOL_SIZE=10)

        with patch("auto_bedrock_chat_fastapi.pgvector_kb_store._import_psycopg") as mock_import:
            mock_psycopg = MagicMock()
            mock_pool_cls = MagicMock()
            mock_register = MagicMock()
            mock_import.return_value = (mock_psycopg, mock_pool_cls, mock_register)

            mock_pool = MagicMock()
            mock_pool_cls.return_value = mock_pool
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

            store = create_kb_store(config)

            # Verify pool was created with max_size=10
            mock_pool_cls.assert_called_once()
            kwargs = mock_pool_cls.call_args
            assert kwargs.kwargs.get("max_size") == 10
            store.close()

    def test_factory_passes_dimensions(self):
        config = self._make_config(BEDROCK_KB_EMBEDDING_DIMENSIONS=384)

        with patch("auto_bedrock_chat_fastapi.pgvector_kb_store._import_psycopg") as mock_import:
            mock_psycopg = MagicMock()
            mock_pool_cls = MagicMock()
            mock_register = MagicMock()
            mock_import.return_value = (mock_psycopg, mock_pool_cls, mock_register)

            mock_pool = MagicMock()
            mock_pool_cls.return_value = mock_pool
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

            store = create_kb_store(config)
            assert store._embedding_dimensions == 384
            store.close()


# ---------------------------------------------------------------------------
# Test: missing dependency gives a clear error
# ---------------------------------------------------------------------------


class TestMissingDependency:
    def test_import_error_message(self):
        with patch.dict("sys.modules", {"psycopg": None}):
            # Force re-import failure
            with patch(
                "auto_bedrock_chat_fastapi.pgvector_kb_store._import_psycopg",
                side_effect=ImportError("The 'pgvector' KB backend requires"),
            ):
                with pytest.raises(ImportError, match="pgvector.*KB backend"):
                    from auto_bedrock_chat_fastapi.pgvector_kb_store import PgVectorKBStore

                    PgVectorKBStore("postgresql://x:x@localhost/x")
