"""Configuration management for auto-bedrock-chat-fastapi"""

import os
from typing import Any, Callable, Dict, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .auth_handler import DEFAULT_SUPPORTED_AUTH_TYPES
from .defaults import (
    DEFAULT_ENABLE_AI_SUMMARIZATION,
    DEFAULT_EXPONENTIAL_BACKOFF,
    DEFAULT_GRACEFUL_DEGRADATION,
    DEFAULT_HISTORY_MSG_LENGTH_THRESHOLD,
    DEFAULT_HISTORY_MSG_TRUNCATION_TARGET,
    DEFAULT_HISTORY_TOTAL_LENGTH_THRESHOLD,
    DEFAULT_LLM_CLIENT_TYPE,
    DEFAULT_MAX_CONVERSATION_MESSAGES,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_SESSIONS,
    DEFAULT_MAX_TOOL_CALL_ROUNDS,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_MAX_TRUNCATION_RECURSION,
    DEFAULT_PRESERVE_SYSTEM_MESSAGE,
    DEFAULT_RETRY_DELAY,
    DEFAULT_SESSION_TIMEOUT,
    DEFAULT_SINGLE_MSG_LENGTH_THRESHOLD,
    DEFAULT_SINGLE_MSG_TRUNCATION_TARGET,
    DEFAULT_TIMEOUT,
    VALID_LLM_CLIENT_TYPES,
)
from .exceptions import ConfigurationError


def _get_env_file() -> str:
    """Determine which .env file to use based on environment"""
    # Check if we're in a test environment
    if (
        os.getenv("PYTEST_CURRENT_TEST")
        or "pytest" in os.getenv("_", "")
        or "pytest" in str(os.getenv("VIRTUAL_ENV", ""))
        or os.getenv("ENVIRONMENT") == "test"
    ):
        return ".env.test"
    # Check if pytest is in sys.modules (running under pytest)
    import sys

    if "pytest" in sys.modules:
        return ".env.test"
    # Default to .env
    return ".env"


