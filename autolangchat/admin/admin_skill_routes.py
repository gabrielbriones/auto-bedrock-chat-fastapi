"""Admin skill-management HTTP routes.

Registered by :meth:`AutoLangChatPlugin._setup_admin_routes` when
``admin_enabled=True`` and a skill store is configured. Every route is gated
by the ``require_admin`` dependency built in ``plugin.py``.

Endpoints
---------
* ``GET /admin/skills`` — list skills (without their instructions).
* ``POST /admin/skills`` — create a skill (409 if the name is taken).
* ``GET /admin/skills/{name}`` — one skill, including its instructions.
* ``PUT /admin/skills/{name}`` — replace the whole skill content (last write
  wins; there is no versioning). ``enabled`` is not touched.
* ``DELETE /admin/skills/{name}`` — permanently delete a skill.
* ``POST /admin/skills/{name}/enable`` / ``.../disable`` — toggle availability.

Every write emits a structured ``bedrock.audit`` record carrying the actor,
the action and the skill name — never the instructions text.

The store is resolved per request (``get_skill_store``) because routes are
registered before the store is opened at startup; if it is unavailable by
then, every route answers 503.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI

from ..db.skill_base import BaseSkillStore
from ..exceptions import AdminAPIError, SkillAlreadyExistsError, SkillNotFoundError
from ..models import ErrorResponse
from ..skills.models import (
    SKILL_NAME_PATTERN,
    Skill,
    SkillCreateRequest,
    SkillListResponse,
    SkillReplaceRequest,
    SkillSummary,
    validate_skill_limits,
)
from .admin_errors import ADMIN_COMMON_RESPONSES

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("bedrock.audit")

_RESPONSES: Dict[int, Dict[str, Any]] = {
    **ADMIN_COMMON_RESPONSES,
    409: {"model": ErrorResponse, "description": "A skill with this name already exists"},
    503: {"model": ErrorResponse, "description": "The skill store is unavailable"},
}


def _not_found(name: str) -> AdminAPIError:
    return AdminAPIError(status_code=404, code="not_found", detail=f"skill {name} not found")


def register_admin_skill_routes(
    app: FastAPI,
    *,
    prefix: str,
    get_skill_store: Callable[[], Optional[BaseSkillStore]],
    require_admin: Callable,
    chat_config: Any,
) -> APIRouter:
    """Register the ``/admin/skills*`` routes on ``app``.

    Parameters
    ----------
    app:
        The host FastAPI application.
    prefix:
        Full admin route prefix (e.g. ``"/chat/admin"``). Routes are mounted
        at ``{prefix}/skills*``.
    get_skill_store:
        Returns the active :class:`BaseSkillStore`, or ``None`` when skills
        are unavailable (e.g. the store failed to open at startup).
    require_admin:
        The authentication/authorization dependency built by
        :meth:`AutoLangChatPlugin._setup_admin_routes`.
    chat_config:
        Source of the ``skill_*_max_*`` limits, read on every write.

    Returns
    -------
    APIRouter
        The router that was attached to ``app`` (for tests and tooling).
    """
    router = APIRouter(prefix=f"{prefix}/skills", tags=["admin-skills"])

    def _store() -> BaseSkillStore:
        store = get_skill_store()
        if store is None:
            raise AdminAPIError(status_code=503, code="skills_unavailable", detail="the skill store is unavailable")
        return store

    def _check_limits(skill: Skill) -> None:
        try:
            validate_skill_limits(
                skill,
                instructions_max_chars=chat_config.skill_instructions_max_chars,
                description_max_chars=chat_config.skill_description_max_chars,
                max_parameters=chat_config.skill_max_parameters,
            )
        except ValueError as exc:
            raise AdminAPIError(status_code=400, code="invalid_skill", detail=str(exc)) from exc

    def _require_valid_name(name: str) -> None:
        # A name that can never exist is simply not found.
        if not SKILL_NAME_PATTERN.fullmatch(name):
            raise _not_found(name)

    def _audit(action: str, identity: Any, name: str, **extra: Any) -> None:
        audit_logger.info(
            action,
            extra={
                "action": action,
                "actor_user_id": identity.user_id,
                "target_id": name,
                **extra,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )

    @router.get(
        "",
        response_model=SkillListResponse,
        responses={**_RESPONSES},
        summary="List skills (without instructions)",
    )
    async def list_skills(identity=Depends(require_admin)) -> SkillListResponse:
        skills = await _store().list_skills()
        return SkillListResponse(items=[SkillSummary(**skill.model_dump(exclude={"instructions"})) for skill in skills])

    @router.post(
        "",
        status_code=201,
        response_model=Skill,
        responses={**_RESPONSES},
        summary="Create a skill",
    )
    async def create_skill(body: SkillCreateRequest, identity=Depends(require_admin)) -> Skill:
        store = _store()
        skill = Skill(**body.model_dump())
        _check_limits(skill)
        try:
            created = await store.create_skill(skill, updated_by=identity.user_id)
        except SkillAlreadyExistsError as exc:
            raise AdminAPIError(status_code=409, code="skill_exists", detail=str(exc)) from exc
        _audit("skill.create", identity, created.name, enabled=created.enabled)
        return created

    @router.get(
        "/{name}",
        response_model=Skill,
        responses={**_RESPONSES},
        summary="Get one skill, including its instructions",
    )
    async def get_skill(name: str, identity=Depends(require_admin)) -> Skill:
        _require_valid_name(name)
        skill = await _store().get_skill(name)
        if skill is None:
            raise _not_found(name)
        return skill

    @router.put(
        "/{name}",
        response_model=Skill,
        responses={**_RESPONSES},
        summary="Replace the whole content of a skill",
    )
    async def replace_skill(name: str, body: SkillReplaceRequest, identity=Depends(require_admin)) -> Skill:
        _require_valid_name(name)
        store = _store()
        skill = Skill(name=name, **body.model_dump())
        _check_limits(skill)
        try:
            replaced = await store.replace_skill(skill, updated_by=identity.user_id)
        except SkillNotFoundError as exc:
            raise _not_found(name) from exc
        _audit("skill.replace", identity, name)
        return replaced

    @router.delete(
        "/{name}",
        status_code=204,
        responses={**_RESPONSES},
        summary="Permanently delete a skill",
    )
    async def delete_skill(name: str, identity=Depends(require_admin)):
        _require_valid_name(name)
        try:
            await _store().delete_skill(name)
        except SkillNotFoundError as exc:
            raise _not_found(name) from exc
        _audit("skill.delete", identity, name)
        return None

    async def _set_enabled(name: str, enabled: bool, identity: Any) -> Skill:
        _require_valid_name(name)
        try:
            skill = await _store().set_enabled(name, enabled, updated_by=identity.user_id)
        except SkillNotFoundError as exc:
            raise _not_found(name) from exc
        _audit("skill.enable" if enabled else "skill.disable", identity, name)
        return skill

    @router.post(
        "/{name}/enable",
        response_model=Skill,
        responses={**_RESPONSES},
        summary="Enable a skill",
    )
    async def enable_skill(name: str, identity=Depends(require_admin)) -> Skill:
        return await _set_enabled(name, True, identity)

    @router.post(
        "/{name}/disable",
        response_model=Skill,
        responses={**_RESPONSES},
        summary="Disable a skill",
    )
    async def disable_skill(name: str, identity=Depends(require_admin)) -> Skill:
        return await _set_enabled(name, False, identity)

    app.include_router(router)
    return router
