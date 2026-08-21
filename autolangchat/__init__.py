"""autolangchat — LangGraph-powered AI chat plugin for FastAPI

Replaces the original auto_bedrock_chat_fastapi package (autolangchat is the new package). The main chat
orchestration layer now runs on a LangGraph StateGraph instead of the
hand-rolled session-management + LLM-call loop.
"""

__version__ = "1.1.0"

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
# ``from . import X`` binds ``autolangchat.graph`` and
# ``autolangchat.model_capabilities`` as direct attributes of this package.
# Python 3.10's import system skips that setattr step when the submodule is
# already in ``sys.modules`` (fixed in 3.11); the ``STORE_NAME`` bytecode from
# a package-relative ``from . import X`` statement always sets the attribute
# in this module's ``__dict__`` regardless of Python version, so the parent
# attribute is reliably present for ``unittest.mock.patch`` traversal.
from . import graph, model_capabilities
from .graph.graph import build_chat_graph
from .graph.tools.generator import ToolsGenerator, create_tools_generator_from_spec
from .graph.tools.manager import AuthInfo, ToolManager
from .message_preprocessor import MessagePreprocessor
from .plugin import AutoLangChatPlugin, add_autolangchat, create_fastapi_with_autolangchat
from .session_manager import ChatSession, ChatSessionManager
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