class ChatConfig(BaseSettings):
    """Configuration for Bedrock Chat Plugin"""

    # Model Configuration
    model_id: str = Field(
        default="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        alias="BEDROCK_MODEL_ID",
        description="Bedrock model identifier",
    )

    temperature: float = Field(
        default=0.7,
        alias="BEDROCK_TEMPERATURE",
        ge=0.0,
        le=1.0,
        description="Sampling temperature for model responses",
    )

    max_tokens: int = Field(
        default=4096,
        alias="BEDROCK_MAX_TOKENS",
        gt=0,
        description="Maximum tokens in model response",
    )

    top_p: float = Field(
        default=0.9,
        alias="BEDROCK_TOP_P",
        ge=0.0,
        le=1.0,
        description="Top-p sampling parameter",
    )

    # System Configuration
    system_prompt: Optional[str] = Field(
        default=None,
        alias="BEDROCK_SYSTEM_PROMPT",
        description="Custom system prompt for the AI assistant",
    )

    # API Tools Configuration
    tools_desc: Optional[Dict] = Field(default_factory=dict, description="Auto-generated tools from FastAPI routes")

    openapi_spec_file: Optional[str] = Field(
        default=None,
        alias="BEDROCK_OPENAPI_SPEC_FILE",
        description="Path to OpenAPI spec file for framework-agnostic tool generation",
    )

    api_base_url: Optional[str] = Field(
        default=None,
        alias="BEDROCK_API_BASE_URL",
        description="Base URL for API calls (e.g., http://localhost:8080). Auto-detected if not specified",
    )

    allowed_paths: List[str] = Field(
        default_factory=list,
        alias="BEDROCK_ALLOWED_PATHS",
        description="Whitelist of API paths to expose as tools",
    )

    excluded_paths: List[str] = Field(
        default_factory=lambda: [
            "/bedrock-chat",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/health",
        ],
        alias="BEDROCK_EXCLUDED_PATHS",
        description="Blacklist of API paths to exclude from tools",
    )

    # Session Configuration
    max_tool_calls: int = Field(
        default=DEFAULT_MAX_TOOL_CALLS,
        alias="BEDROCK_MAX_TOOL_CALLS",
        gt=0,
        description="Maximum tool calls per conversation turn",
    )

    max_tool_call_rounds: int = Field(
        default=DEFAULT_MAX_TOOL_CALL_ROUNDS,
        alias="BEDROCK_MAX_TOOL_CALL_ROUNDS",
        gt=0,
        description="Maximum rounds of recursive tool calls",
    )

    # Conversation History Management
    max_conversation_messages: int = Field(
        default=DEFAULT_MAX_CONVERSATION_MESSAGES,
        alias="BEDROCK_MAX_CONVERSATION_MESSAGES",
        gt=0,
        description="Maximum messages to keep in conversation history",
    )

    preserve_system_message: bool = Field(
        default=DEFAULT_PRESERVE_SYSTEM_MESSAGE,
        alias="BEDROCK_PRESERVE_SYSTEM_MESSAGE",
        description="Whether to always preserve the system message when trimming history",
    )

    # LLM Client Configuration
    llm_client_type: str = Field(
        default=DEFAULT_LLM_CLIENT_TYPE,
        alias="BEDROCK_LLM_CLIENT_TYPE",
        description=(
            "LLM client to use for chat completions. " "Currently supported: 'bedrock'. Future: 'openai', 'anthropic'."
        ),
    )

    # AI Summarization Configuration
    enable_ai_summarization: bool = Field(
        default=DEFAULT_ENABLE_AI_SUMMARIZATION,
        alias="BEDROCK_ENABLE_AI_SUMMARIZATION",
        description=(
            "Enable AI-based summarization for oversized messages and conversation history. "
            "When enabled, uses LLM calls to intelligently condense content instead of plain text truncation. "
            "Default: False (uses plain text truncation). Enabling this will increase LLM token usage."
        ),
    )

    # Single-Message Truncation Configuration (Character-Based)
    single_msg_length_threshold: int = Field(
        default=DEFAULT_SINGLE_MSG_LENGTH_THRESHOLD,
        alias="BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD",
        gt=0,
        description=(
            "Single-message truncation threshold in characters. "
            "Messages exceeding this size are truncated (plain text) or summarized (AI). "
            "Default: 500K chars (~125K tokens). "
            "AI summarization chunk size is derived as half of this value."
        ),
    )

    single_msg_truncation_target: int = Field(
        default=DEFAULT_SINGLE_MSG_TRUNCATION_TARGET,
        alias="BEDROCK_SINGLE_MSG_TRUNCATION_TARGET",
        gt=0,
        description=(
            "Target size after single-message truncation in characters (85% of threshold). "
            "Default: 425K chars (~106K tokens)."
        ),
    )

    # History Truncation Configuration (Character-Based)
    history_total_length_threshold: int = Field(
        default=DEFAULT_HISTORY_TOTAL_LENGTH_THRESHOLD,
        alias="BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD",
        gt=0,
        description=(
            "Total conversation history threshold in characters. "
            "When the sum of all message sizes exceeds this, history truncation is triggered. "
            "Default: 650K chars (~163K-217K tokens depending on content type)."
        ),
    )

    history_msg_length_threshold: int = Field(
        default=DEFAULT_HISTORY_MSG_LENGTH_THRESHOLD,
        alias="BEDROCK_HISTORY_MSG_LENGTH_THRESHOLD",
        gt=0,
        description=(
            "Per-message threshold during history truncation in characters. "
            "Messages exceeding this size are truncated during history-level processing. "
            "Default: 100K chars (~25K tokens)."
        ),
    )

    history_msg_truncation_target: int = Field(
        default=DEFAULT_HISTORY_MSG_TRUNCATION_TARGET,
        alias="BEDROCK_HISTORY_MSG_TRUNCATION_TARGET",
        gt=0,
        description=(
            "Per-message target during history truncation in characters "
            "(85% of history_msg_length_threshold). "
            "Default: 85K chars (~21K tokens)."
        ),
    )

    max_truncation_recursion: int = Field(
        default=DEFAULT_MAX_TRUNCATION_RECURSION,
        alias="BEDROCK_MAX_TRUNCATION_RECURSION",
        ge=1,
        le=10,
        description=(
            "Maximum recursion depth for history truncation safety-net halving. "
            "If history still exceeds threshold after all 3 truncation steps, the process "
            "re-runs with halved thresholds, up to this many times. Default: 3."
        ),
    )

    # NOTE: Legacy tool_result_* settings (BEDROCK_TOOL_RESULT_NEW_RESPONSE_THRESHOLD,
    # BEDROCK_TOOL_RESULT_NEW_RESPONSE_TARGET, BEDROCK_TOOL_RESULT_HISTORY_THRESHOLD,
    # BEDROCK_TOOL_RESULT_HISTORY_TARGET) have been removed in Task 3.6.
    # Use the generalized settings instead:
    #   new_response_threshold → single_msg_length_threshold
    #   new_response_target    → single_msg_truncation_target
    #   history_msg_threshold  → history_msg_length_threshold
    #   history_msg_target     → history_msg_truncation_target

    timeout: int = Field(
        default=DEFAULT_TIMEOUT,
        alias="BEDROCK_TIMEOUT",
        gt=0,
        description="Timeout for API calls in seconds",
    )

    # WebSocket Configuration
    max_sessions: int = Field(
        default=DEFAULT_MAX_SESSIONS,
        alias="BEDROCK_MAX_SESSIONS",
        gt=0,
        description="Maximum concurrent WebSocket sessions",
    )

    session_timeout: int = Field(
        default=DEFAULT_SESSION_TIMEOUT,
        alias="BEDROCK_SESSION_TIMEOUT",
        gt=0,
        description="Session timeout in seconds",
    )

    # AWS Configuration
    aws_region: str = Field(
        default="us-east-1",
        alias="AWS_REGION",
        description="AWS region for Bedrock service",
    )

    aws_access_key_id: Optional[str] = Field(default=None, alias="AWS_ACCESS_KEY_ID", description="AWS access key ID")

    aws_secret_access_key: Optional[str] = Field(
        default=None, alias="AWS_SECRET_ACCESS_KEY", description="AWS secret access key"
    )

    # Endpoint Configuration
    chat_endpoint: str = Field(
        default="/bedrock-chat",
        alias="BEDROCK_CHAT_ENDPOINT",
        description="Base endpoint for chat API",
    )

    websocket_endpoint: str = Field(
        default="/bedrock-chat/ws",
        alias="BEDROCK_WEBSOCKET_ENDPOINT",
        description="WebSocket endpoint",
    )

    ui_endpoint: str = Field(
        default="/bedrock-chat/ui",
        alias="BEDROCK_UI_ENDPOINT",
        description="Web UI endpoint",
    )

    enable_ui: bool = Field(default=True, alias="BEDROCK_ENABLE_UI", description="Enable built-in chat UI")

    ui_title: str = Field(
        default="AI Assistant",
        alias="BEDROCK_UI_TITLE",
        description="Title displayed in the chat UI header",
    )

    ui_welcome_message: str = Field(
        default=(
            "Welcome! I'm your AI assistant. I can help you interact with the API endpoints. "
            "Try asking me to retrieve data, create resources, or explain what operations are available."
        ),
        alias="BEDROCK_UI_WELCOME_MESSAGE",
        description="Welcome message displayed when chat UI first loads",
    )

    preset_prompts: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Preset prompt buttons displayed in the chat UI. Each entry should have 'label' (button text) "
            "and 'template' (prompt text). Use {{JOB_ID}} as a placeholder for a job ID. "
            "Optional 'description' field shown as a tooltip."
        ),
    )

    preset_prompts_file: Optional[str] = Field(
        default=None,
        alias="BEDROCK_PRESET_PROMPTS_FILE",
        description=(
            "Path to a YAML file containing preset prompt button definitions. "
            "The file must have a top-level 'prompts' list, each entry with 'label' and 'template' keys. "
            "Loaded at startup; takes effect only when preset_prompts is empty."
        ),
    )

    # Security Configuration
    auth_dependency: Optional[Callable] = Field(default=None, description="Authentication dependency function")

    rate_limit: Optional[str] = Field(
        default=None,
        alias="BEDROCK_RATE_LIMIT",
        description="Rate limit for chat endpoints (e.g., '10/minute')",
    )

    cors_origins: List[str] = Field(
        default_factory=lambda: ["*"],
        alias="BEDROCK_CORS_ORIGINS",
        description="CORS allowed origins",
    )

    # Tool Call Authentication Configuration
    enable_tool_auth: bool = Field(
        default=True,
        alias="BEDROCK_ENABLE_TOOL_AUTH",
        description="Enable authentication for tool calls",
    )

    supported_auth_types: List[str] = Field(
        default_factory=lambda: DEFAULT_SUPPORTED_AUTH_TYPES.copy(),
        alias="BEDROCK_SUPPORTED_AUTH_TYPES",
        description="List of supported authentication types for tool calls",
    )

    require_tool_auth: bool = Field(
        default=False,
        alias="BEDROCK_REQUIRE_TOOL_AUTH",
        description="Require authentication before any tool calls can be made",
    )

    auth_token_cache_ttl: int = Field(
        default=3600,
        alias="BEDROCK_AUTH_TOKEN_CACHE_TTL",
        gt=0,
        description="Cache TTL for OAuth2 tokens in seconds",
    )

    auth_verification_endpoint: Optional[str] = Field(
        default=None,
        alias="BEDROCK_AUTH_VERIFICATION_ENDPOINT",
        description=(
            "URL of an endpoint that verifies credentials at authentication time. "
            "When set, credentials are forwarded to this endpoint before being accepted. "
            "The endpoint must return a 2XX status code to confirm the credentials are valid. "
            "This prevents users from seeing an 'authenticated' status with invalid credentials."
        ),
    )

    # Logging Configuration
    log_level: str = Field(default="INFO", alias="BEDROCK_LOG_LEVEL", description="Logging level")

    log_api_calls: bool = Field(
        default=False,
        alias="BEDROCK_LOG_API_CALLS",
        description="Log API calls for debugging",
    )

    log_errors: bool = Field(default=True, alias="BEDROCK_LOG_ERRORS", description="Log errors")

    suppress_third_party_logs: bool = Field(
        default=True,
        alias="BEDROCK_SUPPRESS_THIRD_PARTY_LOGS",
        description="Suppress verbose logging from botocore, httpcore, urllib3",
    )

    # Error Handling Configuration
    max_retries: int = Field(
        default=DEFAULT_MAX_RETRIES,
        alias="BEDROCK_MAX_RETRIES",
        ge=0,
        description="Maximum retries for failed requests",
    )

    retry_delay: float = Field(
        default=DEFAULT_RETRY_DELAY,
        alias="BEDROCK_RETRY_DELAY",
        ge=0.0,
        description="Delay between retries in seconds",
    )

    exponential_backoff: bool = Field(
        default=DEFAULT_EXPONENTIAL_BACKOFF,
        alias="BEDROCK_EXPONENTIAL_BACKOFF",
        description="Use exponential backoff for retries",
    )

    fallback_model: Optional[str] = Field(
        default=None,
        alias="BEDROCK_FALLBACK_MODEL",
        description="Fallback model if primary model fails",
    )

    graceful_degradation: bool = Field(
        default=DEFAULT_GRACEFUL_DEGRADATION,
        alias="BEDROCK_GRACEFUL_DEGRADATION",
        description="Enable graceful degradation on errors",
    )

    # Knowledge Base / RAG Configuration (Hybrid Approach)
    enable_rag: bool = Field(
        default=False,
        alias="ENABLE_RAG",
        description=(
            "Enable Retrieval-Augmented Generation (RAG) with knowledge base. "
            "Default: False (backward compatible - existing apps work without changes). "
            "Set to True to enable RAG features."
        ),
    )

    kb_sources_config: str = Field(
        default="kb_sources.yaml",
        alias="KB_SOURCES_CONFIG",
        description="Path to knowledge base sources configuration file",
    )

    kb_database_path: str = Field(
        default="data/knowledge_base.db",
        alias="KB_DATABASE_PATH",
        description="Path to SQLite vector database file",
    )

    kb_populate_on_startup: bool = Field(
        default=False,
        alias="KB_POPULATE_ON_STARTUP",
        description=(
            "Auto-populate knowledge base on startup (development only). "
            "Production should use CLI: python -m auto_bedrock_chat_fastapi.commands.kb populate"
        ),
    )

    kb_allow_empty: bool = Field(
        default=False,
        alias="KB_ALLOW_EMPTY",
        description=(
            "Allow app to start with empty knowledge base when RAG is enabled. "
            "If False, app will fail if RAG is enabled but KB is empty/missing."
        ),
    )

    kb_embedding_model: str = Field(
        default="amazon.titan-embed-text-v1",
        alias="KB_EMBEDDING_MODEL",
        description="AWS Bedrock model ID for generating embeddings",
    )

    kb_chunk_size: int = Field(
        default=512,
        alias="KB_CHUNK_SIZE",
        gt=0,
        description="Token size for text chunks (default: 512 tokens)",
    )

    kb_chunk_overlap: int = Field(
        default=100,
        alias="KB_CHUNK_OVERLAP",
        ge=0,
        description="Token overlap between chunks (default: 100 tokens)",
    )

    kb_top_k_results: int = Field(
        default=5,
        alias="KB_TOP_K_RESULTS",
        gt=0,
        description="Number of top similar chunks to retrieve for RAG (default: 5)",
    )

    kb_similarity_threshold: float = Field(
        default=0.0,
        alias="KB_SIMILARITY_THRESHOLD",
        ge=0.0,
        le=1.0,
        description="Minimum similarity score for KB results (default: 0.0). Set higher (e.g. 0.3-0.5) to filter low-relevance results.",
    )

    kb_semantic_weight: float = Field(
        default=0.7,
        alias="KB_SEMANTIC_WEIGHT",
        ge=0.0,
        le=1.0,
        description="Weight for semantic (embedding) similarity in KB search (default: 0.7). Set to 0 to disable semantic matching.",
    )

    kb_keyword_weight: float = Field(
        default=0.3,
        alias="KB_KEYWORD_WEIGHT",
        ge=0.0,
        le=1.0,
        description="Weight for keyword (word-matching) score in KB search (default: 0.3). Set to 0 to disable keyword matching.",
    )

    model_config = SettingsConfigDict(
        env_file=_get_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_nested_delimiter=None,  # Disable nested parsing
        env_parse_enums=None,  # Disable enum parsing
    )

    @field_validator("allowed_paths", "excluded_paths", "cors_origins", mode="before")
    @classmethod
    def parse_list_from_string(cls, v):
        """Parse comma-separated string into list"""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, v):
        """Validate Bedrock model ID format"""
        if not v:
            raise ValueError("Model ID cannot be empty")

        # Common Bedrock model patterns
        valid_patterns = [
            "anthropic.claude",
            "us.anthropic.claude",  # Cross-region inference profiles
            "amazon.titan",
            "ai21.j2",
            "ai21.jamba",  # Add Jamba support
            "cohere.command",
            "meta.llama2",
            "meta.llama3",  # Add Llama 3.x support
            "us.meta.llama3",  # Cross-region Llama 3.x inference profiles
            "openai.gpt",  # Add OpenAI support
        ]

        if not any(pattern in v for pattern in valid_patterns):
            # Allow override for testing or custom models
            if not v.startswith(("test-", "custom-")):
                raise ValueError(f"Invalid model ID: {v}")

        return v

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v):
        """Validate temperature range"""
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"Temperature must be between 0.0 and 1.0, got {v}")
        return v

    @field_validator("rate_limit")
    @classmethod
    def validate_rate_limit(cls, v):
        """Validate rate limit format"""
        if v is None:
            return v

        # Simple validation for format like "10/minute", "100/hour"
        if "/" not in v:
            raise ValueError("Rate limit must be in format 'number/period' (e.g., '10/minute')")

        return v

    @field_validator("llm_client_type")
    @classmethod
    def validate_llm_client_type(cls, v):
        """Validate LLM client type"""
        if v not in VALID_LLM_CLIENT_TYPES:
            raise ValueError(f"llm_client_type must be one of: {', '.join(sorted(VALID_LLM_CLIENT_TYPES))}")
        return v

    @field_validator("single_msg_truncation_target")
    @classmethod
    def validate_single_msg_truncation_target(cls, v, info):
        """Validate single_msg_truncation_target < single_msg_length_threshold"""
        # Access threshold from info.data (already validated fields)
        threshold = info.data.get("single_msg_length_threshold")
        if threshold is not None and v >= threshold:
            raise ValueError(
                f"single_msg_truncation_target ({v:,}) must be less than "
                f"single_msg_length_threshold ({threshold:,})"
            )
        return v

    @field_validator("history_msg_truncation_target")
    @classmethod
    def validate_history_msg_truncation_target(cls, v, info):
        """Validate history_msg_truncation_target < history_msg_length_threshold"""
        threshold = info.data.get("history_msg_length_threshold")
        if threshold is not None and v >= threshold:
            raise ValueError(
                f"history_msg_truncation_target ({v:,}) must be less than "
                f"history_msg_length_threshold ({threshold:,})"
            )
        return v

    def get_system_prompt(self) -> str:
        """Get effective system prompt"""
        if self.system_prompt:
            return self.system_prompt

        tools_count = len(self.tools_desc.get("functions", [])) if self.tools_desc else 0

        if tools_count > 0:
            return f"""You are a helpful AI assistant with access to {tools_count} tools and functions.

Guidelines:
- Be helpful, accurate, and honest in all responses
- When users request operations that require tool usage, explain what you're doing
- Use available tools appropriately to help users accomplish their goals
- Provide clear, well-formatted responses
- Handle errors gracefully and suggest alternatives when possible
- Ask for clarification when requests are ambiguous"""
        else:
            return """You are a helpful AI assistant. I'm here to assist you with a wide variety of tasks including:

- Answering questions and providing information
- Helping with analysis and problem-solving
- Creative writing and brainstorming
- Explaining complex topics
- Providing recommendations and advice

Please feel free to ask me anything, and I'll do my best to help you!"""

    def get_aws_config(self) -> Dict[str, Any]:
        """Get AWS configuration for boto3"""
        config = {"region_name": self.aws_region}

        if self.aws_access_key_id and self.aws_secret_access_key:
            config.update(
                {
                    "aws_access_key_id": self.aws_access_key_id,
                    "aws_secret_access_key": self.aws_secret_access_key,
                }
            )

        return config

    def get_llm_params(self) -> Dict[str, Any]:
        """Get parameters for LLM API calls."""
        return {
            "model_id": self.model_id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
        }


