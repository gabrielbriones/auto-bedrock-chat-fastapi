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


class BedrockClientError(BedrockChatError):
    """Raised when there's an issue with Bedrock API calls"""
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
    pass