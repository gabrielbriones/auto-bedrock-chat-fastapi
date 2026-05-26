"""Admin API error handlers + OpenAPI metadata (XMGPLAT-10417, T6).

Registers a single set of FastAPI exception handlers that map every
admin-route error into the standardized :class:`ErrorResponse` envelope
``{code, detail, errors?}``. Routes can either raise :class:`AdminAPIError`
directly (for HTTP-shaped errors built at the route layer) or let the
domain-specific exceptions (:class:`FeedbackNotFoundError`,
:class:`InvalidStatusTransitionError`, :class:`KBDocumentNotFoundError`)
propagate — both paths produce identical wire format.

Why centralize:
    Previously each route did its own ``try/except`` + ``raise
    HTTPException(detail={"code": ..., "detail": ...})``, producing a
    nested ``{"detail": {"code": ..., "detail": ...}}`` body (the
    classic "detail within detail" wart). With the central handlers in
    place, routes can drop most of the try/except boilerplate and the
    wire format is flat: ``{"code": ..., "detail": ...}``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .exceptions import AdminAPIError, FeedbackNotFoundError, InvalidStatusTransitionError, KBDocumentNotFoundError
from .models import ErrorResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAPI ``responses=`` payload shared by every admin route.
# ---------------------------------------------------------------------------


# Standard error responses surfaced in /openapi.json. Individual routes
# extend or trim this dict via ``responses={**ADMIN_COMMON_RESPONSES, ...}``
# — 200/201/204 success codes stay on the per-route declaration so the
# success schema can be specific.
ADMIN_COMMON_RESPONSES: Dict[int, Dict[str, Any]] = {
    400: {
        "model": ErrorResponse,
        "description": "Invalid request (malformed query parameters or body)",
    },
    401: {
        "model": ErrorResponse,
        "description": "Not authenticated",
    },
    403: {
        "model": ErrorResponse,
        "description": "Authenticated but not authorized as an admin",
    },
    404: {
        "model": ErrorResponse,
        "description": "Resource not found",
    },
}

# Admin-feedback PATCH declares 409 for invalid status transitions.
# In practice this is currently unreachable via the HTTP surface: the
# ``ReviewUpdateRequest`` Pydantic validator rejects ``pending_review``
# targets before they reach the store, so every invalid-transition path
# is caught at the 422 level first.  The entry is kept so the OpenAPI
# spec stays honest about what the underlying store can raise, and to
# provide a correct error shape if the validator is ever relaxed.
ADMIN_FEEDBACK_PATCH_RESPONSES: Dict[int, Dict[str, Any]] = {
    **ADMIN_COMMON_RESPONSES,
    409: {
        "model": ErrorResponse,
        "description": (
            "Review-status transition not allowed (e.g. targeting "
            "'pending_review'). Currently unreachable via normal HTTP — "
            "the request validator blocks invalid targets at 422."
        ),
    },
}


# ---------------------------------------------------------------------------
# Error envelope construction
# ---------------------------------------------------------------------------


def _envelope(*, code: str, detail: str, errors: Any = None) -> Dict[str, Any]:
    """Build a JSON-serializable error envelope.

    Uses Pydantic round-trip via :class:`ErrorResponse` so the shape
    stays in lockstep with the OpenAPI schema.
    """
    payload: Dict[str, Any] = ErrorResponse(code=code, detail=detail, errors=errors).model_dump(exclude_none=True)
    return payload


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_admin_api_error(_request: Request, exc: AdminAPIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(code=exc.code, detail=exc.detail, errors=jsonable_encoder(exc.errors)),
    )


async def _handle_feedback_not_found(_request: Request, exc: FeedbackNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=_envelope(code="not_found", detail=str(exc) or "feedback not found"),
    )


async def _handle_invalid_status_transition(_request: Request, exc: InvalidStatusTransitionError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=_envelope(code="invalid_status_transition", detail=str(exc)),
    )


async def _handle_kb_document_not_found(_request: Request, exc: KBDocumentNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=_envelope(code="not_found", detail=str(exc) or "kb document not found"),
    )


def register_admin_error_handlers(app: FastAPI) -> None:
    """Attach the four admin error handlers to ``app``.

    Safe to call once per app. Handlers are application-scoped (FastAPI's
    ``add_exception_handler`` registry is per-app), so non-admin routes
    on the same app gain the same uniform mapping for these four
    exception types — which is fine, since they're all admin-layer
    domain errors that should never originate from a chat or health
    route.
    """
    app.add_exception_handler(AdminAPIError, _handle_admin_api_error)
    app.add_exception_handler(FeedbackNotFoundError, _handle_feedback_not_found)
    app.add_exception_handler(InvalidStatusTransitionError, _handle_invalid_status_transition)
    app.add_exception_handler(KBDocumentNotFoundError, _handle_kb_document_not_found)
    logger.info("Admin API error handlers registered")