def load_config(
    model_id: Optional[str] = None,
    temperature: Optional[float] = None,
    system_prompt: Optional[str] = None,
    **kwargs,
) -> ChatConfig:
    """Load configuration with optional overrides"""

    try:
        # Prepare overrides dictionary
        overrides = {}
        if model_id is not None:
            overrides["model_id"] = model_id
        if temperature is not None:
            overrides["temperature"] = temperature
        if system_prompt is not None:
            overrides["system_prompt"] = system_prompt

        # Add any additional kwargs
        overrides.update({k: v for k, v in kwargs.items() if v is not None})

        if overrides:
            # Manual validation for specific fields
            if "temperature" in overrides:
                temp_val = overrides["temperature"]
                if not 0.0 <= temp_val <= 1.0:
                    raise ConfigurationError(f"Temperature must be between 0.0 and 1.0, got {temp_val}")

            if "model_id" in overrides:
                model_val = overrides["model_id"]
                if not model_val:
                    raise ConfigurationError("Model ID cannot be empty")
                # Check for valid patterns
                valid_patterns = [
                    "anthropic.claude",
                    "us.anthropic.claude",
                    "amazon.titan",
                    "ai21.j2",
                    "ai21.jamba",
                    "cohere.command",
                    "meta.llama2",
                    "meta.llama3",
                    "us.meta.llama3",
                    "openai.gpt",
                ]
                if not any(pattern in model_val for pattern in valid_patterns):
                    if not model_val.startswith(("test-", "custom-")):
                        raise ConfigurationError(f"Invalid model ID: {model_val}")

            # Validate conversation management fields
            if "max_conversation_messages" in overrides:
                max_msg_val = overrides["max_conversation_messages"]
                if not isinstance(max_msg_val, int) or max_msg_val <= 0:
                    raise ConfigurationError("max_conversation_messages must be a positive integer")

            # Validate LLM client type
            if "llm_client_type" in overrides:
                if overrides["llm_client_type"] not in VALID_LLM_CLIENT_TYPES:
                    raise ConfigurationError(
                        f"llm_client_type must be one of: {', '.join(sorted(VALID_LLM_CLIENT_TYPES))}"
                    )

            # Validate truncation target < threshold relationships
            if "single_msg_truncation_target" in overrides and "single_msg_length_threshold" in overrides:
                if overrides["single_msg_truncation_target"] >= overrides["single_msg_length_threshold"]:
                    raise ConfigurationError(
                        "single_msg_truncation_target must be less than single_msg_length_threshold"
                    )

            if "history_msg_truncation_target" in overrides and "history_msg_length_threshold" in overrides:
                if overrides["history_msg_truncation_target"] >= overrides["history_msg_length_threshold"]:
                    raise ConfigurationError(
                        "history_msg_truncation_target must be less than history_msg_length_threshold"
                    )

            # Validate max_truncation_recursion
            if "max_truncation_recursion" in overrides:
                val = overrides["max_truncation_recursion"]
                if not isinstance(val, int) or val < 1 or val > 10:
                    raise ConfigurationError("max_truncation_recursion must be between 1 and 10")

            # Create base config from .env
            config = ChatConfig()

            # Apply overrides
            for key, value in overrides.items():
                setattr(config, key, value)
        else:
            # No overrides, use standard .env loading
            config = ChatConfig()

        return config

    except ConfigurationError:
        # Re-raise ConfigurationError as-is
        raise
    except Exception as e:
        raise ConfigurationError(f"Failed to load configuration: {str(e)}")


