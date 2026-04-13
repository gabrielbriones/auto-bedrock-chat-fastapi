"""SSO session storage and session-token signing utilities"""

import logging
import time
import uuid
from typing import Any, Dict, Optional

from jose import JWTError, jwt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SESSION_TOKEN_ALGORITHM = "HS256"
_PENDING_DEFAULT_TTL = 300  # 5 minutes


# ---------------------------------------------------------------------------
# SSOSessionStore
# ---------------------------------------------------------------------------


class SSOSessionStore:
    """In-memory store for SSO sessions and pending authorization requests.

    **Sessions** hold the tokens + user info returned after a successful
    OAuth2 callback.  Each session is identified by an opaque UUID
    (``session_id``) and has a configurable TTL.

    **Pending auth** entries bind an OAuth2 ``state`` parameter to the
    PKCE ``code_verifier`` generated for that login attempt.  They expire
    automatically after a short TTL (default 5 minutes) to prevent replay.

    **Session tokens** are short-lived JWTs signed with HMAC-SHA256 using
    the application's ``sso_session_secret``.  They carry the ``session_id``
    and an expiry claim, so the WebSocket layer can validate them without
    touching the session store directly.

    All data is held in-memory; state is lost on server restart.
    """

    def __init__(self, session_ttl: int = 3600) -> None:
        """Create a new store.

        Args:
            session_ttl: Default session lifetime in seconds.
        """
        self._session_ttl = session_ttl
        # key: session_id (str) → value: session dict
        self._sessions: Dict[str, Dict[str, Any]] = {}
        # key: state (str) → value: {code_verifier, expires_at}
        self._pending: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def create_session(
        self,
        tokens: Dict[str, Any],
        user_info: Optional[Dict[str, Any]] = None,
        id_token_claims: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a new SSO session and return its ``session_id``.

        Args:
            tokens: Token dict from token exchange (access_token, refresh_token, …).
            user_info: User profile from the userinfo endpoint.
            id_token_claims: Decoded ID token claims.

        Returns:
            A UUID string that uniquely identifies this session.
        """
        session_id = str(uuid.uuid4())
        now = time.time()
        self._sessions[session_id] = {
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "id_token": tokens.get("id_token"),
            "id_token_claims": id_token_claims or {},
            "user_info": user_info or {},
            "expires_at": now + self._session_ttl,
            "created_at": now,
        }
        logger.debug("SSO session created: %s (expires in %ds)", session_id, self._session_ttl)
        return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a session by ID.

        Returns ``None`` if the session does not exist or has expired.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if time.time() > session["expires_at"]:
            # Lazy expiry — evict and return None
            del self._sessions[session_id]
            logger.debug("SSO session expired and evicted: %s", session_id)
            return None
        return session

    def delete_session(self, session_id: str) -> None:
        """Remove a session from the store (idempotent)."""
        self._sessions.pop(session_id, None)
        logger.debug("SSO session deleted: %s", session_id)

    def update_tokens(self, session_id: str, new_tokens: Dict[str, Any]) -> bool:
        """Update the tokens stored in an existing session.

        Args:
            session_id: The session to update.
            new_tokens: New token dict (typically from a refresh).

        Returns:
            ``True`` if the session was found and updated, ``False`` otherwise.
        """
        session = self.get_session(session_id)
        if session is None:
            return False
        if "access_token" in new_tokens:
            session["access_token"] = new_tokens["access_token"]
        if "refresh_token" in new_tokens:
            session["refresh_token"] = new_tokens["refresh_token"]
        if "id_token" in new_tokens:
            session["id_token"] = new_tokens["id_token"]
        # Extend expiry on refresh
        session["expires_at"] = time.time() + self._session_ttl
        logger.debug("SSO session tokens updated: %s", session_id)
        return True

    def cleanup_expired(self) -> int:
        """Remove all expired sessions from the store.

        Returns:
            Number of sessions removed.
        """
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if now > s["expires_at"]]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.debug("Cleanup removed %d expired SSO sessions", len(expired))
        return len(expired)

    # ------------------------------------------------------------------
    # Pending auth store (state → code_verifier)
    # ------------------------------------------------------------------

    def store_pending(
        self,
        state: str,
        code_verifier: str,
        ttl: int = _PENDING_DEFAULT_TTL,
    ) -> None:
        """Store a pending OAuth2 state / PKCE code-verifier pair.

        Args:
            state: The opaque state parameter sent to the IdP.
            code_verifier: The PKCE code verifier for this auth attempt.
            ttl: Time-to-live in seconds (default 5 minutes).
        """
        self._pending[state] = {
            "code_verifier": code_verifier,
            "expires_at": time.time() + ttl,
        }

    def get_pending(self, state: str) -> Optional[str]:
        """Retrieve the code verifier for a pending auth state.

        Returns ``None`` if the state is unknown or has expired.
        """
        entry = self._pending.get(state)
        if entry is None:
            return None
        if time.time() > entry["expires_at"]:
            del self._pending[state]
            logger.debug("Pending auth state expired: %s", state)
            return None
        return entry["code_verifier"]

    def delete_pending(self, state: str) -> None:
        """Remove a pending auth entry (one-time use enforcement)."""
        self._pending.pop(state, None)

    # ------------------------------------------------------------------
    # Session token (JWT) helpers
    # ------------------------------------------------------------------

    def generate_session_token(self, session_id: str, sso_session_secret: str) -> str:
        """Create a signed JWT containing the session ID.

        The token is signed with HMAC-SHA256 using *sso_session_secret* and
        expires at the same time as the underlying session.

        Args:
            session_id: The SSO session UUID.
            sso_session_secret: Signing secret from config.

        Returns:
            Compact JWT string.

        Raises:
            KeyError: If the session does not exist.
        """
        session = self._sessions[session_id]
        claims = {
            "session_id": session_id,
            "exp": int(session["expires_at"]),
            "iat": int(session["created_at"]),
        }
        return jwt.encode(claims, sso_session_secret, algorithm=_SESSION_TOKEN_ALGORITHM)

    @staticmethod
    def validate_session_token(token: str, sso_session_secret: str) -> Optional[str]:
        """Verify and decode a session JWT, returning the ``session_id``.

        Returns ``None`` if the token is invalid, expired, or tampered with.

        Args:
            token: JWT string to validate.
            sso_session_secret: Signing secret from config.

        Returns:
            ``session_id`` on success, ``None`` on failure.
        """
        try:
            claims = jwt.decode(
                token,
                sso_session_secret,
                algorithms=[_SESSION_TOKEN_ALGORITHM],
            )
            return claims.get("session_id")
        except JWTError as exc:
            logger.debug("Session token validation failed: %s", exc)
            return None
