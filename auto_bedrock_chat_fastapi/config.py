"""Configuration management for auto-bedrock-chat-fastapi"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Dict, Optional, Any, Callable, Union
import os
from .exceptions import ConfigurationError


def _get_env_file() -> str:
    """Determine which .env file to use based on environment"""
    # Check if we're in a test environment
    if (os.getenv('PYTEST_CURRENT_TEST') or 
        'pytest' in os.getenv('_', '') or
        'pytest' in str(os.getenv('VIRTUAL_ENV', '')) or
        os.getenv('ENVIRONMENT') == 'test'):
        return '.env.test'
    # Check if pytest is in sys.modules (running under pytest)
    import sys
    if 'pytest' in sys.modules:
        return '.env.test'
    # Default to .env
    return '.env'


class ChatConfig(BaseSettings):
    """Configuration for Bedrock Chat Plugin"""
    
    # Model Configuration
    model_id: str = Field(
        default="anthropic.claude-3-5-sonnet-20241022-v2:0",
        alias="BEDROCK_MODEL_ID",
        description="Bedrock model identifier"
    )
    
    temperature: float = Field(
        default=0.7,
        alias="BEDROCK_TEMPERATURE", 
        ge=0.0,
        le=1.0,
        description="Sampling temperature for model responses"
    )
    
    max_tokens: int = Field(
        default=4096,
        alias="BEDROCK_MAX_TOKENS",
        gt=0,
        description="Maximum tokens in model response"
    )
    
    top_p: float = Field(
        default=0.9,
        alias="BEDROCK_TOP_P",
        ge=0.0,
        le=1.0,
        description="Top-p sampling parameter"
    )
    
    # System Configuration
    system_prompt: Optional[str] = Field(
        default=None,
        alias="BEDROCK_SYSTEM_PROMPT",
        description="Custom system prompt for the AI assistant"
    )
    
    # API Tools Configuration
    tools_desc: Optional[Dict] = Field(
        default_factory=dict,
        description="Auto-generated tools from FastAPI routes"
    )
    
    allowed_paths: List[str] = Field(
        default_factory=list,
        alias="BEDROCK_ALLOWED_PATHS",
        description="Whitelist of API paths to expose as tools"
    )
    
    excluded_paths: List[str] = Field(
        default_factory=lambda: ["/bedrock-chat", "/docs", "/redoc", "/openapi.json", "/health"],
        alias="BEDROCK_EXCLUDED_PATHS",
        description="Blacklist of API paths to exclude from tools"
    )
    
    # Session Configuration
    max_tool_calls: int = Field(
        default=10,
        alias="BEDROCK_MAX_TOOL_CALLS",
        gt=0,
        description="Maximum tool calls per conversation turn"
    )
    
    timeout: int = Field(
        default=30,
        alias="BEDROCK_TIMEOUT",
        gt=0,
        description="Timeout for API calls in seconds"
    )
    
    # WebSocket Configuration
    max_sessions: int = Field(
        default=1000,
        alias="BEDROCK_MAX_SESSIONS",
        gt=0,
        description="Maximum concurrent WebSocket sessions"
    )
    
    session_timeout: int = Field(
        default=3600,
        alias="BEDROCK_SESSION_TIMEOUT",
        gt=0,
        description="Session timeout in seconds"
    )
    
    # AWS Configuration
    aws_region: str = Field(
        default="us-east-1",
        alias="AWS_REGION",
        description="AWS region for Bedrock service"
    )
    
    aws_access_key_id: Optional[str] = Field(
        default=None,
        alias="AWS_ACCESS_KEY_ID",
        description="AWS access key ID"
    )
    
    aws_secret_access_key: Optional[str] = Field(
        default=None,
        alias="AWS_SECRET_ACCESS_KEY",
        description="AWS secret access key"
    )
    
    # Endpoint Configuration
    chat_endpoint: str = Field(
        default="/bedrock-chat",
        alias="BEDROCK_CHAT_ENDPOINT",
        description="Base endpoint for chat API"
    )
    
    websocket_endpoint: str = Field(
        default="/bedrock-chat/ws",
        alias="BEDROCK_WEBSOCKET_ENDPOINT", 
        description="WebSocket endpoint"
    )
    
    ui_endpoint: str = Field(
        default="/bedrock-chat/ui",
        alias="BEDROCK_UI_ENDPOINT",
        description="Web UI endpoint"
    )
    
    enable_ui: bool = Field(
        default=True,
        alias="BEDROCK_ENABLE_UI",
        description="Enable built-in chat UI"
    )
    
    # Security Configuration
    auth_dependency: Optional[Callable] = Field(
        default=None,
        description="Authentication dependency function"
    )
    
    rate_limit: Optional[str] = Field(
        default=None,
        alias="BEDROCK_RATE_LIMIT",
        description="Rate limit for chat endpoints (e.g., '10/minute')"
    )
    
    cors_origins: List[str] = Field(
        default_factory=lambda: ["*"],
        alias="BEDROCK_CORS_ORIGINS",
        description="CORS allowed origins"
    )
    
    # Logging Configuration
    log_level: str = Field(
        default="INFO",
        alias="BEDROCK_LOG_LEVEL",
        description="Logging level"
    )
    
    log_api_calls: bool = Field(
        default=False,
        alias="BEDROCK_LOG_API_CALLS",
        description="Log API calls for debugging"
    )
    
    log_errors: bool = Field(
        default=True,
        alias="BEDROCK_LOG_ERRORS",
        description="Log errors"
    )
    
    # Error Handling Configuration
    max_retries: int = Field(
        default=3,
        alias="BEDROCK_MAX_RETRIES",
        ge=0,
        description="Maximum retries for failed requests"
    )
    
    retry_delay: float = Field(
        default=1.0,
        alias="BEDROCK_RETRY_DELAY",
        ge=0.0,
        description="Delay between retries in seconds"
    )
    
    exponential_backoff: bool = Field(
        default=True,
        alias="BEDROCK_EXPONENTIAL_BACKOFF",
        description="Use exponential backoff for retries"
    )
    
    fallback_model: Optional[str] = Field(
        default=None,
        alias="BEDROCK_FALLBACK_MODEL",
        description="Fallback model if primary model fails"
    )
    
    graceful_degradation: bool = Field(
        default=True,
        alias="BEDROCK_GRACEFUL_DEGRADATION",
        description="Enable graceful degradation on errors"
    )
    
    model_config = SettingsConfigDict(
        env_file=_get_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_nested_delimiter=None,  # Disable nested parsing
        env_parse_enums=None  # Disable enum parsing
    )
    
    @field_validator('allowed_paths', 'excluded_paths', 'cors_origins', mode='before')
    @classmethod
    def parse_list_from_string(cls, v):
        """Parse comma-separated string into list"""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v
    
    @field_validator('model_id')
    @classmethod
    def validate_model_id(cls, v):
        """Validate Bedrock model ID format"""
        if not v:
            raise ValueError("Model ID cannot be empty")
        
        # Common Bedrock model patterns
        valid_patterns = [
            "anthropic.claude",
            "amazon.titan",
            "ai21.j2",
            "cohere.command",
            "meta.llama2",
            "openai.gpt"  # Add OpenAI support
        ]
        
        if not any(pattern in v for pattern in valid_patterns):
            # Allow override for testing or custom models
            if not v.startswith(("test-", "custom-")):
                raise ValueError(f"Invalid model ID: {v}")
        
        return v
    
    @field_validator('temperature')
    @classmethod
    def validate_temperature(cls, v):
        """Validate temperature range"""
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"Temperature must be between 0.0 and 1.0, got {v}")
        return v
    
    @field_validator('rate_limit')
    @classmethod
    def validate_rate_limit(cls, v):
        """Validate rate limit format"""
        if v is None:
            return v
        
        # Simple validation for format like "10/minute", "100/hour"
        if "/" not in v:
            raise ValueError("Rate limit must be in format 'number/period' (e.g., '10/minute')")
        
        parts = v.split("/")
        if len(parts) != 2:
            raise ValueError("Rate limit must be in format 'number/period'")
        
        try:
            int(parts[0])
        except ValueError:
            raise ValueError("Rate limit number must be an integer")
        
        valid_periods = ["second", "minute", "hour", "day"]
        if parts[1] not in valid_periods:
            raise ValueError(f"Rate limit period must be one of: {valid_periods}")
        
        return v
    
    def get_system_prompt(self) -> str:
        """Get effective system prompt"""
        if self.system_prompt:
            return self.system_prompt
        
        tools_count = len(self.tools_desc.get("functions", [])) if self.tools_desc else 0
        
        return f"""You are a helpful AI assistant that can interact with API endpoints.

