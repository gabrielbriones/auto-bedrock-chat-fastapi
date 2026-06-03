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
from pydantic import BaseModel, ConfigDict

from .admin_errors import ADMIN_COMMON_RESPONSES
from .db.feedback_base import BaseFeedbackStore
from .db.kb_base import BaseKBStore
from .exceptions import AdminAPIError, AlreadyIntegratedError, FeedbackNotFoundError
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
    errors: list[str] = []
    #: ``None`` for batch runs; set to the feedback_id for per-entry runs.
    feedback_id: Optional[str] = None


class SingleEntrySynthesisResponse(BaseModel):
    """Wire shape for ``POST /admin/synthesis/trigger/{feedback_id}``."""

    model_config = ConfigDict(validate_assignment=True)

    tag: str
    action: str
    kb_doc_id: Optional[str] = None
    feedback_ids_marked: list[str] = []


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

    @property
    def status(self) -> SynthesisStatus:
        # Return a snapshot so callers cannot mutate our internal copy.
        return self._status.model_copy()

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
        already in progress.  Owning the lock internally keeps callers from
        needing to access ``_lock`` directly.
        """
        async with self._lock:
            if self._status.phase == RunPhase.RUNNING:
                return False
            self._mark_running(feedback_id=feedback_id)
            return True


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
    router = APIRouter()

    synthesis_prefix = f"{prefix}/synthesis"

    # ------------------------------------------------------------------
    # GET /admin/synthesis/status
    # ------------------------------------------------------------------

    @app.get(
        f"{synthesis_prefix}/status",
        response_model=SynthesisStatus,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Return current synthesis run state",
        tags=["admin-synthesis"],
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

    @app.post(
        f"{synthesis_prefix}/trigger",
        response_model=SynthesisStatus,
        status_code=202,
        responses={
            **ADMIN_COMMON_RESPONSES,
            409: {
                "description": "A synthesis run is already in progress",
            },
        },
        summary="Trigger a full-batch synthesis run",
        tags=["admin-synthesis"],
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

    @app.post(
        f"{synthesis_prefix}/trigger/{{feedback_id}}",
        response_model=SingleEntrySynthesisResponse,
        status_code=200,
        responses={
            **ADMIN_COMMON_RESPONSES,
            404: {"description": "Feedback entry not found"},
            409: {"description": "Entry is already integrated into the KB"},
            422: {"description": ("Entry is not in 'approved' state or is already integrated")},
        },
        summary="Synthesize a single approved feedback entry on demand",
        tags=["admin-synthesis"],
    )
    async def trigger_synthesis_for_entry(
        feedback_id: UUID,
        identity=Depends(require_admin),  # noqa: B008
    ) -> SingleEntrySynthesisResponse:
        """Synthesize a single approved feedback entry immediately.

        Intended for the per-review "Integrate into KB" button in the
        admin dashboard.  The entry must be ``approved``; returns ``422``
        otherwise.  Returns ``409`` if the entry is already integrated.
        Returns ``404`` if the entry does not exist.
        """
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
