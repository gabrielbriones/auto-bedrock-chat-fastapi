"""
PostgreSQL + pgvector KB Store.

This module provides the :class:`PgVectorKBStore` implementation of
:class:`~auto_bedrock_chat_fastapi.kb_store_base.BaseKBStore`, backed by
PostgreSQL with the ``pgvector`` extension for cosine-similarity search
and PostgreSQL's built-in full-text search (``tsvector`` / ``ts_rank``)
for BM25-style keyword matching.

Install the optional dependencies::

    pip install auto-bedrock-chat-fastapi[postgres]
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .kb_store_base import BaseKBStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-check for the optional ``psycopg`` dependency so that callers get a
# clear error instead of a confusing ImportError deep inside init.
# ---------------------------------------------------------------------------

_MISSING_DEPS_MSG = (
    "The 'pgvector' KB backend requires the optional PostgreSQL packages. "
    "Install them with:  pip install auto-bedrock-chat-fastapi[postgres]"
)


def _import_psycopg():
    """Return (psycopg, ConnectionPool, register_vector) or raise ImportError."""
    try:
        import psycopg
        from pgvector.psycopg import register_vector
        from psycopg_pool import ConnectionPool

        return psycopg, ConnectionPool, register_vector
    except ImportError as exc:
        raise ImportError(_MISSING_DEPS_MSG) from exc


class PgVectorKBStore(BaseKBStore):
    """PostgreSQL + pgvector knowledge-base store.

    Parameters
    ----------
    connection_url:
        A ``libpq``-style connection URL, e.g.
        ``postgresql://user:pass@host:5432/dbname``.
    pool_size:
        Maximum number of connections kept in the pool (default ``5``).
    embedding_dimensions:
        Width of the ``vector`` column.  Must match the embedding model
        output (default ``1536`` — Amazon Titan Embed Text v1).
    """

    def __init__(
        self,
        connection_url: str,
        pool_size: int = 5,
        embedding_dimensions: int = 1536,
    ):
        psycopg, ConnectionPool, register_vector = _import_psycopg()

        self._embedding_dimensions = embedding_dimensions
        self._connection_url = connection_url

        # Create a thread-safe connection pool.
        self._pool: Any = ConnectionPool(
            conninfo=connection_url,
            min_size=1,
            max_size=pool_size,
            open=True,
            kwargs={"autocommit": False},
        )

        # Register pgvector types on every new connection inside the pool.
        self._register_vector = register_vector

        self._init_schema()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _get_conn(self):
        """Borrow a connection from the pool (context-manager)."""
        return self._pool.connection()

    def _register_on(self, conn):
        """Register pgvector type on *conn* (idempotent)."""
        self._register_vector(conn)

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    def _init_schema(self):
        """Create extensions, tables, and indexes if they don't exist."""
        dim = self._embedding_dimensions

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # pgvector extension — must be created BEFORE registering the type
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            self._register_on(conn)
            with conn.cursor() as cur:
                # Documents table
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS documents (
                        id          TEXT PRIMARY KEY,
                        content     TEXT NOT NULL,
                        title       TEXT,
                        source      TEXT,
                        source_url  TEXT,
                        topic       TEXT,
                        date_published TEXT,
                        metadata    TEXT,
                        created_at  TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )

                # Chunks table with embedding column
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS chunks (
                        id           TEXT PRIMARY KEY,
                        document_id  TEXT NOT NULL REFERENCES documents(id),
                        content      TEXT NOT NULL,
                        chunk_index  INTEGER NOT NULL,
                        start_char   INTEGER,
                        end_char     INTEGER,
                        metadata     TEXT,
                        embedding    vector({dim}),
                        content_tsv  tsvector
                            GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
                        created_at   TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )

                # Indexes
                cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_topic ON documents(topic)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_date ON documents(date_published)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)")

                # HNSW index for cosine distance
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_chunks_embedding
                    ON chunks USING hnsw (embedding vector_cosine_ops)
                    """
                )

                # GIN index for full-text search
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_fts ON chunks USING gin(content_tsv)")

            conn.commit()

        logger.info("PgVectorKBStore schema initialized (dimensions=%d)", dim)

    # ------------------------------------------------------------------
    # Document operations
    # ------------------------------------------------------------------

    def add_document(
        self,
        doc_id: str,
        content: str,
        title: Optional[str] = None,
        source: Optional[str] = None,
        source_url: Optional[str] = None,
        topic: Optional[str] = None,
        date_published: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        meta_json = json.dumps(metadata) if metadata else None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents
                        (id, content, title, source, source_url, topic, date_published, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        content        = EXCLUDED.content,
                        title          = EXCLUDED.title,
                        source         = EXCLUDED.source,
                        source_url     = EXCLUDED.source_url,
                        topic          = EXCLUDED.topic,
                        date_published = EXCLUDED.date_published,
                        metadata       = EXCLUDED.metadata
                    """,
                    (doc_id, content, title, source, source_url, topic, date_published, meta_json),
                )
            conn.commit()

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, content, title, source, source_url, topic,
                           date_published, metadata, created_at
                    FROM documents WHERE id = %s
                    """,
                    (doc_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "content": row[1],
                    "title": row[2],
                    "source": row[3],
                    "source_url": row[4],
                    "topic": row[5],
                    "date_published": row[6],
                    "metadata": json.loads(row[7]) if row[7] else {},
                    "created_at": str(row[8]) if row[8] else None,
                }

    def delete_document(self, doc_id: str) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # Delete chunks first (FK constraint)
                cur.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
                cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
            conn.commit()

    def list_sources(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source, COUNT(*) AS count
                    FROM documents
                    WHERE source IS NOT NULL
                    GROUP BY source
                    ORDER BY count DESC
                    """
                )
                return [{"source": row[0], "count": row[1]} for row in cur.fetchall()]

    def list_topics(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT topic, COUNT(*) AS count
                    FROM documents
                    WHERE topic IS NOT NULL
                    GROUP BY topic
                    ORDER BY count DESC
                    """
                )
                return [{"topic": row[0], "count": row[1]} for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Chunk / embedding operations
    # ------------------------------------------------------------------

    def add_chunk(
        self,
        chunk_id: str,
        document_id: str,
        content: str,
        embedding: List[float],
        chunk_index: int,
        start_char: Optional[int] = None,
        end_char: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        meta_json = json.dumps(metadata) if metadata else None
        # Convert to a string pgvector can parse: '[0.1,0.2,…]'
        emb_str = "[" + ",".join(str(v) for v in embedding) + "]"

        with self._get_conn() as conn:
            self._register_on(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chunks
                        (id, document_id, content, chunk_index, start_char, end_char,
                         metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (id) DO UPDATE SET
                        document_id = EXCLUDED.document_id,
                        content     = EXCLUDED.content,
                        chunk_index = EXCLUDED.chunk_index,
                        start_char  = EXCLUDED.start_char,
                        end_char    = EXCLUDED.end_char,
                        metadata    = EXCLUDED.metadata,
                        embedding   = EXCLUDED.embedding
                    """,
                    (chunk_id, document_id, content, chunk_index, start_char, end_char, meta_json, emb_str),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Search operations
    # ------------------------------------------------------------------

    def _build_filter_clause(self, filters: Optional[Dict[str, Any]]) -> tuple[str, list]:
        """Return (WHERE fragment, params) for common document-level filters."""
        if not filters:
            return "", []
        clauses: list[str] = []
        params: list[Any] = []
        if filters.get("source"):
            clauses.append("d.source = %s")
            params.append(filters["source"])
        if filters.get("topic"):
            clauses.append("d.topic = %s")
            params.append(filters["topic"])
        if filters.get("date_after"):
            clauses.append("d.date_published >= %s")
            params.append(filters["date_after"])
        if filters.get("date_before"):
            clauses.append("d.date_published <= %s")
            params.append(filters["date_before"])
        fragment = " AND ".join(clauses)
        if fragment:
            fragment = " AND " + fragment
        return fragment, params

    def semantic_search(
        self,
        query_embedding: List[float],
        limit: int = 3,
        min_score: float = 0.0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        filter_sql, filter_params = self._build_filter_clause(filters)
        emb_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        sql = f"""
            SELECT
                c.id            AS chunk_id,
                c.content,
                c.chunk_index,
                d.id            AS document_id,
                d.title,
                d.source,
                d.source_url,
                d.topic,
                d.date_published,
                d.metadata      AS doc_metadata,
                (c.embedding <=> %s::vector) AS distance
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE c.embedding IS NOT NULL
            {filter_sql}
            ORDER BY distance ASC
            LIMIT %s
        """

        params: list[Any] = [emb_str, *filter_params, limit]

        with self._get_conn() as conn:
            self._register_on(conn)
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        results: list[Dict[str, Any]] = []
        for row in rows:
            similarity = 1.0 - row[10]
            if similarity < min_score:
                continue
            results.append(
                {
                    "chunk_id": row[0],
                    "content": row[1],
                    "chunk_index": row[2],
                    "document_id": row[3],
                    "title": row[4],
                    "source": row[5],
                    "source_url": row[6],
                    "topic": row[7],
                    "date_published": row[8],
                    "metadata": json.loads(row[9]) if row[9] else {},
                    "similarity_score": round(similarity, 4),
                }
            )
        return results

    def keyword_search(
        self,
        query: str,
        limit: int = 3,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        filter_sql, filter_params = self._build_filter_clause(filters)

        sql = f"""
            SELECT
                c.id            AS chunk_id,
                c.content,
                c.chunk_index,
                d.id            AS document_id,
                d.title,
                d.source,
                d.source_url,
                d.topic,
                d.date_published,
                d.metadata      AS doc_metadata,
                ts_rank(c.content_tsv, plainto_tsquery('english', %s)) AS rank
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE c.content_tsv @@ plainto_tsquery('english', %s)
            {filter_sql}
            ORDER BY rank DESC
            LIMIT %s
        """

        params: list[Any] = [query, query, *filter_params, limit]

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        results: list[Dict[str, Any]] = []
        for row in rows:
            raw_rank = row[10]
            normalized = min(1.0, raw_rank / 10.0)
            results.append(
                {
                    "chunk_id": row[0],
                    "content": row[1],
                    "chunk_index": row[2],
                    "document_id": row[3],
                    "title": row[4],
                    "source": row[5],
                    "source_url": row[6],
                    "topic": row[7],
                    "date_published": row[8],
                    "metadata": json.loads(row[9]) if row[9] else {},
                    "keyword_score": round(normalized, 4),
                }
            )
        return results

    def hybrid_search(
        self,
        query: str,
        query_embedding: List[float],
        limit: int = 3,
        min_score: float = 0.0,
        filters: Optional[Dict[str, Any]] = None,
        semantic_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> List[Dict[str, Any]]:
        candidate_limit = limit * 3

        semantic_results = self.semantic_search(
            query_embedding=query_embedding,
            limit=candidate_limit,
            min_score=0.0,
            filters=filters,
        )
        bm25_results = self.keyword_search(
            query=query,
            limit=candidate_limit,
            filters=filters,
        )

        combined: Dict[str, Dict[str, Any]] = {}

        for r in semantic_results:
            cid = r["chunk_id"]
            combined[cid] = r.copy()
            combined[cid]["semantic_score"] = r["similarity_score"]
            combined[cid]["keyword_score"] = 0.0

        for r in bm25_results:
            cid = r["chunk_id"]
            if cid in combined:
                combined[cid]["keyword_score"] = r["keyword_score"]
            else:
                combined[cid] = r.copy()
                combined[cid]["semantic_score"] = 0.0
                combined[cid]["keyword_score"] = r["keyword_score"]

        hybrid: list[Dict[str, Any]] = []
        for data in combined.values():
            sem = data.get("semantic_score", 0.0)
            kw = data.get("keyword_score", 0.0)
            score = semantic_weight * sem + keyword_weight * kw
            if score < min_score:
                continue
            data["similarity_score"] = round(score, 4)
            data["hybrid_score"] = round(score, 4)
            data["semantic_component"] = round(sem, 4)
            data["keyword_component"] = round(kw, 4)
            hybrid.append(data)

        hybrid.sort(key=lambda x: x["hybrid_score"], reverse=True)
        return hybrid[:limit]

    # ------------------------------------------------------------------
    # Lifecycle / stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM documents")
                doc_count = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM chunks")
                chunk_count = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL")
                vector_count = cur.fetchone()[0]

        return {
            "documents": doc_count,
            "chunks": chunk_count,
            "vectors": vector_count,
        }

    def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            self._pool.close()
            self._pool = None
