"""
Vector Database Module using SQLite with sqlite-vec extension.

This module provides vector similarity search capabilities using SQLite,
perfect for MVP and development phases. Easy migration path to production
vector databases (Pinecone, Weaviate, pgvector) if needed.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import sqlite_vec


class VectorDB:
    """Vector database interface using SQLite with vector similarity search."""

    def __init__(self, db_path: str = "knowledge_base.db"):
        """
        Initialize vector database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
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

        # Add embedding vector
        embedding_bytes = np.array(embedding, dtype=np.float32).tobytes()
        cursor.execute(
            """
            INSERT OR REPLACE INTO vec_chunks (chunk_id, embedding)
            VALUES (?, ?)
        """,
            (chunk_id, embedding_bytes),
        )

        self.conn.commit()

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

    def close(self):
        """Close database connection."""
        self.conn.close()
