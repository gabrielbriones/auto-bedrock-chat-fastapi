"""Configuration management for autolangchat"""

import logging
import os
from typing import Annotated, Any, Callable, Dict, List, Optional

from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from .auth_handler import DEFAULT_SUPPORTED_AUTH_TYPES
from .defaults import (
    DEFAULT_ENABLE_AI_SUMMARIZATION,
    DEFAULT_MAX_CONVERSATION_MESSAGES,
    DEFAULT_MAX_SESSIONS,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_MAX_TRUNCATION_RECURSION,
    DEFAULT_PRESERVE_SYSTEM_MESSAGE,
    DEFAULT_SESSION_TIMEOUT,
    DEFAULT_TIMEOUT,
    HISTORY_MSG_LENGTH_THRESHOLD_FRACTION,
    HISTORY_MSG_TRUNCATION_TARGET_FRACTION,
    HISTORY_TOTAL_LENGTH_THRESHOLD_FRACTION,
    SINGLE_MSG_LENGTH_THRESHOLD_FRACTION,
    SINGLE_MSG_TRUNCATION_TARGET_FRACTION,
)
from .exceptions import ConfigurationError

try:
    # Source of truth for supported Bedrock model IDs and their human-readable
    # "name" (used to label the model_id dropdown in the settings sidebar).
    # Only models present here are supported -- see ChatConfig's model_id/
    # fallback_model/available_models validators below.
    from langchain_aws.data._profiles import _PROFILES
except ImportError as exc:  # pragma: no cover
    # langchain-aws is a required dependency; without profiles we cannot
    # enforce model allowlisting or provide stable display names, and silently
    # degrading to "no validation" would contradict the whole point of this
    # restriction (unrecognized model IDs should stop the server, not be
    # silently accepted). Fail fast instead.
    raise ConfigurationError(
        "langchain-aws is required to validate Bedrock model profiles. " "Install with: pip install langchain-aws"
    ) from exc

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dynamic Parameter Overrides
# ---------------------------------------------------------------------------
# Parameters end users may override per-message or per-session via WebSocket
# metadata, gated by `enable_dynamic_overrides` and the `allowed_dynamic_overrides`
# allowlist (see ChatConfig.validate_overrides()). `max_tool_calls` and
# `preserve_system_message` are intentionally excluded for now
OVERRIDABLE_LLM_PARAMS = frozenset({"model_id", "temperature", "max_tokens", "top_p"})
OVERRIDABLE_FEATURE_TOGGLES = frozenset(
    {"enable_ai_summarization", "enable_rag", "kb_top_k_results", "kb_similarity_threshold"}
)
OVERRIDABLE_PARAMS = OVERRIDABLE_LLM_PARAMS | OVERRIDABLE_FEATURE_TOGGLES
# ---------------------------------------------------------------------------
# Model catalog (settings sidebar model_id dropdown)
# ---------------------------------------------------------------------------
# Bedrock cross-region inference-profile prefixes. A model id may be either a
# bare foundation-model id ("anthropic.claude-opus-4-8") or one of these
# prefixed inference-profile ids ("us.anthropic.claude-opus-4-8"). The prefix
# is stripped when deriving the provider so both forms group together; the
# profile's display name already disambiguates the region ("Claude Opus 4.8
# (US)" vs "Claude Opus 4.8").
MODEL_ID_REGION_PREFIXES = frozenset({"us", "eu", "au", "jp", "global"})

# Human-readable labels for the provider segment of a Bedrock model id, used as
# the first level of the grouped model dropdown. Unknown providers fall back to
# a title-cased version of the raw segment, so a newly-added vendor still shows
# up (just without a curated label).
PROVIDER_DISPLAY_NAMES = {
    "ai21": "AI21 Labs",
    "amazon": "Amazon",
    "anthropic": "Anthropic",
    "cohere": "Cohere",
    "deepseek": "DeepSeek",
    "google": "Google",
    "meta": "Meta",
    "minimax": "MiniMax",
    "mistral": "Mistral AI",
    "moonshot": "Moonshot AI",
    "moonshotai": "Moonshot AI",
    "nvidia": "NVIDIA",
    "openai": "OpenAI",
    "qwen": "Qwen",
    "stability": "Stability AI",
    "writer": "Writer",
    "xai": "xAI",
    "zai": "Z.ai",
}


def split_model_id(model_id: str) -> "tuple[Optional[str], str]":
    """Split a Bedrock model id into ``(region_prefix, provider_key)``.

    ``region_prefix`` is ``None`` for bare foundation-model ids::

        "us.anthropic.claude-opus-4-8" -> ("us", "anthropic")
        "anthropic.claude-opus-4-8"    -> (None, "anthropic")
        "openai.gpt-5.5"               -> (None, "openai")
    """
    parts = model_id.split(".")
    if len(parts) > 2 and parts[0] in MODEL_ID_REGION_PREFIXES:
        return parts[0], parts[1]
    return None, parts[0]


def get_model_provider(model_id: str) -> str:
    """Human-readable provider label for ``model_id`` (e.g. ``"Anthropic"``)."""
    _region, provider_key = split_model_id(model_id)
    return PROVIDER_DISPLAY_NAMES.get(provider_key, provider_key.replace("-", " ").title())


def _build_default_available_models() -> List[str]:
    """Full langchain-aws model catalog, for the sidebar's model_id dropdown.

    Derived from ``_PROFILES`` rather than hand-maintained so the dropdown
    tracks whatever the installed ``langchain-aws`` release supports. Models
    without ``tool_calling`` are excluded: the chat graph always binds the
    generated API tools to the LLM, so a model that can't call tools would
    fail (or silently ignore every tool) once selected.

    Some of these ids (e.g. bare foundation-model ids for newer models like
    Llama 3.3 70B Instruct) are only invokable through a cross-region
    inference profile id, not the bare id -- rather than hand-maintaining a
    denylist of which ones (Bedrock's supported set changes over time, and
    _PROFILES doesn't expose this as a flag), ``_build_llm()`` in
    graph/nodes/llm_call.py detects that failure at call time from Bedrock's
    own error message and retries once with an inference-profile-prefixed id.

    Hosts that want a narrower list still set ``AUTOCHAT_AVAILABLE_MODELS``.
    """
    return sorted(model_id for model_id, profile in _PROFILES.items() if profile.get("tool_calling"))


