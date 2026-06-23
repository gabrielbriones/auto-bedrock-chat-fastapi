"""Admin Feedback Review HTTP routes.

Registered by :meth:`BedrockChatPlugin._setup_admin_routes` when
``admin_enabled=True``. Every route is gated by the ``require_admin``
dependency built in [plugin.py](plugin.py) — see T1.

Endpoints
---------
* ``GET /admin/feedback`` — paginated, filterable list of feedback entries.
* ``GET /admin/feedback/stats`` — aggregate counts + top tags + oldest pending age.
* ``GET /admin/feedback/{id}`` — fetch a single entry by id.
* ``PATCH /admin/feedback/{id}`` — apply a reviewer decision (approve / reject).

The PATCH endpoint emits a structured ``bedrock.audit`` log entry containing
``{action, actor_user_id, target_id, before, after, ts}`` so downstream log
shippers (CloudWatch, Loki, …) can build review-trail dashboards without
needing direct DB access.

No caching is performed for ``/stats``: admin traffic is human-paced, the
aggregate queries are indexed and run in milliseconds, and per-process
caching would be misleading in multi-worker deployments (matching the
pattern established for :class:`autolangchat.admin_auth.RemoteAdminAuthorizer`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from ..db.feedback_base import BaseFeedbackStore
from ..exceptions import AdminAPIError
from ..models import (
    FeedbackEntry,
    FeedbackListFilters,
    FeedbackListResponse,
    FeedbackStats,
    Rating,
    ReviewStatus,
    ReviewUpdateRequest,
)
from .admin_errors import ADMIN_COMMON_RESPONSES, ADMIN_FEEDBACK_PATCH_RESPONSES

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("bedrock.audit")


# Bound checks for the ``limit`` query parameter. Kept module-level so
# tests can introspect them and the OpenAPI examples stay in sync.
_LIMIT_DEFAULT = 50
_LIMIT_MAX = 200


def _parse_tags_csv(raw: Optional[str]) -> Optional[List[str]]:
    """Parse a comma-separated ``tags`` query value.

    ``None`` (parameter omitted) and an all-blank value both collapse to
    ``None`` — i.e. "no tag filter". The :class:`FeedbackListFilters`
    validator applies the same collapse for safety; doing it here keeps
    the 400 message specific when callers send malformed input via the
    HTTP layer.
    """
    if raw is None:
        return None
    parts = [t.strip() for t in raw.split(",") if t and t.strip()]
    return parts or None


def register_admin_feedback_routes(
    app: FastAPI,
    *,
    prefix: str,
    feedback_store: BaseFeedbackStore,
    require_admin: Callable,
) -> APIRouter:
    """Register the ``/admin/feedback*`` routes on ``app``.

    Parameters
    ----------
    app:
        The host FastAPI application.
    prefix:
        Full route prefix (e.g. ``"/chat/admin"``). The four routes are
        mounted at ``{prefix}/feedback*``.
    feedback_store:
        The active :class:`BaseFeedbackStore`. May be ``None``-checked by
        the caller; this function expects a non-None instance.
    require_admin:
        The authentication/authorization dependency built by
        :meth:`BedrockChatPlugin._setup_admin_routes`.

    Returns
    -------
    APIRouter
        The router that was attached to ``app`` (for tests and tooling).
    """
    router = APIRouter(prefix=f"{prefix}/feedback", tags=["admin-feedback"])

    @router.get(
        "",
        response_model=FeedbackListResponse,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="List feedback entries (paginated, filterable)",
    )
    async def list_feedback(
        request: Request,
        identity=Depends(require_admin),
        status: Optional[ReviewStatus] = Query(None, description="Filter by review status"),
        rating: Optional[Rating] = Query(None, description="Filter by user rating"),
        has_correction: Optional[bool] = Query(
            None,
            description=(
                "Filter by presence of a user-proposed correction. "
                "``true`` returns only entries that include correction_text; "
                "``false`` returns only entries without it."
            ),
        ),
        tags: Optional[str] = Query(
            None,
            description="Comma-separated reviewer tag overlap filter (e.g. 'perf,ipc')",
        ),
        user_id: Optional[str] = Query(None, description="Filter by submitting user"),
        date_from: Optional[datetime] = Query(None, description="Inclusive lower bound on created_at (ISO 8601)"),
        date_to: Optional[datetime] = Query(None, description="Exclusive upper bound on created_at (ISO 8601)"),
        limit: int = Query(_LIMIT_DEFAULT, ge=1, le=_LIMIT_MAX),
        offset: int = Query(0, ge=0),
    ) -> FeedbackListResponse:
        try:
            filters = FeedbackListFilters(
                status=status,
                rating=rating,
                has_correction=has_correction,
                tags=_parse_tags_csv(tags),
                user_id=user_id,
                date_from=date_from,
                date_to=date_to,
            )
        except ValidationError as exc:
            # Surface filter-validation errors (e.g. ``date_to <= date_from``)
            # as 400 with the offending field details. FastAPI would emit a
            # 422 if these were declared on the function signature, but we
            # build the model manually so we can collapse blank-only tag
            # lists, hence the manual catch.
            raise AdminAPIError(
                status_code=400,
                code="invalid_filters",
                detail="invalid filter parameters",
                errors=jsonable_encoder(exc.errors()),
            ) from exc

        items = await feedback_store.list_entries(filters, limit=limit, offset=offset)
        total = await feedback_store.count_entries(filters)
        return FeedbackListResponse(items=items, total=total, limit=limit, offset=offset)

    @router.get(
        "/stats",
        response_model=FeedbackStats,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Aggregate feedback statistics",
    )
    async def feedback_stats(identity=Depends(require_admin)) -> FeedbackStats:
        return await feedback_store.stats()

    @router.get(
        "/{feedback_id}",
        response_model=FeedbackEntry,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Fetch one feedback entry by id",
    )
    async def get_feedback(feedback_id: UUID, identity=Depends(require_admin)) -> FeedbackEntry:
        entry = await feedback_store.get(feedback_id)
        if entry is None:
            raise AdminAPIError(status_code=404, code="not_found", detail="feedback not found")
        return entry

    @router.patch(
        "/{feedback_id}",
        response_model=FeedbackEntry,
        responses={**ADMIN_FEEDBACK_PATCH_RESPONSES},
        summary="Apply a reviewer decision to a feedback entry",
    )
    async def patch_feedback(
        feedback_id: UUID,
        body: ReviewUpdateRequest,
        identity=Depends(require_admin),
    ) -> FeedbackEntry:
        actor = identity.user_id
        # Snapshot the "before" state for audit logging. ``get`` is cheap
        # (single-row PK lookup) and we'd hit the row anyway via
        # ``update_review`` — but doing it explicitly here means a missing
        # entry produces a clean 404 before we touch the write path.
        before = await feedback_store.get(feedback_id)
        if before is None:
            raise AdminAPIError(status_code=404, code="not_found", detail="feedback not found")

        # ``FeedbackNotFoundError`` (race: row deleted between get + update)
        # and ``InvalidStatusTransitionError`` propagate to the central
        # admin error handlers (404 / 409 respectively) — see
        # ``admin_errors.register_admin_error_handlers``.
        updated = await feedback_store.update_review(
            feedback_id,
            body.review_status,
            reviewer_id=actor,
            tags=body.reviewer_tags,
            comment=body.reviewer_comment,
        )

        audit_logger.info(
            "feedback.review.update",
            extra={
                "action": "feedback.review.update",
                "actor_user_id": actor,
                "target_id": str(feedback_id),
                "before": {
                    "status": before.review_status.value,
                    "tags": list(before.reviewer_tags),
                    "comment": before.reviewer_comment,
                },
                "after": {
                    "status": updated.review_status.value,
                    "tags": list(updated.reviewer_tags),
                    "comment": updated.reviewer_comment,
                },
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )
        return updated

    app.include_router(router)
    logger.info("Admin feedback routes registered under %s/feedback", prefix)
    return router
