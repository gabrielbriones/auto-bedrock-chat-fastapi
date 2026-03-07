"""Auto Bedrock Chat FastAPI Plugin

A FastAPI plugin that automatically adds conversational AI capabilities to your API
using Amazon Bedrock models with real-time WebSocket chat and automatic tool generation.
"""

__version__ = "1.0.0"

# Additional exports for advanced usage
from .auth_handler import DEFAULT_SUPPORTED_AUTH_TYPES, AuthenticationHandler, AuthType, Credentials
from .bedrock_client import BedrockClient
from .chat_manager import ChatManager
from .config import ChatConfig, load_config, validate_config
from .exceptions import (
    AuthenticationError,
    BedrockChatError,
    ConfigurationError,
    ContextWindowExceededError,
    LLMClientError,
    ModelError,
    RateLimitError,
    SessionError,
    ToolError,
)
from .message_preprocessor import MessagePreprocessor
from .models import ChatCompletionResult

# Main exports
from .plugin import BedrockChatPlugin, add_bedrock_chat, create_fastapi_with_bedrock_chat
from .session_manager import ChatMessage, ChatSession, ChatSessionManager
from .tool_manager import AuthInfo, ToolManager, ToolsGenerator, create_tools_generator_from_spec
from .websocket_handler import WebSocketChatHandler

__all__ = [
    # Main plugin
    "add_bedrock_chat",
    "BedrockChatPlugin",
    "create_fastapi_with_bedrock_chat",
    "create_tools_generator_from_spec",
    # Configuration
    "ChatConfig",
    "load_config",
    "validate_config",
    # Authentication (new)
    "DEFAULT_SUPPORTED_AUTH_TYPES",
    "AuthenticationHandler",
    "AuthType",
    "Credentials",
    # Core components (for advanced usage)
    "BedrockClient",
    "ChatManager",
    "ChatCompletionResult",
    "MessagePreprocessor",
    "ChatSessionManager",
    "ChatSession",
    "ChatMessage",
    "ToolsGenerator",
    "ToolManager",
    "AuthInfo",
    "WebSocketChatHandler",
    # Exceptions
    "BedrockChatError",
    "ConfigurationError",
    "ContextWindowExceededError",
    "LLMClientError",
    "ModelError",
    "SessionError",
    "ToolError",
    "AuthenticationError",
    "RateLimitError",
]