# Built-in fallback for the settings sidebar's model_id dropdown, used when
# `AUTOCHAT_AVAILABLE_MODELS` isn't set (see `ChatConfig.get_available_models()`).
DEFAULT_AVAILABLE_MODELS = _build_default_available_models()


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
    """Configuration for AutoLangChat"""

    # Model Configuration
    model_id: str = Field(
        default="us.anthropic.claude-sonnet-5",
        alias="AUTOCHAT_MODEL_ID",
        description="Model identifier",
    )

    temperature: float = Field(
        default=0.7,
        alias="AUTOCHAT_TEMPERATURE",
        ge=0.0,
        le=1.0,
        description="Sampling temperature for model responses",
    )

    max_tokens: int = Field(
        default=8192,
        alias="AUTOCHAT_MAX_TOKENS",
        gt=0,
        description=(
            "Maximum tokens in model response. Raised from 4096 to 8192: long "
            "multi-round tool-calling turns combined with verbose structured-"
            "output prompts could exhaust the lower budget before any visible "
            "text was emitted, producing an empty (0 chars, 0 tool_calls) final "
            "answer with response_metadata stopReason='max_tokens'."
        ),
    )

    top_p: float = Field(
        default=0.9,
        alias="AUTOCHAT_TOP_P",
        ge=0.0,
        le=1.0,
        description="Top-p sampling parameter",
    )

    # Dynamic Parameter Overrides Configuration
    enable_dynamic_overrides: bool = Field(
        default=False,
        alias="AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES",
        description=(
            "Master switch allowing end users to override LLM params and feature toggles "
            "per message or per session via WebSocket metadata. Default: False."
        ),
    )

    allowed_dynamic_overrides: Optional[List[str]] = Field(
        default=None,
        alias="AUTOCHAT_ALLOWED_DYNAMIC_OVERRIDES",
        description=(
            "Allowlist of parameter names end users may override. When None and "
            "enable_dynamic_overrides is True, all overridable params are allowed. "
            "See OVERRIDABLE_PARAMS in config.py for the full set."
        ),
    )

    enable_config_sidebar: bool = Field(
        default=False,
        alias="AUTOCHAT_ENABLE_CONFIG_SIDEBAR",
        description="Whether to show the dynamic parameter overrides settings sidebar in the chat UI.",
    )

    available_models: Optional[List[str]] = Field(
        default=None,
        alias="AUTOCHAT_AVAILABLE_MODELS",
        description=(
            "Comma-separated list of model IDs offered in the settings sidebar's "
            "model_id dropdown (dynamic parameter overrides). When unset, falls back "
            "to the full tool-calling-capable langchain-aws catalog -- see "
            "DEFAULT_AVAILABLE_MODELS in config.py and ChatConfig.get_available_models()."
        ),
    )

    providers: Optional[List[str]] = Field(
        default=None,
        alias="AUTOCHAT_PROVIDERS",
        description=(
            "Comma-separated allowlist of model providers (e.g. "
            "AUTOCHAT_PROVIDERS=Anthropic,Meta,OpenAI) to offer in the settings sidebar's "
            "model_id dropdown. When set, ChatConfig.get_available_models() is filtered to "
            "only include models whose provider (see get_model_provider()) matches one of "
            "these names -- matched case-insensitively against the provider labels derived "
            "from langchain_aws.data._profiles._PROFILES. Combines with "
            "AUTOCHAT_AVAILABLE_MODELS: the provider filter is applied on top of whichever "
            "model list is in effect (the available_models override, or the full "
            "DEFAULT_AVAILABLE_MODELS catalog). When unset, models from all providers are "
            "offered."
        ),
    )

    model_discovery_enabled: bool = Field(
        default=True,
        alias="AUTOCHAT_MODEL_DISCOVERY_ENABLED",
        description=(
            "Filter the settings sidebar's model_id dropdown down to models this AWS "
            "account can actually invoke in this region, by querying the Bedrock control "
            "plane (ListFoundationModels + ListInferenceProfiles) once at startup. The "
            "static langchain-aws profile catalog lists models that may not exist in a "
            "given account/region -- selecting one fails with 'The provided model "
            "identifier is invalid'. Requires bedrock:ListFoundationModels (and, for "
            "region-prefixed inference-profile ids, bedrock:ListInferenceProfiles). "
            "Degrades safely: if the calls fail, are denied or time out, the unfiltered "
            "static catalog is used. Set to false to skip the startup calls entirely."
        ),
    )

    # Populated at startup by AutoLangChatPlugin via set_invocable_model_ids()
    # when model_discovery_enabled is on. ``None`` means "discovery didn't run
    # or failed" and is deliberately distinct from an empty set.
    _invocable_model_ids: Optional[set] = PrivateAttr(default=None)

    # System Configuration
    system_prompt: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SYSTEM_PROMPT",
        description="Custom system prompt for the AI assistant",
    )

    # API Tools Configuration
    tools_desc: Optional[Dict] = Field(default_factory=dict, description="Auto-generated tools from FastAPI routes")
    langchain_tools: Optional[List] = Field(
        default=None, description="Pre-built LangChain StructuredTool list for LLM binding"
    )

    openapi_spec_file: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_OPENAPI_SPEC_FILE",
        description="Path to OpenAPI spec file for framework-agnostic tool generation",
    )

    api_base_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_API_BASE_URL",
        description="Base URL for API calls (e.g., http://localhost:8080). Auto-detected if not specified",
    )

    allowed_paths: List[str] = Field(
        default_factory=list,
        alias="AUTOCHAT_ALLOWED_PATHS",
        description="Whitelist of API paths to expose as tools",
    )

    excluded_paths: List[str] = Field(
        default_factory=lambda: [
            "/chat",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/health",
        ],
        alias="AUTOCHAT_EXCLUDED_PATHS",
        description="Blacklist of API paths to exclude from tools",
    )

    # Session Configuration
    max_tool_calls: Optional[int] = Field(
        default=DEFAULT_MAX_TOOL_CALLS,
        alias="AUTOCHAT_MAX_TOOL_CALLS",
        gt=0,
        description="Maximum tool calls per conversation turn (None = unlimited)",
    )

    # Conversation History Management
    max_conversation_messages: int = Field(
        default=DEFAULT_MAX_CONVERSATION_MESSAGES,
        alias="AUTOCHAT_MAX_CONVERSATION_MESSAGES",
        gt=0,
        description="Maximum messages to keep in conversation history",
    )

    preserve_system_message: bool = Field(
        default=DEFAULT_PRESERVE_SYSTEM_MESSAGE,
        alias="AUTOCHAT_PRESERVE_SYSTEM_MESSAGE",
        description="Whether to always preserve the system message when trimming history",
    )

    # AI Summarization Configuration
    enable_ai_summarization: bool = Field(
        default=DEFAULT_ENABLE_AI_SUMMARIZATION,
        alias="AUTOCHAT_ENABLE_AI_SUMMARIZATION",
        description=(
            "Enable AI-based summarization for oversized messages and conversation history. "
            "When enabled, uses LLM calls to intelligently condense content instead of plain text truncation. "
            "Default: False (uses plain text truncation). Enabling this will increase LLM token usage."
        ),
    )

    summarization_model_id: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SUMMARIZATION_MODEL_ID",
        description=(
            "Bedrock model id used for AI summarization (see enable_ai_summarization). "
            "When unset, falls back to the main chat model_id."
        ),
    )

    summarization_temperature: Optional[float] = Field(
        default=None,
        alias="AUTOCHAT_SUMMARIZATION_TEMPERATURE",
        ge=0.0,
        le=1.0,
        description=(
            "Sampling temperature for AI summarization LLM calls. When unset, "
            "falls back to DEFAULT_SUMMARIZATION_TEMPERATURE (see defaults.py)."
        ),
    )

    summarization_max_tokens: Optional[int] = Field(
        default=None,
        alias="AUTOCHAT_SUMMARIZATION_MAX_TOKENS",
        gt=0,
        description=(
            "Maximum tokens for AI summarization LLM responses. When unset, falls back to the main chat max_tokens."
        ),
    )

    summarization_top_p: Optional[float] = Field(
        default=None,
        alias="AUTOCHAT_SUMMARIZATION_TOP_P",
        ge=0.0,
        le=1.0,
        description=(
            "Top-p sampling parameter for AI summarization LLM calls. Only applied "
            "when no summarization temperature is in effect (Bedrock Converse rejects "
            "both being set simultaneously)."
        ),
    )

    max_truncation_recursion: int = Field(
        default=DEFAULT_MAX_TRUNCATION_RECURSION,
        alias="AUTOCHAT_MAX_TRUNCATION_RECURSION",
        ge=1,
        le=10,
        description=(
            "Maximum recursion depth for history truncation safety-net halving. "
            "If history still exceeds threshold after all 3 truncation steps, the process "
            "re-runs with halved thresholds, up to this many times. Default: 3."
        ),
    )

    timeout: int = Field(
        default=DEFAULT_TIMEOUT,
        alias="AUTOCHAT_TIMEOUT",
        gt=0,
        description="Timeout for API calls in seconds",
    )

    # WebSocket Configuration
    max_sessions: int = Field(
        default=DEFAULT_MAX_SESSIONS,
        alias="AUTOCHAT_MAX_SESSIONS",
        gt=0,
        description="Maximum concurrent WebSocket sessions",
    )

    session_timeout: int = Field(
        default=DEFAULT_SESSION_TIMEOUT,
        alias="AUTOCHAT_SESSION_TIMEOUT",
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
        default="/chat",
        alias="AUTOCHAT_CHAT_ENDPOINT",
        description="Base endpoint for chat API",
    )

    websocket_endpoint: str = Field(
        default="/chat/ws",
        alias="AUTOCHAT_WEBSOCKET_ENDPOINT",
        description="WebSocket endpoint",
    )

    ui_endpoint: str = Field(
        default="/chat/ui",
        alias="AUTOCHAT_UI_ENDPOINT",
        description="Web UI endpoint",
    )

    sso_allowed_return_prefixes: Annotated[List[str], NoDecode] = Field(
        default_factory=list,
        alias="AUTOCHAT_SSO_ALLOWED_RETURN_PREFIXES",
        description="Same-site path prefixes allowed as post-SSO redirect targets",
        validate_default=True,
    )

    enable_ui: bool = Field(default=True, alias="AUTOCHAT_ENABLE_UI", description="Enable built-in chat UI")

    ui_title: str = Field(
        default="AI Assistant",
        alias="AUTOCHAT_UI_TITLE",
        description="Title displayed in the chat UI header",
    )

    ui_welcome_message: str = Field(
        default=(
            "Welcome! I'm your AI assistant. I can help you interact with the API endpoints. "
            "Try asking me to retrieve data, create resources, or explain what operations are available."
        ),
        alias="AUTOCHAT_UI_WELCOME_MESSAGE",
        description="Welcome message displayed when chat UI first loads",
    )

    ui_lock_input_while_responding: bool = Field(
        default=True,
        alias="AUTOCHAT_UI_LOCK_INPUT_WHILE_RESPONDING",
        description=(
            "When enabled, the chat input and Send button are disabled from the moment a user "
            "sends a message until the assistant's response is fully received. "
            "Set to false to allow sending additional messages while a response is in flight."
        ),
    )

    preset_prompts: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Preset prompt buttons displayed in the chat UI. Each entry should have 'label' (button text) "
            "and 'template' (prompt text). Use {{JOB_ID}} as a placeholder for a job ID. "
            "Optional 'description' field shown as a tooltip. Optional 'id' field is a stable identifier "
            "used by deep-link query strings (?prompt=<id>); auto-generated by slugifying 'label' when omitted."
        ),
    )

    preset_prompts_file: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_PRESET_PROMPTS_FILE",
        description=(
            "Path to a YAML file containing preset prompt button definitions. "
            "The file must have a top-level 'prompts' list, each entry with 'label' and 'template' keys. "
            "Loaded at startup; takes effect only when preset_prompts is empty."
        ),
    )

    preset_variables: List[Dict] = Field(
        default_factory=list,
        description=(
            "Variable definitions for preset prompt placeholders. Each entry should have 'name' "
            "(SCREAMING_SNAKE_CASE matching {{NAME}} in templates) and optional 'label', "
            "'input_type', 'validate', 'detect_pattern', 'placeholder', and 'default' fields. "
            "When not provided, variables are automatically inferred from {{PLACEHOLDER}} patterns "
            "found in preset prompt templates."
        ),
    )

    # Security Configuration
    auth_dependency: Optional[Callable] = Field(default=None, description="Authentication dependency function")

    # Tool Call Authentication Configuration
    enable_tool_auth: bool = Field(
        default=True,
        alias="AUTOCHAT_ENABLE_TOOL_AUTH",
        description="Enable authentication for tool calls",
    )

    supported_auth_types: List[str] = Field(
        default_factory=lambda: DEFAULT_SUPPORTED_AUTH_TYPES.copy(),
        alias="AUTOCHAT_SUPPORTED_AUTH_TYPES",
        description="List of supported authentication types for tool calls",
    )

    default_auth_type: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_DEFAULT_AUTH_TYPE",
        description="Default auth type to pre-select in the UI modal. Must be one of supported_auth_types.",
    )

    require_tool_auth: bool = Field(
        default=False,
        alias="AUTOCHAT_REQUIRE_TOOL_AUTH",
        description="Require authentication before any tool calls can be made",
    )

    auth_verification_endpoint: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_AUTH_VERIFICATION_ENDPOINT",
        description=(
            "URL of an endpoint that verifies credentials at authentication time. "
            "When set, credentials are forwarded to this endpoint before being accepted. "
            "The endpoint must return a 2XX status code to confirm the credentials are valid. "
            "This prevents users from seeing an 'authenticated' status with invalid credentials."
        ),
    )

    include_auth_info_in_prompts: bool = Field(
        default=False,
        alias="AUTOCHAT_INCLUDE_AUTH_INFO_IN_PROMPTS",
        description=(
            "Include authenticated user information in the system prompt. "
            "When enabled and a verification endpoint is configured, user metadata returned by "
            "the verification endpoint (stored in session.metadata['verified_user_info']) is "
            "appended to the system prompt. This allows the LLM to answer queries like 'who am I?' "
            "with context about the authenticated user. Only works when auth_verification_endpoint "
            "is configured and returns user information."
        ),
    )

    # SSO Configuration
    sso_enabled: bool = Field(
        default=False,
        alias="AUTOCHAT_SSO_ENABLED",
        description="Master switch for SSO authentication via OAuth2 Authorization Code flow with PKCE",
    )

    sso_provider: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SSO_PROVIDER",
        description=(
            "SSO provider hint for preset defaults. "
            "Supported values: 'okta', 'azure_ad', 'auth0', 'keycloak', 'cognito', 'generic'"
        ),
    )

    sso_client_id: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SSO_CLIENT_ID",
        description="OAuth2 application client ID registered with the Identity Provider",
    )

    sso_client_secret: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SSO_CLIENT_SECRET",
        description="OAuth2 client secret for confidential client flow",
    )

    sso_discovery_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SSO_DISCOVERY_URL",
        description=(
            "OIDC discovery endpoint (e.g., https://idp.example.com/.well-known/openid-configuration). "
            "When set, auto-configures authorization, token, userinfo, and JWKS endpoints."
        ),
    )

    sso_authorization_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SSO_AUTHORIZATION_URL",
        description="Manual override for the OAuth2 authorization endpoint (used if discovery URL is not set)",
    )

    sso_token_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SSO_TOKEN_URL",
        description="Manual override for the OAuth2 token endpoint (used if discovery URL is not set)",
    )

    sso_userinfo_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SSO_USERINFO_URL",
        description="Manual override for the OIDC userinfo endpoint",
    )

    sso_jwks_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SSO_JWKS_URL",
        description="JWKS endpoint URL for ID token signature validation",
    )

    sso_scopes: str = Field(
        default="openid profile email",
        alias="AUTOCHAT_SSO_SCOPES",
        description="Space-separated OAuth2 scopes to request from the Identity Provider",
    )

    sso_callback_path: str = Field(
        default="/chat/auth/callback",
        alias="AUTOCHAT_SSO_CALLBACK_PATH",
        description="Redirect URI path on this server for the IdP callback",
    )

    sso_public_base_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SSO_PUBLIC_BASE_URL",
        description=(
            "Public-facing base URL sent to the IdP as the redirect_uri base "
            "(e.g., https://myapp.example.com). Must match a registered callback URL in your IdP. "
            "Defaults to api_base_url when not set. "
            "Use this when the tool-call base URL (api_base_url) differs from the "
            "browser-visible URL — e.g., same-process plugins where tool calls use "
            "localhost but users access the app via a hostname or IP."
        ),
    )

    sso_session_secret: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_SSO_SESSION_SECRET",
        description="Secret key for signing session cookies/tokens issued after SSO login",
    )

    sso_session_ttl: int = Field(
        default=3600,
        alias="AUTOCHAT_SSO_SESSION_TTL",
        gt=0,
        description="SSO session duration in seconds before requiring re-authentication",
    )

    sso_trust_external_idp_cookies: bool = Field(
        default=False,
        alias="AUTOCHAT_SSO_TRUST_EXTERNAL_IDP_COOKIES",
        description=(
            "Opt-in: silently establish an SSO session from an existing Cognito "
            "IdP session cookie set by ANOTHER app that shares this app's exact "
            "SSO App Client ID (e.g. a sibling internal tool on a related "
            "subdomain that deliberately configured its Cognito SDK to store "
            "tokens in cookies scoped to a shared parent domain). When enabled, "
            "the chat UI page load looks for "
            "'CognitoIdentityServiceProvider.<sso_client_id>.LastAuthUser' and "
            "the matching '...idToken'/'...accessToken'/'...refreshToken' "
            "cookies; if present, the ID token is validated exactly like a "
            "normal SSO callback (JWKS signature, issuer, audience, expiry) "
            "before an session is created, skipping the IdP redirect round "
            "trip entirely. Falls through silently to the normal SSO login "
            "flow if the cookies are absent or the token fails validation — "
            "this is a UX shortcut, never a fallback authentication path. "
            "Leave disabled unless you have explicitly confirmed (a) the "
            "calling app's Cognito SDK is configured to write these as "
            "cookies (not the SDK's localStorage default) with a Domain "
            "attribute covering this app's origin, and (b) it uses the SAME "
            "App Client ID as sso_client_id — a different App Client ID "
            "would fail audience validation and safely do nothing, but "
            "should not be relied upon as the enforcement point."
        ),
    )

    # Logging Configuration
    log_level: str = Field(default="INFO", alias="AUTOCHAT_LOG_LEVEL", description="Logging level")

    suppress_third_party_logs: bool = Field(
        default=True,
        alias="AUTOCHAT_SUPPRESS_THIRD_PARTY_LOGS",
        description="Suppress verbose logging from botocore, httpcore, urllib3",
    )

    fallback_model: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_FALLBACK_MODEL",
        description="Fallback model if primary model fails",
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

    kb_storage_type: str = Field(
        default="sqlite",
        alias="AUTOCHAT_KB_STORAGE_TYPE",
        description=("Knowledge-base storage backend. " "Valid values: 'sqlite' (default), 'pgvector'."),
    )

    kb_postgres_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_KB_POSTGRES_URL",
        description=(
            "PostgreSQL connection URL for pgvector backend "
            "(e.g., postgresql://user:pass@host:5432/dbname). "
            "Required when kb_storage_type='pgvector'."
        ),
    )

    kb_postgres_pool_size: int = Field(
        default=5,
        alias="AUTOCHAT_KB_POSTGRES_POOL_SIZE",
        gt=0,
        le=100,
        description="Connection pool size for PostgreSQL backend (default: 5).",
    )

    # ------------------------------------------------------------------
    # Feedback Storage Backend
    # ------------------------------------------------------------------

    feedback_enabled: bool = Field(
        default=False,
        alias="AUTOCHAT_FEEDBACK_ENABLED",
        description=(
            "Master switch for the feedback collection backend. When True, "
            "the plugin calls ``db.create_feedback_store(config)`` to build "
            "a ``BaseFeedbackStore`` implementation (SQLite or Postgres, "
            "selected by ``feedback_storage_type``) and wires it into the "
            "WebSocket handler so clients can submit ``feedback`` messages. "
            "If the factory cannot construct a usable backend at runtime "
            "(missing connection URL, missing optional dependency, etc.), "
            "the feature is silently disabled in-place and submissions are "
            "rejected with ``feedback_unavailable`` rather than crashing the "
            "app."
        ),
    )

    feedback_allow_anonymous: bool = Field(
        default=False,
        alias="AUTOCHAT_FEEDBACK_ALLOW_ANONYMOUS",
        description=(
            "When True, the feedback UI is rendered and submissions are "
            "accepted even when no SSO/tool-auth user identity is available. "
            "Intended for local development and standalone deployments where "
            "authentication is not configured."
        ),
    )

    feedback_authorized_users: List[str] = Field(
        default_factory=list,
        alias="AUTOCHAT_FEEDBACK_AUTHORIZED_USERS",
        description=(
            "Comma-separated list of user identifiers (email addresses or SSO "
            "sub claims) allowed to submit feedback. When non-empty, only listed "
            "users can submit feedback; the WebSocket handler rejects others with "
            "an explanatory error. Email-like identifiers are normalized to "
            "lowercase for comparison, but opaque identifiers such as SSO/OIDC "
            "sub claims are matched case-sensitively and must use exact casing. "
            "When empty or unset, any authenticated user may submit feedback "
            "(subject to feedback_enabled and feedback_allow_anonymous settings)."
        ),
    )

    feedback_storage_type: str = Field(
        default="sqlite",
        alias="AUTOCHAT_FEEDBACK_STORAGE_TYPE",
        description=(
            "Feedback storage backend. Valid values: 'sqlite' (default, "
            "zero-config) or 'postgres' (requires AUTOCHAT_FEEDBACK_POSTGRES_URL "
            "or AUTOCHAT_KB_POSTGRES_URL)."
        ),
    )

    feedback_database_path: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_FEEDBACK_DATABASE_PATH",
        description=(
            "Filesystem path to the SQLite feedback database when "
            "feedback_storage_type='sqlite'. When unset, falls back to "
            "kb_database_path so a single SQLite file can host both KB and "
            "feedback tables."
        ),
    )

    feedback_postgres_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_FEEDBACK_POSTGRES_URL",
        description=(
            "PostgreSQL connection URL for the feedback table when "
            "feedback_storage_type='postgres'. If unset, falls back to "
            "AUTOCHAT_KB_POSTGRES_URL so a single Postgres instance can host "
            "both the KB and feedback schemas."
        ),
    )

    feedback_postgres_pool_size: int = Field(
        default=5,
        alias="AUTOCHAT_FEEDBACK_POSTGRES_POOL_SIZE",
        gt=0,
        le=100,
        description="Async connection pool size for the feedback Postgres backend.",
    )

    feedback_init_schema: bool = Field(
        default=True,
        alias="AUTOCHAT_FEEDBACK_INIT_SCHEMA",
        description=(
            "Apply the feedback DDL on startup. Set False if a separate "
            "database-provisioning task owns the schema lifecycle."
        ),
    )

    feedback_max_history_context: int = Field(
        default=5,
        ge=0,
        alias="AUTOCHAT_FEEDBACK_MAX_HISTORY_CONTEXT",
        description=(
            "Number of preceding user/assistant messages to capture alongside "
            "the rated response when feedback is submitted. Gives reviewers "
            "conversational context. Set to 0 to disable history capture."
        ),
    )

    # Feedback Configuration
    feedback_metadata_enrichment_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_FEEDBACK_METADATA_ENRICHMENT_URL",
        description=(
            "Optional URL of an HTTP endpoint called on every feedback submission. "
            "Returns a dict stored verbatim in FeedbackEntry.entry_metadata. "
            "When unset, entry_metadata is stored as an empty dict and no HTTP call is made."
        ),
    )

    feedback_metadata_enrichment_timeout: float = Field(
        default=2.0,
        alias="AUTOCHAT_FEEDBACK_METADATA_ENRICHMENT_TIMEOUT",
        gt=0,
        description="Timeout in seconds for the metadata enrichment HTTP call.",
    )

    feedback_metadata_enrichment_fail_on_error: bool = Field(
        default=False,
        alias="AUTOCHAT_FEEDBACK_METADATA_ENRICHMENT_FAIL_ON_ERROR",
        description=(
            "When True, enrichment failures cause the feedback submission to be rejected. "
            "When False (default), failures are logged and the submission proceeds with entry_metadata={}."
        ),
    )

    @field_validator("feedback_metadata_enrichment_url")
    @classmethod
    def _validate_enrichment_url_scheme(cls, v: Optional[str]) -> Optional[str]:
        # Basic SSRF *mitigation*: the URL is operator-supplied, but reject non-HTTP(S)
        # schemes (file://, gopher://, etc.) at config load time. Note this does not
        # prevent http/https URLs from targeting internal hosts.
        if v is None:
            return None
        from urllib.parse import urlparse

        scheme = urlparse(v).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError("feedback_metadata_enrichment_url must use http or https scheme")
        return v

    # ------------------------------------------------------------------
    # Token Usage Storage Backend
    # ------------------------------------------------------------------

    token_usage_enabled: bool = Field(
        default=False,
        alias="AUTOCHAT_TOKEN_USAGE_ENABLED",
        description=(
            "Master switch for per-turn token-usage recording. When True, "
            "the plugin calls ``db.create_token_usage_store(config)`` to build "
            "a ``BaseTokenUsageStore`` implementation (SQLite or Postgres, "
            "selected by ``token_usage_storage_type``) and records "
            "(input_tokens, output_tokens) for every chat turn. If the factory "
            "cannot construct a usable backend at runtime (missing connection "
            "URL, missing optional dependency, etc.), the feature is silently "
            "disabled in-place rather than crashing the app."
        ),
    )

    token_usage_storage_type: str = Field(
        default="sqlite",
        alias="AUTOCHAT_TOKEN_USAGE_STORAGE_TYPE",
        description=(
            "Token-usage storage backend. Valid values: 'sqlite' (default, "
            "zero-config) or 'postgres' (requires AUTOCHAT_TOKEN_USAGE_POSTGRES_URL, "
            "AUTOCHAT_FEEDBACK_POSTGRES_URL, or AUTOCHAT_KB_POSTGRES_URL)."
        ),
    )

    token_usage_database_path: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_TOKEN_USAGE_DATABASE_PATH",
        description=(
            "Filesystem path to the SQLite token-usage database when "
            "token_usage_storage_type='sqlite'. When unset, falls back to "
            "feedback_database_path, then kb_database_path, so a single SQLite "
            "file can host KB, feedback, and token-usage tables."
        ),
    )

    token_usage_postgres_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_TOKEN_USAGE_POSTGRES_URL",
        description=(
            "PostgreSQL connection URL for the token_usage table when "
            "token_usage_storage_type='postgres'. If unset, falls back to "
            "AUTOCHAT_FEEDBACK_POSTGRES_URL, then AUTOCHAT_KB_POSTGRES_URL, so "
            "a single Postgres instance can host all three schemas."
        ),
    )

    # ------------------------------------------------------------------
    # Conversation Storage Backend
    # ------------------------------------------------------------------

    conversation_persistence_enabled: bool = Field(
        default=False,
        alias="AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED",
        description=(
            "Master switch for per-user, named conversation persistence. "
            "When True, the plugin calls ``db.create_conversation_store(config)`` "
            "to build a ``BaseConversationStore`` implementation (SQLite or "
            "Postgres, selected by ``conversation_storage_type``) that indexes "
            "LangGraph conversation threads by user (id, title, timestamps). "
            "LangGraph checkpoint data remains the source of truth for message "
            "history; this store only tracks metadata for the conversation "
            "list/sidebar. If the factory cannot construct a usable backend at "
            "runtime (missing connection URL, missing optional dependency, "
            "etc.), the feature is silently disabled rather than crashing the "
            "app."
        ),
    )

    conversation_storage_type: str = Field(
        default="sqlite",
        alias="AUTOCHAT_CONVERSATION_STORAGE_TYPE",
        description=(
            "Conversation metadata storage backend. Valid values: 'sqlite' "
            "(default, zero-config) or 'postgres' (requires "
            "AUTOCHAT_CONVERSATION_POSTGRES_URL, AUTOCHAT_FEEDBACK_POSTGRES_URL, "
            "or AUTOCHAT_KB_POSTGRES_URL)."
        ),
    )

    conversation_db_path: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_CONVERSATION_DB_PATH",
        description=(
            "Filesystem path to the SQLite conversations database when "
            "conversation_storage_type='sqlite'. When unset, falls back to "
            "feedback_database_path, then kb_database_path, so a single "
            "SQLite file can host KB, feedback, and conversation tables."
        ),
    )

    conversation_postgres_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_CONVERSATION_POSTGRES_URL",
        description=(
            "PostgreSQL connection URL for the conversations table when "
            "conversation_storage_type='postgres'. If unset, falls back to "
            "AUTOCHAT_FEEDBACK_POSTGRES_URL, then AUTOCHAT_KB_POSTGRES_URL, so "
            "a single Postgres instance can host all schemas."
        ),
    )

    max_conversations_per_user: int = Field(
        default=100,
        alias="AUTOCHAT_MAX_CONVERSATIONS_PER_USER",
        ge=0,
        description=(
            "Maximum number of conversations retained per user. Set to 0 to "
            "disable pruning. Enforcement (e.g. pruning the oldest conversation "
            "on overflow) is implemented by ConversationStore.create_conversation."
        ),
    )

    conversation_title_model_id: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_CONVERSATION_TITLE_MODEL_ID",
        description=(
            "Bedrock model id used to auto-generate a short conversation title "
            "from the first turn. When unset, falls back to the main chat "
            "``model_id``."
        ),
    )

    # ------------------------------------------------------------------
    # User Settings Storage Backend
    # ------------------------------------------------------------------

    user_settings_persistence_enabled: bool = Field(
        default=True,
        alias="AUTOCHAT_USER_SETTINGS_PERSISTENCE_ENABLED",
        description=(
            "Master switch for persisting each user's Settings-sidebar "
            "configuration (model_id, temperature, max_tokens, ...). When "
            "True, the plugin calls ``db.create_user_settings_store(config)`` "
            "to build a ``BaseUserSettingsStore`` implementation, hydrates "
            "``session.metadata['config_overrides']`` from it on authenticated "
            "connect, and writes back on every ``config_update`` / "
            "``config_reset``. Requires ``enable_dynamic_overrides``. "
            "This is the only user-settings setting: the backend, connection "
            "URL and file path are all inferred from whichever database the "
            "app already uses (see ``db.create_user_settings_store``). "
            "Anonymous (unauthenticated) sessions are never persisted. If no "
            "usable backend can be built at runtime (missing connection URL, "
            "missing optional dependency, etc.), the feature is silently "
            "disabled rather than crashing the app."
        ),
    )

    # ------------------------------------------------------------------
    # LangGraph Checkpoint
    # ------------------------------------------------------------------

    checkpoint_postgres_url: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_CHECKPOINT_POSTGRES_URL",
        description=(
            "PostgreSQL connection URL for LangGraph conversation checkpoints. "
            "When set, conversation history survives process restarts and "
            "clients can reconnect to the same session. "
            "Falls back to AUTOCHAT_KB_POSTGRES_URL when unset and "
            "kb_storage_type='pgvector'."
        ),
    )

    checkpoint_pool_size: int = Field(
        default=5,
        alias="AUTOCHAT_CHECKPOINT_POOL_SIZE",
        gt=0,
        le=50,
        description="Async connection pool size for the LangGraph checkpoint backend (default: 5).",
    )

    checkpoint_ttl_seconds: int = Field(
        default=7 * 24 * 3600,  # 7 days
        alias="AUTOCHAT_CHECKPOINT_TTL_SECONDS",
        gt=0,
        description=(
            "How long (in seconds) to retain LangGraph checkpoints before they "
            "are purged by the background cleanup task. Default: 7 days. "
            "Only applies when using the Postgres checkpointer."
        ),
    )

    # ------------------------------------------------------------------
    # Admin API
    # ------------------------------------------------------------------

    admin_enabled: bool = Field(
        default=False,
        alias="AUTOCHAT_ADMIN_ENABLED",
        description=(
            "Master switch for the Expert Review admin endpoints "
            "(``/admin/feedback`` and ``/admin/kb``). When False, the "
            "entire ``/admin/*`` block is not registered so unauthorized "
            "callers receive a clean 404. Disabling at runtime is NOT a "
            "security boundary — authorization is enforced per request "
            "via the configured ``AdminAuthorizer``."
        ),
    )

    admin_verification_endpoint: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_ADMIN_VERIFICATION_ENDPOINT",
        description=(
            "URL of an endpoint that decides whether a given user is an "
            "admin. When set, the plugin selects ``RemoteAdminAuthorizer``: "
            "each admin request POSTs ``{user_id, email, groups, claims}`` "
            "to this endpoint and expects a JSON body ``{is_admin: bool}`` "
            "in the 2xx response. Relative paths (``/admin/check``) are "
            "resolved against ``app_base_url`` to match the existing "
            "``auth_verification_endpoint`` semantics. Decisions are not "
            "cached, so revocations propagate immediately \u2014 admin traffic "
            "is human-paced and the load on the endpoint is negligible."
        ),
    )

    admin_required_groups: List[str] = Field(
        default_factory=list,
        alias="AUTOCHAT_ADMIN_REQUIRED_GROUPS",
        description=(
            "Comma-separated list of SSO group names that grant admin "
            "access. Used only when ``admin_verification_endpoint`` is "
            "not set. Selects ``SSOGroupAdminAuthorizer`` when non-empty. "
            "The IdP must populate ``groups`` (or ``cognito:groups`` / "
            "``roles``) in the userinfo or ID-token claims."
        ),
    )

    # ------------------------------------------------------------------
    # MCP Server
    # ------------------------------------------------------------------

    mcp_enabled: bool = Field(
        default=False,
        alias="AUTOCHAT_MCP_ENABLED",
        description=(
            "Master switch for the MCP (Model Context Protocol) Streamable "
            "HTTP endpoint. When False, the endpoint is not registered so "
            "unauthorized callers receive a clean 404. Exposes the same "
            "OpenAPI-derived tools (via ``ToolsGenerator``/``ToolManager``) "
            "to MCP clients (Claude Desktop, VS Code Copilot, etc.) without "
            "going through the Bedrock/LangGraph chat loop."
        ),
    )

    mcp_endpoint: str = Field(
        default="/chat/mcp",
        alias="AUTOCHAT_MCP_ENDPOINT",
        description="Endpoint path for the MCP Streamable HTTP server, mounted when mcp_enabled is True.",
    )

    kb_embedding_dimensions: int = Field(
        default=1536,
        alias="AUTOCHAT_KB_EMBEDDING_DIMENSIONS",
        gt=0,
        description=(
            "Embedding vector dimensions. Must match the output of the embedding model. "
            "Default: 1536 (Amazon Titan Embed Text v1). "
            "Common values: 1536 (Titan/OpenAI), 1024, 384."
        ),
    )

    feedback_synthesis_system_prompt: Optional[str] = Field(
        default=None,
        alias="AUTOCHAT_FEEDBACK_SYNTHESIS_SYSTEM_PROMPT",
        description=(
            "Override the default system prompt used when synthesizing approved "
            "feedback entries into KB articles.  When unset, a built-in generic "
            "prompt is used.  Set this to tailor the synthesizer to your domain "
            "(e.g. add domain-specific terminology, output constraints, or tone "
            "requirements).  The prompt must instruct the LLM to respond with the "
            "same JSON schema expected by the synthesizer: title, problem, "
            "correct_methodology, key_terms, examples, source_feedback_ids, action."
        ),
    )

    kb_populate_on_startup: bool = Field(
        default=False,
        alias="KB_POPULATE_ON_STARTUP",
        description=(
            "Auto-populate knowledge base on startup (development only). "
            "Production should use CLI: python -m autolangchat.commands.kb populate"
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
        description="Model ID for generating embeddings",
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
        default=0.3,
        alias="KB_SIMILARITY_THRESHOLD",
        ge=0.0,
        le=1.0,
        description="Minimum similarity score for KB results (default: 0.3). Set lower (e.g. 0.0-0.2) to broaden matches.",
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

    kb_credibility_decay_enabled: bool = Field(
        default=False,
        alias="AUTOCHAT_KB_CREDIBILITY_DECAY_ENABLED",
        description=(
            "Enable the background credibility-decay task for synthesized KB articles. "
            "When disabled (default) all articles keep their credibility_score indefinitely "
            "and must be removed manually. Set to true to activate automatic aging."
        ),
    )

    kb_credibility_decay_rate: float = Field(
        default=0.05,
        alias="AUTOCHAT_KB_CREDIBILITY_DECAY_RATE",
        gt=0.0,
        lt=1.0,
        description="Amount subtracted from credibility_score per decay cycle for synthesized articles.",
    )

    kb_credibility_removal_threshold: float = Field(
        default=0.3,
        alias="AUTOCHAT_KB_CREDIBILITY_REMOVAL_THRESHOLD",
        ge=0.0,
        le=1.0,
        description="credibility_score at or below which a synthesized article is flagged for removal.",
    )

    kb_credibility_decay_interval_hours: int = Field(
        default=168,
        alias="AUTOCHAT_KB_CREDIBILITY_DECAY_INTERVAL_HOURS",
        gt=0,
        description="How often (in hours) the credibility decay background task runs. Default: 168 h (1 week).",
    )

    kb_credibility_citation_boost_enabled: bool = Field(
        default=False,
        alias="AUTOCHAT_KB_CREDIBILITY_CITATION_BOOST_ENABLED",
        description="Enable citation-boost signal: each time a feedback document is cited in a RAG response its credibility_score increases by kb_credibility_citation_boost.",
    )

    kb_credibility_citation_boost: float = Field(
        default=0.05,
        alias="AUTOCHAT_KB_CREDIBILITY_CITATION_BOOST",
        ge=0.0,
        le=1.0,
        description="Amount added to credibility_score each time a feedback document is cited in a RAG response.",
    )

    kb_credibility_feedback_signal_enabled: bool = Field(
        default=False,
        alias="AUTOCHAT_KB_CREDIBILITY_FEEDBACK_SIGNAL_ENABLED",
        description="Enable rated-feedback credibility adjustment: when an admin first reviews a feedback entry, cited feedback documents are boosted or penalised.",
    )

    kb_credibility_positive_delta: float = Field(
        default=0.5,
        alias="AUTOCHAT_KB_CREDIBILITY_POSITIVE_DELTA",
        ge=0.0,
        le=1.0,
        description="Amount added to credibility_score for feedback documents cited by a positively-rated, admin-approved feedback entry.",
    )

    kb_credibility_negative_delta: float = Field(
        default=0.5,
        alias="AUTOCHAT_KB_CREDIBILITY_NEGATIVE_DELTA",
        ge=0.0,
        le=1.0,
        description="Amount subtracted from credibility_score for feedback documents cited by a negatively-rated, admin-approved feedback entry.",
    )

    model_config = SettingsConfigDict(
        env_file=_get_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_nested_delimiter=None,  # Disable nested parsing
        env_parse_enums=None,  # Disable enum parsing
    )

    @field_validator(
        "allowed_paths",
        "excluded_paths",
        "admin_required_groups",
        "feedback_authorized_users",
        "sso_allowed_return_prefixes",
        "allowed_dynamic_overrides",
        "available_models",
        "providers",
        mode="before",
    )
    @classmethod
    def parse_list_from_string(cls, v):
        """Parse comma-separated string into list"""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("sso_allowed_return_prefixes")
    @classmethod
    def default_sso_allowed_return_prefixes(cls, v, info):
        return v or [info.data["ui_endpoint"]]

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v):
        """Validate temperature range"""
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"Temperature must be between 0.0 and 1.0, got {v}")
        return v

    @field_validator("model_id")
    @classmethod
    def validate_model_id_is_supported(cls, v):
        """Restrict model_id to known langchain-aws model profiles.

        _PROFILES (langchain_aws.data._profiles) is also the source of the
        human-readable "name" used to label the model_id dropdown in the
        settings sidebar -- only profiled model IDs are supported so a
        display name is always available. Skipped (not enforced) if
        _PROFILES failed to import, since we then have nothing to validate
        against.
        """
        if _PROFILES and v not in _PROFILES:
            raise ValueError(
                f"model_id '{v}' is not a recognized Bedrock model profile "
                "(see langchain_aws.data._profiles._PROFILES for the supported set)."
            )
        return v

    @field_validator("fallback_model")
    @classmethod
    def validate_fallback_model_is_supported(cls, v):
        """Same profile restriction as model_id, when configured."""
        if v is not None and _PROFILES and v not in _PROFILES:
            raise ValueError(
                f"fallback_model '{v}' is not a recognized Bedrock model profile "
                "(see langchain_aws.data._profiles._PROFILES for the supported set)."
            )
        return v

    @field_validator("available_models")
    @classmethod
    def validate_available_models_are_supported(cls, v):
        """Same profile restriction as model_id, applied to every entry."""
        if v is None or not _PROFILES:
            return v
        unknown = [m for m in v if m not in _PROFILES]
        if unknown:
            raise ValueError(
                f"available_models contains unrecognized model id(s): {unknown} "
                "(see langchain_aws.data._profiles._PROFILES for the supported set)."
            )
        return v

    @field_validator("providers")
    @classmethod
    def validate_providers_are_known(cls, v):
        """Validate and normalize provider names against the known catalog.

        Matches each entry case-insensitively against the provider labels
        derived from ``_PROFILES`` (via ``get_model_provider()``) and
        normalizes it to that label's canonical casing, so
        ``AUTOCHAT_PROVIDERS=anthropic,META`` behaves the same as
        ``AUTOCHAT_PROVIDERS=Anthropic,Meta``.
        """
        if v is None or not _PROFILES:
            return v
        known = {get_model_provider(model_id) for model_id in _PROFILES}
        known_lookup = {name.lower(): name for name in known}
        normalized: List[str] = []
        unknown: List[str] = []
        for name in v:
            canonical = known_lookup.get(name.strip().lower())
            if canonical is None:
                unknown.append(name)
            else:
                normalized.append(canonical)
        if unknown:
            raise ValueError(
                f"providers contains unrecognized provider name(s): {unknown} " f"(known providers: {sorted(known)})"
            )
        return normalized

    @field_validator("sso_provider")
    @classmethod
    def validate_sso_provider(cls, v):
        """Validate SSO provider is a known value"""
        if v is not None:
            valid_providers = {"okta", "azure_ad", "auth0", "keycloak", "cognito", "generic"}
            if v.lower() not in valid_providers:
                raise ValueError(f"sso_provider must be one of: {', '.join(sorted(valid_providers))}. Got: {v}")
            return v.lower()
        return v

    @model_validator(mode="after")
    def validate_sso_config(self):
        """Validate SSO configuration when SSO is enabled"""
        if not self.sso_enabled:
            return self

        # Require client_id when SSO is enabled
        if not self.sso_client_id:
            raise ValueError(
                "sso_client_id is required when sso_enabled=True. "
                "Set AUTOCHAT_SSO_CLIENT_ID to your OAuth2 application's client ID."
            )

        # Require session secret when SSO is enabled
        if not self.sso_session_secret:
            raise ValueError(
                "sso_session_secret is required when sso_enabled=True. "
                "Set AUTOCHAT_SSO_SESSION_SECRET to a strong random secret for signing session tokens."
            )

        def _has_value(v: Optional[str]) -> bool:
            return bool(v and v.strip())

        # Require either discovery URL or manual authorization + token URLs
        has_discovery = _has_value(self.sso_discovery_url)
        has_manual_auth = _has_value(self.sso_authorization_url)
        has_manual_token = _has_value(self.sso_token_url)

        if not has_discovery and not (has_manual_auth and has_manual_token):
            raise ValueError(
                "SSO requires either sso_discovery_url (AUTOCHAT_SSO_DISCOVERY_URL) "
                "or both sso_authorization_url (AUTOCHAT_SSO_AUTHORIZATION_URL) and "
                "sso_token_url (AUTOCHAT_SSO_TOKEN_URL) when sso_enabled=True."
            )

        return self

    @model_validator(mode="after")
    def validate_default_auth_type(self):
        """Validate default_auth_type is one of the supported auth types."""
        if self.default_auth_type is not None and self.default_auth_type not in self.supported_auth_types:
            raise ValueError(
                f"default_auth_type '{self.default_auth_type}' is not in supported_auth_types: "
                f"{self.supported_auth_types}"
            )
        return self

    @model_validator(mode="after")
    def resolve_checkpoint_postgres_url(self) -> "ChatConfig":
        """Fall back checkpoint_postgres_url to kb_postgres_url when unset."""
        if not self.checkpoint_postgres_url and self.kb_postgres_url:
            self.checkpoint_postgres_url = self.kb_postgres_url
        return self

    @model_validator(mode="after")
    def resolve_api_base_url(self) -> "ChatConfig":
        """Auto-detect api_base_url when not explicitly configured."""
        if not self.api_base_url:
            self.api_base_url = self._detect_base_url()
        return self

    @staticmethod
    def _detect_base_url() -> str:
        """Detect the API base URL from environment variables, falling back to localhost:8000."""
        _https = os.getenv("HTTPS", "").lower() in ("1", "true")

        host = os.getenv("HOST")
        port = os.getenv("PORT")
        if host is not None and port is not None:
            return f"{'https' if _https else 'http'}://{host}:{port}"

        for host_var, port_var in [
            ("SERVER_HOST", "SERVER_PORT"),
            ("APP_HOST", "APP_PORT"),
            ("WEB_HOST", "WEB_PORT"),
        ]:
            h = os.getenv(host_var)
            p = os.getenv(port_var)
            if h and p:
                return f"{'https' if _https else 'http'}://{h}:{p}"

        return "http://localhost:8000"

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

    def set_invocable_model_ids(self, model_ids: Optional[set]) -> None:
        """Record the set of model ids Bedrock reports as addressable here.

        Called once from ``AutoLangChatPlugin`` startup with the result of
        :func:`~autolangchat.model_discovery.discover_invocable_model_ids`.
        Passing ``None`` (discovery disabled or failed) restores the
        unfiltered behaviour.
        """
        self._invocable_model_ids = model_ids

    def get_available_models(self) -> List[str]:
        """Model IDs to offer in the settings sidebar's model_id dropdown.

        Returns ``available_models`` when explicitly configured (via
        ``AUTOCHAT_AVAILABLE_MODELS``), otherwise falls back to
        ``DEFAULT_AVAILABLE_MODELS`` -- the full tool-calling-capable
        langchain-aws catalog (see ``_build_default_available_models()``).

        When ``providers`` is configured (via ``AUTOCHAT_PROVIDERS``), the
        resulting list is further filtered down to only models whose
        provider (``get_model_provider()``) is in that allowlist.

        Finally, when startup model discovery ran successfully (see
        ``model_discovery_enabled`` and ``set_invocable_model_ids()``), the
        list is intersected with the ids Bedrock actually offers in this
        account/region, so the dropdown can't advertise a model that fails
        with "The provided model identifier is invalid". If that intersection
        would be empty the filter is skipped -- an empty dropdown is worse
        than an optimistic one, and it almost certainly means discovery
        returned something unexpected rather than that no model works.
        """
        models = self.available_models or DEFAULT_AVAILABLE_MODELS
        if self.providers:
            allowed_providers = set(self.providers)
            models = [m for m in models if get_model_provider(m) in allowed_providers]

        if self._invocable_model_ids is not None:
            invocable = [m for m in models if m in self._invocable_model_ids]
            if invocable:
                models = invocable
            else:
                logger.warning(
                    "Bedrock model discovery matched none of the %d configured model(s); "
                    "leaving the catalog unfiltered.",
                    len(models),
                )
        return models

    def get_available_models_for_ui(self) -> List[Dict[str, Any]]:
        """``get_available_models()``, paired with each model's human-readable
        display name, provider label and ``temperature``-support flag from
        ``_PROFILES``, for rendering in the settings sidebar.

        The UI only ever sees ``name`` for display; the backend keeps using
        the raw ``id`` (``model_id``) for everything else. The currently
        configured ``model_id`` is always included (even if omitted from
        ``available_models``) so the dropdown never lacks the active model.

        ``supports_temperature`` mirrors ``_PROFILES[id]["temperature"]`` and
        is used by the frontend to show/hide the temperature *and* top_p
        controls together: `_PROFILES` has no separate top_p flag, and models
        that disable temperature sampling (e.g. some reasoning models) don't
        accept top_p either -- Bedrock Converse only lets a request specify
        one of the two anyway (see ``_build_llm`` in ``graph/nodes/llm_call.py``).
        Defaults to ``True`` (show the controls) when unknown, so a missing
        profile entry degrades to the pre-existing always-show behavior.

        ``max_output_tokens`` mirrors ``_PROFILES[id]["max_output_tokens"]`` and
        is used by the frontend to cap the max_tokens control's upper bound (and
        clamp its current value down) per selected model. ``None`` when unknown,
        in which case the frontend falls back to its own static ceiling.
        """
        model_ids = list(self.get_available_models())
        if self.model_id not in model_ids:
            model_ids = [self.model_id] + model_ids

        return [
            {
                "id": model_id,
                "name": _PROFILES.get(model_id, {}).get("name", model_id),
                "provider": get_model_provider(model_id),
                "supports_temperature": _PROFILES.get(model_id, {}).get("temperature", True),
                "max_output_tokens": _PROFILES.get(model_id, {}).get("max_output_tokens"),
            }
            for model_id in model_ids
        ]

    def get_available_models_grouped_for_ui(self) -> List[Dict[str, Any]]:
        """``get_available_models_for_ui()``, grouped by provider for the
        two-level model dropdown (provider ``<optgroup>`` -> model ``<option>``).

        Returns ``[{"provider": "Anthropic", "models": [<ui entries>]}, ...]``,
        providers sorted alphabetically and models sorted by display name
        within each provider. The provider holding the currently configured
        ``model_id`` is listed first so the active model is easy to find.
        """
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for model in self.get_available_models_for_ui():
            groups.setdefault(model["provider"], []).append(model)

        current_provider = get_model_provider(self.model_id)
        ordered_providers = sorted(groups, key=lambda p: (p != current_provider, p.lower()))

        return [
            {
                "provider": provider,
                "models": sorted(groups[provider], key=lambda m: m["name"]),
            }
            for provider in ordered_providers
        ]

    def get_model_display_name(self, model_id: Optional[str] = None) -> str:
        """Human-readable ``_PROFILES[model_id]["name"]`` for the given (or current)
        model_id, e.g. for the "Powered by ..." chat header. Falls back to the raw
        model_id itself when it has no profile entry (shouldn't normally happen
        since model_id is validated against ``_PROFILES`` at construction time).
        """
        resolved_model_id = model_id or self.model_id
        return _PROFILES.get(resolved_model_id, {}).get("name", resolved_model_id)

    def _scaled_truncation_threshold(self, fraction: float) -> int:
        """Compute a truncation threshold in characters as a fraction of the
        selected model's max_input_tokens.

        There is no static fallback value: the threshold is purely
        ``fraction * _PROFILES[self.model_id]["max_input_tokens"]``, so a
        model with a smaller context window gets a proportionally smaller
        absolute char budget, and a model with a larger one gets a larger
        budget -- entirely determined by the selected model, not a
        hardcoded default (XMGPLAT-11175). ``model_id`` is guaranteed to be
        a key in ``_PROFILES`` with a ``max_input_tokens`` entry (enforced
        by ``validate_model_id_is_supported``).
        """
        max_input_tokens = _PROFILES[self.model_id]["max_input_tokens"]
        return round(fraction * max_input_tokens)

    @property
    def single_msg_length_threshold(self) -> int:
        """Single-message truncation threshold in characters, derived from the
        selected model's max_input_tokens. Not configurable via env var or
        constructor kwarg."""
        return self._scaled_truncation_threshold(SINGLE_MSG_LENGTH_THRESHOLD_FRACTION)

    @property
    def single_msg_truncation_target(self) -> int:
        """Target size after single-message truncation, in characters.
        Derived from the selected model's max_input_tokens. Not configurable
        via env var or constructor kwarg."""
        return self._scaled_truncation_threshold(SINGLE_MSG_TRUNCATION_TARGET_FRACTION)

    @property
    def history_total_length_threshold(self) -> int:
        """Total conversation history threshold in characters, derived from
        the selected model's max_input_tokens. Not configurable via env var
        or constructor kwarg."""
        return self._scaled_truncation_threshold(HISTORY_TOTAL_LENGTH_THRESHOLD_FRACTION)

    @property
    def history_msg_length_threshold(self) -> int:
        """Per-message threshold during history truncation, in characters.
        Derived from the selected model's max_input_tokens. Not configurable
        via env var or constructor kwarg."""
        return self._scaled_truncation_threshold(HISTORY_MSG_LENGTH_THRESHOLD_FRACTION)

    @property
    def history_msg_truncation_target(self) -> int:
        """Per-message target during history truncation, in characters.
        Derived from the selected model's max_input_tokens. Not configurable
        via env var or constructor kwarg."""
        return self._scaled_truncation_threshold(HISTORY_MSG_TRUNCATION_TARGET_FRACTION)

    def get_override_defaults(self) -> Dict[str, Any]:
        """Return the current global value of every overridable parameter.

        Used by the settings sidebar so its controls start at the actual
        effective defaults rather than an arbitrary client-side fallback, and
        as the baseline the client diffs ``active_overrides`` against for the
        override badge count. These values are *not* written to a user's
        ``user_settings`` row: rows are created empty and only ever hold the
        parameters the user actually changed.
        """
        return {
            "model_id": self.model_id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "enable_ai_summarization": self.enable_ai_summarization,
            "enable_rag": self.enable_rag,
            "kb_top_k_results": self.kb_top_k_results,
            "kb_similarity_threshold": self.kb_similarity_threshold,
        }

    def validate_overrides(self, overrides: Dict[str, Any]) -> "tuple[Dict[str, Any], List[str]]":
        """Validate and filter a dict of proposed dynamic parameter overrides.

        Applies the ``enable_dynamic_overrides`` master switch, the
        ``allowed_dynamic_overrides`` allowlist, and per-parameter type/range
        validation. Invalid or disallowed keys are rejected individually (with a
        reason) rather than aborting the whole batch, so a request can end up
        with a partial set of applied overrides.

        Args:
            overrides: Proposed ``{param_name: value}`` overrides.

        Returns:
            Tuple of ``(valid_overrides, rejection_reasons)``.
        """
        valid_overrides: Dict[str, Any] = {}
        rejection_reasons: List[str] = []

        if not overrides:
            return valid_overrides, rejection_reasons

        if not self.enable_dynamic_overrides:
            rejection_reasons.append("Dynamic parameter overrides are disabled (enable_dynamic_overrides=False)")
            return valid_overrides, rejection_reasons

        allowlist = set(self.allowed_dynamic_overrides) if self.allowed_dynamic_overrides is not None else None

        # Resolve the effective model_id for this batch so max_tokens can be
        # capped against the model that will actually be in effect, not just
        # the globally configured one -- a client can override both model_id
        # and max_tokens in the same payload. Only trust the proposed model_id
        # when it would itself pass validation and isn't blocked by the
        # allowlist; its own rejection (if any) is still reported normally by
        # the main loop below.
        effective_model_id = self.model_id
        proposed_model_id = overrides.get("model_id")
        if (
            isinstance(proposed_model_id, str)
            and proposed_model_id.strip()
            and (allowlist is None or "model_id" in allowlist)
            and not self._validate_override_value("model_id", proposed_model_id)
        ):
            effective_model_id = proposed_model_id

        for key, value in overrides.items():
            if key not in OVERRIDABLE_PARAMS:
                rejection_reasons.append(f"'{key}' is not an overridable parameter")
                continue
            if allowlist is not None and key not in allowlist:
                rejection_reasons.append(f"'{key}' is not in allowed_dynamic_overrides")
                continue

            error = self._validate_override_value(key, value, effective_model_id=effective_model_id)
            if error:
                rejection_reasons.append(error)
                continue

            valid_overrides[key] = value

        return valid_overrides, rejection_reasons

    @staticmethod
    def _validate_override_value(key: str, value: Any, effective_model_id: Optional[str] = None) -> Optional[str]:
        """Return an error message if ``value`` is invalid for ``key``, else ``None``.

        ``effective_model_id`` (only used by the ``max_tokens`` check) is the
        model that will actually be in effect for this batch -- either a valid
        ``model_id`` override in the same payload, or the caller's current
        ``model_id`` when none is being overridden. See ``validate_overrides()``.
        """
        if key == "model_id":
            if not isinstance(value, str) or not value.strip():
                return "model_id must be a non-empty string"
            if _PROFILES and value not in _PROFILES:
                return f"model_id '{value}' is not a recognized Bedrock model profile"
        elif key == "temperature":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return "temperature must be a number"
            if not 0.0 <= float(value) <= 1.0:
                return "temperature must be between 0.0 and 1.0"
        elif key == "max_tokens":
            if isinstance(value, bool) or not isinstance(value, int):
                return "max_tokens must be an integer"
            if value <= 0:
                return "max_tokens must be greater than 0"
            if effective_model_id:
                model_cap = _PROFILES.get(effective_model_id, {}).get("max_output_tokens")
                if model_cap is not None and value > model_cap:
                    return f"max_tokens ({value}) exceeds model '{effective_model_id}' max_output_tokens ({model_cap})"
        elif key == "top_p":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return "top_p must be a number"
            if not 0.0 <= float(value) <= 1.0:
                return "top_p must be between 0.0 and 1.0"
        elif key in ("enable_ai_summarization", "enable_rag"):
            if not isinstance(value, bool):
                return f"{key} must be a boolean"
        elif key == "kb_top_k_results":
            if isinstance(value, bool) or not isinstance(value, int):
                return "kb_top_k_results must be an integer"
            if value <= 0:
                return "kb_top_k_results must be greater than 0"
        elif key == "kb_similarity_threshold":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return "kb_similarity_threshold must be a number"
            if not 0.0 <= float(value) <= 1.0:
                return "kb_similarity_threshold must be between 0.0 and 1.0"
        return None


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

            # Validate conversation management fields
            if "max_conversation_messages" in overrides:
                max_msg_val = overrides["max_conversation_messages"]
                if not isinstance(max_msg_val, int) or max_msg_val <= 0:
                    raise ConfigurationError("max_conversation_messages must be a positive integer")

            # NOTE: single_msg_length_threshold, single_msg_truncation_target,
            # history_total_length_threshold, history_msg_length_threshold, and
            # history_msg_truncation_target are no longer configurable fields
            # they are computed properties derived from the
            # model's context window, so no override validation is needed here.

            # Validate max_truncation_recursion
            if "max_truncation_recursion" in overrides:
                val = overrides["max_truncation_recursion"]
                if not isinstance(val, int) or val < 1 or val > 10:
                    raise ConfigurationError("max_truncation_recursion must be between 1 and 10")

            # Create base config from .env
            config = ChatConfig()

            # Re-create with overrides to ensure validators run.
            # model_copy accepts field names; model_validate re-runs validators.
            if overrides:
                config = ChatConfig.model_validate(config.model_copy(update=overrides).model_dump(by_alias=True))
        else:
            # No overrides, use standard .env loading
            config = ChatConfig()

        return config

    except ConfigurationError:
        # Re-raise ConfigurationError as-is
        raise
    except Exception as e:
        raise ConfigurationError(f"Failed to load configuration: {str(e)}")


