"""Custom exceptions for autolangchat"""


class AutoLangChatError(Exception):
    """Base exception for all autolangchat errors"""

    pass


class ConfigurationError(AutoLangChatError):
    """Raised when there's an issue with configuration"""

    pass


class SessionError(AutoLangChatError):
    """Raised when there's an issue with session management"""

    pass


class LLMClientError(AutoLangChatError):
    """Raised when there's an issue with LLM API calls.

    This is the generic, service-agnostic base for all LLM transport
    errors.  Subclass it for provider-specific errors (e.g.
    ``BedrockClientError``).
    """

    pass


class BedrockClientError(LLMClientError):
    """Raised when there's an issue with Bedrock API calls.

    Bedrock-specific subclass of ``LLMClientError``.  Raised by
    :mod:`autolangchat.rag.bedrock_embeddings` for embedding failures.
    """

    pass


class ContextWindowExceededError(LLMClientError):
    """Raised when the input exceeds the model's context window.

    This is a recoverable error — the LangGraph preprocessing node can
    attempt to reduce message history or summarize content before retrying.
    """

    pass


class ModelError(AutoLangChatError):
    """Raised when there's an issue with AI model operations"""

    pass


class ToolError(AutoLangChatError):
    """Raised when there's an issue with tool operations"""

    pass


class AuthenticationError(AutoLangChatError):
    """Raised when there's an authentication issue"""

    pass


class RateLimitError(AutoLangChatError):
    """Raised when rate limits are exceeded"""

    pass


class ToolsGenerationError(AutoLangChatError):
    """Raised when there's an issue generating tools from FastAPI routes"""

    pass


class WebSocketError(AutoLangChatError):
    """Raised when there's an issue with WebSocket communication"""

    pass


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


class FeedbackError(AutoLangChatError):
    """Base exception for feedback-store operations."""

    pass


class FeedbackNotFoundError(FeedbackError):
    """Raised when a feedback entry cannot be located by id."""

    pass


class InvalidStatusTransitionError(FeedbackError):
    """Raised when an attempted ``review_status`` change is not allowed."""

    pass


class AlreadyIntegratedError(FeedbackError):
    """Raised when synthesis is attempted on an entry already linked to a KB document."""

    pass


class UnauthorizedFeedbackError(FeedbackError):
    """Raised when a user is not permitted to submit or modify feedback."""

    pass


# ---------------------------------------------------------------------------
# Knowledge-base store
# ---------------------------------------------------------------------------


class KBStoreError(AutoLangChatError):
    """Base exception for knowledge-base store admin operations."""

    pass


class KBDocumentNotFoundError(KBStoreError):
    """Raised when a KB document cannot be located by id."""

    pass


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------


class AdminAPIError(AutoLangChatError):
    """Application-layer error raised by admin HTTP routes.

    A central exception handler (registered by
    :meth:`AutoLangChatPlugin._setup_admin_routes`) maps this — and the
    domain-specific :class:`FeedbackNotFoundError` /
    :class:`InvalidStatusTransitionError` / :class:`KBDocumentNotFoundError`
    — to a standardized response envelope ``{detail, code}`` so every
    admin endpoint emits consistent error JSON.

    Attributes
    ----------
    status_code:
        HTTP status code to emit.
    code:
        Stable machine-readable error code (e.g. ``"invalid_filters"``).
        Surfaced as the ``code`` field of the error envelope and meant
        to be programmatically matched by clients — never localize.
    detail:
        Human-readable message, optionally with structured ``errors``
        appended (only carried on validation failures). Surfaced as
        ``detail`` of the envelope.
    """

    def __init__(self, *, status_code: int, code: str, detail: str, errors: object = None):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.errors = errors
