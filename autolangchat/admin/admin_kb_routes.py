"""Admin Knowledge-Base management HTTP routes.

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
* ``POST   /admin/kb/sources/web``     — trigger a web-crawl ingestion run.
* ``POST   /admin/kb/sources/file``    — trigger an ingestion run from
  uploaded file content (multipart form; admins don't have filesystem
  access to the running service).
* ``GET    /admin/kb/sources/status``  — poll the in-flight ingestion run.

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
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..db.kb_base import BaseKBStore
from ..exceptions import AdminAPIError
from ..models import ErrorResponse, KBDocument, KBDocumentListFilters, KBDocumentListResponse
from .admin_errors import ADMIN_COMMON_RESPONSES

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


# ---------------------------------------------------------------------------
# KB source ingestion (web crawl / uploaded file) — request + status models
# ---------------------------------------------------------------------------


class KBSourcePhase(str, Enum):
    """Lifecycle phase of the in-memory KB source-ingestion runner."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class KBSourceWebRequest(BaseModel):
    """Request body for ``POST /admin/kb/sources/web``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    urls: List[str]
    topic: Optional[str] = None
    max_depth: int = 2
    # Defaults to each URL's own hostname (see ContentCrawler.crawl_url) so a
    # crawl doesn't wander onto unrelated external sites unless opted into.
    allowed_domains: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    # Real cap on pages fetched per URL (see ContentCrawler._crawl_recursive) —
    # previously a no-op in the CLI populate pipeline; kept usable here too.
    max_pages: int = 100
    # For pages that gate content behind auth (e.g. a bearer token header or
    # a session cookie). Not echoed back in the status response or audit log.
    headers: Optional[Dict[str, str]] = None
    cookies: Optional[Dict[str, str]] = None

    @field_validator("urls")
    @classmethod
    def _urls_not_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("urls must contain at least one URL")
        return v


class KBSourceStatus(BaseModel):
    """Wire shape for the KB source-ingestion run state.

    Returned by both ``POST /admin/kb/sources/{web,file}`` (on claim) and
    ``GET /admin/kb/sources/status`` (on poll).
    """

    model_config = ConfigDict(validate_assignment=True)

    run_id: Optional[str] = None
    phase: KBSourcePhase = KBSourcePhase.IDLE
    source_name: Optional[str] = None
    source_type: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    pages_crawled: int = 0
    pages_processed: int = 0
    files_processed: int = 0
    chunks_written: int = 0
    #: Fatal error that aborted the whole run (unhandled exception).
    error: Optional[str] = None
    #: Per-item failures (a page that failed to fetch, a file that failed to
    #: chunk/embed) that did NOT abort the run — the run can still complete
    #: with 0 documents indexed while this is non-empty.
    errors: List[str] = Field(default_factory=list)


class _KBSourceRunState:
    """Mutable in-memory state for the KB source-ingestion runner.

    Mirrors the ``_RunState`` pattern in ``admin_synthesis_routes.py``: a
    single in-flight run at a time, claimed via :meth:`try_claim_run` and
    polled via the :attr:`status` property (read without holding the lock —
    eventual consistency is acceptable for status checks).
    """

    def __init__(self) -> None:
        self._status = KBSourceStatus()
        self._lock = asyncio.Lock()

    @property
    def status(self) -> KBSourceStatus:
        # Deep snapshot so callers cannot mutate our internal state via the
        # returned object.
        return self._status.model_copy(deep=True)

    async def try_claim_run(self, *, run_id: str, source_name: str, source_type: str) -> bool:
        """Atomically transition to RUNNING if not already in progress."""
        async with self._lock:
            if self._status.phase == KBSourcePhase.RUNNING:
                return False
            self._status = KBSourceStatus(
                run_id=run_id,
                phase=KBSourcePhase.RUNNING,
                source_name=source_name,
                source_type=source_type,
                started_at=datetime.now(timezone.utc),
            )
            return True

    def record_progress(self, metric: str, amount: int = 1) -> None:
        """``progress_cb`` passed to ``ingest_web_source``/``ingest_uploaded_files``."""
        if metric == "pages_crawled":
            self._status.pages_crawled += amount
        elif metric == "pages_processed":
            self._status.pages_processed += amount
        elif metric == "files_processed":
            self._status.files_processed += amount
        elif metric == "chunks_written":
            self._status.chunks_written += amount

    def _mark_completed(self, errors: Optional[List[str]] = None) -> None:
        self._status = self._status.model_copy(
            update={
                "phase": KBSourcePhase.COMPLETED,
                "finished_at": datetime.now(timezone.utc),
                "errors": errors or [],
            }
        )

    def _mark_failed(self, error: str) -> None:
        self._status = self._status.model_copy(
            update={"phase": KBSourcePhase.FAILED, "finished_at": datetime.now(timezone.utc), "error": error}
        )


async def _run_web_source_ingestion(
    *,
    state: _KBSourceRunState,
    actor: str,
    run_id: str,
    kb_store: BaseKBStore,
    embedding_client: Any,
    embedding_model: str,
    chunker: Any,
    body: KBSourceWebRequest,
) -> None:
    """Run a web-source ingestion as an ``asyncio.create_task`` background task."""
    from ..rag.kb_ingestion import ingest_web_source

    try:
        result = await ingest_web_source(
            vector_db=kb_store,
            bedrock_client=embedding_client,
            chunker=chunker,
            embedding_model=embedding_model,
            source_name=body.name,
            urls=body.urls,
            topic=body.topic,
            max_depth=body.max_depth,
            allowed_domains=body.allowed_domains,
            exclude_patterns=body.exclude_patterns,
            max_pages=body.max_pages,
            extra_headers=body.headers,
            cookies=body.cookies,
            progress_cb=state.record_progress,
        )
        state._mark_completed(errors=result["errors"])
        logger.info(
            "KB web source ingestion complete: source=%s documents=%d chunks=%d errors=%d",
            body.name,
            result["documents"],
            result["chunks"],
            len(result["errors"]),
        )
    except Exception as exc:  # pragma: no cover — defensive outer catch
        logger.exception("KB web source ingestion failed for run %s: %s", run_id, exc)
        state._mark_failed(str(exc))
    finally:
        audit_logger.info(
            "kb.source.populate",
            extra={
                "action": "kb.source.populate",
                "actor_user_id": actor,
                "target_id": run_id,
                "source_name": body.name,
                "source_type": "web",
                "phase": "complete",
                "run_status": state.status.phase.value,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )


async def _run_file_source_ingestion(
    *,
    state: _KBSourceRunState,
    actor: str,
    run_id: str,
    kb_store: BaseKBStore,
    embedding_client: Any,
    embedding_model: str,
    chunker: Any,
    source_name: str,
    topic: Optional[str],
    files: List[Tuple[str, str]],
) -> None:
    """Run an uploaded-file ingestion as an ``asyncio.create_task`` background task."""
    from ..rag.kb_ingestion import ingest_uploaded_files

    try:
        result = await ingest_uploaded_files(
            vector_db=kb_store,
            bedrock_client=embedding_client,
            chunker=chunker,
            embedding_model=embedding_model,
            source_name=source_name,
            files=files,
            topic=topic,
            progress_cb=state.record_progress,
        )
        state._mark_completed(errors=result["errors"])
        logger.info(
            "KB file source ingestion complete: source=%s documents=%d chunks=%d errors=%d",
            source_name,
            result["documents"],
            result["chunks"],
            len(result["errors"]),
        )
    except Exception as exc:  # pragma: no cover — defensive outer catch
        logger.exception("KB file source ingestion failed for run %s: %s", run_id, exc)
        state._mark_failed(str(exc))
    finally:
        audit_logger.info(
            "kb.source.populate",
            extra={
                "action": "kb.source.populate",
                "actor_user_id": actor,
                "target_id": run_id,
                "source_name": source_name,
                "source_type": "file",
                "phase": "complete",
                "run_status": state.status.phase.value,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )


def register_admin_kb_routes(
    app: FastAPI,
    *,
    prefix: str,
    kb_store: BaseKBStore,
    require_admin: Callable,
    re_embed_document: Optional[Callable] = None,
    embedding_client: Any = None,
    embedding_model: Optional[str] = None,
    chunker: Any = None,
) -> APIRouter:
    """Register the ``/admin/kb/documents*`` and ``/admin/kb/sources*`` routes on ``app``.

    Parameters
    ----------
    app:
        Host FastAPI application.
    prefix:
        Full admin prefix (e.g. ``"/chat/admin"``). Document routes mount at
        ``{prefix}/kb/documents*``; source-ingestion routes mount at
        ``{prefix}/kb/sources*``.
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
    embedding_client:
        Wired embedding client (``BedrockEmbeddingClient``) used by the
        ``/kb/sources/*`` ingestion routes to embed newly crawled/read
        content. Required (together with ``embedding_model``) for those
        routes to actually run a crawl/ingest; when either is ``None``
        they respond with ``503``.
    embedding_model:
        Bedrock embedding model id (``config.kb_embedding_model``) passed
        to ``embedding_client.generate_embeddings_batch``.
    chunker:
        Optional pre-built ``TextChunker`` shared by the source-ingestion
        routes. Defaults to a plain ``TextChunker()`` (same defaults as
        the populate pipeline) when ``None``.
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
        removal_flagged: Optional[bool] = Query(
            None,
            description="Filter by removal_flagged status (true = flagged only, false = unflagged only)",
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
                removal_flagged=removal_flagged,
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

    @router.post(
        "/reset-credibility/{doc_id:path}",
        response_model=KBDocument,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Reset credibility score to 1.0 and clear removal_flagged",
    )
    async def reset_credibility(
        doc_id: str,
        identity=Depends(require_admin),
    ) -> KBDocument:
        """Reset a document's credibility_score to 1.0 and removal_flagged to False.

        Raises 404 via the global KBDocumentNotFoundError handler if the
        document does not exist.
        """
        actor = identity.user_id
        lock = await _lock_for(doc_id)
        async with lock:
            updated = await asyncio.to_thread(kb_store.reset_credibility, doc_id)
            audit_logger.info(
                "kb.document.reset_credibility",
                extra={
                    "action": "kb.document.reset_credibility",
                    "actor_user_id": actor,
                    "target_id": doc_id,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
            return updated

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
            credibility_score=float(raw["credibility_score"]) if raw.get("credibility_score") is not None else 1.0,
            removal_flagged=bool(raw.get("removal_flagged")),
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

    # ------------------------------------------------------------------
    # /admin/kb/sources* — runtime web-crawl / file-upload ingestion
    # ------------------------------------------------------------------
    from ..rag.embedding_pipeline import TextChunker  # local import: heavy module

    _source_chunker = chunker or TextChunker()
    _source_state = _KBSourceRunState()

    sources_router = APIRouter(prefix=f"{prefix}/kb/sources", tags=["admin-kb"])

    def _error_json(status_code: int, code: str, detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content=ErrorResponse(code=code, detail=detail).model_dump(exclude_none=True),
        )

    def _ingestion_unavailable_response() -> Optional[JSONResponse]:
        # Returned directly (rather than raised as AdminAPIError) so this
        # route doesn't depend on the host app's global exception-handler
        # wiring to produce a clean error instead of a 500.
        if embedding_client is None or not embedding_model:
            return _error_json(
                503,
                "kb_source_ingestion_unavailable",
                "KB source ingestion is not configured (missing embedding client/model)",
            )
        return None

    @sources_router.post(
        "/web",
        response_model=KBSourceStatus,
        status_code=202,
        responses={
            **ADMIN_COMMON_RESPONSES,
            409: {"model": ErrorResponse, "description": "A KB source ingestion run is already in progress"},
            503: {"model": ErrorResponse, "description": "KB source ingestion is not configured"},
        },
        summary="Trigger a web-crawl KB ingestion run",
    )
    async def trigger_web_source(
        body: KBSourceWebRequest,
        identity=Depends(require_admin),
    ) -> KBSourceStatus | JSONResponse:
        """Start a background web crawl and index the results into the KB.

        Returns ``202 Accepted`` immediately with a ``run_id`` and the
        ``running`` state. Poll ``GET /admin/kb/sources/status`` for
        completion. Returns ``409`` if a run is already in progress.
        """
        unavailable = _ingestion_unavailable_response()
        if unavailable is not None:
            return unavailable

        actor = identity.user_id
        run_id = str(uuid4())

        if not await _source_state.try_claim_run(run_id=run_id, source_name=body.name, source_type="web"):
            return _error_json(
                409,
                "kb_source_run_already_in_progress",
                "a KB source ingestion run is already in progress",
            )

        audit_logger.info(
            "kb.source.populate",
            extra={
                "action": "kb.source.populate",
                "actor_user_id": actor,
                "target_id": run_id,
                "source_name": body.name,
                "source_type": "web",
                "phase": "start",
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )

        asyncio.create_task(
            _run_web_source_ingestion(
                state=_source_state,
                actor=actor,
                run_id=run_id,
                kb_store=kb_store,
                embedding_client=embedding_client,
                embedding_model=embedding_model,
                chunker=_source_chunker,
                body=body,
            )
        )

        return _source_state.status

    @sources_router.post(
        "/file",
        response_model=KBSourceStatus,
        status_code=202,
        responses={
            **ADMIN_COMMON_RESPONSES,
            409: {"model": ErrorResponse, "description": "A KB source ingestion run is already in progress"},
            422: {"model": ErrorResponse, "description": "No files uploaded, or a file is not valid UTF-8 text"},
            503: {"model": ErrorResponse, "description": "KB source ingestion is not configured"},
        },
        summary="Trigger a KB ingestion run from uploaded file content",
    )
    async def trigger_file_source(
        name: str = Form(...),
        topic: Optional[str] = Form(None),
        # FastAPI/Pydantic v2 emit OpenAPI 3.1's `contentMediaType` for
        # `UploadFile` items, which older Swagger UI builds don't render as
        # file pickers — force the OpenAPI 3.0-style `format: binary` hint
        # too so "Choose File" buttons show up instead of a text input.
        # Optional (not `File(...)`) so a request with zero files reaches
        # the `if not files:` check below and gets our flat ErrorResponse
        # envelope, instead of FastAPI's own "field required" 422 shape.
        files: Optional[List[UploadFile]] = File(
            default=None, json_schema_extra={"items": {"type": "string", "format": "binary"}}
        ),
        identity=Depends(require_admin),
    ) -> KBSourceStatus | JSONResponse:
        """Start a background ingestion of admin-uploaded file content into the KB.

        Admins don't have shell access to the running service, so this
        takes uploaded file content directly (multipart form) rather than
        a server-side filesystem path — unlike the offline CLI populate
        pipeline's ``type: local`` sources, which do read from disk.

        Returns ``202 Accepted`` immediately with a ``run_id`` and the
        ``running`` state. Poll ``GET /admin/kb/sources/status`` for
        completion. Returns ``409`` if a run is already in progress.
        """
        unavailable = _ingestion_unavailable_response()
        if unavailable is not None:
            return unavailable

        if not files:
            return _error_json(422, "no_files_uploaded", "at least one file must be uploaded")

        # Read + decode uploads now, before claiming the run: the uploaded
        # files' temporary storage is cleaned up once this request handler
        # returns, so the background task (which runs after the response is
        # sent) cannot read them itself.
        decoded_files: List[Tuple[str, str]] = []
        for upload in files:
            raw = await upload.read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return _error_json(
                    422,
                    "invalid_file_encoding",
                    f"file {upload.filename!r} is not valid UTF-8 text",
                )
            decoded_files.append((upload.filename or "unnamed", text))

        actor = identity.user_id
        run_id = str(uuid4())

        if not await _source_state.try_claim_run(run_id=run_id, source_name=name, source_type="file"):
            return _error_json(
                409,
                "kb_source_run_already_in_progress",
                "a KB source ingestion run is already in progress",
            )

        audit_logger.info(
            "kb.source.populate",
            extra={
                "action": "kb.source.populate",
                "actor_user_id": actor,
                "target_id": run_id,
                "source_name": name,
                "source_type": "file",
                "phase": "start",
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )

        asyncio.create_task(
            _run_file_source_ingestion(
                state=_source_state,
                actor=actor,
                run_id=run_id,
                kb_store=kb_store,
                embedding_client=embedding_client,
                embedding_model=embedding_model,
                chunker=_source_chunker,
                source_name=name,
                topic=topic,
                files=decoded_files,
            )
        )

        return _source_state.status

    @sources_router.get(
        "/status",
        response_model=KBSourceStatus,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Return current KB source-ingestion run state",
    )
    async def get_source_status(identity=Depends(require_admin)) -> KBSourceStatus:
        """Return the in-memory KB source-ingestion run state.

        Only a single run is ever in flight at a time (see ``try_claim_run``),
        so this always reflects that one global run — there's no per-run_id
        lookup. Only the most recent run is tracked (no history) and the
        state resets on each process restart. ``phase`` is one of: ``idle``,
        ``running``, ``completed``, ``failed``.
        """
        return _source_state.status

    app.include_router(sources_router)

    app.include_router(router)
    logger.info("Admin KB routes registered under %s/kb/documents and %s/kb/sources", prefix, prefix)
    return router


# ---------------------------------------------------------------------------
# Re-embed callback factory
# ---------------------------------------------------------------------------


def build_default_re_embed_callback(
    *,
    kb_store: BaseKBStore,
    bedrock_client,  # BedrockEmbeddingClient (or legacy BedrockClient)
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

    ``bedrock_client`` accepts either a ``BedrockEmbeddingClient`` (preferred)
    or the legacy ``BedrockClient`` — both expose ``generate_embeddings_batch``.
    """
    from ..rag.embedding_pipeline import TextChunker  # local import: heavy module

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
