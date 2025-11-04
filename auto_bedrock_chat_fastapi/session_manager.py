"""Chat session management for WebSocket connections"""

import asyncio
import uuid
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from fastapi import WebSocket

from .config import ChatConfig
from .exceptions import SessionError


logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Represents a single chat message"""
    role: str  # 'user', 'assistant', 'system', 'tool'
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tool_calls: Optional[List[Dict]] = None
    tool_results: Optional[List[Dict]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        """Create from dictionary"""
        timestamp = datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat()))
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=timestamp,
            tool_calls=data.get("tool_calls"),
            tool_results=data.get("tool_results"),
            metadata=data.get("metadata", {})
        )


@dataclass
class ChatSession:
    """Represents a chat session with conversation history"""
    session_id: str
    websocket: WebSocket
    conversation_history: List[ChatMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    user_id: Optional[str] = None
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Session settings
    max_history_length: int = 50
    context_window: int = 20  # Number of recent messages to include in context
    
    def add_message(self, message: ChatMessage):
        """Add a message to conversation history"""
        self.conversation_history.append(message)
        self.last_activity = datetime.now()
        
        # Trim history if it gets too long
        if len(self.conversation_history) > self.max_history_length:
            # Keep the first message (usually system prompt) and recent messages
            if self.conversation_history[0].role == "system":
                system_msg = self.conversation_history[0]
                recent_msgs = self.conversation_history[-(self.max_history_length - 1):]
                self.conversation_history = [system_msg] + recent_msgs
            else:
                self.conversation_history = self.conversation_history[-self.max_history_length:]
    
    def get_context_messages(self) -> List[ChatMessage]:
        """Get recent messages for context"""
        if not self.conversation_history:
            return []
        
        # Always include system message if present
        if self.conversation_history[0].role == "system":
            system_msg = self.conversation_history[0]
            recent_msgs = self.conversation_history[-(self.context_window - 1):]
            return [system_msg] + recent_msgs
        else:
            return self.conversation_history[-self.context_window:]
    
    def get_message_count(self) -> int:
        """Get total message count"""
        return len(self.conversation_history)
    
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
            "message_count": self.get_message_count(),
            "duration_seconds": self.get_duration().total_seconds(),
            "conversation_history": [msg.to_dict() for msg in self.conversation_history]
        }


class ChatSessionManager:
    """Manages chat sessions and WebSocket connections"""
    
    def __init__(self, config: ChatConfig):
        self.config = config
        self._sessions: Dict[str, ChatSession] = {}
        self._websocket_to_session: Dict[WebSocket, str] = {}
        self._user_sessions: Dict[str, List[str]] = {}  # user_id -> session_ids
        
        # Statistics
        self._total_sessions_created = 0
        self._total_messages_processed = 0
        
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
        **metadata
    ) -> str:
        """Create a new chat session for the WebSocket connection"""
        
        # Check if we're at capacity
        if len(self._sessions) >= self.config.max_sessions:
            await self._cleanup_oldest_sessions(10)  # Remove 10 oldest sessions
            
            if len(self._sessions) >= self.config.max_sessions:
                raise SessionError(f"Maximum session limit reached: {self.config.max_sessions}")
        
        # Generate unique session ID
        session_id = str(uuid.uuid4())
        
        # Create session
        session = ChatSession(
            session_id=session_id,
            websocket=websocket,
            user_id=user_id,
            user_agent=user_agent,
            ip_address=ip_address,
            metadata=metadata
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
    
    async def add_message(self, session_id: str, message: ChatMessage):
        """Add a message to session conversation history"""
        session = self._sessions.get(session_id)
        if not session:
            raise SessionError(f"Session {session_id} not found")
        
        session.add_message(message)
        self._total_messages_processed += 1
        
        logger.debug(f"Added message to session {session_id}: {message.role}")
    
    async def get_conversation_history(self, session_id: str) -> List[ChatMessage]:
        """Get conversation history for a session"""
        session = self._sessions.get(session_id)
        if not session:
            return []
        
        return session.conversation_history
    
    async def get_context_messages(self, session_id: str) -> List[ChatMessage]:
        """Get context messages for a session"""
        session = self._sessions.get(session_id)
        if not session:
            return []
        
        return session.get_context_messages()
    
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
                logger.info(
                    f"Removed session {session_id} after {duration.total_seconds():.1f}s, "
                    f"{session.get_message_count()} messages"
                )
                
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
    
    async def get_user_sessions(self, user_id: str) -> List[ChatSession]:
        """Get all sessions for a user"""
        session_ids = self._user_sessions.get(user_id, [])
        sessions = []
        
        for session_id in session_ids:
            session = self._sessions.get(session_id)
            if session:
                sessions.append(session)
        
        return sessions
    
    async def get_session_count(self) -> int:
        """Get current number of active sessions"""
        return len(self._sessions)
    
    async def get_statistics(self) -> Dict[str, Any]:
        """Get session manager statistics"""
        active_sessions = len(self._sessions)
        
        # Calculate session durations
        durations = [session.get_duration().total_seconds() for session in self._sessions.values()]
        avg_duration = sum(durations) / len(durations) if durations else 0
        
        # Calculate message counts
        message_counts = [session.get_message_count() for session in self._sessions.values()]
        total_active_messages = sum(message_counts)
        avg_messages_per_session = total_active_messages / active_sessions if active_sessions else 0
        
        return {
            "active_sessions": active_sessions,
            "total_sessions_created": self._total_sessions_created,
            "total_messages_processed": self._total_messages_processed,
            "average_session_duration_seconds": avg_duration,
            "average_messages_per_session": avg_messages_per_session,
            "unique_users": len(self._user_sessions),
            "max_sessions": self.config.max_sessions,
            "session_timeout": self.config.session_timeout
        }
    
    async def _cleanup_expired_sessions(self):
        """Background task to clean up expired sessions"""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                
                current_time = datetime.now()
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
        sorted_sessions = sorted(
            self._sessions.values(),
            key=lambda s: s.created_at
        )
        
        # Remove oldest sessions
        for i in range(min(count, len(sorted_sessions))):
            session = sorted_sessions[i]
            await self.remove_session_by_id(session.session_id)
        
        logger.info(f"Cleaned up {min(count, len(sorted_sessions))} oldest sessions")
    
    async def broadcast_to_user_sessions(self, user_id: str, message: Dict[str, Any]):
        """Broadcast a message to all sessions of a user"""
        user_sessions = await self.get_user_sessions(user_id)
        
        for session in user_sessions:
            try:
                await session.websocket.send_json(message)
            except Exception as e:
                logger.error(f"Failed to broadcast to session {session.session_id}: {str(e)}")
                # Remove broken session
                await self.remove_session_by_id(session.session_id)
    
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
        if hasattr(self, '_cleanup_task') and self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()