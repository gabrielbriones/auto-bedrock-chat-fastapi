"""Admin Knowledge-Base management HTTP routes (XMGPLAT-10417, T5).

Registered by :meth:`BedrockChatPlugin._setup_admin_routes` when
``admin_enabled=True`` **and** a KB store is wired. Every route is gated
by the ``require_admin`` dependency built in [plugin.py](plugin.py) (T1).

Endpoints
---------
* ``GET    /admin/kb/documents``       — paginated/filterable list.
* ``GET    /admin/kb/documents/{id}``  — fetch a single document.
* ``PATCH  /admin/kb/documents/{id}``  — partial update; on content
  change the route re-embeds the document and writes new chunks.
* ``DELETE /admin/kb/documents/{id}``  — hard delete (document + chunks).

Concurrency
-----------
A per-document ``asyncio.Lock`` registry serializes PATCH/DELETE for the
same id. This is a single-worker best-effort guard — distributed
locking is explicitly out of scope (see plan §7). Cross-worker
concurrent edits are still possible; the KB store's transactional
chunk-swap keeps the document in a consistent state even under
contention, and a follow-up `409 conflict` HTTP envelope is open for
v2 if it becomes a real problem.

Re-embedding
------------
The KB store deliberately does **not** import the embedding pipeline
(see T4 design notes). When PATCH supplies a new ``content`` value the
route runs the existing async chunker → ``bedrock_client.generate_embeddings_batch``
flow already used by the populate pipeline in
[commands/kb.py](commands/kb.py), then writes chunks via
``kb_store.add_chunk``. The chunk replacement is done **after**
``update_document`` returns (which has already cleared old chunks in a
transaction), so a failure during embedding leaves the document with
empty chunks rather than stale ones — explicitly logged as a warning so
operators can re-run the update.

Audit logging
-------------
PATCH and DELETE emit structured ``bedrock.audit`` records carrying a
SHA-256 ``content_hash`` of the before/after content rather than the
full text (T5.5). Full content can be retrieved via the GET endpoint
or the KB store directly; the audit log keeps a tamper-evident pointer
without ballooning log volume.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .admin_errors import ADMIN_COMMON_RESPONSES
from .db.kb_base import BaseKBStore
from .exceptions import AdminAPIError
from .models import KBDocument, KBDocumentListFilters, KBDocumentListResponse

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("bedrock.audit")


_LIMIT_DEFAULT = 50
_LIMIT_MAX = 200


def _parse_tags_csv(raw: Optional[str]) -> Optional[List[str]]:
    """Parse a comma-separated ``tags`` query value (same hygiene as T3)."""
    if raw is None:
        return None
    parts = [t.strip() for t in raw.split(",") if t and t.strip()]
    return parts or None


def _content_hash(content: Optional[str]) -> Optional[str]:
    if content is None:
        return None
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


class KBDocumentUpdateRequest(BaseModel):
    """Request body for ``PATCH /admin/kb/documents/{id}``.

    Every field is optional. ``None`` means "don't touch"; pass an
    explicit empty value to clear (``""`` for text, ``[]`` for tags,
    ``{}`` for metadata).

    ``extra='forbid'`` so attempts to inject server-derived fields like
    ``id`` / ``created_at`` produce a 422 instead of being silently
    ignored.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    content: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    topic: Optional[str] = None
    date_published: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None

    @field_validator("tags")
    @classmethod
    def _strip_tags(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        # Allow an explicit empty list (caller wants to clear tags); also
        # collapse a list that is entirely blank strings to ``[]`` so the
        # caller's "clear tags" intent is preserved.
        return [t.strip() for t in v if isinstance(t, str) and t.strip()]


def register_admin_kb_routes(
    app: FastAPI,
    *,
    prefix: str,
    kb_store: BaseKBStore,
    require_admin: Callable,
    re_embed_document: Optional[Callable] = None,
) -> APIRouter:
    """Register the ``/admin/kb/documents*`` routes on ``app``.

    Parameters
    ----------
    app:
        Host FastAPI application.
    prefix:
        Full admin prefix (e.g. ``"/chat/admin"``). Routes mount at
        ``{prefix}/kb/documents*``.
    kb_store:
        The active :class:`BaseKBStore`.
    require_admin:
        Auth/authz dependency from
        :meth:`BedrockChatPlugin._setup_admin_routes`.
    re_embed_document:
        Async callable ``(doc_id: str, content: str) -> int`` that
        chunks + embeds ``content`` and writes chunks via
        ``kb_store.add_chunk``, returning the number of chunks written.
        Optional — when ``None``, content-changing PATCHes still clear
        old chunks (via the store) but leave the document un-embedded.
        That's an explicit operator decision (e.g. embedding model
        unavailable); the response carries ``chunk_count == 0`` and a
        warning is logged.
    """
    router = APIRouter(prefix=f"{prefix}/kb/documents", tags=["admin-kb"])

    # Per-document async lock registry. Lazily populated to avoid
    # holding entries for every doc that's ever been touched. Pruning
    # is best-effort (we don't actively GC entries) — the worst-case
    # memory growth is one ``asyncio.Lock`` per distinct doc id ever
    # edited in this process, which is tiny.
    _doc_locks: Dict[str, asyncio.Lock] = {}
    _registry_lock = asyncio.Lock()

    async def _lock_for(doc_id: str) -> asyncio.Lock:
        async with _registry_lock:
            lock = _doc_locks.get(doc_id)
            if lock is None:
                lock = asyncio.Lock()
                _doc_locks[doc_id] = lock
            return lock

    @router.get(
        "",
        response_model=KBDocumentListResponse,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="List KB documents (paginated, filterable)",
    )
    async def list_documents(
        request: Request,
        identity=Depends(require_admin),
        source: Optional[str] = Query(None, description="Filter by document source"),
        topic: Optional[str] = Query(None, description="Filter by document topic"),
        tags: Optional[str] = Query(
            None,
            description="Comma-separated tag overlap filter (e.g. 'IPC,perf')",
        ),
        date_from: Optional[datetime] = Query(
            None,
            description="Inclusive lower bound on date_published (ISO 8601)",
        ),
        date_to: Optional[datetime] = Query(
            None,
            description="Exclusive upper bound on date_published (ISO 8601)",
        ),
        limit: int = Query(_LIMIT_DEFAULT, ge=1, le=_LIMIT_MAX),
        offset: int = Query(0, ge=0),
    ) -> KBDocumentListResponse:
        try:
            filters = KBDocumentListFilters(
                source=source,
                topic=topic,
                tags=_parse_tags_csv(tags),
                date_from=date_from,
                date_to=date_to,
            )
        except ValidationError as exc:
            raise AdminAPIError(
                status_code=400,
                code="invalid_filters",
                detail="invalid filter parameters",
                errors=jsonable_encoder(exc.errors()),
            ) from exc

        items = await asyncio.to_thread(kb_store.list_documents, filters, limit, offset)
        total = await asyncio.to_thread(kb_store.count_documents, filters)
        return KBDocumentListResponse(items=items, total=total, limit=limit, offset=offset)

    @router.get(
        "/{doc_id:path}",
        response_model=KBDocument,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Fetch one KB document by id",
    )
    async def get_document(doc_id: str, identity=Depends(require_admin)) -> KBDocument:
        # ``BaseKBStore.get_document`` returns the legacy dict shape with
        # no JOIN-derived ``chunk_count``. We deliberately leave
        # ``chunk_count=None`` on the single-document path — callers
        # that need the count can use ``GET /admin/kb/documents`` which
        # carries it via the JOIN. Keeping this path as a single
        # primary-key fetch avoids a second query on the hot
        # "open one document" flow.
        raw = await asyncio.to_thread(kb_store.get_document, doc_id)
        if raw is None:
            raise AdminAPIError(
                status_code=404,
                code="not_found",
                detail=f"kb document {doc_id} not found",
            )

        metadata = raw.get("metadata") or {}
        raw_tags = metadata.get("tags") if isinstance(metadata, dict) else None
        tags = list(raw_tags) if isinstance(raw_tags, list) else []
        return KBDocument(
            id=raw["id"],
            content=raw["content"],
            title=raw.get("title"),
            source=raw.get("source"),
            source_url=raw.get("source_url"),
            topic=raw.get("topic"),
            date_published=raw.get("date_published"),
            metadata=metadata,
            tags=tags,
            chunk_count=None,
            created_at=raw.get("created_at"),
        )

    @router.patch(
        "/{doc_id:path}",
        response_model=KBDocument,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Update a KB document (re-embeds on content change)",
    )
    async def patch_document(
        doc_id: str,
        body: KBDocumentUpdateRequest,
        identity=Depends(require_admin),
    ) -> KBDocument:
        actor = identity.user_id

        lock = await _lock_for(doc_id)
        async with lock:
            # Snapshot for audit logging. Missing → clean 404.
            before_raw = await asyncio.to_thread(kb_store.get_document, doc_id)
            if before_raw is None:
                raise AdminAPIError(
                    status_code=404,
                    code="not_found",
                    detail=f"kb document {doc_id} not found",
                )

            before_content_hash = _content_hash(before_raw.get("content"))
            content_changed = body.content is not None and body.content != before_raw.get("content")

            # KBDocumentNotFoundError (race: row deleted between get +
            # update) propagates to the central admin error handler
            # which maps it to 404 with the standard envelope.
            updated = await asyncio.to_thread(
                kb_store.update_document,
                doc_id,
                content=body.content,
                title=body.title,
                source=body.source,
                source_url=body.source_url,
                topic=body.topic,
                date_published=body.date_published,
                metadata=body.metadata,
                tags=body.tags,
            )

            # Re-embed if content changed. The store has already cleared
            # the old chunks; we now refill them. Failure here logs a
            # warning and leaves the document with zero chunks — the
            # caller can retry the PATCH with the same content to
            # re-embed without losing the metadata changes that
            # already landed.
            if content_changed and re_embed_document is not None:
                try:
                    n_chunks = await re_embed_document(doc_id, updated.content)
                    updated.chunk_count = n_chunks
                except Exception:  # noqa: BLE001 — see comment above
                    logger.exception(
                        "Re-embedding failed for kb doc %s after content update; "
                        "document has 0 chunks until a successful retry",
                        doc_id,
                    )
            elif content_changed and re_embed_document is None:
                logger.warning(
                    "Content changed for kb doc %s but no re-embed callback is "
                    "configured; document now has 0 chunks",
                    doc_id,
                )

            audit_logger.info(
                "kb.document.update",
                extra={
                    "action": "kb.document.update",
                    "actor_user_id": actor,
                    "target_id": doc_id,
                    "before": {
                        "content_hash": before_content_hash,
                        "tags": list((before_raw.get("metadata") or {}).get("tags") or []),
                        "title": before_raw.get("title"),
                        "source": before_raw.get("source"),
                    },
                    "after": {
                        "content_hash": _content_hash(updated.content),
                        "tags": list(updated.tags),
                        "title": updated.title,
                        "source": updated.source,
                    },
                    "content_changed": content_changed,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
            return updated

    @router.delete(
        "/{doc_id:path}",
        status_code=204,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Hard-delete a KB document (and its chunks)",
    )
    async def delete_document(
        doc_id: str,
        identity=Depends(require_admin),
    ):
        actor = identity.user_id

        lock = await _lock_for(doc_id)
        async with lock:
            before_raw = await asyncio.to_thread(kb_store.get_document, doc_id)
            if before_raw is None:
                raise AdminAPIError(
                    status_code=404,
                    code="not_found",
                    detail=f"kb document {doc_id} not found",
                )

            await asyncio.to_thread(kb_store.delete_document, doc_id)

            audit_logger.info(
                "kb.document.delete",
                extra={
                    "action": "kb.document.delete",
                    "actor_user_id": actor,
                    "target_id": doc_id,
                    "before": {
                        "content_hash": _content_hash(before_raw.get("content")),
                        "tags": list((before_raw.get("metadata") or {}).get("tags") or []),
                        "title": before_raw.get("title"),
                        "source": before_raw.get("source"),
                    },
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
            # FastAPI returns 204 with no body when the handler returns None.
            return None

    app.include_router(router)
    logger.info("Admin KB routes registered under %s/kb/documents", prefix)
    return router


# ---------------------------------------------------------------------------
# Re-embed callback factory
# ---------------------------------------------------------------------------


def build_default_re_embed_callback(
    *,
    kb_store: BaseKBStore,
    bedrock_client,
    embedding_model: str,
    chunker=None,
    batch_size: int = 25,
) -> Callable:
    """Return an ``async (doc_id, content) -> int`` callback.

    Re-uses the same chunker + ``bedrock_client.generate_embeddings_batch``
    + ``kb_store.add_chunk`` flow as the populate pipeline in
    [commands/kb.py](commands/kb.py). Kept here as a free function so
    [plugin.py](plugin.py) can build it once at registration time and
    so tests can inject a stub without touching the route module.
    """
    from .embedding_pipeline import TextChunker  # local import: heavy module

    _chunker = chunker or TextChunker()

    async def _re_embed(doc_id: str, content: str) -> int:
        # Fetch metadata so chunk_metadata can carry doc-level provenance.
        raw = await asyncio.to_thread(kb_store.get_document, doc_id)
        if raw is None:
            # Shouldn't happen — the route just updated it. Treat
            # defensively as a no-op rather than crashing the request.
            logger.warning("re_embed: doc %s vanished mid-flight", doc_id)
            return 0

        doc_dict = {
            "id": doc_id,
            "content": content,
            "title": raw.get("title", ""),
            "source": raw.get("source"),
            "url": raw.get("source_url"),
            "topic": raw.get("topic"),
            "date_published": raw.get("date_published"),
        }

        # Chunking is CPU-bound but cheap; run inline to keep the
        # control flow obvious. If it becomes a hotspot, wrap in
        # asyncio.to_thread.
        chunks_data = _chunker.chunk_document(doc_dict)
        if not chunks_data:
            return 0

        texts = [c["text"] for c in chunks_data]
        embeddings = await bedrock_client.generate_embeddings_batch(
            texts=texts, model_id=embedding_model, batch_size=batch_size
        )

        for idx, (chunk_data, embedding) in enumerate(zip(chunks_data, embeddings)):
            chunk_id = f"{doc_id}_{idx}"
            chunk_metadata = {
                "doc_id": doc_id,
                "title": raw.get("title", ""),
                "source": raw.get("source"),
                "url": raw.get("source_url"),
                "topic": raw.get("topic"),
                "date_published": raw.get("date_published"),
            }
            await asyncio.to_thread(
                kb_store.add_chunk,
                chunk_id=chunk_id,
                document_id=doc_id,
                content=chunk_data["text"],
                embedding=embedding,
                chunk_index=idx,
                start_char=chunk_data.get("start_char"),
                end_char=chunk_data.get("end_char"),
                metadata=chunk_metadata,
            )

        return len(chunks_data)

    return _re_embed
