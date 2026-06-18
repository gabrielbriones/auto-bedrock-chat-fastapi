"""Chat session management for WebSocket connections"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from .auth_handler import AuthenticationHandler, Credentials
from .config import ChatConfig
from .exceptions import SessionError

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    """Represents a chat session (live WebSocket connection context).

    Conversation history is owned by the LangGraph checkpoint — this class
    holds only the live-connection data that LangGraph cannot provide.
    """

    session_id: str
    websocket: WebSocket
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    user_id: Optional[str] = None
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Authentication
    credentials: Credentials = field(default_factory=Credentials)
    auth_handler: Optional[AuthenticationHandler] = field(default=None, init=False)

    def __post_init__(self):
        """Initialize auth handler after dataclass initialization"""
        self.auth_handler = AuthenticationHandler(self.credentials)

    def get_duration(self) -> timedelta:
        """Get session duration"""
        return datetime.now() - self.created_at

    def is_expired(self, timeout: int) -> bool:
        """Check if session has expired"""
        return (datetime.now() - self.last_activity).total_seconds() > timeout

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "user_id": self.user_id,
            "user_agent": self.user_agent,
            "ip_address": self.ip_address,
            "metadata": self.metadata,
            "duration_seconds": self.get_duration().total_seconds(),
        }


class ChatSessionManager:
    """Manages chat sessions and WebSocket connections"""

    def __init__(self, config: ChatConfig):
        self.config = config
        self._sessions: Dict[str, ChatSession] = {}
        self._websocket_to_session: Dict[WebSocket, str] = {}
        # user_id -> session_ids
        self._user_sessions: Dict[str, List[str]] = {}

        # Statistics
        self._total_sessions_created = 0

        # Start background tasks
        self._cleanup_task = None

        # Only start cleanup task if we're in an async context
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                self._start_cleanup_task()
        except RuntimeError:
            # No event loop running, will start cleanup task later if needed
            pass

    def _start_cleanup_task(self):
        """Start the cleanup task for expired sessions"""
        try:
            if self._cleanup_task is None or self._cleanup_task.done():
                self._cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())
        except RuntimeError:
            # No event loop running
            pass

    async def create_session(
        self,
        websocket: WebSocket,
        user_id: Optional[str] = None,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
        preferred_session_id: Optional[str] = None,
        **metadata,
    ) -> str:
        """Create a new chat session for the WebSocket connection"""

        # Check if we're at capacity
        if len(self._sessions) >= self.config.max_sessions:
            # Remove 10 oldest sessions
            await self._cleanup_oldest_sessions(10)

            if len(self._sessions) >= self.config.max_sessions:
                raise SessionError(f"Maximum session limit reached: {self.config.max_sessions}")

        # Use the caller-supplied session_id if it looks like a valid UUID,
        # otherwise generate a fresh one. This allows clients to reconnect
        # to the same LangGraph checkpoint thread after a process restart.
        if preferred_session_id:
            try:
                import uuid as _uuid

                _uuid.UUID(preferred_session_id)  # validate format
                session_id = preferred_session_id
                logger.info(
                    "Reusing provided session_id=%s for user=%s",
                    session_id,
                    user_id or "anonymous",
                )
            except ValueError:
                session_id = str(uuid.uuid4())
                logger.warning(
                    "Provided session_id=%r is not a valid UUID; generated new id=%s",
                    preferred_session_id,
                    session_id,
                )
        else:
            session_id = str(uuid.uuid4())

        # Create session
        session = ChatSession(
            session_id=session_id,
            websocket=websocket,
            user_id=user_id,
            user_agent=user_agent,
            ip_address=ip_address,
            metadata=metadata,
        )

        # Store session mappings
        self._sessions[session_id] = session
        self._websocket_to_session[websocket] = session_id

        # Track user sessions
        if user_id:
            if user_id not in self._user_sessions:
                self._user_sessions[user_id] = []
            self._user_sessions[user_id].append(session_id)

        # Update statistics
        self._total_sessions_created += 1

        logger.info(f"Created session {session_id} for user {user_id or 'anonymous'}")

        return session_id

    async def get_session(self, websocket: WebSocket) -> Optional[ChatSession]:
        """Get session by WebSocket connection"""
        session_id = self._websocket_to_session.get(websocket)
        if session_id:
            session = self._sessions.get(session_id)
            if session:
                # Update last activity
                session.last_activity = datetime.now()
                return session
        return None

    async def get_session_by_id(self, session_id: str) -> Optional[ChatSession]:
        """Get session by ID"""
        return self._sessions.get(session_id)

    async def update_session_user_id(self, session_id: str, user_id: str) -> bool:
        """Update the user_id for an existing session.

        This is useful when a session is created without authentication
        and the user authenticates later (e.g., OAuth2 verification).

        Args:
            session_id: The session ID to update
            user_id: The new user_id to set

        Returns:
            True if the session was updated, False if session not found
        """
        session = self._sessions.get(session_id)
        if not session:
            logger.warning(f"Cannot update user_id: session {session_id} not found")
            return False

        old_user_id = session.user_id

        # Remove from old user's session list if needed
        if old_user_id and old_user_id in self._user_sessions:
            user_sessions = self._user_sessions[old_user_id]
            if session_id in user_sessions:
                user_sessions.remove(session_id)
            if not user_sessions:
                del self._user_sessions[old_user_id]

        # Update session user_id
        session.user_id = user_id

        # Add to new user's session list
        if user_id:
            if user_id not in self._user_sessions:
                self._user_sessions[user_id] = []
            if session_id not in self._user_sessions[user_id]:
                self._user_sessions[user_id].append(session_id)

        logger.info(f"Updated session {session_id} user_id: {old_user_id} -> {user_id}")
        return True

    async def remove_session(self, websocket: WebSocket) -> Optional[str]:
        """Remove session when WebSocket disconnects"""
        session_id = self._websocket_to_session.pop(websocket, None)
        if session_id:
            session = self._sessions.pop(session_id, None)
            if session:
                # Remove from user sessions
                if session.user_id and session.user_id in self._user_sessions:
                    user_sessions = self._user_sessions[session.user_id]
                    if session_id in user_sessions:
                        user_sessions.remove(session_id)
                    if not user_sessions:
                        del self._user_sessions[session.user_id]

                duration = session.get_duration()
                logger.info(f"Removed session {session_id} after {duration.total_seconds():.1f}s")

                return session_id

        return None

    async def remove_session_by_id(self, session_id: str) -> bool:
        """Remove session by ID"""
        session = self._sessions.get(session_id)
        if not session:
            return False

        # Remove WebSocket mapping
        if session.websocket in self._websocket_to_session:
            del self._websocket_to_session[session.websocket]

        # Remove session
        del self._sessions[session_id]

        # Remove from user sessions
        if session.user_id and session.user_id in self._user_sessions:
            user_sessions = self._user_sessions[session.user_id]
            if session_id in user_sessions:
                user_sessions.remove(session_id)
            if not user_sessions:
                del self._user_sessions[session.user_id]

        logger.info(f"Removed session {session_id}")
        return True

    async def get_session_count(self) -> int:
        """Get current number of active sessions"""
        return len(self._sessions)

    async def get_statistics(self) -> Dict[str, Any]:
        """Get session manager statistics"""
        active_sessions = len(self._sessions)

        # Calculate session durations
        durations = [session.get_duration().total_seconds() for session in self._sessions.values()]
        avg_duration = sum(durations) / len(durations) if durations else 0

        return {
            "active_sessions": active_sessions,
            "total_sessions_created": self._total_sessions_created,
            "average_session_duration_seconds": avg_duration,
            "unique_users": len(self._user_sessions),
            "max_sessions": self.config.max_sessions,
            "session_timeout": self.config.session_timeout,
        }

    async def _cleanup_expired_sessions(self):
        """Background task to clean up expired sessions"""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes

                expired_sessions = []

                for session_id, session in self._sessions.items():
                    if session.is_expired(self.config.session_timeout):
                        expired_sessions.append(session_id)

                # Remove expired sessions
                for session_id in expired_sessions:
                    await self.remove_session_by_id(session_id)

                if expired_sessions:
                    logger.info(f"Cleaned up {len(expired_sessions)} expired sessions")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in session cleanup: {str(e)}")
                await asyncio.sleep(60)  # Wait a minute before retrying

    async def _cleanup_oldest_sessions(self, count: int):
        """Remove the oldest sessions to free up space"""
        if not self._sessions:
            return

        # Sort sessions by creation time
        sorted_sessions = sorted(self._sessions.values(), key=lambda s: s.created_at)

        # Remove oldest sessions
        for i in range(min(count, len(sorted_sessions))):
            session = sorted_sessions[i]
            await self.remove_session_by_id(session.session_id)

        logger.info(f"Cleaned up {min(count, len(sorted_sessions))} oldest sessions")

    async def shutdown(self):
        """Shutdown the session manager"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Close all WebSocket connections
        for session in list(self._sessions.values()):
            try:
                await session.websocket.close()
            except Exception as e:
                logger.error(f"Error closing WebSocket for session {session.session_id}: {str(e)}")

        # Clear all sessions
        self._sessions.clear()
        self._websocket_to_session.clear()
        self._user_sessions.clear()

        logger.info("Session manager shutdown complete")

    def __del__(self):
        """Cleanup on deletion"""
        if hasattr(self, "_cleanup_task") and self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
