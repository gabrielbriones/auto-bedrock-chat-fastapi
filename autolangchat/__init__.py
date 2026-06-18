"""autolangchat — LangGraph-powered AI chat plugin for FastAPI

Replaces the original auto_bedrock_chat_fastapi package (autolangchat is the new package). The main chat
orchestration layer now runs on a LangGraph StateGraph instead of the
hand-rolled session-management + LLM-call loop.
"""

__version__ = "2.0.0"

from .auth_handler import DEFAULT_SUPPORTED_AUTH_TYPES, AuthenticationHandler, AuthType, Credentials
from .config import ChatConfig, load_config, validate_config
from .exceptions import (
    AuthenticationError,
    ConfigurationError,
    ContextWindowExceededError,
    LLMClientError,
    ModelError,
    RateLimitError,
    SessionError,
    ToolError,
)
from .graph.graph import build_chat_graph
from .message_preprocessor import MessagePreprocessor
from .plugin import AutoLangChatPlugin, add_autolangchat, create_fastapi_with_autolangchat
from .session_manager import ChatSession, ChatSessionManager
from .tool_manager import AuthInfo, ToolManager, ToolsGenerator, create_tools_generator_from_spec
from .websocket_handler import WebSocketChatHandler

__all__ = [
    # Main plugin
    "add_autolangchat",
    "AutoLangChatPlugin",
    "create_fastapi_with_autolangchat",
    "create_tools_generator_from_spec",
    # Configuration
    "ChatConfig",
    "load_config",
    "validate_config",
    # Authentication
    "DEFAULT_SUPPORTED_AUTH_TYPES",
    "AuthenticationHandler",
    "AuthType",
    "Credentials",
    # Core components (advanced usage)
    "build_chat_graph",
    "MessagePreprocessor",
    "ChatSessionManager",
    "ChatSession",
    "ToolsGenerator",
    "ToolManager",
    "AuthInfo",
    "WebSocketChatHandler",
    # Exceptions
    "ConfigurationError",
    "ContextWindowExceededError",
    "LLMClientError",
    "ModelError",
    "SessionError",
    "ToolError",
    "AuthenticationError",
    "RateLimitError",
]
