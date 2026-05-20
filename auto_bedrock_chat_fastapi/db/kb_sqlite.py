"""
SQLite KB Store \u2014 vector database backed by SQLite + sqlite-vec.

Implements :class:`~auto_bedrock_chat_fastapi.db.kb_base.BaseKBStore` using
SQLite + sqlite-vec (cosine similarity) + FTS5 (BM25 keyword search).
"""

import functools
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import sqlite_vec

from ..exceptions import KBDocumentNotFoundError
from ..models import KBDocument, KBDocumentListFilters
from .kb_base import BaseKBStore


def _locked(func):
    """Serialize access to ``self.conn`` via ``self._lock``.

    ``SQLiteKBStore`` shares a single ``sqlite3.Connection`` with
    ``check_same_thread=False`` across multiple worker threads (the
    admin routes wrap every call in ``asyncio.to_thread``). Without
    explicit serialization concurrent admin traffic can interleave
    transactions on the same connection and trigger "database is
    locked" / corruption. The lock is an :class:`threading.RLock` so a
    decorated method may call another decorated method without
    deadlocking.
    """

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return func(self, *args, **kwargs)

    return wrapper


class SQLiteKBStore(BaseKBStore):
    """SQLite-backed knowledge-base store (sqlite-vec + FTS5)."""

    def __init__(self, db_path: str = "knowledge_base.db"):
        """
        Initialize vector database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        # RLock so decorated methods may call other decorated methods
        # without deadlocking. All access to ``self.conn`` is serialized
        # through ``@_locked``; ``_init_schema`` is invoked from
        # ``__init__`` before any concurrent caller can exist and so
        # runs without the lock.
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.enable_load_extension(True)

        # Load sqlite-vec extension
        sqlite_vec.load(self.conn)

        # Initialize database schema
        self._init_schema()

    def _init_schema(self):
        """Create necessary tables and indexes."""
        cursor = self.conn.cursor()

        # Documents table with metadata
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                title TEXT,
                source TEXT,
                source_url TEXT,
                topic TEXT,
                date_published TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Chunks table (documents split into smaller pieces)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                content TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                start_char INTEGER,
                end_char INTEGER,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (document_id) REFERENCES documents(id)
            )
        """
        )

        # Virtual table for vector similarity search
        cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
                chunk_id TEXT PRIMARY KEY,
                embedding FLOAT[1536]
            )
        """
        )

        # Virtual table for full-text search (BM25)
        cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(
                chunk_id UNINDEXED,
                content,
                tokenize='porter unicode61'
            )
        """
        )

        # Backfill FTS5 index for any chunks not yet indexed
        cursor.execute(
            """
            INSERT INTO fts_chunks (chunk_id, content)
            SELECT c.id, c.content
            FROM chunks c
            WHERE c.id NOT IN (
                SELECT chunk_id FROM fts_chunks
            )
        """
        )
        backfilled = cursor.rowcount
        if backfilled > 0:
            import logging

            logging.getLogger(__name__).info(f"Backfilled {backfilled} chunks into FTS5 index")

        # Create indexes for faster filtering
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_documents_source
            ON documents(source)
        """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_documents_topic
            ON documents(topic)
        """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_documents_date
            ON documents(date_published)
        """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chunks_document
            ON chunks(document_id)
        """
        )

        self.conn.commit()

    @_locked
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
        """
        Add a document to the database.

        Args:
            doc_id: Unique document identifier
            content: Full document content
            title: Document title
            source: Source type (e.g., "blog", "docs", "faq")
            source_url: Original URL
            topic: Topic/category
            date_published: Publication date (ISO format)
            metadata: Additional metadata as dict
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO documents
            (id, content, title, source, source_url, topic, date_published, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                doc_id,
                content,
                title,
                source,
                source_url,
                topic,
                date_published,
                json.dumps(metadata) if metadata else None,
            ),
        )
        self.conn.commit()

    @_locked
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
        """
        Add a chunk with its embedding to the database.

        Args:
            chunk_id: Unique chunk identifier
            document_id: Parent document ID
            content: Chunk content
            embedding: Vector embedding (1536 dimensions for OpenAI)
            chunk_index: Index of chunk in document
            start_char: Starting character position in original document
            end_char: Ending character position in original document
            metadata: Additional metadata
        """
        cursor = self.conn.cursor()

        # Add chunk metadata
        cursor.execute(
            """
            INSERT OR REPLACE INTO chunks
            (id, document_id, content, chunk_index, start_char, end_char, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                chunk_id,
                document_id,
                content,
                chunk_index,
                start_char,
                end_char,
                json.dumps(metadata) if metadata else None,
            ),
        )

        # Add embedding vector.
        # NOTE: sqlite-vec's vec0 virtual tables do NOT support
        # `INSERT OR REPLACE` — attempting it raises
        # `UNIQUE constraint failed on vec_chunks primary key`.
        # Delete-then-insert is the supported upsert pattern.
        embedding_bytes = np.array(embedding, dtype=np.float32).tobytes()
        cursor.execute("DELETE FROM vec_chunks WHERE chunk_id = ?", (chunk_id,))
        cursor.execute(
            """
            INSERT INTO vec_chunks (chunk_id, embedding)
            VALUES (?, ?)
        """,
            (chunk_id, embedding_bytes),
        )

        # Add to FTS5 index for keyword search.
        # FTS5 external-content tables similarly don't support
        # `INSERT OR REPLACE`, so mirror the delete-then-insert pattern.
        cursor.execute("DELETE FROM fts_chunks WHERE chunk_id = ?", (chunk_id,))
        cursor.execute(
            """
            INSERT INTO fts_chunks (chunk_id, content)
            VALUES (?, ?)
        """,
            (chunk_id, content),
        )

        self.conn.commit()

    @_locked
    def semantic_search(
        self,
        query_embedding: List[float],
        limit: int = 3,
        min_score: float = 0.0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Perform semantic similarity search.

        Args:
            query_embedding: Query vector embedding
            limit: Maximum number of results
            min_score: Minimum similarity score (0-1)
            filters: Optional filters (source, topic, date_after, date_before)

        Returns:
            List of matching chunks with metadata and scores
        """
        cursor = self.conn.cursor()

        # Build query with optional filters
        query = """
            SELECT
                c.id as chunk_id,
                c.content,
                c.chunk_index,
                d.id as document_id,
                d.title,
                d.source,
                d.source_url,
                d.topic,
                d.date_published,
                d.metadata as doc_metadata,
                vec_distance_cosine(v.embedding, ?) as distance
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            JOIN vec_chunks v ON c.id = v.chunk_id
            WHERE 1=1
        """

        params = [np.array(query_embedding, dtype=np.float32).tobytes()]

        # Apply filters
        if filters:
            if filters.get("source"):
                query += " AND d.source = ?"
                params.append(filters["source"])

            if filters.get("topic"):
                query += " AND d.topic = ?"
                params.append(filters["topic"])

            if filters.get("date_after"):
                query += " AND d.date_published >= ?"
                params.append(filters["date_after"])

            if filters.get("date_before"):
                query += " AND d.date_published <= ?"
                params.append(filters["date_before"])

        # Order by similarity and limit results
        query += " ORDER BY distance ASC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        results = cursor.fetchall()

        # Format results
        formatted_results = []
        for row in results:
            # Convert distance to similarity score (1 - distance for cosine)
            similarity_score = 1.0 - row[10]

            # Skip if below minimum score
            if similarity_score < min_score:
                continue

            formatted_results.append(
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
                    "similarity_score": round(similarity_score, 4),
                }
            )

        return formatted_results

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        """
        Sanitize a raw text query for use with FTS5 MATCH.

        Strips characters that FTS5 interprets as syntax operators
        and joins remaining words with OR so that natural-language
        queries match documents containing *any* of the terms
        (FTS5 default is implicit AND, which is too strict for
        conversational user input).

        Args:
            query: Raw user query text

        Returns:
            Sanitized query safe for FTS5 MATCH, or empty string
        """
        import re

        # Remove FTS5 special characters: " ( ) * + - ^ ~ : ? { } $ / \ @ # & | < > = [ ] ! %
        # The $ and / are common in paths like $SNIPER_ROOT/lib/...
        sanitized = re.sub(r'["\(\)\*\+\-\^~:\?\{\}\$\\/\@#&\|<>=\[\]!%]', " ", query)
        # Collapse whitespace and strip
        words = sanitized.split()
        # Filter out FTS5 boolean keywords when used standalone
        fts5_keywords = {"AND", "OR", "NOT", "NEAR"}
        words = [w for w in words if w.upper() not in fts5_keywords]
        # Join with OR so natural-language queries match any term
        # (FTS5 default is implicit AND which is too restrictive)
        return " OR ".join(words)

    @_locked
    def keyword_search(
        self,
        query: str,
        limit: int = 3,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Perform keyword-based full-text search (BM25 ranking).

        Args:
            query: Search query text
            limit: Maximum number of results
            filters: Optional filters (source, topic, date_after, date_before)

        Returns:
            List of matching chunks with metadata and keyword scores
        """
        cursor = self.conn.cursor()

        # Sanitize query for FTS5 syntax safety
        safe_query = self._sanitize_fts5_query(query)
        if not safe_query:
            return []

        # Build FTS5 query with filters
        sql_query = """
            SELECT
                c.id as chunk_id,
                c.content,
                c.chunk_index,
                d.id as document_id,
                d.title,
                d.source,
                d.source_url,
                d.topic,
                d.date_published,
                d.metadata as doc_metadata,
                fts.rank as bm25_score
            FROM fts_chunks fts
            JOIN chunks c ON fts.chunk_id = c.id
            JOIN documents d ON c.document_id = d.id
            WHERE fts_chunks MATCH ?
        """

        params = [safe_query]

        # Apply filters
        if filters:
            if filters.get("source"):
                sql_query += " AND d.source = ?"
                params.append(filters["source"])

            if filters.get("topic"):
                sql_query += " AND d.topic = ?"
                params.append(filters["topic"])

            if filters.get("date_after"):
                sql_query += " AND d.date_published >= ?"
                params.append(filters["date_after"])

            if filters.get("date_before"):
                sql_query += " AND d.date_published <= ?"
                params.append(filters["date_before"])

        # Order by BM25 score (rank is negative in FTS5, lower is better)
        sql_query += " ORDER BY fts.rank LIMIT ?"
        params.append(limit)

        cursor.execute(sql_query, params)
        results = cursor.fetchall()

        # Format results
        formatted_results = []
        for row in results:
            # Normalize BM25 score to 0-1 range (rank is negative, convert to positive similarity)
            # FTS5 rank is typically in range [-inf, 0], we'll normalize it
            bm25_score = abs(row[10])  # Make positive
            normalized_score = min(1.0, bm25_score / 10.0)  # Normalize roughly to 0-1

            formatted_results.append(
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
                    "keyword_score": round(normalized_score, 4),
                }
            )

        return formatted_results

    @_locked
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
        """
        Perform combined search using semantic similarity and keyword matching.

        Args:
            query: Search query text
            query_embedding: Query vector embedding
            limit: Maximum number of results
            min_score: Minimum combined score (0-1)
            filters: Optional filters (source, topic, date_after, date_before)
            semantic_weight: Weight for semantic similarity (default 0.7)
            keyword_weight: Weight for keyword matching score (default 0.3)

        Returns:
            List of matching chunks with combined scores
        """
        # Perform both searches with higher limits to ensure good candidate pool
        candidate_limit = limit * 3

        semantic_results = self.semantic_search(
            query_embedding=query_embedding, limit=candidate_limit, min_score=0.0, filters=filters
        )

        bm25_results = self.keyword_search(query=query, limit=candidate_limit, filters=filters)

        # Combine results by chunk_id
        combined = {}

        # Add semantic results
        for result in semantic_results:
            chunk_id = result["chunk_id"]
            combined[chunk_id] = result.copy()
            combined[chunk_id]["semantic_score"] = result["similarity_score"]
            combined[chunk_id]["keyword_score"] = 0.0  # Default if not found in keyword search

        # Add/update with keyword results
        for result in bm25_results:
            chunk_id = result["chunk_id"]
            if chunk_id in combined:
                combined[chunk_id]["keyword_score"] = result["keyword_score"]
            else:
                combined[chunk_id] = result.copy()
                combined[chunk_id]["semantic_score"] = 0.0  # Default if not found in semantic
                combined[chunk_id]["keyword_score"] = result["keyword_score"]

        # Calculate hybrid scores
        hybrid_results = []
        for _, data in combined.items():
            semantic_score = data.get("semantic_score", 0.0)
            keyword_score_val = data.get("keyword_score", 0.0)

            # Combined score using weighted average
            combined_score = (semantic_weight * semantic_score) + (keyword_weight * keyword_score_val)

            # Skip if below minimum threshold
            if combined_score < min_score:
                continue

            # Update result with scores
            data["similarity_score"] = round(combined_score, 4)  # Main score for compatibility
            data["hybrid_score"] = round(combined_score, 4)
            data["semantic_component"] = round(semantic_score, 4)
            data["keyword_component"] = round(keyword_score_val, 4)

            hybrid_results.append(data)

        # Sort by combined score and limit
        hybrid_results.sort(key=lambda x: x["hybrid_score"], reverse=True)
        return hybrid_results[:limit]

    @_locked
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a document by ID.

        Args:
            doc_id: Document identifier

        Returns:
            Document data or None if not found
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, content, title, source, source_url, topic,
                   date_published, metadata, created_at
            FROM documents
            WHERE id = ?
        """,
            (doc_id,),
        )

        row = cursor.fetchone()
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
            "created_at": row[8],
        }

    @_locked
    def list_sources(self) -> List[Dict[str, Any]]:
        """Get list of all unique sources with document counts."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT source, COUNT(*) as count
            FROM documents
            WHERE source IS NOT NULL
            GROUP BY source
            ORDER BY count DESC
        """
        )

        return [{"source": row[0], "count": row[1]} for row in cursor.fetchall()]

    @_locked
    def list_topics(self) -> List[Dict[str, Any]]:
        """Get list of all unique topics with document counts."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT topic, COUNT(*) as count
            FROM documents
            WHERE topic IS NOT NULL
            GROUP BY topic
            ORDER BY count DESC
        """
        )

        return [{"topic": row[0], "count": row[1]} for row in cursor.fetchall()]

    @_locked
    def delete_document(self, doc_id: str) -> None:
        """Delete a document and all its chunks."""
        cursor = self.conn.cursor()

        # Get chunk IDs to delete from vector table
        cursor.execute("SELECT id FROM chunks WHERE document_id = ?", (doc_id,))
        chunk_ids = [row[0] for row in cursor.fetchall()]

        # Delete from vector table
        for chunk_id in chunk_ids:
            cursor.execute("DELETE FROM vec_chunks WHERE chunk_id = ?", (chunk_id,))

        # Delete chunks
        cursor.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))

        # Delete document
        cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

        self.conn.commit()

    # ------------------------------------------------------------------
    # Admin operations (XMGPLAT-10417 — Phase 2)
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_document(row, chunk_count: Optional[int] = None) -> KBDocument:
        """Convert a SELECT row (id, content, title, source, source_url, topic,
        date_published, metadata, created_at[, chunk_count]) into a KBDocument.
        """
        metadata = json.loads(row[7]) if row[7] else {}
        raw_tags = metadata.get("tags") if isinstance(metadata, dict) else None
        tags = list(raw_tags) if isinstance(raw_tags, list) else []
        # created_at comes back as a string from SQLite's TIMESTAMP DEFAULT
        return KBDocument(
            id=row[0],
            content=row[1],
            title=row[2],
            source=row[3],
            source_url=row[4],
            topic=row[5],
            date_published=row[6],
            metadata=metadata,
            tags=tags,
            chunk_count=chunk_count,
            created_at=row[8],
        )

    @staticmethod
    def _build_list_where(filters: Optional[KBDocumentListFilters]) -> tuple[str, list]:
        """Build the WHERE clause + params for list/count_documents."""
        if filters is None:
            return "", []

        clauses: List[str] = []
        params: List[Any] = []

        if filters.source is not None:
            clauses.append("d.source = ?")
            params.append(filters.source)
        if filters.topic is not None:
            clauses.append("d.topic = ?")
            params.append(filters.topic)
        if filters.tags:
            # Overlap: any of the supplied tags must appear in metadata.tags
            placeholders = ",".join("?" for _ in filters.tags)
            clauses.append(
                f"EXISTS (SELECT 1 FROM json_each(json_extract(d.metadata, '$.tags')) "
                f"WHERE value IN ({placeholders}))"
            )
            params.extend(filters.tags)
        if filters.date_from is not None:
            # ``date_published`` is stored as an ISO-style string (typically
            # date-only ``YYYY-MM-DD``). Compare against a date-only prefix
            # so lexicographic ordering matches calendar ordering for both
            # date-only and full-datetime stored values.
            clauses.append("d.date_published >= ?")
            params.append(filters.date_from.strftime("%Y-%m-%d"))
        if filters.date_to is not None:
            clauses.append("d.date_published < ?")
            params.append(filters.date_to.strftime("%Y-%m-%d"))

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    @_locked
    def list_documents(
        self,
        filters: Optional[KBDocumentListFilters] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[KBDocument]:
        where, params = self._build_list_where(filters)
        sql = f"""
            SELECT d.id, d.content, d.title, d.source, d.source_url, d.topic,
                   d.date_published, d.metadata, d.created_at,
                   COALESCE(cc.chunk_count, 0) AS chunk_count
            FROM documents d
            LEFT JOIN (
                SELECT document_id, COUNT(*) AS chunk_count
                FROM chunks
                GROUP BY document_id
            ) cc ON cc.document_id = d.id
            {where}
            ORDER BY d.created_at DESC, d.id ASC
            LIMIT ? OFFSET ?
        """
        cursor = self.conn.cursor()
        cursor.execute(sql, params + [int(limit), int(offset)])
        rows = cursor.fetchall()
        return [self._row_to_document(row[:9], chunk_count=row[9]) for row in rows]

    @_locked
    def count_documents(
        self,
        filters: Optional[KBDocumentListFilters] = None,
    ) -> int:
        where, params = self._build_list_where(filters)
        sql = f"SELECT COUNT(*) FROM documents d {where}"
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        return int(cursor.fetchone()[0])

    def _delete_chunks_for(self, cursor: sqlite3.Cursor, doc_id: str) -> None:
        """Remove all chunks for ``doc_id`` from chunks, vec_chunks, and
        fts_chunks. Caller owns the transaction.
        """
        cursor.execute("SELECT id FROM chunks WHERE document_id = ?", (doc_id,))
        chunk_ids = [r[0] for r in cursor.fetchall()]
        for chunk_id in chunk_ids:
            cursor.execute("DELETE FROM vec_chunks WHERE chunk_id = ?", (chunk_id,))
            cursor.execute("DELETE FROM fts_chunks WHERE chunk_id = ?", (chunk_id,))
        cursor.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))

    @_locked
    def update_document(
        self,
        doc_id: str,
        *,
        content: Optional[str] = None,
        title: Optional[str] = None,
        source: Optional[str] = None,
        source_url: Optional[str] = None,
        topic: Optional[str] = None,
        date_published: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> KBDocument:
        cursor = self.conn.cursor()
        # Use an explicit transaction so the content-change + chunk-delete
        # happen atomically. sqlite3's default isolation level commits on
        # the next "non-DML" statement, but we want a single visible step.
        cursor.execute("BEGIN")
        try:
            cursor.execute(
                """
                SELECT id, content, title, source, source_url, topic,
                       date_published, metadata, created_at
                FROM documents
                WHERE id = ?
                """,
                (doc_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise KBDocumentNotFoundError(f"kb document {doc_id} not found")

            existing_metadata: Dict[str, Any] = json.loads(row[7]) if row[7] else {}

            # Compose the new column values; ``None`` == "don't touch".
            new_content = row[1] if content is None else content
            new_title = row[2] if title is None else title
            new_source = row[3] if source is None else source
            new_source_url = row[4] if source_url is None else source_url
            new_topic = row[5] if topic is None else topic
            new_date_published = row[6] if date_published is None else date_published

            # Merge metadata + tags. ``metadata=None`` keeps the stored dict.
            # ``tags=None`` keeps the stored tags. When both are provided,
            # ``tags`` wins over any ``tags`` key inside the supplied metadata.
            if metadata is None:
                new_metadata: Dict[str, Any] = dict(existing_metadata)
            else:
                new_metadata = dict(metadata)

            if tags is None:
                if "tags" not in new_metadata and isinstance(existing_metadata.get("tags"), list):
                    new_metadata["tags"] = list(existing_metadata["tags"])
            else:
                # Normalize via the model's validator (strip/dedupe/order).
                normalized = KBDocument._normalize_tags(list(tags))
                new_metadata["tags"] = normalized

            content_changed = content is not None and content != row[1]
            if content_changed:
                self._delete_chunks_for(cursor, doc_id)

            cursor.execute(
                """
                UPDATE documents
                SET content = ?, title = ?, source = ?, source_url = ?,
                    topic = ?, date_published = ?, metadata = ?
                WHERE id = ?
                """,
                (
                    new_content,
                    new_title,
                    new_source,
                    new_source_url,
                    new_topic,
                    new_date_published,
                    json.dumps(new_metadata) if new_metadata else None,
                    doc_id,
                ),
            )

            # Compute current chunk_count for the response. After a
            # content change this is 0 by construction.
            if content_changed:
                chunk_count = 0
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
                    (doc_id,),
                )
                chunk_count = int(cursor.fetchone()[0])

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        updated_row = (
            doc_id,
            new_content,
            new_title,
            new_source,
            new_source_url,
            new_topic,
            new_date_published,
            json.dumps(new_metadata) if new_metadata else None,
            row[8],
        )
        return self._row_to_document(updated_row, chunk_count=chunk_count)

    @_locked
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM documents")
        doc_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM chunks")
        chunk_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM vec_chunks")
        vector_count = cursor.fetchone()[0]

        return {
            "documents": doc_count,
            "chunks": chunk_count,
            "vectors": vector_count,
            "db_size_bytes": Path(self.db_path).stat().st_size if Path(self.db_path).exists() else 0,
        }

    @_locked
    def close(self):
        """Close database connection."""
        self.conn.close()
