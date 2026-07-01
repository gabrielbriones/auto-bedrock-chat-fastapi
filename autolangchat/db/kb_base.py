"""Abstract base class for Knowledge Base storage backends.

Concrete backends live in :mod:`autolangchat.db.kb_sqlite`
and :mod:`autolangchat.db.kb_postgres`. Use
:func:`autolangchat.db.create_kb_store` to instantiate the
backend selected by ``ChatConfig.kb_storage_type``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ..models import KBDocument, KBDocumentListFilters

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
    # Admin operations
    # ------------------------------------------------------------------

    @abstractmethod
    def list_documents(
        self,
        filters: Optional[KBDocumentListFilters] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[KBDocument]:
        """Return paginated documents matching ``filters``, ordered by
        ``created_at`` descending (newest first). Each returned
        :class:`KBDocument` has ``chunk_count`` populated via a JOIN
        against the ``chunks`` table.
        """

    @abstractmethod
    def count_documents(
        self,
        filters: Optional[KBDocumentListFilters] = None,
    ) -> int:
        """Return the total number of documents matching ``filters``
        (ignoring pagination). Must be consistent with
        :meth:`list_documents` under the same filters.
        """

    @abstractmethod
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
        """Partial-update a document.

        ``None`` for any field means "don't touch". To clear a field
        explicitly, pass an empty value (``""`` for text columns,
        ``{}`` for metadata, ``[]`` for tags).

        When ``content`` is provided and differs from the stored value,
        the store transactionally deletes all existing chunks for that
        document so the caller can re-embed cleanly. The store does
        **not** invoke the embedding pipeline itself — that
        orchestration is the responsibility of the admin route layer
        (T5). After a content-clearing update, ``chunk_count`` on the
        returned :class:`KBDocument` is ``0``.

        ``tags`` are persisted inside ``metadata['tags']``. Passing
        ``metadata`` and ``tags`` together merges as follows:
        ``metadata['tags']`` from the explicit ``tags`` argument wins
        and overwrites anything in the supplied ``metadata`` dict.

        Raises
        ------
        KBDocumentNotFoundError
            If no document with ``doc_id`` exists.
        """

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
        exclude_flagged: bool = True,
    ) -> List[Dict[str, Any]]:
        """Vector-similarity search.  Return results sorted by descending score."""

    @abstractmethod
    def keyword_search(
        self,
        query: str,
        limit: int = 3,
        filters: Optional[Dict[str, Any]] = None,
        exclude_flagged: bool = True,
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
        exclude_flagged: bool = True,
    ) -> List[Dict[str, Any]]:
        """Combined semantic + keyword search with configurable weights."""

    # ------------------------------------------------------------------
    # Lifecycle / stats
    # ------------------------------------------------------------------

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """Return storage statistics (document/chunk/vector counts, etc.)."""

    @abstractmethod
    def adjust_credibility(self, doc_ids: List[str], delta: float, threshold: float) -> int:
        """Adjust credibility scores for a set of feedback documents.

        Adds *delta* to ``credibility_score`` for each document in *doc_ids*
        that has ``source='feedback'``, clamping the result to ``[0.0, 1.0]``.
        When *delta* is negative, documents whose new score is at or below
        *threshold* are also set ``removal_flagged=True``.

        Only documents with ``source='feedback'`` are touched.

        Returns:
            Number of rows updated.
        """

    @abstractmethod
    def apply_credibility_decay(self, decay_rate: float, threshold: float) -> tuple[int, int]:
        """Decay credibility scores for synthesized (source='feedback') documents.

        Subtracts *decay_rate* from each non-flagged feedback document's
        ``credibility_score``, clamping to ``0.0``.  Documents whose new score
        is at or below *threshold* are also set ``removal_flagged=True``.

        Returns:
            ``(total_updated, newly_flagged)`` — counts for logging.
        """

    @abstractmethod
    def reset_credibility(self, doc_id: str) -> "KBDocument":
        """Reset *doc_id*'s credibility_score to 1.0 and removal_flagged to False.

        Raises :class:`~autolangchat.exceptions.KBDocumentNotFoundError` if the
        document does not exist.
        """

    @abstractmethod
    def close(self) -> None:
        """Release resources (connections, file handles, …)."""
