"""Admin skill-management HTTP routes.

Registered by :meth:`AutoLangChatPlugin._setup_admin_routes` when
``admin_enabled=True`` and a skill store is configured. Every route is gated
by the ``require_admin`` dependency built in ``plugin.py``.

Endpoints
---------
* ``GET /admin/skills`` — list skills (without their instructions) and the
  configured size ``limits``.
* ``POST /admin/skills`` — create a skill at revision 1 (409 if the name is taken).
* ``GET /admin/skills/{name}`` — one skill, including its instructions.
* ``PUT /admin/skills/{name}`` — replace the whole skill content as a new
  revision. ``enabled`` is not touched. An optional ``expected_revision``
  turns on the conflict check.
* ``PATCH /admin/skills/{name}`` — change some content fields as a new
  revision; ``expected_revision`` is required.
* ``GET /admin/skills/{name}/revisions`` — revisions, newest first, without content.
* ``GET /admin/skills/{name}/revisions/{revision}`` — the content of one revision.
* ``POST /admin/skills/{name}/rollback`` — restore a revision's content as a
  new revision (optional ``expected_revision``).
* ``DELETE /admin/skills/{name}`` — permanently delete a skill and its history.
* ``POST /admin/skills/{name}/enable`` / ``.../disable`` — toggle availability
  (no new revision).

A stale ``expected_revision`` answers 409 ``revision_conflict`` with the
stored skill under ``current``; nothing is written.

Every write emits a structured ``bedrock.audit`` record carrying the actor,
the action, the skill name and its resulting revision — never the
instructions text.

The store is resolved per request (``get_skill_store``) because routes are
registered before the store is opened at startup; if it is unavailable by
then, every route answers 503.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse

from ..db.skill_base import BaseSkillStore
from ..exceptions import AdminAPIError, SkillAlreadyExistsError, SkillNotFoundError, SkillRevisionConflictError
from ..models import ErrorResponse
from ..skills.models import (
    CONTENT_FIELDS,
    SKILL_NAME_PATTERN,
    Skill,
    SkillCreateRequest,
    SkillLimits,
    SkillListResponse,
    SkillPatchRequest,
    SkillReplaceRequest,
    SkillRevision,
    SkillRevisionListResponse,
    SkillRollbackRequest,
    SkillSummary,
    validate_skill_limits,
)
from .admin_errors import ADMIN_COMMON_RESPONSES

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("bedrock.audit")

REVISION_CONFLICT = "revision_conflict"


class SkillRevisionConflictResponse(ErrorResponse):
    """409 body for a stale ``expected_revision``: the standard envelope plus the stored skill."""

    current: Skill


_RESPONSES: Dict[int, Dict[str, Any]] = {
    **ADMIN_COMMON_RESPONSES,
    409: {"model": ErrorResponse, "description": "A skill with this name already exists"},
    503: {"model": ErrorResponse, "description": "The skill store is unavailable"},
}

_CONTENT_WRITE_RESPONSES: Dict[int, Dict[str, Any]] = {
    **_RESPONSES,
    409: {
        "model": SkillRevisionConflictResponse,
        "description": "expected_revision is stale; the body carries the stored skill under `current`",
    },
}


def _not_found(name: str) -> AdminAPIError:
    return AdminAPIError(status_code=404, code="not_found", detail=f"skill {name} not found")


def _revision_conflict(exc: SkillRevisionConflictError) -> JSONResponse:
    body = SkillRevisionConflictResponse(code=REVISION_CONFLICT, detail=str(exc), current=exc.current)
    return JSONResponse(status_code=409, content=body.model_dump(mode="json", exclude={"errors"}))


def register_admin_skill_routes(
    app: FastAPI,
    *,
    prefix: str,
    get_skill_store: Callable[[], Optional[BaseSkillStore]],
    require_admin: Callable,
    chat_config: Any,
    on_change: Optional[Callable[[], None]] = None,
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
    on_change:
        Called after every successful write (``SkillService.mark_changed``),
        so the chat runtime rebinds ``load_skill`` and the catalog.

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

    def _audit(action: str, identity: Any, name: str, revision: int, **extra: Any) -> None:
        if on_change is not None:
            on_change()
        audit_logger.info(
            action,
            extra={
                "action": action,
                "actor_user_id": identity.user_id,
                "target_id": name,
                "revision": revision,
                **extra,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _write_content(
        store: BaseSkillStore,
        skill: Skill,
        identity: Any,
        action: str,
        expected_revision: Optional[int],
        **audit_extra: Any,
    ) -> Any:
        """Store ``skill``'s content as a new revision; a stale ``expected_revision`` returns the 409 response."""
        _check_limits(skill)
        try:
            written = await store.replace_skill(skill, updated_by=identity.user_id, expected_revision=expected_revision)
        except SkillNotFoundError as exc:
            raise _not_found(skill.name) from exc
        except SkillRevisionConflictError as exc:
            return _revision_conflict(exc)
        _audit(action, identity, skill.name, written.revision, **audit_extra)
        return written

    @router.get(
        "",
        response_model=SkillListResponse,
        responses={**_RESPONSES},
        summary="List skills (without instructions) and the configured size limits",
    )
    async def list_skills(identity=Depends(require_admin)) -> SkillListResponse:
        skills = await _store().list_skills()
        return SkillListResponse(
            items=[SkillSummary(**skill.model_dump(exclude={"instructions"})) for skill in skills],
            limits=SkillLimits(
                skill_instructions_max_chars=chat_config.skill_instructions_max_chars,
                skill_description_max_chars=chat_config.skill_description_max_chars,
                skill_max_parameters=chat_config.skill_max_parameters,
            ),
        )

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
        _audit("skill.create", identity, created.name, created.revision, enabled=created.enabled)
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
        responses={**_CONTENT_WRITE_RESPONSES},
        summary="Replace the whole content of a skill (new revision)",
    )
    async def replace_skill(name: str, body: SkillReplaceRequest, identity=Depends(require_admin)) -> Any:
        _require_valid_name(name)
        store = _store()
        skill = Skill(name=name, **body.model_dump(exclude={"expected_revision"}))
        return await _write_content(store, skill, identity, "skill.replace", body.expected_revision)

    @router.patch(
        "/{name}",
        response_model=Skill,
        responses={**_CONTENT_WRITE_RESPONSES},
        summary="Change some content fields of a skill (new revision, expected_revision required)",
    )
    async def patch_skill(name: str, body: SkillPatchRequest, identity=Depends(require_admin)) -> Any:
        _require_valid_name(name)
        store = _store()
        current = await store.get_skill(name)
        if current is None:
            raise _not_found(name)
        if current.revision != body.expected_revision:
            return _revision_conflict(
                SkillRevisionConflictError(
                    f"Skill {name!r} is at revision {current.revision}, not {body.expected_revision}",
                    current=current,
                )
            )
        content = {**current.model_dump(include=set(CONTENT_FIELDS)), **body.changes()}
        skill = Skill(name=name, **content)
        # The store re-checks the revision, so a write landing in between still conflicts.
        return await _write_content(store, skill, identity, "skill.patch", body.expected_revision)

    @router.get(
        "/{name}/revisions",
        response_model=SkillRevisionListResponse,
        responses={**_RESPONSES},
        summary="List the revisions of a skill, newest first (without content)",
    )
    async def list_skill_revisions(name: str, identity=Depends(require_admin)) -> SkillRevisionListResponse:
        _require_valid_name(name)
        try:
            items = await _store().list_revisions(name)
        except SkillNotFoundError as exc:
            raise _not_found(name) from exc
        return SkillRevisionListResponse(items=items)

    @router.get(
        "/{name}/revisions/{revision}",
        response_model=SkillRevision,
        responses={**_RESPONSES},
        summary="Get the full content of one revision of a skill",
    )
    async def get_skill_revision(name: str, revision: int, identity=Depends(require_admin)) -> SkillRevision:
        _require_valid_name(name)
        found = await _store().get_revision(name, revision)
        if found is None:
            raise AdminAPIError(
                status_code=404, code="not_found", detail=f"revision {revision} of skill {name} not found"
            )
        return found

    @router.post(
        "/{name}/rollback",
        response_model=Skill,
        responses={**_CONTENT_WRITE_RESPONSES},
        summary="Restore the content of an earlier revision as a new revision",
    )
    async def rollback_skill(name: str, body: SkillRollbackRequest, identity=Depends(require_admin)) -> Any:
        _require_valid_name(name)
        store = _store()
        target = await store.get_revision(name, body.revision)
        if target is None:
            if await store.get_skill(name) is None:
                raise _not_found(name)
            raise AdminAPIError(
                status_code=404, code="not_found", detail=f"revision {body.revision} of skill {name} not found"
            )
        skill = Skill(name=name, **target.model_dump(include=set(CONTENT_FIELDS)))
        return await _write_content(
            store, skill, identity, "skill.rollback", body.expected_revision, restored_revision=body.revision
        )

    @router.delete(
        "/{name}",
        status_code=204,
        responses={**_RESPONSES},
        summary="Permanently delete a skill and its revision history",
    )
    async def delete_skill(name: str, identity=Depends(require_admin)):
        _require_valid_name(name)
        try:
            revision = await _store().delete_skill(name)
        except SkillNotFoundError as exc:
            raise _not_found(name) from exc
        _audit("skill.delete", identity, name, revision)
        return None

    async def _set_enabled(name: str, enabled: bool, identity: Any) -> Skill:
        _require_valid_name(name)
        try:
            skill = await _store().set_enabled(name, enabled, updated_by=identity.user_id)
        except SkillNotFoundError as exc:
            raise _not_found(name) from exc
        _audit("skill.enable" if enabled else "skill.disable", identity, name, skill.revision)
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