Available tools: {tools_count} API endpoints
Model: {self.model_id}

Guidelines:
- Be helpful and explain what you're doing when calling API endpoints
- Use tools when users request operations that require API calls
- Provide clear, accurate responses based on the data you receive
- Handle errors gracefully and suggest alternatives when possible
- Always explain what tools you're using and why
- Format responses in a user-friendly way"""
    
    def get_aws_config(self) -> Dict[str, Any]:
        """Get AWS configuration for boto3"""
        config = {
            "region_name": self.aws_region
        }
        
        if self.aws_access_key_id and self.aws_secret_access_key:
            config.update({
                "aws_access_key_id": self.aws_access_key_id,
                "aws_secret_access_key": self.aws_secret_access_key
            })
        
        return config
    
    def get_bedrock_params(self) -> Dict[str, Any]:
        """Get parameters for Bedrock API calls"""
        return {
            "model_id": self.model_id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p
        }


def load_config(
    model_id: Optional[str] = None,
    temperature: Optional[float] = None,
    system_prompt: Optional[str] = None,
    **kwargs
) -> ChatConfig:
    """Load configuration with optional overrides"""
    
    try:
        # Prepare overrides dictionary
        overrides = {}
        if model_id is not None:
            overrides['model_id'] = model_id
        if temperature is not None:
            overrides['temperature'] = temperature
        if system_prompt is not None:
            overrides['system_prompt'] = system_prompt
        
        # Add any additional kwargs
        overrides.update({k: v for k, v in kwargs.items() if v is not None})
        
        if overrides:
            # Manual validation for specific fields
            if 'temperature' in overrides:
                temp_val = overrides['temperature']
                if not 0.0 <= temp_val <= 1.0:
                    raise ConfigurationError(f"Temperature must be between 0.0 and 1.0, got {temp_val}")
            
            if 'model_id' in overrides:
                model_val = overrides['model_id']
                if not model_val:
                    raise ConfigurationError("Model ID cannot be empty")
                # Check for valid patterns
                valid_patterns = ["anthropic.claude", "amazon.titan", "ai21.j2", "cohere.command", "meta.llama2", "openai.gpt"]
                if not any(pattern in model_val for pattern in valid_patterns):
                    if not model_val.startswith(("test-", "custom-")):
                        raise ConfigurationError(f"Invalid model ID: {model_val}")
            
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