def load_preset_prompts_from_yaml(path: str) -> List[Dict[str, Any]]:
    """
    Load preset prompt button definitions from a YAML file.

    The file must have a top-level ``prompts`` key whose value is a list of
    mappings with at minimum ``label`` and ``template`` keys::

        prompts:
          - label: "Workload Analysis"
            description: "Full CPU characterization"
            template: |
              JOB_ID = {{JOB_ID}}
              ...

    Returns an empty list and logs a warning if the file is missing or invalid
    so that the chat UI still starts normally.
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        import yaml  # pyyaml — listed as an optional dependency
    except ImportError:  # pragma: no cover
        logger.warning(
            "pyyaml is not installed; cannot load preset prompts from '%s'. " "Install it with: pip install pyyaml",
            path,
        )
        return []

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        prompts = data.get("prompts", []) if isinstance(data, dict) else []
        logger.info("Loaded %d preset prompt(s) from %s", len(prompts), path)
        return prompts
    except FileNotFoundError:
        logger.debug("Preset prompts file not found: %s", path)
        return []
    except Exception as exc:
        logger.warning("Could not load preset prompts from '%s': %s", path, exc)
        return []


def validate_config(config: ChatConfig) -> None:
    """Validate configuration for common issues"""

    # Check AWS credentials if not using IAM roles
    if not config.aws_access_key_id and not config.aws_secret_access_key:
        # Check if AWS CLI is configured or IAM role is available
        import boto3

        try:
            session = boto3.Session()
            credentials = session.get_credentials()
            if not credentials:
                raise ConfigurationError(
                    "AWS credentials not found. Please configure AWS CLI, "
                    "set environment variables, or use IAM roles."
                )
        except Exception as e:
            raise ConfigurationError(f"AWS configuration error: {str(e)}")

    # Validate endpoint paths don't conflict
    endpoints = [config.chat_endpoint, config.websocket_endpoint, config.ui_endpoint]
    if len(set(endpoints)) != len(endpoints):
        raise ConfigurationError("Chat endpoints cannot have duplicate paths")

    # Warn about common misconfigurations
    if config.temperature > 0.9:
        print(f"Warning: High temperature ({config.temperature}) may cause unpredictable responses")

    if config.max_tool_calls > 20:
        print(f"Warning: High max_tool_calls ({config.max_tool_calls}) may cause long response times")

    if config.session_timeout < 300:  # 5 minutes
        print(f"Warning: Low session timeout ({config.session_timeout}s) may disconnect users frequently")
