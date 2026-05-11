"""WebSocket handler for real-time chat communication"""

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import WebSocket, WebSocketDisconnect

from .auth_handler import AuthenticationHandler, AuthType, Credentials
from .chat_manager import ChatManager
from .config import ChatConfig
from .exceptions import FeedbackError, InvalidStatusTransitionError, UnauthorizedFeedbackError, WebSocketError
from .feedback_store import AuthenticatedUserAuthorizer, FeedbackAuthorizer, FeedbackStore
from .kb_store_base import BaseKBStore
from .models import FeedbackEntry, Rating
from .session_manager import ChatMessage, ChatSessionManager
from .sso_session_store import SSOSessionStore
from .tool_manager import AuthInfo

logger = logging.getLogger(__name__)


class WebSocketChatHandler:
    """Handles WebSocket connections and chat communication.

    The handler manages WebSocket transport, session lifecycle, and
    authentication, and constructs
    :class:`~auto_bedrock_chat_fastapi.tool_manager.AuthInfo` objects and
    forwards progress callbacks for tool usage.  LLM calls, message
    preprocessing, and actual tool execution are delegated to
    :class:`~auto_bedrock_chat_fastapi.chat_manager.ChatManager` and the
    tool manager.
    """

    def __init__(
        self,
        session_manager: ChatSessionManager,
        config: ChatConfig,
        chat_manager: ChatManager,
        app_base_url: str = "http://localhost:8000",
        sso_session_store: Optional[SSOSessionStore] = None,
        kb_store: Optional[BaseKBStore] = None,
        feedback_store: Optional[FeedbackStore] = None,
        feedback_authorizer: Optional[FeedbackAuthorizer] = None,
    ):
        self.session_manager = session_manager
        self.config = config
        self.app_base_url = app_base_url.rstrip("/")
        self.chat_manager = chat_manager
        self.sso_session_store = sso_session_store
        self.kb_store = kb_store
        self.feedback_store = feedback_store
        # Default to permissive (any authenticated user); access-control task
        # swaps this in without touching the handler.
        self.feedback_authorizer: FeedbackAuthorizer = feedback_authorizer or AuthenticatedUserAuthorizer()

        # HTTP client for making internal API calls
        self.http_client = httpx.AsyncClient(timeout=config.timeout)

        # Statistics
        self._total_messages_handled = 0
        self._total_errors = 0

    async def handle_connection(self, websocket: WebSocket, user_id: Optional[str] = None):
        """Handle new WebSocket connection"""

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
                sso_session_id = SSOSessionStore.validate_session_token(session_token, self.config.sso_session_secret)
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

            # Add user message to history
            user_chat_message = ChatMessage(role="user", content=user_message, metadata={"source": "websocket"})
            logger.debug(f"Received user message: {user_message}")
            await self.session_manager.add_message(session.session_id, user_chat_message)

            # Send typing indicator
            await self._send_message(
                websocket,
                {
                    "type": "typing",
                    "message": "AI is thinking...",
                    "timestamp": datetime.now().isoformat(),
                },
            )

            # Get conversation context
            context_messages = await self.session_manager.get_context_messages(session.session_id)

            # Convert ChatMessage objects to dicts for LLM formatting
            message_dicts = [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "tool_calls": msg.tool_calls if hasattr(msg, "tool_calls") and msg.tool_calls else [],
                    "tool_results": msg.tool_results if hasattr(msg, "tool_results") and msg.tool_results else [],
                }
                for msg in context_messages
            ]

            # RAG: Retrieve relevant KB context if enabled
            kb_context_text = None
            kb_results = None
            if self.config.enable_rag:
                kb_results = await self._retrieve_kb_context(user_message)
                if kb_results:
                    kb_context_text = self._format_kb_context(kb_results)
                    logger.info(f"RAG: Injecting {len(kb_results)} KB chunks into context")
                    logger.debug(f"RAG: KB context length: {len(kb_context_text)} chars")
                    logger.debug(f"RAG: KB context preview (first 300 chars):\n{kb_context_text[:300]}...")

            # Auth Info: Include verified user info if enabled
            auth_context_text = None
            if self.config.include_auth_info_in_prompts:
                verified_user_info = session.metadata.get("verified_user_info")
                if verified_user_info:
                    auth_context_text = self._format_auth_context(verified_user_info)
                    logger.debug("Including authenticated user info in system prompt")

            # Inject KB context and/or auth context into system message if available
            if kb_context_text or auth_context_text:
                # Get the base system prompt
                base_system_prompt = self.config.get_system_prompt()

                # Build enhanced system prompt with available context
                context_parts = []
                if kb_context_text:
                    context_parts.append(kb_context_text)
                if auth_context_text:
                    context_parts.append(auth_context_text)

                # Combine contexts and base prompt
                enhanced_system_prompt = "\n\n".join(context_parts + [base_system_prompt])

                logger.debug(f"Enhanced system prompt length: {len(enhanced_system_prompt)} chars")

                if kb_context_text:
                    logger.debug(
                        f"KB context added to enhanced system prompt (first 500 chars):\n{enhanced_system_prompt[:500]}..."
                    )

                # Add enhanced system message to the beginning of message_dicts
                # First, remove any existing system messages
                message_dicts = [msg for msg in message_dicts if msg.get("role") != "system"]
                # Insert the enhanced system prompt at the beginning
                message_dicts.insert(0, {"role": "system", "content": enhanced_system_prompt})

            # Get LLM parameters
            llm_params = self.config.get_llm_params()

            # ------------------------------------------------------------------
            # Delegate to ChatManager (preprocessing + LLM call + tool loop)
            # ------------------------------------------------------------------

            # Build auth_info from session for tool call execution
            auth_info = AuthInfo(
                credentials=session.credentials,
                auth_handler=session.auth_handler,
                metadata=session.metadata,
            )

            # Closure: send progress updates to the WebSocket client
            async def _on_progress(msg_dict: Dict[str, Any]) -> None:
                await self._send_message(websocket, msg_dict)

            result = await self.chat_manager.chat_completion(
                messages=message_dicts,
                auth_info=auth_info,
                on_progress=_on_progress,
                **llm_params,
            )

            final_response = result.response
            content = final_response.get("content") or ""
            logger.debug(f"Chat completion response ({len(content):,} chars): {content[:100]}")

            # ------------------------------------------------------------------
            # Sync intermediate tool-loop messages back to session history
            # ------------------------------------------------------------------
            tool_rounds = result.metadata.get("tool_call_rounds", 0)
            if tool_rounds > 0:
                # Find the last user message in result.messages — everything
                # after it was appended during the tool call loop.
                last_user_idx = None
                for i in range(len(result.messages) - 1, -1, -1):
                    if result.messages[i].get("role") == "user":
                        last_user_idx = i
                        break

                if last_user_idx is not None:
                    for msg_dict in result.messages[last_user_idx + 1 :]:
                        chat_msg = ChatMessage(
                            role=msg_dict["role"],
                            content=msg_dict.get("content", ""),
                            tool_calls=msg_dict.get("tool_calls", []),
                            tool_results=msg_dict.get("tool_results", []),
                            metadata=msg_dict.get("metadata", {}),
                        )
                        await self.session_manager.add_message(session.session_id, chat_msg)

            # Build response metadata up-front so it can be persisted on the
            # assistant ChatMessage. The feedback message handler recovers
            # ``kb_sources_used`` and ``model_id`` from this metadata when
            # constructing a FeedbackEntry.
            response_metadata = final_response.get("metadata", {}).copy()
            response_metadata.setdefault("model_id", self.config.model_id)
            if kb_results:
                response_metadata["kb_used"] = True
                response_metadata["kb_chunks"] = len(kb_results)
                response_metadata["kb_sources"] = [
                    {
                        "title": r.get("title"),
                        "source": r.get("source"),
                        "url": r.get("source_url"),
                        "score": r["similarity_score"],
                    }
                    for r in kb_results
                ]

            # Add the final AI response to history (if not a dangling tool call)
            ai_message: Optional[ChatMessage] = None
            if not final_response.get("tool_calls"):
                ai_message = ChatMessage(
                    role="assistant",
                    content=final_response.get("content") or "",
                    tool_calls=[],
                    tool_results=[],
                    metadata=response_metadata.copy(),
                )
                # Capture the user's preceding message so the feedback handler
                # can recover it without scanning history.
                ai_message.metadata["query"] = user_message
                await self.session_manager.add_message(session.session_id, ai_message)

            # Send response to client
            await self._send_message(
                websocket,
                {
                    "type": "ai_response",
                    "message_id": ai_message.message_id if ai_message else None,
                    "message": final_response.get("content") or "",
                    "tool_calls": final_response.get("tool_calls", []),
                    "tool_results": result.tool_results,
                    "timestamp": datetime.now().isoformat(),
                    "metadata": response_metadata,
                },
            )

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

    async def _handle_feedback_message(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle a ``feedback`` message from the chat client.

        Validates the payload, recovers the original AI response context from
        session history (keyed by ``message_id``), enforces authorization,
        persists a :class:`FeedbackEntry` via :class:`FeedbackStore`, and
        replies with a ``feedback_ack`` envelope.
        """
        if self.feedback_store is None:
            await self._send_feedback_error(websocket, "feedback_unavailable", "Feedback collection is not enabled")
            return

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        # Authorization (stub by default; access-control task swaps in the
        # real implementation).
        if not self.feedback_authorizer.can_submit(session.user_id):
            logger.info(
                "Feedback rejected: unauthorized user_id=%s session=%s",
                session.user_id,
                session.session_id,
            )
            await self._send_feedback_error(
                websocket,
                "unauthorized_feedback",
                "You are not authorized to submit feedback",
            )
            return

        message_id = data.get("message_id")
        rating_raw = data.get("rating")
        if not message_id or not rating_raw:
            await self._send_feedback_error(websocket, "invalid_feedback", "message_id and rating are required")
            return

        try:
            rating = Rating(rating_raw)
        except ValueError:
            await self._send_feedback_error(websocket, "invalid_feedback", f"Unknown rating: {rating_raw!r}")
            return

        # Recover the original assistant response by message_id from session
        # history. ``ChatMessage.metadata['query']`` was populated when the
        # response was sent so we don't need to rescan for the user message.
        history = await self.session_manager.get_conversation_history(session.session_id)
        ai_message = next(
            (m for m in history if getattr(m, "message_id", None) == message_id and m.role == "assistant"),
            None,
        )
        if ai_message is None:
            await self._send_feedback_error(
                websocket,
                "invalid_feedback",
                f"No assistant message found for message_id={message_id!r}",
            )
            return

        meta = ai_message.metadata or {}
        try:
            entry = FeedbackEntry(
                session_id=session.session_id,
                user_id=session.user_id or "",
                query=meta.get("query", ""),
                ai_response=ai_message.content or "",
                rating=rating,
                score=data.get("score"),
                correction_text=data.get("correction_text"),
                user_comment=data.get("user_comment"),
                kb_sources_used=meta.get("kb_sources", []) or [],
                model_id=meta.get("model_id") or self.config.model_id,
            )
        except ValueError as exc:
            await self._send_feedback_error(websocket, "invalid_feedback", str(exc))
            return

        try:
            persisted = await self.feedback_store.create(entry)
        except (FeedbackError, InvalidStatusTransitionError, UnauthorizedFeedbackError) as exc:
            logger.warning("Feedback persistence failed: %s", exc)
            await self._send_feedback_error(websocket, "feedback_error", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unexpected error persisting feedback")
            self._total_errors += 1
            await self._send_feedback_error(websocket, "feedback_error", str(exc))
            return

        await self._send_message(
            websocket,
            {
                "type": "feedback_ack",
                "feedback_id": str(persisted.id),
                "status": persisted.review_status.value,
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _send_feedback_error(self, websocket: WebSocket, code: str, detail: str) -> None:
        await self._send_message(
            websocket,
            {
                "type": "error",
                "code": code,
                "detail": detail,
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _handle_history_request(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle history request"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        history = await self.session_manager.get_conversation_history(session.session_id)

        await self._send_message(
            websocket,
            {
                "type": "history",
                "messages": [msg.to_dict() for msg in history],
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def _handle_clear_history(self, websocket: WebSocket, data: Dict[str, Any]):
        """Handle clear history request"""

        session = await self.session_manager.get_session(websocket)
        if not session:
            await self._send_error(websocket, "Session not found")
            return

        # Clear conversation history but keep system message if present
        if session.conversation_history and session.conversation_history[0].role == "system":
            system_msg = session.conversation_history[0]
            session.conversation_history = [system_msg]
        else:
            session.conversation_history = []

        await self._send_message(
            websocket,
            {
                "type": "history_cleared",
                "message": "Conversation history cleared",
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
                sso_session_id = SSOSessionStore.validate_session_token(
                    session.credentials.session_token, self.config.sso_session_secret
                )
                if sso_session_id:
                    self.sso_session_store.delete_session(sso_session_id)
                    logger.debug("SSO session deleted on WS logout: %s", sso_session_id)

            # Clear credentials from session
            session.credentials = None
            session.auth_handler = None

            # Clear conversation history so new auth context is fresh
            session.conversation_history = []

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
        sso_session_id = SSOSessionStore.validate_session_token(session_token, self.config.sso_session_secret)
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

        # Determine user_id (prefer email, fall back to other identifiers)
        user_id = (
            user_info.get("email")
            or id_token_claims.get("email")
            or user_info.get("sub")
            or id_token_claims.get("sub")
            or user_info.get("username")
            or id_token_claims.get("cognito:username")
            or id_token_claims.get("preferred_username")
        )

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

        tool_manager = getattr(self.chat_manager, "tool_manager", None)
        return {
            "websocket": {
                "total_messages_handled": self._total_messages_handled,
                "total_errors": self._total_errors,
            },
            "sessions": session_stats,
            "tools": tool_manager.get_statistics() if tool_manager else {},
        }

    async def _retrieve_kb_context(self, query: str) -> Optional[List[Dict[str, Any]]]:
        """
        Retrieve relevant knowledge base chunks for the given query.

        Args:
            query: User's message/question

        Returns:
            List of KB chunks with metadata, or None if RAG is disabled or retrieval fails
        """
        # Skip if RAG is disabled
        if not self.config.enable_rag:
            return None

        try:
            # Use the shared KB store if available; fall back to factory
            if self.kb_store is not None:
                vector_db = self.kb_store
                _close_after = False
            else:
                from .kb_store_base import create_kb_store

                vector_db = create_kb_store(self.config)
                _close_after = True

            # Generate embedding for the query
            query_embedding = await self.chat_manager.llm_client.generate_embedding(
                text=query, model_id=self.config.kb_embedding_model
            )

            # Perform search using configured weights
            # (set kb_keyword_weight=0 for pure semantic, kb_semantic_weight=0 for pure keyword)
            search_mode = f"semantic={self.config.kb_semantic_weight}, keyword={self.config.kb_keyword_weight}"
            logger.debug(f"RAG search mode: {search_mode}")
            try:
                results = vector_db.hybrid_search(
                    query=query,
                    query_embedding=query_embedding,
                    limit=self.config.kb_top_k_results,
                    min_score=self.config.kb_similarity_threshold,
                    filters=None,
                    semantic_weight=self.config.kb_semantic_weight,
                    keyword_weight=self.config.kb_keyword_weight,
                )
            finally:
                if _close_after:
                    vector_db.close()

            # Log with the actual threshold used
            logger.info(
                f"RAG retrieval: Found {len(results)} relevant chunks (threshold={self.config.kb_similarity_threshold})"
            )

            if results:
                logger.debug(f"Top result score: {results[0]['similarity_score']:.4f}")
                # Debug: Log each chunk's details with component scores
                for i, result in enumerate(results, 1):
                    title = result.get("title", "N/A")[:60]
                    content_preview = result["content"][:150].replace("\n", " ")
                    score = result["similarity_score"]
                    semantic = result.get("semantic_component", "N/A")
                    keyword = result.get("keyword_component", "N/A")
                    if isinstance(semantic, float) and isinstance(keyword, float):
                        logger.debug(
                            f"  Chunk {i}: [hybrid={score:.4f}] "
                            f"(semantic={semantic:.4f} × {self.config.kb_semantic_weight} "
                            f"+ keyword={keyword:.4f} × {self.config.kb_keyword_weight}) "
                            f"{title} - {content_preview}..."
                        )
                    else:
                        logger.debug(f"  Chunk {i}: [{score:.4f}] {title} - {content_preview}...")

            return results if results else None

        except Exception as e:
            logger.error(f"KB retrieval failed: {str(e)}")
            return None

    def _format_kb_context(self, kb_results: List[Dict[str, Any]]) -> str:
        """
        Format KB chunks for inclusion in system prompt.

        Args:
            kb_results: List of KB search results

        Returns:
            Formatted string with KB context
        """
        if not kb_results:
            return ""

        context_parts = ["RELEVANT KNOWLEDGE BASE CONTEXT:"]
        context_parts.append("=" * 60)

        for i, result in enumerate(kb_results, 1):
            context_parts.append(f"\n[Context {i}] (Relevance: {result['similarity_score']:.2f})")

            # Add source attribution
            if result.get("title"):
                context_parts.append(f"Title: {result['title']}")
            if result.get("source"):
                context_parts.append(f"Source: {result['source']}")
            if result.get("source_url"):
                context_parts.append(f"URL: {result['source_url']}")

            context_parts.append(f"\n{result['content']}\n")
            context_parts.append("-" * 60)

        context_parts.append("\nINSTRUCTIONS:")
        context_parts.append("- The context above is provided for your information only - the user cannot see it")
        context_parts.append("- Use the context to inform your response when relevant")
        context_parts.append("- When citing information from the context, reference the actual source Title and URL")
        context_parts.append(
            "  Example: 'According to [Article Title](URL)...' or 'As mentioned in the documentation...'"
        )
        context_parts.append(
            "- DO NOT use internal references like '[Context 1]' or '[Context N]' - these mean nothing to the user"
        )
        context_parts.append("- If the context is not relevant to the question, answer from your general knowledge")
        context_parts.append("- Always be accurate and acknowledge if you're unsure")
        context_parts.append("=" * 60)

        return "\n".join(context_parts)

    def _format_auth_context(self, user_info: Optional[Dict[str, Any]]) -> str:
        """
        Format authenticated user information for inclusion in system prompt.

        Args:
            user_info: User metadata from verification endpoint (or None if not available)

        Returns:
            Formatted string with user context
        """
        if not user_info:
            return ""

        context_parts = ["AUTHENTICATED USER CONTEXT:"]
        context_parts.append("=" * 60)
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
