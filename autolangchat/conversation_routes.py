"""Conversation REST API — per-user, named, persisted conversations.

Registered by :meth:`AutoLangChatPlugin._setup_routes` when
``conversation_persistence_enabled=True`` and a :class:`BaseConversationStore`
backend is configured. LangGraph checkpoint data (accessed via
``chat_graph.aget_state``) remains the single source of truth for message
history — this module only manages the ``conversations`` metadata rows
(id, title, timestamps) and reads history through the checkpointer.

Endpoints (mounted at ``{prefix}/conversations``, e.g. ``/chat/conversations``)
------------------------------------------------------------------------------
* ``GET    /conversations?user_id=&limit=&offset=``            — list
* ``GET    /conversations/{conversation_id}``                   — metadata only
* ``GET    /conversations/{conversation_id}/messages?limit=&before=`` — history
* ``POST   /conversations``                                     — create
* ``PATCH  /conversations/{conversation_id}``                    — rename / update metadata
* ``DELETE /conversations/{conversation_id}``                    — delete one
* ``DELETE /conversations?user_id=``                             — delete all

Auth: every route requires ``require_conversation_user`` (built by
``AutoLangChatPlugin._build_require_conversation_user``) to resolve a caller
identity (401 otherwise), and every route enforces that the resolved
identity's ``user_id`` matches the resource being accessed — 403 for the
``user_id`` query-param endpoints, 404 (not "403 forbidden") for
path-addressed conversations, to avoid confirming that a conversation id
owned by someone else exists (same non-enumerable-404 principle already
used by the WebSocket ``conversation_load`` handler).

Error shape: plain FastAPI ``HTTPException`` with a ``{code, message}``
``detail`` dict — deliberately not the admin API's centrally-registered
``{code, detail}`` envelope (``AdminAPIError`` + ``register_admin_error_handlers``),
since that machinery is only wired when ``admin_enabled=True`` and
conversations are an independent, non-admin feature.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query

from .admin.admin_auth import AdminIdentity
from .db.conversation_base import BaseConversationStore
from .exceptions import ConversationNotFoundError
from .models import (
    ConversationCreateRequest,
    ConversationDeleteAllResponse,
    ConversationListResponse,
    ConversationMessageItem,
    ConversationMessagesResponse,
    ConversationResponse,
    ConversationUpdateRequest,
)

logger = logging.getLogger(__name__)


def _format_history_messages(raw_messages: List[Dict[str, Any]]) -> List[ConversationMessageItem]:
    """Convert LangGraph checkpoint message dicts to the API response shape.

    Mirrors ``WebSocketChatHandler._format_history_messages`` (kept as a
    separate copy rather than importing from ``websocket_handler`` to avoid
    a REST-routes -> WebSocket-handler import dependency for what is a
    small, stable mapping).
    """
    return [
        ConversationMessageItem(
            message_id=m.get("metadata", {}).get("message_id"),
            role=m.get("role"),
            content=m.get("content", ""),
            timestamp=m.get("metadata", {}).get("timestamp"),
            tool_calls=m.get("tool_calls", []),
            tool_results=m.get("tool_results", []),
            metadata=m.get("metadata", {}),
        )
        for m in raw_messages
    ]


def _ensure_self(user_id: str, identity: AdminIdentity) -> None:
    """Raise 403 unless ``user_id`` matches the authenticated caller."""
    if user_id != identity.user_id:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "You may only access your own conversations"},
        )


def _not_found() -> HTTPException:
    # Deliberately identical whether the id doesn't exist or belongs to
    # another user — see module docstring.
    return HTTPException(
        status_code=404,
        detail={"code": "conversation_not_found", "message": "Conversation not found"},
    )


def register_conversation_routes(
    app: FastAPI,
    *,
    prefix: str,
    conversation_store: BaseConversationStore,
    chat_graph: Any,
    require_conversation_user: Callable,
) -> APIRouter:
    """Register the ``{prefix}/conversations*`` routes on ``app``.

    Parameters
    ----------
    app:
        The host FastAPI application.
    prefix:
        Base path (e.g. ``config.chat_endpoint``, typically ``"/chat"``).
        Routes are mounted at ``{prefix}/conversations*``.
    conversation_store:
        The active :class:`BaseConversationStore`. Caller is responsible
        for only invoking this function when a store is actually configured.
    chat_graph:
        The compiled LangGraph state machine — used for ``aget_state`` to
        read message history for the ``/messages`` endpoint.
    require_conversation_user:
        Async dependency resolving the caller's identity (an
        :class:`AdminIdentity`, reused here purely as a generic identity
        carrier — no admin-authorization semantics apply). Raises 401 if
        no identity can be resolved.

    Returns
    -------
    APIRouter
        The router that was attached to ``app`` (for tests/tooling).
    """
    router = APIRouter(prefix=f"{prefix}/conversations", tags=["conversations"])

    @router.get("", response_model=ConversationListResponse, summary="List a user's conversations")
    async def list_conversations(
        user_id: str = Query(..., description="Must match the authenticated caller's own user id"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        identity: AdminIdentity = Depends(require_conversation_user),
    ) -> ConversationListResponse:
        _ensure_self(user_id, identity)
        items = await conversation_store.list_conversations(identity.user_id, limit=limit, offset=offset)
        total = await conversation_store.get_conversation_count(identity.user_id)
        return ConversationListResponse(
            items=[ConversationResponse(**item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    @router.post("", response_model=ConversationResponse, status_code=201, summary="Create a new conversation")
    async def create_conversation(
        body: ConversationCreateRequest,
        identity: AdminIdentity = Depends(require_conversation_user),
    ) -> ConversationResponse:
        _ensure_self(body.user_id, identity)
        conversation_id = str(uuid.uuid4())
        await conversation_store.create_conversation(conversation_id, identity.user_id, title=body.title)
        conversation = await conversation_store.get_conversation(conversation_id)
        assert conversation is not None  # just written; defensive only
        return ConversationResponse(**conversation)

    @router.delete("", response_model=ConversationDeleteAllResponse, summary="Delete all of a user's conversations")
    async def delete_all_conversations(
        user_id: str = Query(..., description="Must match the authenticated caller's own user id"),
        identity: AdminIdentity = Depends(require_conversation_user),
    ) -> ConversationDeleteAllResponse:
        _ensure_self(user_id, identity)
        deleted_count = await conversation_store.delete_all_conversations(identity.user_id)
        return ConversationDeleteAllResponse(deleted_count=deleted_count)

    @router.get("/{conversation_id}", response_model=ConversationResponse, summary="Get one conversation's metadata")
    async def get_conversation(
        conversation_id: str,
        identity: AdminIdentity = Depends(require_conversation_user),
    ) -> ConversationResponse:
        conversation = await conversation_store.get_conversation(conversation_id)
        if conversation is None or conversation.get("user_id") != identity.user_id:
            raise _not_found()
        return ConversationResponse(**conversation)

    @router.patch("/{conversation_id}", response_model=ConversationResponse, summary="Rename / update a conversation")
    async def update_conversation(
        conversation_id: str,
        body: ConversationUpdateRequest,
        identity: AdminIdentity = Depends(require_conversation_user),
    ) -> ConversationResponse:
        conversation = await conversation_store.get_conversation(conversation_id)
        if conversation is None or conversation.get("user_id") != identity.user_id:
            raise _not_found()
        try:
            await conversation_store.update_conversation(conversation_id, title=body.title, metadata=body.metadata)
        except ConversationNotFoundError:
            # Race: deleted between the ownership check above and this call.
            raise _not_found()
        updated = await conversation_store.get_conversation(conversation_id)
        assert updated is not None  # just updated; defensive only
        return ConversationResponse(**updated)

    @router.delete("/{conversation_id}", status_code=204, summary="Delete one conversation")
    async def delete_conversation(
        conversation_id: str,
        identity: AdminIdentity = Depends(require_conversation_user),
    ) -> None:
        conversation = await conversation_store.get_conversation(conversation_id)
        if conversation is None or conversation.get("user_id") != identity.user_id:
            raise _not_found()
        await conversation_store.delete_conversation(conversation_id)
        return None

    @router.get(
        "/{conversation_id}/messages",
        response_model=ConversationMessagesResponse,
        summary="Get a conversation's message history",
    )
    async def get_conversation_messages(
        conversation_id: str,
        limit: int = Query(50, ge=1, le=500),
        before: Optional[str] = Query(
            None, description="message_id cursor; returns the `limit` messages immediately before this one"
        ),
        identity: AdminIdentity = Depends(require_conversation_user),
    ) -> ConversationMessagesResponse:
        conversation = await conversation_store.get_conversation(conversation_id)
        if conversation is None or conversation.get("user_id") != identity.user_id:
            raise _not_found()

        cfg = {"configurable": {"thread_id": conversation_id}}
        checkpoint_state = await chat_graph.aget_state(cfg)
        checkpoint_values = checkpoint_state.values if checkpoint_state else None
        if not checkpoint_values:
            # Process likely restarted since this conversation was created
            # and the active checkpointer is MemorySaver (non-persistent).
            # Distinct from "no messages yet" — see module docstring.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "conversation_history_unavailable",
                    "message": "This conversation's message history is unavailable",
                },
            )

        raw_messages: List[Dict[str, Any]] = checkpoint_values.get("messages", [])
        formatted = _format_history_messages(raw_messages)

        if before:
            cursor_idx = next((i for i, m in enumerate(formatted) if m.message_id == before), None)
            window = formatted[:cursor_idx] if cursor_idx is not None else formatted
        else:
            window = formatted

        page = window[-limit:] if limit else window
        return ConversationMessagesResponse(conversation_id=conversation_id, messages=page)

    app.include_router(router)
    logger.info("Conversation REST routes registered at %s/conversations", prefix)
    return router
