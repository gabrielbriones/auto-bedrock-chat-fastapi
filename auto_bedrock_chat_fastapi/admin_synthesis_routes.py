"""Admin Synthesis HTTP routes.

Registered by :meth:`BedrockChatPlugin._setup_admin_routes` when
``admin_enabled=True`` **and** both a feedback store and KB store are
available.  Every route is gated by the ``require_admin`` dependency
built in ``plugin.py``.

Endpoints
---------
* ``GET  /admin/synthesis/status``              — current run state (ephemeral).
* ``POST /admin/synthesis/trigger``             — manual full-batch run.
* ``POST /admin/synthesis/trigger/{feedback_id}``
                                                — per-review on-demand synthesis.
* ``POST /admin/synthesis/rollback/{article_id}``
                                                — roll back a synthesized KB article.

Run state is purely in-memory; it is reset on each process restart.
Feedback entries that were mid-integration when the process died will
be retried on the next manual trigger because their
``integrated_into_kb_id`` is still ``NULL``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from .admin_errors import ADMIN_COMMON_RESPONSES
from .db.feedback_base import BaseFeedbackStore
from .db.kb_base import BaseKBStore
from .exceptions import AdminAPIError, AlreadyIntegratedError, FeedbackNotFoundError
from .models import ErrorResponse
from .synthesizer import FeedbackSynthesizer, SynthesisRunResult, TagGroupResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Run-state types
# ---------------------------------------------------------------------------


class RunPhase(str, Enum):
    """Lifecycle phase of the in-memory synthesis runner."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SynthesisStatus(BaseModel):
    """Wire shape for ``GET /admin/synthesis/status``."""

    model_config = ConfigDict(validate_assignment=True)

    phase: RunPhase = RunPhase.IDLE
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    total_integrated: int = 0
    errors: list[str] = Field(default_factory=list)
    #: ``None`` for batch runs; set to the feedback_id for per-entry runs.
    feedback_id: Optional[str] = None


class SingleEntrySynthesisResponse(BaseModel):
    """Wire shape for ``POST /admin/synthesis/trigger/{feedback_id}``."""

    model_config = ConfigDict(validate_assignment=True)

    tag: str
    action: str
    kb_doc_id: Optional[str] = None
    feedback_ids_marked: list[str] = Field(default_factory=list)


class RollbackRequest(BaseModel):
    """Optional request body for ``POST /admin/synthesis/rollback/{article_id}``."""

    model_config = ConfigDict(validate_assignment=True)

    reason: Optional[str] = None


class RollbackResponse(BaseModel):
    """Wire shape for a successful rollback response."""

    model_config = ConfigDict(validate_assignment=True)

    article_id: str
    rolled_back_at: datetime
    rolled_back_by: str
    reason: Optional[str] = None
    feedback_entries_reverted: int


# ---------------------------------------------------------------------------
# In-memory run state
# ---------------------------------------------------------------------------


