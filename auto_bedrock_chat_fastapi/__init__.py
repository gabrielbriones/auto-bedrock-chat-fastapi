"""Auto Bedrock Chat FastAPI Plugin

A FastAPI plugin that automatically adds conversational AI capabilities to your API
using Amazon Bedrock models with real-time WebSocket chat and automatic tool generation.
"""

__version__ = "1.0.0"

# Main exports
from .plugin import add_bedrock_chat, BedrockChatPlugin
from .config import ChatConfig, load_config, validate_config
from .exceptions import (
    BedrockChatError,
    ConfigurationError,
    ModelError,
    SessionError,
    ToolError,
    AuthenticationError,
    RateLimitError
)

# Additional exports for advanced usage
from .bedrock_client import BedrockClient
from .session_manager import ChatSessionManager, ChatSession, ChatMessage
from .tools_generator import ToolsGenerator
from .websocket_handler import WebSocketChatHandler

__all__ = [
    # Main plugin
    "add_bedrock_chat",
    "BedrockChatPlugin",
    
    # Configuration
    "ChatConfig", 
    "load_config",
    "validate_config",
    
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
    "RateLimitError"
]