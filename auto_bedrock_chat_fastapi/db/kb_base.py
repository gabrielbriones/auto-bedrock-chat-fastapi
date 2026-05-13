"""Abstract base class for Knowledge Base storage backends.

Concrete backends live in :mod:`auto_bedrock_chat_fastapi.db.kb_sqlite`
and :mod:`auto_bedrock_chat_fastapi.db.kb_postgres`. Use
:func:`auto_bedrock_chat_fastapi.db.create_kb_store` to instantiate the
backend selected by ``ChatConfig.kb_storage_type``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BaseKBStore(ABC):
    """Abstract interface for knowledge-base storage backends.

    Every concrete implementation (SQLite, pgvector, …) must subclass this
    and implement **all** abstract methods.  Consumers should depend on this
    type rather than on a concrete class.
    """

    # ------------------------------------------------------------------
    # Document operations
    # ------------------------------------------------------------------

    @abstractmethod
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
        """Insert or replace a document."""

    @abstractmethod
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a document by ID, or ``None`` if not found."""

    @abstractmethod
    def delete_document(self, doc_id: str) -> None:
        """Delete a document **and** all its chunks/embeddings."""

    @abstractmethod
    def list_sources(self) -> List[Dict[str, Any]]:
        """Return unique sources with document counts."""

    @abstractmethod
    def list_topics(self) -> List[Dict[str, Any]]:
        """Return unique topics with document counts."""

    # ------------------------------------------------------------------
    # Chunk / embedding operations
    # ------------------------------------------------------------------

    @abstractmethod
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
        """Insert or replace a chunk with its embedding vector."""

    # ------------------------------------------------------------------
    # Search operations
    # ------------------------------------------------------------------

    @abstractmethod
    def semantic_search(
        self,
        query_embedding: List[float],
        limit: int = 3,
        min_score: float = 0.0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Vector-similarity search.  Return results sorted by descending score."""

    @abstractmethod
    def keyword_search(
        self,
        query: str,
        limit: int = 3,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Full-text keyword search (BM25 or equivalent)."""

    @abstractmethod
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
        """Combined semantic + keyword search with configurable weights."""

    # ------------------------------------------------------------------
    # Lifecycle / stats
    # ------------------------------------------------------------------

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """Return storage statistics (document/chunk/vector counts, etc.)."""

    @abstractmethod
    def close(self) -> None:
        """Release resources (connections, file handles, …)."""
