"""Custom exceptions for auto-bedrock-chat-fastapi"""


class BedrockChatError(Exception):
    """Base exception for all auto-bedrock-chat-fastapi errors"""

    pass


class ConfigurationError(BedrockChatError):
    """Raised when there's an issue with configuration"""

    pass


class SessionError(BedrockChatError):
    """Raised when there's an issue with session management"""

    pass


class LLMClientError(BedrockChatError):
    """Raised when there's an issue with LLM API calls.

    This is the generic, service-agnostic base for all LLM transport
    errors.  Subclass it for provider-specific errors (e.g.
    ``BedrockClientError``).
    """

    pass


class BedrockClientError(LLMClientError):
    """Raised when there's an issue with Bedrock API calls.

    Bedrock-specific subclass of ``LLMClientError``.  Code in
    ``bedrock_client.py`` and ``retry_handler.py`` raises this;
    the generic ``chat_manager.py`` catches the parent ``LLMClientError``.
    """

    pass


class ContextWindowExceededError(LLMClientError):
    """Raised when the input exceeds the model's context window.

    This is a recoverable error — the ChatManager can attempt to reduce
    message history or summarize content before retrying.
    """

    pass


class ModelError(BedrockChatError):
    """Raised when there's an issue with AI model operations"""

    pass


class ToolError(BedrockChatError):
    """Raised when there's an issue with tool operations"""

    pass


class AuthenticationError(BedrockChatError):
    """Raised when there's an authentication issue"""

    pass


class RateLimitError(BedrockChatError):
    """Raised when rate limits are exceeded"""

    pass


class ToolsGenerationError(BedrockChatError):
    """Raised when there's an issue generating tools from FastAPI routes"""

    pass


class WebSocketError(BedrockChatError):
    """Raised when there's an issue with WebSocket communication"""

    pass
