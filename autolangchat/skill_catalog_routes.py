"""User-facing skill catalog route.

``GET {chat_endpoint}/skills/catalog`` lists the *enabled* skills so the chat
UI can offer them. It returns each skill's ``name``, ``display_name``,
``description`` and ``parameters`` only — never ``instructions`` — and never a
disabled skill.

Independent of ``admin_enabled``: this is a regular user-facing feature. The
caller's identity is resolved by the injected ``require_catalog_user``
dependency (401 when it cannot be resolved; the plugin skips the check
entirely when ``require_tool_auth=False``).

The store is resolved per request because the route is registered before the
store is opened at startup; if it is unavailable by then the route answers 503.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from .db.skill_base import BaseSkillStore
from .skills.models import SkillCatalogResponse

logger = logging.getLogger(__name__)


def register_skill_catalog_route(
    app: FastAPI,
    *,
    prefix: str,
    get_skill_store: Callable[[], Optional[BaseSkillStore]],
    require_catalog_user: Callable,
) -> APIRouter:
    """Register ``GET {prefix}/skills/catalog`` on ``app``.

    Parameters
    ----------
    app:
        The host FastAPI application.
    prefix:
        Base path (e.g. ``config.chat_endpoint``, typically ``"/chat"``).
    get_skill_store:
        Returns the active :class:`BaseSkillStore`, or ``None`` when skills
        are unavailable.
    require_catalog_user:
        Async dependency that resolves the caller's identity and raises 401
        when it cannot (or returns ``None`` when no authentication is
        required).

    Returns
    -------
    APIRouter
        The router that was attached to ``app`` (for tests/tooling).
    """
    router = APIRouter(prefix=f"{prefix}/skills", tags=["skills"])

    @router.get(
        "/catalog",
        response_model=SkillCatalogResponse,
        summary="List the enabled skills (without instructions)",
    )
    async def skill_catalog(identity=Depends(require_catalog_user)) -> SkillCatalogResponse:
        store = get_skill_store()
        if store is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "skills_unavailable", "message": "Skills are unavailable"},
            )
        return SkillCatalogResponse(items=await store.list_enabled_catalog())

    app.include_router(router)
    return router