class _RunState:
    """Mutable in-memory state for the synthesis runner.

    A single instance is shared by all routes registered in one
    :func:`register_admin_synthesis_routes` call.  Concurrent callers
    hitting ``POST /trigger`` are serialized by :attr:`_lock`; the ``GET
    /status`` endpoint reads without holding the lock (eventual
    consistency is acceptable for status checks).
    """

    def __init__(self) -> None:
        self._status = SynthesisStatus()
        self._lock = asyncio.Lock()
        self._entry_in_progress: int = 0  # count of in-flight per-entry runs

    @property
    def status(self) -> SynthesisStatus:
        # Return a deep snapshot so callers cannot mutate nested fields
        # (e.g. the ``errors`` list) on the returned object and affect our
        # internal state.
        return self._status.model_copy(deep=True)

    def _mark_running(self, feedback_id: Optional[str] = None) -> None:
        self._status = SynthesisStatus(
            phase=RunPhase.RUNNING,
            started_at=datetime.now(timezone.utc),
            feedback_id=feedback_id,
        )

    def _mark_completed(self, result: SynthesisRunResult) -> None:
        self._status = SynthesisStatus(
            phase=RunPhase.COMPLETED,
            started_at=self._status.started_at,
            finished_at=datetime.now(timezone.utc),
            total_integrated=result.total_integrated,
            errors=result.errors,
        )

    def _mark_failed(self, error: str) -> None:
        self._status = SynthesisStatus(
            phase=RunPhase.FAILED,
            started_at=self._status.started_at,
            finished_at=datetime.now(timezone.utc),
            errors=[error],
        )

    async def try_claim_run(self, feedback_id: Optional[str] = None) -> bool:
        """Atomically transition to RUNNING if not already in progress.

        Returns ``True`` if the claim succeeded; ``False`` if a run was
        already in progress or a per-entry run is in flight.  Owning the
        lock internally keeps callers from needing to access ``_lock``
        directly.
        """
        async with self._lock:
            if self._status.phase == RunPhase.RUNNING or self._entry_in_progress > 0:
                return False
            self._mark_running(feedback_id=feedback_id)
            return True

    async def try_claim_entry_run(self) -> bool:
        """Atomically check no batch is running and increment the in-flight counter.

        Returns ``True`` if the entry run was claimed; ``False`` if a batch
        run is currently in progress.  Callers **must** call
        :meth:`release_entry_run` in a ``finally`` block after the work
        completes to keep the counter accurate.
        """
        async with self._lock:
            if self._status.phase == RunPhase.RUNNING:
                return False
            self._entry_in_progress += 1
            return True

    def release_entry_run(self) -> None:
        """Decrement the in-flight per-entry counter."""
        self._entry_in_progress -= 1


# ---------------------------------------------------------------------------
# Registration factory
# ---------------------------------------------------------------------------


