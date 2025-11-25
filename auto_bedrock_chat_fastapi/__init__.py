"""Auto Bedrock Chat FastAPI Plugin

A FastAPI plugin that automatically adds conversational AI capabilities to your API
using Amazon Bedrock models with real-time WebSocket chat and automatic tool generation.
"""

__version__ = "1.0.0"

# Additional exports for advanced usage
from .auth_handler import AuthenticationHandler, AuthType, Credentials
from .bedrock_client import BedrockClient
from .config import ChatConfig, load_config, validate_config
from .exceptions import (
    AuthenticationError,
    BedrockChatError,
    ConfigurationError,
    ModelError,
    RateLimitError,
    SessionError,
    ToolError,
)

# Main exports
from .plugin import (
    BedrockChatPlugin,
    add_bedrock_chat,
    create_fastapi_with_bedrock_chat,
    create_tools_generator_from_spec,
)
from .session_manager import ChatMessage, ChatSession, ChatSessionManager
from .tools_generator import ToolsGenerator
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
    "AuthenticationHandler",
    "AuthType",
    "Credentials",
    # Core components (for advanced usage)
    "BedrockClient",
    "ChatSessionManager",
    "ChatSession",
    "ChatMessage",
    "ToolsGenerator",
    "WebSocketChatHandler",
    # Exceptions
    "BedrockChatError",
    "ConfigurationError",
    "ModelError",
    "SessionError",
    "ToolError",
    "AuthenticationError",
    "RateLimitError",
]
