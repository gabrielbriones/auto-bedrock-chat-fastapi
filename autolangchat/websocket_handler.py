"""WebSocket handler for real-time chat communication"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import httpx
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .auth_handler import AuthenticationHandler, AuthType, Credentials
from .config import ChatConfig
from .conversation_titler import generate_conversation_title
from .db import (
    AuthenticatedUserAuthorizer,
    BaseConversationStore,
    BaseFeedbackStore,
    BaseKBStore,
    BaseTokenUsageStore,
    FeedbackAuthorizer,
)
from .exceptions import (
    ConversationNotFoundError,
    FeedbackError,
    InvalidStatusTransitionError,
    UnauthorizedFeedbackError,
    WebSocketError,
)
from .graph.tools.manager import AuthInfo
from .models import FeedbackEntry, Rating
from .session_manager import ChatSession, ChatSessionManager

if TYPE_CHECKING:
    from .sso.sso_session_store import SSOSessionStore

logger = logging.getLogger(__name__)

# Upper bound on conversation_list's ``limit`` — mirrors the REST
# GET /chat/conversations endpoint's ``Query(50, ge=1, le=200)`` cap, so a
# client can't force a huge single-response allocation/serialization via
# the WebSocket path when the same guard already exists over REST.
_CONVERSATION_LIST_MAX_LIMIT = 200


def _get_sso_session_store_class():
    """Lazy import of SSOSessionStore (requires PyJWT)."""
    from .sso.sso_session_store import SSOSessionStore

    return SSOSessionStore


def _get_extract_user_id():
    """Lazy import of extract_user_id_from_sso_session (requires PyJWT)."""
    from .sso.sso_session_store import extract_user_id_from_sso_session

    return extract_user_id_from_sso_session


class WebSocketChatHandler:
    """Handles WebSocket connections and chat communication.

    The handler manages WebSocket transport, session lifecycle, and
    authentication.  LLM calls, message preprocessing, tool execution, and
    RAG retrieval are delegated to the LangGraph ``chat_graph`` compiled
    state machine.
    """

    def __init__(
        self,
        session_manager: ChatSessionManager,
        config: ChatConfig,
        chat_graph: Any,
        app_base_url: str = "http://localhost:8000",
        embedding_client: Optional[Any] = None,
        sso_session_store: Optional[SSOSessionStore] = None,
        kb_store: Optional[BaseKBStore] = None,
        feedback_store: Optional[BaseFeedbackStore] = None,
        feedback_authorizer: Optional[FeedbackAuthorizer] = None,
        token_usage_store: Optional[BaseTokenUsageStore] = None,
        conversation_store: Optional[BaseConversationStore] = None,
    ):
        self.session_manager = session_manager
        self.config = config
        self.app_base_url = app_base_url.rstrip("/")
        self.chat_graph = chat_graph
        self.embedding_client = embedding_client
        self.sso_session_store = sso_session_store
        self.kb_store = kb_store
        self.feedback_store = feedback_store
        self.feedback_authorizer: FeedbackAuthorizer = feedback_authorizer or AuthenticatedUserAuthorizer(
            allow_anonymous=getattr(config, "feedback_allow_anonymous", False)
        )
        self.token_usage_store = token_usage_store
        self.conversation_store = conversation_store
        # Strong references to fire-and-forget background tasks (e.g.
        # conversation auto-titling) so they aren't garbage-collected
        # mid-flight; each task removes itself on completion.
        self._background_tasks: set = set()

        self.http_client = httpx.AsyncClient(timeout=config.timeout)

        self._total_messages_handled = 0
        self._total_errors = 0

    async def handle_connection(
        self,
        websocket: WebSocket,
        user_id: Optional[str] = None,
        preferred_session_id: Optional[str] = None,
    ):
        """Handle new WebSocket connection.

        Parameters
        ----------
        websocket:
            The incoming WebSocket connection.
        user_id:
            Optional user identifier supplied by the application layer.
        preferred_session_id:
            When provided and a valid UUID, the session (and therefore the
            LangGraph ``thread_id``) is created with this value rather than a
            fresh UUID.  Clients can persist the ``session_id`` received in
            ``connection_established`` and send it back on reconnect to resume
            conversation history from the Postgres checkpoint.
        """

        try:
            # Accept WebSocket connection
            await websocket.accept()

            # Extract connection info
            user_agent = websocket.headers.get("user-agent")
            ip_address = self._get_client_ip(websocket)

            # SSO pre-auth: check for session token BEFORE creating session
            extracted_user_id = user_id  # Start with passed-in user_id (if any)
            sso_credentials = None
            sso_auth_handler = None
            sso_metadata = {}
            sso_display_name = None

            session_token = websocket.cookies.get("sso_session_token")
            if session_token and self.sso_session_store and self.config.sso_session_secret:
                # Validate and extract user info before session creation
                user_info = await self._validate_sso_token_and_extract_user(session_token)
                if user_info:
                    extracted_user_id = user_info["user_id"]
                    sso_credentials = user_info["credentials"]
                    sso_auth_handler = user_info["auth_handler"]
                    sso_metadata = user_info["metadata"]
                    sso_display_name = user_info["display_name"]
                    logger.debug(
                        "SSO pre-auth successful: user_id=%s, display_name=%s", extracted_user_id, sso_display_name
                    )

            # Create chat session with actual user_id (from SSO, passed parameter, or None)
            session_id = await self.session_manager.create_session(
                websocket=websocket,
                user_id=extracted_user_id,
                user_agent=user_agent,
                ip_address=ip_address,
                preferred_session_id=preferred_session_id,
            )

            logger.info(f"WebSocket connected: session={session_id}, user={extracted_user_id}, ip={ip_address}")

            # If SSO pre-auth succeeded, set credentials in the session
            if sso_credentials:
                session = await self.session_manager.get_session(websocket)
                if session:
                    session.credentials = sso_credentials
                    session.auth_handler = sso_auth_handler
                    session.metadata.update(sso_metadata)

                    # Send auth_configured message
                    await self._send_message(
                        websocket,
                        {
                            "type": "auth_configured",
                            "message": f"Authenticated as {sso_display_name}",
                            "auth_type": "sso",
                            "display_name": sso_display_name,
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                    logger.info(
                        "SSO auto-authentication configured for session %s (user: %s)",
                        session_id,
                        sso_display_name,
                    )

            # Send welcome message
            await self._send_message(
                websocket,
                {
                    "type": "connection_established",
                    "session_id": session_id,
                    "message": "Connected to AI assistant",
                    "timestamp": datetime.now().isoformat(),
                },
            )

            # Main message handling loop
            await self._message_loop(websocket)

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected normally")
        except Exception as e:
            logger.error(f"WebSocket connection error: {str(e)}")
            self._total_errors += 1

            try:
                await self._send_error(websocket, f"Connection error: {str(e)}")
            except BaseException:
                pass  # Connection already closed
        finally:
            # Clean up session
            await self.session_manager.remove_session(websocket)

    async def _message_loop(self, websocket: WebSocket):
        """Main message handling loop"""

        while True:
            try:
                # Receive message from client
                data = await websocket.receive_text()

                # Parse JSON
                try:
                    message_data = json.loads(data)
                except json.JSONDecodeError as e:
                    await self._send_error(websocket, f"Invalid JSON: {str(e)}")
                    continue

                # Handle different message types
                message_type = message_data.get("type", "chat")

                if message_type == "chat":
                    await self._handle_chat_message(websocket, message_data)
                elif message_type == "ping":
                    await self._handle_ping(websocket, message_data)
                elif message_type == "history":
                    await self._handle_history_request(websocket, message_data)
                elif message_type == "clear":
                    await self._handle_clear_history(websocket, message_data)
                elif message_type == "auth":
                    await self._handle_auth_message(websocket, message_data)
                elif message_type == "logout":
                    await self._handle_logout(websocket, message_data)
                elif message_type == "feedback":
                    await self._handle_feedback_message(websocket, message_data)
                elif message_type == "conversation_list":
                    await self._handle_conversation_list(websocket, message_data)
                elif message_type == "conversation_new":
                    await self._handle_conversation_new(websocket, message_data)
                elif message_type == "conversation_load":
                    await self._handle_conversation_load(websocket, message_data)
                elif message_type == "conversation_delete":
                    await self._handle_conversation_delete(websocket, message_data)
                elif message_type == "conversation_delete_all":
                    await self._handle_conversation_delete_all(websocket, message_data)
                elif message_type == "conversation_rename":
                    await self._handle_conversation_rename(websocket, message_data)
                else:
                    await self._send_error(websocket, f"Unknown message type: {message_type}")

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error in message loop: {str(e)}")
                self._total_errors += 1
                await self._send_error(websocket, f"Message processing error: {str(e)}")

    async def _handle_chat_message(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle incoming chat message"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        # SSO session expiry check
        if session.credentials and session.credentials.auth_type == AuthType.SSO and self.sso_session_store:
            session_token = session.credentials.session_token
            if session_token:
                sso_session_id = _get_sso_session_store_class().validate_session_token(
                    session_token, self.config.sso_session_secret
                )
                if not sso_session_id or not self.sso_session_store.get_session(sso_session_id):
                    # SSO session expired — notify client and clear credentials
                    session.credentials = None
                    session.auth_handler = None
                    await self._send_message(
                        websocket,
                        {
                            "type": "auth_expired",
                            "message": "Your SSO session has expired. Please log in again.",
                            "redirect_url": f"{self.config.chat_endpoint}/auth/sso/login",
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                    return

        user_message = data.get("message", "")
        if not user_message.strip():
            await self._send_error(websocket, "Empty message")
            return

        self._total_messages_handled += 1

        try:
            # Check if authentication is required before sending messages to LLM
            if self.config.require_tool_auth:
                auth_type_str = session.credentials.get_auth_type_string() if session.credentials else "none"
                if auth_type_str == "none":
                    await self._send_error(
                        websocket, "Authentication is required before sending messages. Please authenticate first."
                    )
                    return

            logger.debug(f"Received user message: {user_message}")

            # Send typing indicator
            await self._send_message(
                websocket,
                {
                    "type": "typing",
                    "message": "AI is thinking...",
                    "timestamp": datetime.now().isoformat(),
                },
            )

            # Auth context: format verified-user info for injection into system prompt.
            # The rag_node (inside the graph) handles the actual injection.
            auth_context_text: Optional[str] = None
            if self.config.include_auth_info_in_prompts:
                verified_user_info = session.metadata.get("verified_user_info")
                if verified_user_info:
                    auth_context_text = self._format_auth_context(verified_user_info)
                    logger.debug("Including authenticated user info in system prompt")

            # Build auth_info from session for tool call execution
            auth_info = AuthInfo(
                credentials=session.credentials,
                auth_handler=session.auth_handler,
                metadata=session.metadata,
            )

            # Closure: send progress updates to the WebSocket client
            async def _on_progress(msg_dict: Dict[str, Any]) -> None:
                await self._send_message(websocket, msg_dict)

            # ------------------------------------------------------------------
            # Resolve the LangGraph thread_id for this turn.
            #
            # When conversation persistence is enabled *and* the connection is
            # authenticated, the thread_id is the active conversation id
            # (session.metadata["conversation_id"]) rather than session_id —
            # this decouples the WebSocket connection identity from the
            # LangGraph conversation identity so a single connection can
            # switch between conversations via `conversation_load`/`conversation_new`.
            # Anonymous connections (no user_id) never get persisted
            # conversations — a shared "anonymous" bucket would let unrelated
            # anonymous sessions list each other's conversations — so they
            # keep the legacy session_id-as-thread_id behavior.
            # ------------------------------------------------------------------
            use_conversation_persistence = self._conversation_persistence_active_for_session(session)
            conversation_id: Optional[str] = None
            conversation_just_created = False
            if use_conversation_persistence:
                conversation_id = session.metadata.get("conversation_id")
                if conversation_id is None:
                    conversation_id = str(uuid.uuid4())
                    await self.conversation_store.create_conversation(conversation_id, session.user_id)
                    session.metadata["conversation_id"] = conversation_id
                    conversation_just_created = True
                    await self._send_message(
                        websocket,
                        {
                            "type": "conversation_created",
                            "conversation_id": conversation_id,
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                thread_id = conversation_id
            else:
                thread_id = session.session_id

            # ------------------------------------------------------------------
            # Delegate entirely to LangGraph.
            # Pass only ``user_message`` — the ``init_turn`` node prepends it
            # to the checkpointed conversation history inside the graph.
            # Because ``messages`` is absent from the input, LangGraph carries
            # it forward from the checkpoint automatically (total=False
            # TypedDict pass-through), so no manual aget_state is needed.
            # ------------------------------------------------------------------
            _turn_start = time.perf_counter()
            graph_state = await self.chat_graph.ainvoke(
                {"user_message": user_message},
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "on_progress": _on_progress,
                        "auth_info": auth_info,
                        "kb_store": self.kb_store,
                        "embedding_client": self.embedding_client,
                        "auth_context_text": auth_context_text,
                    }
                },
            )
            _turn_latency_ms = (time.perf_counter() - _turn_start) * 1000

            # Extract the final assistant message from graph state
            graph_messages = graph_state.get("messages", [])
            graph_metadata = graph_state.get("metadata", {})
            kb_results: List[Dict[str, Any]] = graph_state.get("kb_results") or []
            final_msg = graph_messages[-1] if graph_messages else {}
            # Graph messages are dicts: {"role": "assistant", "content": "...", ...}
            content = final_msg.get("content") or ""
            final_response = final_msg

            audit_logger = logging.getLogger("autochat.audit")
            audit_logger.info(
                "chat.turn",
                extra={
                    "action": "chat.turn",
                    "turn_latency_ms": round(_turn_latency_ms, 1),
                    "tool_call_rounds": graph_metadata.get("tool_call_rounds", 0),
                    "total_tool_calls": graph_metadata.get("total_tool_calls", 0),
                    "preprocessing_applied": graph_metadata.get("preprocessing_applied", False),
                    "kb_chunks": len(kb_results) if kb_results else 0,
                    "model_id": self.config.model_id,
                    "ts": datetime.now().astimezone().isoformat(),
                },
            )
            logger.debug(f"Chat graph response ({len(content):,} chars): {content[:100]}")

            response_metadata = final_response.get("metadata", {}).copy()
            response_metadata["model_id"] = self.config.model_id
            response_metadata["tool_call_rounds"] = graph_metadata.get("tool_call_rounds", 0)
            response_metadata["total_tool_calls"] = graph_metadata.get("total_tool_calls", 0)
            response_metadata["preprocessing_applied"] = graph_metadata.get("preprocessing_applied", False)
            # Surface token counts at the top level (bubbled by llm_call into graph_metadata)
            if graph_metadata.get("input_tokens") is not None:
                response_metadata["input_tokens"] = graph_metadata["input_tokens"]
            if graph_metadata.get("output_tokens") is not None:
                response_metadata["output_tokens"] = graph_metadata["output_tokens"]
            if kb_results:
                response_metadata["kb_used"] = True
                response_metadata["kb_chunks"] = len(kb_results)
                response_metadata["kb_sources"] = [
                    {
                        "document_id": r.get("document_id"),
                        "title": r.get("title"),
                        "source": r.get("source"),
                        "url": r.get("source_url"),
                        "score": r["similarity_score"],
                    }
                    for r in kb_results
                ]

            # Extract the stable message_id embedded by llm_call_node; fall
            # back to a fresh UUID only if the graph didn't produce one (tests).
            message_id = final_response.get("metadata", {}).get("message_id") or str(uuid.uuid4())

            # Store per-turn feedback metadata in the live session so the
            # feedback handler can recover query / kb_sources by message_id.
            session.metadata.setdefault("feedback_meta", {})[message_id] = {
                "query": user_message,
                "kb_sources": response_metadata.get("kb_sources", []),
            }

            # Send response to client
            await self._send_message(
                websocket,
                {
                    "type": "ai_response",
                    "message_id": message_id,
                    "message": final_response.get("content") or "",
                    "tool_calls": final_response.get("tool_calls", []),
                    "tool_results": final_response.get("tool_results", []),
                    "timestamp": datetime.now().isoformat(),
                    "metadata": response_metadata,
                    "conversation_id": conversation_id,
                },
            )

            # Persist the turn against the conversation metadata store
            # (best-effort, mirrors the token-usage persistence below — the
            # response has already been sent, so a failure here must not
            # surface as a second ``ai_response``).
            if use_conversation_persistence and conversation_id is not None:
                try:
                    await self.conversation_store.record_turn(conversation_id)
                except Exception:
                    logger.exception("Failed to record conversation turn for conversation_id=%s", conversation_id)

            # Auto-title a freshly created conversation in the background so
            # title-generation latency never delays the ai_response that was
            # just sent above. Fires once per conversation (only on the turn
            # that created it).
            if conversation_just_created and conversation_id is not None:
                self._spawn_background_task(
                    self._generate_and_apply_title(
                        websocket,
                        conversation_id,
                        [
                            {"role": "user", "content": user_message},
                            {"role": "assistant", "content": content},
                        ],
                    )
                )

            # Persist per-turn token usage (best-effort). This must never
            # affect chat delivery: the response has already been sent to
            # the client above, so a persistence failure here is logged and
            # swallowed rather than propagated to the outer except-block,
            # which would otherwise attempt to send a second (error)
            # ``ai_response`` after a successful one.
            if self.token_usage_store is not None:
                input_tokens = response_metadata.get("input_tokens")
                output_tokens = response_metadata.get("output_tokens")
                if input_tokens is not None and output_tokens is not None:
                    try:
                        # NOTE: intentionally NOT response_metadata["model_id"] —
                        # that field is unconditionally overwritten with the
                        # statically configured model a few lines above (for the
                        # client-facing payload). graph_metadata["model_id"] is
                        # the model that actually produced this response, which
                        # may be the fallback_model if a context-window retry
                        # occurred; that's what billing/observability needs.
                        await self.token_usage_store.record_turn(
                            turn_id=message_id,
                            session_id=session.session_id,
                            user_id=session.user_id,
                            model_id=graph_metadata.get("model_id") or self.config.model_id,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            turn_ts=datetime.now(timezone.utc),
                        )
                    except Exception:
                        logger.exception("Failed to record token usage for message_id=%s", message_id)

        except Exception as e:
            logger.error(f"Error processing chat message: {str(e)}")
            self._total_errors += 1

            # Send error to user
            error_response = self._create_error_response(str(e))
            await self._send_message(
                websocket,
                {
                    "type": "ai_response",
                    "message": error_response,
                    "error": True,
                    "timestamp": datetime.now().isoformat(),
                },
            )

    async def _handle_ping(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle ping message"""

        await self._send_message(websocket, {"type": "pong", "timestamp": datetime.now().isoformat()})

    async def _fetch_feedback_metadata(
        self,
        session: ChatSession,
        conversation_history: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """Call the optional enrichment endpoint and return its metadata dict.

        POSTs the session context and the already-filtered conversation history
        to ``config.feedback_metadata_enrichment_url`` and returns the JSON
        object the endpoint responds with, to be stored verbatim in
        ``FeedbackEntry.entry_metadata``.

        On any failure (network error, timeout, non-200 status, oversized or
        non-object body) the behaviour depends on
        ``config.feedback_metadata_enrichment_fail_on_error``: when True the
        error is re-raised as :class:`FeedbackError` (rejecting the
        submission); when False the error is logged at WARNING and an empty
        dict is returned so feedback collection still proceeds.
        """
        # Build the session payload field-by-field. Do NOT use
        # session.to_dict(): it emits duration_seconds (outside the contract)
        # and must never expose credentials / auth_handler.
        session_payload = {
            "session_id": session.session_id,
            "user_id": session.user_id,
            "user_agent": session.user_agent,
            "ip_address": session.ip_address,
            "metadata": session.metadata,
            "created_at": session.created_at.isoformat(),
            "last_activity": session.last_activity.isoformat(),
        }

        try:
            response = await self.http_client.post(
                self.config.feedback_metadata_enrichment_url,
                json={"session": session_payload, "conversation_history": conversation_history},
                # Per-request timeout override; do not rely on the global
                # self.http_client timeout for this call.
                timeout=self.config.feedback_metadata_enrichment_timeout,
            )

            if response.status_code != 200:
                raise ValueError(f"enrichment endpoint returned status {response.status_code}")

            metadata = response.json()
            if not isinstance(metadata, dict):
                raise ValueError("enrichment response body is not a JSON object")

            return metadata
        except Exception as exc:
            logger.warning(
                "Feedback metadata enrichment failed: %s (session=%s)",
                exc,
                session.session_id,
                exc_info=True,
            )
            if self.config.feedback_metadata_enrichment_fail_on_error:
                raise FeedbackError("Feedback metadata enrichment failed") from exc
            return {}

    async def _handle_feedback_message(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle a ``feedback`` message from the chat client.

        Validates the payload, recovers the original AI response context from
        session history (keyed by ``message_id``), enforces authorization,
        persists a :class:`FeedbackEntry` via the configured
        :class:`BaseFeedbackStore` backend (SQLite or Postgres, selected by
        :func:`autolangchat.db.create_feedback_store`), and
        replies with a ``feedback_ack`` envelope. Failures emit a
        dedicated ``feedback_error`` envelope (see
        :meth:`_send_feedback_error`).
        """
        # Best-effort: try to echo the client's message_id on every reply so
        # the UI can reconcile optimistic state. Missing/invalid payloads
        # may not have one — the client tolerates ``None``.
        #
        # Strict typing: only a *non-empty* ``str`` is accepted. A malicious
        # or buggy client could send a list/object/number, which would then
        # be echoed straight back into the JSON envelope and break the
        # browser handler (``CSS.escape`` / dataset comparisons expect a
        # string). We coerce anything else to ``None`` so the downstream
        # required-field check rejects the request with ``invalid_feedback``.
        raw_message_id = data.get("message_id") if isinstance(data, dict) else None
        message_id = raw_message_id if isinstance(raw_message_id, str) and raw_message_id else None

        if self.feedback_store is None:
            logger.warning(
                "Received feedback message but no FeedbackStore is configured; feedback collection is unavailable"
            )
            await self._send_feedback_error(
                websocket,
                "feedback_unavailable",
                "Feedback collection is not enabled",
                message_id=message_id,
            )
            return

        session = await self.session_manager.get_session(websocket)
        if not session:
            logger.warning("Received feedback message but session not found for websocket %s", websocket)
            await self._send_feedback_error(
                websocket,
                "feedback_unavailable",
                "Session not found",
                message_id=message_id,
            )
            return

        # Authorization (stub by default; access-control task swaps in the
        # real implementation).
        if not self.feedback_authorizer.can_submit(session.user_id):
            logger.warning(
                "Feedback rejected: unauthorized user_id=%s session=%s",
                session.user_id,
                session.session_id,
            )
            await self._send_feedback_error(
                websocket,
                "unauthorized_feedback",
                "You are not authorized to submit feedback",
                message_id=message_id,
            )
            return

        rating_raw = data.get("rating")
        if not message_id or not rating_raw:
            logger.warning("Invalid feedback payload: missing message_id or rating (session=%s)", session.session_id)
            await self._send_feedback_error(
                websocket,
                "invalid_feedback",
                "message_id and rating are required",
                message_id=message_id,
            )
            return

        try:
            rating = Rating(rating_raw)
        except ValueError:
            logger.warning("Invalid feedback payload: unknown rating %r (session=%s)", rating_raw, session.session_id)
            await self._send_feedback_error(
                websocket,
                "invalid_feedback",
                f"Unknown rating: {rating_raw!r}",
                message_id=message_id,
            )
            return

        # Recover the original assistant response by message_id from the
        # LangGraph checkpoint (source of truth for conversation history).
        thread_id = self._active_thread_id(session)
        if thread_id is None:
            await self._send_feedback_error(
                websocket,
                "no_active_conversation",
                "No active conversation. Start chatting or load a conversation first.",
                message_id=message_id,
            )
            return
        cfg: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        checkpoint_state = await self.chat_graph.aget_state(cfg)
        messages: List[Dict[str, Any]] = (checkpoint_state.values or {}).get("messages", []) if checkpoint_state else []
        ai_message_dict = next(
            (
                m
                for m in messages
                if m.get("metadata", {}).get("message_id") == message_id and m.get("role") == "assistant"
            ),
            None,
        )
        if ai_message_dict is None:
            await self._send_feedback_error(
                websocket,
                "invalid_feedback",
                f"No assistant message found for message_id={message_id!r}",
                message_id=message_id,
            )
            return

        # Slice the preceding conversation context window for feedback
        max_context = self.config.feedback_max_history_context
        conversation_history: List[Dict[str, str]] = []

        if max_context > 0:
            ai_idx = next(
                (
                    i
                    for i, m in enumerate(messages)
                    if m.get("metadata", {}).get("message_id") == message_id and m.get("role") == "assistant"
                ),
                -1,
            )
            if ai_idx > 0:
                preceding = [
                    {"role": m.get("role", ""), "content": m.get("content", "")}
                    for m in messages[:ai_idx]
                    if m.get("role") in ("user", "assistant")
                ]
                conversation_history = preceding[-max_context:]

        # Recover query and kb_sources from per-turn feedback metadata stored
        # in the live session object.  Fall back to scanning preceding history
        # when the session was reconnected after the turn (feedback_meta lost).
        fb_meta = session.metadata.get("feedback_meta", {}).get(message_id, {})
        query = fb_meta.get("query", "")
        if not query:
            # Scan backwards for the preceding user message
            ai_idx = next(
                (i for i, m in enumerate(messages) if m.get("metadata", {}).get("message_id") == message_id),
                -1,
            )
            for i in range(ai_idx - 1, -1, -1):
                if messages[i].get("role") == "user":
                    query = messages[i].get("content", "")
                    break

        kb_sources = fb_meta.get("kb_sources", [])
        model_id_from_msg = ai_message_dict.get("metadata", {}).get("model_id")

        # Optional pluggable metadata enrichment (no-op when URL is unset).
        entry_metadata: Dict[str, Any] = {}
        if self.config.feedback_metadata_enrichment_url is not None:
            try:
                entry_metadata = await self._fetch_feedback_metadata(session, conversation_history)
            except FeedbackError as exc:
                await self._send_feedback_error(websocket, "feedback_error", str(exc), message_id=message_id)
                return

        normalized_user_id = (session.user_id or "").strip()
        effective_user_id = normalized_user_id or "anonymous"
        try:
            entry = FeedbackEntry(
                session_id=session.session_id,
                user_id=effective_user_id,
                query=query,
                ai_response=ai_message_dict.get("content") or "",
                rating=rating,
                score=data.get("score"),
                correction_text=data.get("correction_text"),
                user_comment=data.get("user_comment"),
                kb_sources_used=kb_sources,
                model_id=model_id_from_msg or self.config.model_id,
                conversation_history=conversation_history,
                entry_metadata=entry_metadata,
            )
        except ValidationError as exc:
            # Pydantic v2 ValidationError is NOT a ValueError subclass; surface
            # a concise message rather than the full multi-error dump.
            first = exc.errors()[0] if exc.errors() else {"loc": (), "msg": "invalid feedback payload"}
            loc = ".".join(str(p) for p in first.get("loc", ())) or "payload"
            logger.warning("Feedback payload validation error: %s (session=%s)", exc, session.session_id)
            await self._send_feedback_error(
                websocket,
                "invalid_feedback",
                f"{loc}: {first.get('msg', 'invalid value')}",
                message_id=message_id,
            )
            return
        except ValueError as exc:
            logger.warning("Feedback payload validation error: %s (session=%s)", exc, session.session_id)
            await self._send_feedback_error(websocket, "invalid_feedback", str(exc), message_id=message_id)
            return

        try:
            persisted = await self.feedback_store.create(entry)
            logger.info(
                "Feedback persisted: entry_id=%s session=%s rating=%s",
                persisted.id,
                session.session_id,
                rating.value,
            )
        except (FeedbackError, InvalidStatusTransitionError, UnauthorizedFeedbackError) as exc:
            logger.warning("Feedback persistence failed: %s (session=%s)", exc, session.session_id)
            await self._send_feedback_error(websocket, "feedback_error", str(exc), message_id=message_id)
            return
        except Exception:  # pragma: no cover - defensive
            # Do NOT echo str(exc) to the client: psycopg/driver errors can
            # leak SQL fragments, constraint names, table names, etc. Log the
            # detail server-side and return a generic message.
            logger.exception("Unexpected error persisting feedback (session=%s)", session.session_id)
            self._total_errors += 1
            await self._send_feedback_error(
                websocket,
                "feedback_error",
                "Internal error while processing feedback",
                message_id=message_id,
            )
            return

        await self._send_message(
            websocket,
            {
                "type": "feedback_ack",
                "message_id": message_id,
                "feedback_id": str(persisted.id),
                "status": persisted.review_status.value,
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _send_feedback_error(
        self,
        websocket: WebSocket,
        code: str,
        message: str,
        *,
        message_id: Optional[str] = None,
    ) -> None:
        """Send a ``feedback_error`` envelope matching the chat-client contract.

        The client's ``_handleFeedbackError`` reads ``data.message_id`` (to
        locate the optimistic indicator) and ``data.message`` (to display
        inline). ``code`` is retained for programmatic branching and is
        purely additive — the legacy generic ``{type: "error", code,
        detail}`` envelope is no longer used for feedback failures.
        """
        payload: Dict[str, Any] = {
            "type": "feedback_error",
            "code": code,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        if message_id is not None:
            payload["message_id"] = message_id
        await self._send_message(websocket, payload)

    async def _handle_history_request(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle history request"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            logger.warning("Session not found for websocket %s", websocket)
            await self._send_error(websocket, "Session not found")
            return

        thread_id = self._active_thread_id(session)
        if thread_id is None:
            await self._send_error(websocket, "No active conversation. Start chatting or load a conversation first.")
            return

        cfg: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        checkpoint_state = await self.chat_graph.aget_state(cfg)
        raw_messages: List[Dict[str, Any]] = (
            (checkpoint_state.values or {}).get("messages", []) if checkpoint_state else []
        )

        history = self._format_history_messages(raw_messages)

        await self._send_message(
            websocket,
            {
                "type": "history",
                "messages": history,
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _handle_clear_history(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle clear history request"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        thread_id = self._active_thread_id(session)
        if thread_id is None:
            await self._send_error(websocket, "No active conversation. Start chatting or load a conversation first.")
            return

        cfg: Dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        await self.chat_graph.aupdate_state(cfg, {"messages": [], "metadata": {}})
        # Clear in-memory feedback metadata for this session too
        session.metadata.pop("feedback_meta", None)

        await self._send_message(
            websocket,
            {
                "type": "history_cleared",
                "message": "Conversation history cleared",
                "timestamp": datetime.now().isoformat(),
            },
        )

    # ------------------------------------------------------------------
    # Conversation management (per-user, named, persisted conversations)
    # ------------------------------------------------------------------

    def _conversation_persistence_active_for_session(self, session: ChatSession) -> bool:
        """Return True when ``session`` should use a persisted conversation id as its LangGraph ``thread_id``.

        Requires the feature to be enabled, an actual :class:`BaseConversationStore`
        to be wired, *and* an authenticated ``user_id`` — matches the gate
        ``_handle_chat_message`` uses before lazily creating a conversation.
        Anonymous connections and misconfigured deployments (flag enabled but
        no store wired) keep the legacy ``session_id``-as-``thread_id``
        behavior everywhere (chat, feedback, history, clear) rather than only
        in ``_handle_chat_message`` — otherwise those other handlers would
        incorrectly report "no active conversation" for a thread that chat is
        still happily writing to under ``session.session_id``.
        """
        return (
            getattr(self.config, "conversation_persistence_enabled", False)
            and self.conversation_store is not None
            and bool(session.user_id and session.user_id.strip())
        )

    def _active_thread_id(self, session: ChatSession) -> Optional[str]:
        """Return the LangGraph ``thread_id`` for this connection's next graph operation.

        When conversation persistence is not active for this session (see
        :meth:`_conversation_persistence_active_for_session`), ``session.session_id``
        doubles as the ``thread_id`` (legacy behavior, unchanged). Otherwise the
        active conversation is tracked separately in
        ``session.metadata["conversation_id"]`` — ``None`` until the user
        has sent a chat message or loaded a conversation on this connection.
        """
        if not self._conversation_persistence_active_for_session(session):
            return session.session_id
        return session.metadata.get("conversation_id")

    async def _send_conversation_error(
        self,
        websocket: WebSocket,
        code: str,
        message: str,
        *,
        conversation_id: Optional[str] = None,
    ) -> None:
        """Send a ``conversation_error`` envelope for conversation_* message handlers.

        Mirrors :meth:`_send_feedback_error`'s ``{type, code, message}``
        contract so the client can branch on ``code`` (e.g.
        ``conversation_persistence_disabled``, ``conversation_not_found``,
        ``conversation_history_unavailable``) rather than parsing free text.
        """
        payload: Dict[str, Any] = {
            "type": "conversation_error",
            "code": code,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        await self._send_message(websocket, payload)

    @staticmethod
    def _format_history_messages(raw_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert LangGraph checkpoint message dicts to the client-facing history shape."""
        return [
            {
                "message_id": m.get("metadata", {}).get("message_id"),
                "role": m.get("role"),
                "content": m.get("content", ""),
                "timestamp": m.get("metadata", {}).get("timestamp"),
                "tool_calls": m.get("tool_calls", []),
                "tool_results": m.get("tool_results", []),
                "metadata": m.get("metadata", {}),
            }
            for m in raw_messages
        ]

    async def _conversation_guard(self, websocket: WebSocket) -> Optional[ChatSession]:
        """Common preamble for every ``conversation_*`` message handler.

        Sends a ``conversation_error`` and returns ``None`` when conversation
        persistence isn't enabled/configured, the session can't be found, or
        the connection has no authenticated ``user_id`` (conversations are a
        per-user feature — there is no anonymous conversation list).
        """
        if not getattr(self.config, "conversation_persistence_enabled", False) or self.conversation_store is None:
            await self._send_conversation_error(
                websocket,
                "conversation_persistence_disabled",
                "Conversation persistence is not enabled on this server",
            )
            return None

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return None

        if not session.user_id or not session.user_id.strip():
            await self._send_conversation_error(
                websocket,
                "unauthorized_conversation",
                "Authentication is required to use conversations",
            )
            return None

        return session

    def _spawn_background_task(self, coro: Any) -> None:
        """Schedule ``coro`` as a fire-and-forget task.

        Keeps a strong reference in ``self._background_tasks`` until the
        task completes (removed via a done-callback) so it isn't
        garbage-collected mid-flight — a well-known asyncio footgun for
        tasks with no other referent. The done-callback also retrieves the
        task's result/exception so a bug in a future fire-and-forget
        caller that doesn't handle its own exceptions internally still gets
        logged, rather than only surfacing as an easy-to-miss "Task
        exception was never retrieved" warning.
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _on_done(t: "asyncio.Task[Any]") -> None:
            self._background_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error("Background task failed", exc_info=exc)

        task.add_done_callback(_on_done)

    def _build_title_llm_client(self) -> Optional[Any]:
        """Build a lightweight LLM client for conversation-title generation.

        Uses ``conversation_title_model_id`` when configured, falling back
        to the main chat ``model_id``. Returns ``None`` (triggering the
        truncated-first-message fallback in
        :func:`~autolangchat.conversation_titler.generate_conversation_title`)
        when ``langchain-aws`` isn't installed or client construction fails.
        """
        try:
            from langchain_aws import ChatBedrockConverse
        except ImportError:
            return None

        kwargs: Dict[str, Any] = {
            "model": self.config.conversation_title_model_id or self.config.model_id,
            "region_name": self.config.aws_region,
            # Titles are short (5-8 words); keep the cap small.
            "max_tokens": 60,
        }
        if self.config.aws_access_key_id and self.config.aws_secret_access_key:
            kwargs["aws_access_key_id"] = self.config.aws_access_key_id
            kwargs["aws_secret_access_key"] = self.config.aws_secret_access_key

        try:
            return ChatBedrockConverse(**kwargs)
        except Exception:
            logger.warning("Failed to build title-generation LLM client", exc_info=True)
            return None

    async def _generate_and_apply_title(
        self,
        websocket: WebSocket,
        conversation_id: str,
        messages: List[Dict[str, Any]],
    ) -> None:
        """Fire-and-forget: title a freshly created conversation and notify the client.

        Runs after the ``ai_response`` has already been sent so
        title-generation latency never delays the chat reply. Failures are
        logged and otherwise swallowed — an untitled conversation is a
        cosmetic issue, not a hard failure, and the client already has a
        working conversation either way.
        """
        try:
            llm_client = self._build_title_llm_client()
            title = await generate_conversation_title(llm_client, messages)
            await self.conversation_store.update_conversation(conversation_id, title=title)
            await self._send_message(
                websocket,
                {
                    "type": "conversation_titled",
                    "conversation_id": conversation_id,
                    "title": title,
                    "timestamp": datetime.now().isoformat(),
                },
            )
        except Exception:
            logger.warning("Conversation auto-titling failed for conversation_id=%s", conversation_id, exc_info=True)

    async def _handle_conversation_list(self, websocket: WebSocket, data: Dict[str, Any]) -> None:
        """Handle a ``conversation_list`` request — return the user's conversations."""
        session = await self._conversation_guard(websocket)
        if session is None:
            return

        try:
            limit = int(data.get("limit", 50))
            offset = int(data.get("offset", 0))
        except (TypeError, ValueError):
            await self._send_conversation_error(
                websocket, "invalid_conversation_request", "limit and offset must be integers"
            )
            return
        if limit <= 0 or offset < 0:
            await self._send_conversation_error(
                websocket, "invalid_conversation_request", "limit must be positive and offset non-negative"
            )
            return
        if limit > _CONVERSATION_LIST_MAX_LIMIT:
            await self._send_conversation_error(
                websocket,
                "invalid_conversation_request",
                f"limit must be <= {_CONVERSATION_LIST_MAX_LIMIT}",
            )
            return

        conversations = await self.conversation_store.list_conversations(session.user_id, limit=limit, offset=offset)

        await self._send_message(
            websocket,
            {
                "type": "conversation_list",
                "conversations": conversations,
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _handle_conversation_new(self, websocket: WebSocket, data: Dict[str, Any]) -> None:
        """Handle a ``conversation_new`` request — detach from any active conversation.

        Conversation creation stays lazy: no row is written here. The next
        chat message on this connection mints a brand-new ``thread_id`` and
        creates the conversation (see ``_handle_chat_message``).
        """
        session = await self._conversation_guard(websocket)
        if session is None:
            return

        session.metadata["conversation_id"] = None
        # Stale per-turn feedback cache from the previous conversation no
        # longer applies once we've detached from it.
        session.metadata.pop("feedback_meta", None)

    async def _handle_conversation_load(self, websocket: WebSocket, data: Dict[str, Any]) -> None:
        """Handle a ``conversation_load`` request — switch this connection to an existing conversation."""
        session = await self._conversation_guard(websocket)
        if session is None:
            return

        conversation_id = data.get("conversation_id")
        if not conversation_id or not isinstance(conversation_id, str):
            await self._send_conversation_error(
                websocket, "invalid_conversation_request", "conversation_id is required"
            )
            return

        conversation = await self.conversation_store.get_conversation(conversation_id)
        # Do not distinguish "not found" from "belongs to another user" in
        # the response — that would let a client enumerate valid conversation
        # ids it doesn't own.
        if conversation is None or conversation.get("user_id") != session.user_id:
            await self._send_conversation_error(
                websocket, "conversation_not_found", "Conversation not found", conversation_id=conversation_id
            )
            return

        cfg: Dict[str, Any] = {"configurable": {"thread_id": conversation_id}}
        checkpoint_state = await self.chat_graph.aget_state(cfg)
        checkpoint_values = checkpoint_state.values if checkpoint_state else None
        if not checkpoint_values:
            if conversation.get("message_count", 0) > 0:
                # This conversation has recorded turns, so a missing
                # checkpoint means the process likely restarted with a
                # non-persistent checkpointer (MemorySaver) — history was
                # actually lost, distinct from "no messages yet".
                await self._send_conversation_error(
                    websocket,
                    "conversation_history_unavailable",
                    "This conversation's message history is unavailable",
                    conversation_id=conversation_id,
                )
                return
            # A brand-new conversation (e.g. created via REST POST, or the
            # WebSocket lazy-create failed before the first ainvoke) that
            # has never had a turn recorded — an empty history is the
            # correct, loadable state here, not "unavailable".
            raw_messages: List[Dict[str, Any]] = []
        else:
            raw_messages = checkpoint_values.get("messages", [])

        history = self._format_history_messages(raw_messages)

        session.metadata["conversation_id"] = conversation_id
        # Switching conversations invalidates the per-turn feedback cache
        # collected against the previously active thread.
        session.metadata.pop("feedback_meta", None)

        await self._send_message(
            websocket,
            {
                "type": "conversation_loaded",
                "conversation_id": conversation_id,
                "conversation": conversation,
                "messages": history,
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _handle_conversation_delete(self, websocket: WebSocket, data: Dict[str, Any]) -> None:
        """Handle a ``conversation_delete`` request — remove one conversation's metadata row."""
        session = await self._conversation_guard(websocket)
        if session is None:
            return

        conversation_id = data.get("conversation_id")
        if not conversation_id or not isinstance(conversation_id, str):
            await self._send_conversation_error(
                websocket, "invalid_conversation_request", "conversation_id is required"
            )
            return

        conversation = await self.conversation_store.get_conversation(conversation_id)
        if conversation is None or conversation.get("user_id") != session.user_id:
            await self._send_conversation_error(
                websocket, "conversation_not_found", "Conversation not found", conversation_id=conversation_id
            )
            return

        await self.conversation_store.delete_conversation(conversation_id)
        if session.metadata.get("conversation_id") == conversation_id:
            session.metadata["conversation_id"] = None
            session.metadata.pop("feedback_meta", None)

        await self._send_message(
            websocket,
            {
                "type": "conversation_deleted",
                "conversation_id": conversation_id,
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _handle_conversation_delete_all(self, websocket: WebSocket, data: Dict[str, Any]) -> None:
        """Handle a ``conversation_delete_all`` request — remove all of the user's conversations."""
        session = await self._conversation_guard(websocket)
        if session is None:
            return

        deleted_count = await self.conversation_store.delete_all_conversations(session.user_id)
        session.metadata["conversation_id"] = None
        session.metadata.pop("feedback_meta", None)

        await self._send_message(
            websocket,
            {
                "type": "conversation_all_deleted",
                "deleted_count": deleted_count,
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _handle_conversation_rename(self, websocket: WebSocket, data: Dict[str, Any]) -> None:
        """Handle a ``conversation_rename`` request — set an explicit user-chosen title."""
        session = await self._conversation_guard(websocket)
        if session is None:
            return

        conversation_id = data.get("conversation_id")
        if not conversation_id or not isinstance(conversation_id, str):
            await self._send_conversation_error(
                websocket, "invalid_conversation_request", "conversation_id is required"
            )
            return

        title = data.get("title")
        if not title or not isinstance(title, str) or not title.strip():
            await self._send_conversation_error(
                websocket, "invalid_conversation_request", "title is required", conversation_id=conversation_id
            )
            return
        title = title.strip()

        conversation = await self.conversation_store.get_conversation(conversation_id)
        if conversation is None or conversation.get("user_id") != session.user_id:
            await self._send_conversation_error(
                websocket, "conversation_not_found", "Conversation not found", conversation_id=conversation_id
            )
            return

        try:
            await self.conversation_store.update_conversation(conversation_id, title=title)
        except ConversationNotFoundError:
            await self._send_conversation_error(
                websocket, "conversation_not_found", "Conversation not found", conversation_id=conversation_id
            )
            return

        await self._send_message(
            websocket,
            {
                "type": "conversation_renamed",
                "conversation_id": conversation_id,
                "title": title,
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _handle_auth_message(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle authentication message from client"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        try:
            # Extract credentials from message
            auth_type = data.get("auth_type", "bearer_token").lower()

            # Create credentials based on auth type
            credentials = None

            if auth_type == "bearer_token":
                token = data.get("token")
                if not token:
                    await self._send_error(websocket, "Bearer token required")
                    return
                credentials = Credentials(
                    auth_type=AuthType.BEARER_TOKEN,
                    bearer_token=token,
                )

            elif auth_type == "basic_auth":
                username = data.get("username")
                password = data.get("password")
                if not username or not password:
                    await self._send_error(websocket, "Username and password required for basic auth")
                    return
                credentials = Credentials(
                    auth_type=AuthType.BASIC_AUTH,
                    username=username,
                    password=password,
                )

            elif auth_type == "api_key":
                api_key = data.get("api_key")
                api_key_header = data.get("api_key_header", "X-API-Key")
                if not api_key:
                    await self._send_error(websocket, "API key required")
                    return
                credentials = Credentials(
                    auth_type=AuthType.API_KEY,
                    api_key=api_key,
                    api_key_header=api_key_header,
                )

            elif auth_type == "oauth2" or auth_type == "oauth2_client_credentials":
                client_id = data.get("client_id")
                client_secret = data.get("client_secret")
                token_url = data.get("token_url")
                scope = data.get("scope")

                if not client_id or not client_secret or not token_url:
                    await self._send_error(websocket, "client_id, client_secret, and token_url required for OAuth2")
                    return

                credentials = Credentials(
                    auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
                    client_id=client_id,
                    client_secret=client_secret,
                    token_url=token_url,
                    scope=scope,
                )

            elif auth_type == "custom":
                custom_headers = data.get("custom_headers", {})
                credentials = Credentials(
                    auth_type=AuthType.CUSTOM,
                    custom_headers=custom_headers,
                    metadata=data.get("metadata", {}),
                )

            elif auth_type == "sso":
                session_token = data.get("session_token") or websocket.cookies.get("sso_session_token")
                # Log token presence and hash instead of raw token for security
                token_present = bool(session_token)
                token_hash = hashlib.sha256(session_token.encode()).hexdigest()[:8] if session_token else None
                logger.debug("SSO auth attempt: token_present=%s, token_hash=%s", token_present, token_hash)
                if not session_token:
                    await self._send_error(websocket, "session_token required for SSO auth")
                    return
                if not self.sso_session_store:
                    await self._send_error(websocket, "SSO is not enabled on this server")
                    return
                await self._try_sso_auth_from_message(websocket, session_token)
                return  # _try_sso_auth_from_message sends its own reply

            else:
                await self._send_error(websocket, f"Unknown auth type: {auth_type}")
                return

            # Validate credentials
            if not credentials:
                await self._send_error(websocket, "Failed to create credentials")
                return

            auth_handler = AuthenticationHandler(credentials)
            if not auth_handler.validate_credentials():
                await self._send_error(websocket, "Invalid credentials provided")
                return

            # Set HTTP client for OAuth2 if needed
            if auth_type == "oauth2" or auth_type == "oauth2_client_credentials":
                auth_handler.set_http_client(self.http_client)

            # Initialize user_info (will be populated by verification endpoint if configured)
            user_info = None

            # Verify credentials against remote endpoint if configured
            if self.config.auth_verification_endpoint:
                verification_url = self.config.auth_verification_endpoint
                # Resolve relative paths (e.g. "/api/v1/auth/verify") against app base URL
                if verification_url.startswith("/"):
                    verification_url = f"{self.app_base_url}{verification_url}"
                logger.info(f"Verifying credentials for session {session.session_id} against {verification_url}")
                is_valid, message, user_info = await auth_handler.verify_credentials_remote(
                    verification_url, http_client=self.http_client
                )
                # Log only user_id and keys to avoid PII in logs
                user_id_from_info = user_info.get("user_id") if user_info else None
                user_info_keys = list(user_info.keys()) if user_info else []
                logger.info(
                    "Verification result for session %s: is_valid=%s, user_id=%s, fields=%s",
                    session.session_id,
                    is_valid,
                    user_id_from_info,
                    user_info_keys,
                )
                logger.debug("Full user_info for session %s: %s", session.session_id, user_info)
                if not is_valid:
                    await self._send_message(
                        websocket,
                        {
                            "type": "auth_failed",
                            "message": message,
                            "auth_type": auth_type,
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                    return

                # Extract user_id from verification response if available
                if user_info and not session.user_id:
                    # Try common user_id fields: user_id, sub, email, username
                    extracted_user_id = (
                        user_info.get("user_id")
                        or user_info.get("sub")
                        or user_info.get("email")
                        or user_info.get("username")
                        or user_info.get("user")
                    )
                    if extracted_user_id:
                        await self.session_manager.update_session_user_id(session.session_id, extracted_user_id)
                        # Refresh session reference after update
                        session = await self.session_manager.get_session(websocket)

                # Store full user_info in session metadata (always, not just when user_id missing)
                if user_info:
                    session.metadata["verified_user_info"] = user_info
                    logger.debug("Session metadata updated with verified_user_info")

            # Store credentials in session
            session.credentials = credentials
            session.auth_handler = auth_handler

            # Extract display name for UI
            display_name = None
            if user_info:
                # Try to get a user-friendly display name
                display_name = (
                    user_info.get("name")
                    or user_info.get("display_name")
                    or user_info.get("email")
                    or user_info.get("username")
                    or user_info.get("user_id")
                    or user_info.get("sub")
                )
                logger.debug(f"Extracted display_name from user_info: {display_name}")
            # If no user_info, use the session user_id (if set)
            if not display_name and session.user_id:
                display_name = session.user_id
                logger.debug(f"Using session.user_id as display_name: {display_name}")

            # Store display name in session metadata
            if display_name:
                session.metadata["display_name"] = display_name

            logger.info(
                "Authentication configured for session %s: auth_type=%s, user_id=%s",
                session.session_id,
                auth_type,
                session.user_id,
            )
            logger.debug(
                "Session credentials configured for session %s: auth_type=%s, has_credentials=%s, display_name=%s",
                session.session_id,
                auth_type,
                bool(credentials),
                display_name,
            )

            auth_response = {
                "type": "auth_configured",
                "message": f"Authentication configured: {auth_type}",
                "auth_type": auth_type,
                "timestamp": datetime.now().isoformat(),
            }

            # Include display name if available
            if display_name:
                auth_response["display_name"] = display_name
                auth_response["message"] = f"Authenticated as {display_name}"

            await self._send_message(websocket, auth_response)

        except Exception as e:
            logger.error(f"Error handling authentication message: {str(e)}")
            self._total_errors += 1
            await self._send_error(websocket, f"Authentication error: {str(e)}")

    async def _handle_logout(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle logout message from client"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        try:
            # If SSO session, also delete the server-side SSO session
            if (
                session.credentials
                and session.credentials.auth_type == AuthType.SSO
                and self.sso_session_store
                and session.credentials.session_token
            ):
                sso_session_id = _get_sso_session_store_class().validate_session_token(
                    session.credentials.session_token, self.config.sso_session_secret
                )
                if sso_session_id:
                    self.sso_session_store.delete_session(sso_session_id)
                    logger.debug("SSO session deleted on WS logout: %s", sso_session_id)

            # Clear credentials from session
            session.credentials = None
            session.auth_handler = None

            logger.info(f"User logged out from session {session.session_id}")

            # Try to send logout_success, but don't fail if the client closed the connection
            try:
                await self._send_message(
                    websocket,
                    {
                        "type": "logout_success",
                        "message": "Successfully logged out",
                        "timestamp": datetime.now().isoformat(),
                    },
                )
            except Exception as send_error:
                # Client may have already closed the connection, which is fine
                logger.debug(f"Could not send logout_success (client may have closed connection): {str(send_error)}")

        except Exception as e:
            logger.error(f"Error handling logout: {str(e)}")
            self._total_errors += 1
            try:
                await self._send_error(websocket, f"Logout error: {str(e)}")
            except Exception:
                # Connection might be closed, ignore
                pass

    # ------------------------------------------------------------------
    # SSO helpers
    # ------------------------------------------------------------------

    async def _validate_sso_token_and_extract_user(self, session_token: str) -> Optional[Dict[str, Any]]:
        """Validate SSO token and extract user information.

        Returns dict with user_id, credentials, auth_handler, metadata, and display_name if valid.
        Returns None if token is invalid or session not found.
        """
        # Validate token signature + expiry
        sso_session_id = _get_sso_session_store_class().validate_session_token(
            session_token, self.config.sso_session_secret
        )
        if not sso_session_id:
            logger.debug("Invalid or expired SSO session token")
            return None

        # Lookup SSO session
        sso_session = self.sso_session_store.get_session(sso_session_id)
        if not sso_session:
            logger.debug("SSO session not found: %s", sso_session_id)
            return None

        # Extract access token
        access_token = sso_session.get("access_token")
        if not access_token:
            logger.warning("SSO session missing access token: %s", sso_session_id)
            self.sso_session_store.delete_session(sso_session_id)
            return None

        # Extract user info
        user_info = sso_session.get("user_info", {})
        id_token_claims = sso_session.get("id_token_claims", {})

        # Determine user_id using the shared canonical resolution helper.
        user_id = _get_extract_user_id()(user_info, id_token_claims)

        display_name = (
            user_info.get("name")
            or id_token_claims.get("name")
            or user_info.get("email")
            or id_token_claims.get("email")
            or "SSO User"
        )

        # Build credentials
        credentials = Credentials(
            auth_type=AuthType.SSO,
            bearer_token=access_token,
            session_token=session_token,
            sso_user_info=user_info,
            metadata={"sso_session_id": sso_session_id, "display_name": display_name},
        )
        auth_handler = AuthenticationHandler(credentials)

        # Call verification endpoint to get application-specific user metadata
        verified_user_info = None
        if self.config.auth_verification_endpoint:
            verification_url = self.config.auth_verification_endpoint
            # Resolve relative paths (e.g. "/api/v1/auth/verify") against app base URL
            if verification_url.startswith("/"):
                verification_url = f"{self.app_base_url}{verification_url}"

            logger.info(f"SSO: Verifying credentials against {verification_url}")
            try:
                is_valid, message, verified_user_info = await auth_handler.verify_credentials_remote(
                    verification_url, http_client=self.http_client
                )
                # Log only user_id and keys to avoid PII in logs
                verified_user_id = verified_user_info.get("user_id") if verified_user_info else None
                verified_info_keys = list(verified_user_info.keys()) if verified_user_info else []
                logger.info(
                    "SSO verification result: is_valid=%s, user_id=%s, fields=%s",
                    is_valid,
                    verified_user_id,
                    verified_info_keys,
                )
                logger.debug("Full verified_user_info: %s", verified_user_info)

                if not is_valid:
                    logger.warning("SSO token verification failed: %s", message)
                    # Don't fail SSO auth if verification fails - just log it
                    # This preserves backward compatibility
            except Exception as e:
                logger.warning("SSO verification endpoint call failed: %s", str(e))
                # Don't fail SSO auth if verification endpoint is unreachable

        # Build metadata dict
        metadata = {
            "sso_user_info": user_info,
            "display_name": display_name,
        }

        # Add verified_user_info if available
        if verified_user_info:
            metadata["verified_user_info"] = verified_user_info

        return {
            "user_id": user_id,
            "credentials": credentials,
            "auth_handler": auth_handler,
            "display_name": display_name,
            "metadata": metadata,
        }

    async def _try_sso_auth_from_message(self, websocket: WebSocket, session_token: str) -> bool:
        """Authenticate via an SSO auth message (sent after connection).

        Sends ``auth_configured`` on success, ``auth_failed`` on failure.
        Returns ``True`` if authenticated.
        """
        # Validate and extract user info
        user_info_dict = await self._validate_sso_token_and_extract_user(session_token)
        if not user_info_dict:
            await self._send_message(
                websocket,
                {
                    "type": "auth_failed",
                    "message": "Invalid or expired SSO session token. Please log in again.",
                    "auth_type": "sso",
                    "redirect_url": f"{self.config.chat_endpoint}/auth/sso/login",
                    "timestamp": datetime.now().isoformat(),
                },
            )
            return False

        chat_session = await self.session_manager.get_session(websocket)
        if not chat_session:
            return False

        # Update session with SSO credentials and user info
        chat_session.credentials = user_info_dict["credentials"]
        chat_session.auth_handler = user_info_dict["auth_handler"]
        chat_session.metadata.update(user_info_dict["metadata"])

        # Update session user_id if not already set
        if not chat_session.user_id and user_info_dict["user_id"]:
            await self.session_manager.update_session_user_id(chat_session.session_id, user_info_dict["user_id"])
            # Refresh session reference after update
            chat_session = await self.session_manager.get_session(websocket)
            logger.debug("Updated session user_id: %s", user_info_dict["user_id"])

        display_name = user_info_dict["display_name"]
        logger.info(
            "SSO authentication successful for session %s (user: %s)",
            chat_session.session_id,
            display_name,
        )

        await self._send_message(
            websocket,
            {
                "type": "auth_configured",
                "message": f"Authenticated as {display_name}",
                "auth_type": "sso",
                "display_name": display_name,
                "timestamp": datetime.now().isoformat(),
            },
        )
        return True

    async def _send_message(self, websocket: WebSocket, message: Dict[str, Any]):
        """Send message to WebSocket client"""

        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send message: {str(e)}")
            raise WebSocketError(f"Failed to send message: {str(e)}")

    async def _send_error(self, websocket: WebSocket, error_message: str):
        """Send error message to client"""

        await self._send_message(
            websocket,
            {
                "type": "error",
                "message": error_message,
                "timestamp": datetime.now().isoformat(),
            },
        )

    def _get_client_ip(self, websocket: WebSocket) -> str:
        """Extract client IP address from WebSocket"""

        # Check for forwarded headers first
        forwarded_for = websocket.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = websocket.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        # Fallback to client host
        client = websocket.client
        return client.host if client else "unknown"

    def _create_error_response(self, error_message: str) -> str:
        """Create user-friendly error response"""

        if "timeout" in error_message.lower():
            return "I'm taking longer than usual to respond. Please try again."
        elif "rate limit" in error_message.lower():
            return "I'm receiving too many requests. Please wait a moment and try again."
        elif "access denied" in error_message.lower():
            return "I don't have access to that model or service. Please contact support."
        elif "model" in error_message.lower():
            return "I'm having trouble with the AI model. Please try again in a moment."
        else:
            return f"I encountered an error: {error_message}. Please try again."

    async def get_statistics(self) -> Dict[str, Any]:
        """Get WebSocket handler statistics"""

        session_stats = await self.session_manager.get_statistics()

        return {
            "websocket": {
                "total_messages_handled": self._total_messages_handled,
                "total_errors": self._total_errors,
            },
            "sessions": session_stats,
            "tools": {},
        }

    def _format_auth_context(self, user_info: Optional[Dict[str, Any]]) -> str:
        """Format authenticated user information for injection into system prompt."""
        if not user_info:
            return ""

        context_parts = ["AUTHENTICATED USER CONTEXT:", "=" * 60]
        context_parts.append("You are currently interacting with an authenticated user.")
        context_parts.append("The following information is available about this user:\n")

        # Format user info as key-value pairs
        for key, value in user_info.items():
            # Skip complex nested structures, only include simple values
            if isinstance(value, (str, int, float, bool)):
                context_parts.append(f"  {key}: {value}")
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                context_parts.append(f"  {key}: {', '.join(value)}")

        context_parts.append("\nINSTRUCTIONS:")
        context_parts.append("- This information is provided for context only - the user cannot see it")
        context_parts.append("- Use this information to personalize your responses when appropriate")
        context_parts.append("- If the user asks 'who am I?' or similar identity questions, use this context")
        context_parts.append("- Respect user privacy - only share information when directly asked")
        context_parts.append("- Be natural and conversational when using this information")
        context_parts.append("=" * 60)

        return "\n".join(context_parts)

    async def shutdown(self):
        """Shutdown the WebSocket handler"""

        # Close HTTP client
        await self.http_client.aclose()

        # Shutdown session manager
        await self.session_manager.shutdown()

        logger.info("WebSocket handler shutdown complete")
