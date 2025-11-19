# Configuration Guide

## Using .env Files for Configuration

The Bedrock Chat FastAPI plugin supports configuration through environment variables and `.env` files. This makes it easy to manage different settings for development, testing, and production.

## Quick Start

1. **Copy the example file:**

   ```bash
   cp .env.example .env
   ```

2. **Edit your `.env` file** with your AWS credentials and preferred settings.

3. **Use in your app:**

   ```python
   from fastapi import FastAPI
   from auto_bedrock_chat_fastapi import add_bedrock_chat

   app = FastAPI()

   # Most configuration loaded from .env automatically
   # Override list fields due to Pydantic v2 limitations
   bedrock_chat = add_bedrock_chat(
       app,
       allowed_paths=["/api/products", "/api/users"],  # Must be set in code
       excluded_paths=["/docs", "/admin"]              # Must be set in code
   )
   ```

## Configuration Options

### ✅ Works from .env file

- `AWS_REGION`: AWS region where Bedrock is available
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`: AWS credentials
- `BEDROCK_MODEL_ID`: Which Bedrock model to use
- `BEDROCK_TEMPERATURE`: Response randomness (0.0-1.0)
- `BEDROCK_SYSTEM_PROMPT`: AI assistant instructions
- `BEDROCK_CHAT_ENDPOINT`: REST API endpoint path
- `BEDROCK_WEBSOCKET_ENDPOINT`: WebSocket endpoint path
- `BEDROCK_UI_ENDPOINT`: Built-in UI endpoint path
- `BEDROCK_ENABLE_UI`: Enable/disable web UI
- `BEDROCK_MAX_TOOL_CALLS`: Limit tool calls per turn
- `BEDROCK_TIMEOUT`: API timeout in seconds
- `BEDROCK_LOG_LEVEL`: Logging level
- All other single-value settings

### ⚠️ Must be set in code (Pydantic v2 limitation)

- `allowed_paths`: API paths the AI can access
- `excluded_paths`: API paths to exclude
- `cors_origins`: CORS allowed origins

## Overriding Settings

You can override any `.env` setting by passing parameters directly:

```python
# Use .env for most settings, override specific ones
bedrock_chat = add_bedrock_chat(
    app,
    temperature=0.3,  # Override temperature
    enable_ui=False,  # Disable UI for production
    allowed_paths=["/api/v1/products", "/api/v1/orders"],  # Set allowed paths
)
```

## Environment-Specific Configurations

### Development (.env)

```env
BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0
BEDROCK_TEMPERATURE=0.3
BEDROCK_LOG_LEVEL=DEBUG
BEDROCK_LOG_API_CALLS=true
BEDROCK_ENABLE_UI=true
```

### Production (.env.prod)

```env
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
BEDROCK_TEMPERATURE=0.7
BEDROCK_LOG_LEVEL=INFO
BEDROCK_LOG_API_CALLS=false
BEDROCK_ENABLE_UI=false
```

### Testing (.env.test)

```env
BEDROCK_TEMPERATURE=0.0
BEDROCK_MAX_TOOL_CALLS=3
BEDROCK_TIMEOUT=10
BEDROCK_ENABLE_UI=false
```

## Pydantic v2 List Limitations

Due to changes in Pydantic v2, list fields cannot be reliably parsed from `.env` files. The following fields must be set in your Python code:

- `allowed_paths`
- `excluded_paths`
- `cors_origins`

**Example workaround:**

```python
# Instead of setting in .env:
# BEDROCK_ALLOWED_PATHS=/api/products,/api/users,/api/orders

# Set in Python code:
bedrock_chat = add_bedrock_chat(
    app,
    allowed_paths=["/api/products", "/api/users", "/api/orders"]
)
```

## Security Best Practices

1. **Never commit `.env` files** to version control
2. **Use IAM roles** instead of hardcoded AWS keys when possible
3. **Set appropriate rate limits** for production
4. **Restrict allowed paths** to only what the AI needs
5. **Disable UI** in production environments

## Examples

See the following example files:

## 📂 Example Files

- `app_plugin.py`: E-commerce API with AI chat using .env configuration
- `app_plugin_override.py`: Shows different ways to override .env settings
- `.env.example`: Complete example configuration file
