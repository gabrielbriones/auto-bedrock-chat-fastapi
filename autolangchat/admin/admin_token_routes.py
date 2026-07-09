"""Admin Token Usage Analytics HTTP routes.

Registered by :meth:`AutoLangChatPlugin._setup_admin_routes` when
``admin_enabled=True`` and a ``_token_usage_store`` is configured. Every
route is gated by the ``require_admin`` dependency built in ``plugin.py``.

Endpoints
---------
* ``GET /admin/tokens/summary`` — per-model aggregate token usage.
* ``GET /admin/tokens/by-user`` — per-turn token usage rows for one user.
* ``GET /admin/tokens/by-day`` — per-day aggregate token usage within a
  date range.
* ``GET /admin/tokens/top-users`` — top users ranked by combined token
  usage.

These routes only ever read from :class:`BaseTokenUsageStore`; they never
write, so there is no audit-log entry to emit (unlike the feedback PATCH
endpoint).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, Query

from ..db.token_usage_base import BaseTokenUsageStore
from ..exceptions import AdminAPIError
from ..models import TokenByDayResponse, TokenByUserResponse, TokenSummaryResponse, TokenTopUsersResponse
from .admin_errors import ADMIN_COMMON_RESPONSES

logger = logging.getLogger(__name__)


# Bound checks for the ``limit`` query parameters. Kept module-level so
# tests can introspect them and the OpenAPI examples stay in sync.
_BY_USER_LIMIT_DEFAULT = 50
_BY_USER_LIMIT_MAX = 200
_TOP_USERS_LIMIT_DEFAULT = 10
_TOP_USERS_LIMIT_MAX = 100


def register_admin_token_routes(
    app: FastAPI,
    *,
    prefix: str,
    token_usage_store: BaseTokenUsageStore,
    require_admin: Callable,
) -> APIRouter:
    """Register the ``/admin/tokens*`` routes on ``app``.

    Parameters
    ----------
    app:
        The host FastAPI application.
    prefix:
        Full route prefix (e.g. ``"/chat/admin"``). The four routes are
        mounted at ``{prefix}/tokens*``.
    token_usage_store:
        The active :class:`BaseTokenUsageStore`. Accepts the abstract
        interface (not ``SQLiteTokenUsageStore``) so these routes work with
        both backends. May be ``None``-checked by the caller; this function
        expects a non-None instance.
    require_admin:
        The authentication/authorization dependency built by
        :meth:`AutoLangChatPlugin._setup_admin_routes`.

    Returns
    -------
    APIRouter
        The router that was attached to ``app`` (for tests and tooling).
    """
    router = APIRouter(prefix=f"{prefix}/tokens", tags=["admin-tokens"])

    @router.get(
        "/summary",
        response_model=TokenSummaryResponse,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Aggregate token usage per model",
    )
    async def tokens_summary(identity=Depends(require_admin)) -> TokenSummaryResponse:
        items = await token_usage_store.aggregate_by_model()
        return TokenSummaryResponse(items=items)

    @router.get(
        "/by-user",
        response_model=TokenByUserResponse,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="List per-turn token usage for one user",
    )
    async def tokens_by_user(
        user_id: str = Query(..., description="Return rows for this user only"),
        limit: int = Query(_BY_USER_LIMIT_DEFAULT, ge=1, le=_BY_USER_LIMIT_MAX),
        offset: int = Query(0, ge=0),
        identity=Depends(require_admin),
    ) -> TokenByUserResponse:
        items = await token_usage_store.list_by_user(user_id, limit=limit, offset=offset)
        return TokenByUserResponse(user_id=user_id, items=items)

    @router.get(
        "/by-day",
        response_model=TokenByDayResponse,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Aggregate token usage per UTC calendar day",
    )
    async def tokens_by_day(
        start: datetime = Query(..., description="Inclusive lower bound (ISO 8601)"),
        end: datetime = Query(..., description="Exclusive upper bound (ISO 8601)"),
        identity=Depends(require_admin),
    ) -> TokenByDayResponse:
        # ``aggregate_by_day`` also validates this, but we check here so the
        # 400 is raised before any DB round-trip and with a specific code —
        # mirroring the date_from/date_to validation in
        # admin_feedback_routes.py.
        if end <= start:
            raise AdminAPIError(status_code=400, code="invalid_date_range", detail="end must be after start")
        items = await token_usage_store.aggregate_by_day(start, end)
        return TokenByDayResponse(items=items)

    @router.get(
        "/top-users",
        response_model=TokenTopUsersResponse,
        responses={**ADMIN_COMMON_RESPONSES},
        summary="Top users ranked by combined token usage",
    )
    async def tokens_top_users(
        limit: int = Query(_TOP_USERS_LIMIT_DEFAULT, ge=1, le=_TOP_USERS_LIMIT_MAX),
        identity=Depends(require_admin),
    ) -> TokenTopUsersResponse:
        items = await token_usage_store.aggregate_by_user(limit=limit)
        return TokenTopUsersResponse(items=items)

    app.include_router(router)
    logger.debug("Admin token-usage routes registered (prefix=%s/tokens)", prefix)

    return router