def register_admin_synthesis_routes(
    app: FastAPI,
    *,
    prefix: str,
    feedback_store: BaseFeedbackStore,
    kb_store: BaseKBStore,
    require_admin: Callable,
    synthesizer: Optional[FeedbackSynthesizer] = None,
    bedrock_client: Any,
) -> APIRouter:
    """Register the ``/admin/synthesis*`` routes on ``app``.

    Parameters
    ----------
    app:
        The host FastAPI application.
    prefix:
        Full route prefix (e.g. ``"/chat/admin"``). Routes are mounted at
        ``{prefix}/synthesis*``.
    feedback_store:
        Opened :class:`~.db.feedback_base.BaseFeedbackStore` instance.
    kb_store:
        Opened :class:`~.db.kb_base.BaseKBStore` instance.
    require_admin:
        FastAPI ``Depends``-compatible callable that raises 401/403 for
        unauthenticated / unauthorized callers.
    synthesizer:
        Optional pre-built :class:`~.synthesizer.FeedbackSynthesizer`.
        When ``None``, a default instance is created with no explicit
        ``model_id`` (falls back to ``bedrock_client.config.model_id`` at
        call time).
    bedrock_client:
        Wired Bedrock client passed through to the synthesizer.
    """
    synth = synthesizer or FeedbackSynthesizer()
    state = _RunState()
    router = APIRouter(prefix=f"{prefix}/synthesis", tags=["admin-synthesis"])

    # ------------------------------------------------------------------
    # GET /admin/synthesis/status
    # ------------------------------------------------------------------

    @router.get(
        "/status",
        response_model=SynthesisStatus,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Return current synthesis run state",
    )
    async def get_synthesis_status(identity=Depends(require_admin)) -> SynthesisStatus:  # noqa: B008
        """Return the in-memory synthesis run state.

        The state resets on each process restart.  ``phase`` is one of:
        ``idle``, ``running``, ``completed``, ``failed``.
        """
        return state.status

    # ------------------------------------------------------------------
    # POST /admin/synthesis/trigger  (full batch)
    # ------------------------------------------------------------------

    @router.post(
        "/trigger",
        response_model=SynthesisStatus,
        status_code=202,
        responses={
            **ADMIN_COMMON_RESPONSES,
            409: {
                "model": ErrorResponse,
                "description": "A synthesis run is already in progress",
            },
        },
        summary="Trigger a full-batch synthesis run",
    )
    async def trigger_synthesis(identity=Depends(require_admin)) -> SynthesisStatus:  # noqa: B008
        """Trigger an on-demand full-batch synthesis run.

        Processes all ``approved`` feedback entries where
        ``integrated_into_kb_id IS NULL``.

        Returns ``202 Accepted`` immediately with the new ``running``
        state.  Poll ``GET /admin/synthesis/status`` for completion.
        Returns ``409`` if a run is already in progress.
        """
        if not await state.try_claim_run():
            raise AdminAPIError(
                status_code=409,
                code="synthesis_already_running",
                detail="a synthesis run is already in progress",
            )

        asyncio.create_task(_run_batch(synth, feedback_store, kb_store, bedrock_client, state))

        return state.status

    # ------------------------------------------------------------------
    # POST /admin/synthesis/trigger/{feedback_id}  (per-entry)
    # ------------------------------------------------------------------

    @router.post(
        "/trigger/{feedback_id}",
        response_model=SingleEntrySynthesisResponse,
        status_code=200,
        responses={
            **ADMIN_COMMON_RESPONSES,
            404: {"model": ErrorResponse, "description": "Feedback entry not found"},
            409: {
                "model": ErrorResponse,
                "description": "Entry is already integrated into the KB or a batch run is in progress",
            },
            422: {"model": ErrorResponse, "description": "Entry is not in 'approved' state"},
            500: {"model": ErrorResponse, "description": "Synthesis failed internally"},
        },
        summary="Synthesize a single approved feedback entry on demand",
    )
    async def trigger_synthesis_for_entry(
        feedback_id: UUID,
        identity=Depends(require_admin),  # noqa: B008
    ) -> SingleEntrySynthesisResponse:
        """Synthesize a single approved feedback entry immediately.

        Intended for the per-review "Integrate into KB" button in the
        admin dashboard.  The entry must be ``approved``; returns ``422``
        otherwise.  Returns ``409`` if the entry is already integrated or
        if a batch run is currently in progress.  Returns ``404`` if the
        entry does not exist.
        """
        if not await state.try_claim_entry_run():
            raise AdminAPIError(
                status_code=409,
                code="synthesis_already_running",
                detail="a batch synthesis run is in progress; retry after it completes",
            )
        try:
            result: TagGroupResult = await synth.synthesize_entry(
                feedback_id,
                feedback_store,
                kb_store,
                bedrock_client,
            )
        except FeedbackNotFoundError as exc:
            raise AdminAPIError(
                status_code=404,
                code="not_found",
                detail=str(exc),
            ) from exc
        except AlreadyIntegratedError as exc:
            raise AdminAPIError(
                status_code=409,
                code="already_integrated",
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise AdminAPIError(
                status_code=422,
                code="synthesis_precondition_failed",
                detail=str(exc),
            ) from exc
        finally:
            state.release_entry_run()

        if result.error:
            raise AdminAPIError(
                status_code=500,
                code="synthesis_failed",
                detail=result.error,
            )

        return SingleEntrySynthesisResponse(
            tag=result.tag,
            action=result.action.value,
            kb_doc_id=result.kb_doc_id,
            feedback_ids_marked=[str(fid) for fid in result.feedback_ids_marked],
        )

    # ------------------------------------------------------------------
    # POST /admin/synthesis/rollback/{article_id}
    # ------------------------------------------------------------------

    @router.post(
        "/rollback/{article_id}",
        response_model=RollbackResponse,
        status_code=200,
        responses={
            **ADMIN_COMMON_RESPONSES,
            404: {"model": ErrorResponse, "description": "Article not found"},
            422: {"model": ErrorResponse, "description": "Article is not a synthesized (source='feedback') document"},
            500: {"model": ErrorResponse, "description": "Revert or delete failed; see `code` for which step failed"},
        },
        summary="Roll back a synthesized KB article",
    )
    async def rollback_article(
        article_id: str,
        request: Optional[RollbackRequest] = None,
        identity=Depends(require_admin),  # noqa: B008
    ) -> RollbackResponse:
        """Remove a synthesized KB article and revert its source feedback entries.

        Feedback entries are reverted first, then the KB document is deleted.
        This ordering ensures that if the revert step fails the system remains
        in a consistent state (KB document still present, feedback unchanged)
        rather than leaving feedback entries reverted with the KB doc intact.

        If the feedback revert step fails, HTTP 500 (`rollback_revert_failed`)
        is returned, the KB document is left intact, and an ERROR is logged.
        If the KB delete step fails after a successful revert, HTTP 500
        (`rollback_delete_failed`) is returned and an ERROR is logged.

        Returns 404 if the article does not exist.
        Returns 422 if the article was not created by the synthesizer
        (``source != 'feedback'``).
        """
        if request is None:
            request = RollbackRequest()

        doc = await asyncio.to_thread(kb_store.get_document, article_id)
        if doc is None:
            raise AdminAPIError(
                status_code=404,
                code="not_found",
                detail=f"KB article '{article_id}' not found",
            )

        if doc.get("source") != "feedback":
            raise AdminAPIError(
                status_code=422,
                code="not_synthesized",
                detail=(
                    f"KB article '{article_id}' has source='{doc.get('source')}'; "
                    "only synthesized articles (source='feedback') can be rolled back"
                ),
            )

        rolled_back_by: str = getattr(identity, "sub", None) or getattr(identity, "user_id", None) or str(identity)
        rolled_back_at = datetime.now(timezone.utc)

        # Revert feedback entries BEFORE deleting the KB document so that a
        # revert failure leaves the system in a consistent state (KB doc still
        # present, feedback entries unchanged) instead of partially rolled back.
        try:
            count = await feedback_store.revert_integrated(
                article_id,
                rolled_back_by=rolled_back_by,
                reason=request.reason,
            )
        except Exception as exc:
            logger.error(
                "Rollback: feedback revert failed for KB doc '%s' — "
                "source_feedback_ids=%s error=%s (KB doc NOT deleted)",
                article_id,
                (doc.get("metadata") or {}).get("source_feedback_ids", []),
                exc,
            )
            raise AdminAPIError(
                status_code=500,
                code="rollback_revert_failed",
                detail="Feedback entries could not be reverted; KB article was NOT removed. See server logs.",
            ) from exc

        try:
            await asyncio.to_thread(kb_store.delete_document, article_id)
        except Exception as exc:
            logger.error(
                "Rollback: feedback reverted for KB doc '%s' but delete failed — error=%s",
                article_id,
                exc,
            )
            raise AdminAPIError(
                status_code=500,
                code="rollback_delete_failed",
                detail="Feedback entries were reverted but KB article could not be deleted. See server logs.",
            ) from exc

        logger.info(
            "Rollback complete: article_id=%s rolled_back_by=%s reason=%r feedback_entries_reverted=%d",
            article_id,
            rolled_back_by,
            request.reason,
            count,
        )

        return RollbackResponse(
            article_id=article_id,
            rolled_back_at=rolled_back_at,
            rolled_back_by=rolled_back_by,
            reason=request.reason,
            feedback_entries_reverted=count,
        )

    app.include_router(router)
    return router


# ---------------------------------------------------------------------------
# Background task helper
# ---------------------------------------------------------------------------


async def _run_batch(
    synth: FeedbackSynthesizer,
    feedback_store: BaseFeedbackStore,
    kb_store: BaseKBStore,
    bedrock_client: Any,
    state: _RunState,
) -> None:
    """Run ``synthesize_all`` as an ``asyncio.create_task`` background task."""
    try:
        result = await synth.synthesize_all(feedback_store, kb_store, bedrock_client)
        state._mark_completed(result)
        logger.info(
            "Synthesis batch complete: integrated=%d errors=%d",
            result.total_integrated,
            len(result.errors),
        )
    except Exception as exc:  # pragma: no cover — defensive outer catch
        logger.exception("Synthesis batch failed with unhandled exception: %s", exc)
        state._mark_failed(str(exc))
