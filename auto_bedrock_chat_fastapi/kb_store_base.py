"""Abstract base class for Knowledge Base storage backends.

This module defines the interface that all KB storage implementations must
follow, plus a factory function (``create_kb_store``) that instantiates the
correct backend based on configuration.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .config import ChatConfig

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


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------

_STORAGE_BACKENDS = {
    "sqlite": "auto_bedrock_chat_fastapi.vector_db.SQLiteKBStore",
    "pgvector": "auto_bedrock_chat_fastapi.pgvector_kb_store.PgVectorKBStore",
}


def create_kb_store(config: "ChatConfig") -> BaseKBStore:
    """Instantiate the KB store configured by *config.kb_storage_type*.

    Parameters
    ----------
    config:
        Application configuration.  The factory inspects ``kb_storage_type``
        (default ``"sqlite"``) and passes the relevant config fields to the
        chosen backend constructor.

    Returns
    -------
    BaseKBStore
        A ready-to-use store instance.

    Raises
    ------
    ValueError
        If the requested storage type is unknown.
    """
    storage_type = config.kb_storage_type.lower()

    if storage_type not in _STORAGE_BACKENDS:
        raise ValueError(
            f"Unknown kb_storage_type={storage_type!r}. " f"Valid options: {', '.join(sorted(_STORAGE_BACKENDS))}"
        )

    # Lazy-import the concrete class to avoid hard dependencies.
    fqn = _STORAGE_BACKENDS[storage_type]
    module_path, class_name = fqn.rsplit(".", 1)

    import importlib

    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)

    # Construct with backend-specific args
    if storage_type == "sqlite":
        return cls(db_path=config.kb_database_path)

    if storage_type == "pgvector":
        if not config.kb_postgres_url:
            raise ValueError("kb_storage_type='pgvector' requires BEDROCK_KB_POSTGRES_URL to be set.")
        return cls(
            connection_url=config.kb_postgres_url,
            pool_size=config.kb_postgres_pool_size,
            embedding_dimensions=config.kb_embedding_dimensions,
        )

    # Future backends will be handled here.
    raise ValueError(f"No constructor logic for storage type {storage_type!r}")  # pragma: no cover