def load_preset_config_from_yaml(path: str) -> Dict[str, Any]:
    """
    Load preset prompts and variable definitions from a YAML file.

    Returns a dict with ``{"prompts": [...], "variables": [...]}``.  Both lists
    are empty when the corresponding top-level key is absent from the file.
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        import yaml
    except ImportError:  # pragma: no cover
        logger.warning(
            "pyyaml is not installed; cannot load preset config from '%s'. " "Install it with: pip install pyyaml",
            path,
        )
        return {"prompts": [], "variables": []}

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            return {"prompts": [], "variables": []}
        prompts = data.get("prompts", []) or []
        variables = data.get("variables", []) or []
        logger.info("Loaded %d preset prompt(s) and %d variable(s) from %s", len(prompts), len(variables), path)
        return {"prompts": prompts, "variables": variables}
    except FileNotFoundError:
        logger.debug("Preset config file not found: %s", path)
        return {"prompts": [], "variables": []}
    except Exception as exc:
        logger.warning("Could not load preset config from '%s': %s", path, exc)
        return {"prompts": [], "variables": []}


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

    # Raise errors for critical misconfigurations
    if config.feedback_max_history_context < 0:
        raise ConfigurationError("Feedback max history context cannot be negative")

    endpoints = [config.chat_endpoint, config.websocket_endpoint, config.ui_endpoint]
    if config.mcp_enabled:
        endpoints.append(config.mcp_endpoint)
    # Validate endpoint paths don't conflict
    if len(set(endpoints)) != len(endpoints):
        raise ConfigurationError("Chat endpoints cannot have duplicate paths")

    # Warn about common misconfigurations
    if config.temperature > 0.9:
        print(f"Warning: High temperature ({config.temperature}) may cause unpredictable responses")

    if config.max_tool_calls is not None and config.max_tool_calls > 20:
        print(f"Warning: High max_tool_calls ({config.max_tool_calls}) may cause long response times")

    if config.session_timeout < 300:  # 5 minutes
        print(f"Warning: Low session timeout ({config.session_timeout}s) may disconnect users frequently")
