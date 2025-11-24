# Environment Configuration Guide

## Overview

The auto-bedrock-chat-fastapi package now supports environment-aware configuration that automatically chooses the appropriate configuration file based on the runtime context.

## Configuration Files

### `.env` (Production/Development)

- **Purpose**: Environment-specific configuration for production or development
- **Git Status**: **Ignored** (not committed to source control)
- **Usage**: Contains sensitive information and environment-specific settings
- **Location**: Project root directory

### `.env.example`

- **Purpose**: Template showing all available configuration options
- **Git Status**: **Tracked** (committed to source control)
- **Usage**: Copy to `.env` and customize for your environment
- **Location**: Project root directory

### `.env.test`

- **Purpose**: Test-specific configuration with safe defaults
- **Git Status**: **Tracked** (committed to source control)
- **Usage**: Automatically used during pytest runs
- **Location**: Project root directory

## Environment Detection

The configuration system automatically detects the runtime environment:

### Test Environment Detection

The system uses `.env.test` when any of these conditions are met:

- `PYTEST_CURRENT_TEST` environment variable is set
- `pytest` appears in the command path
- `pytest` module is imported
- `ENVIRONMENT=test` environment variable is set

### Production/Development Environment

The system uses `.env` for normal application runtime.

### 🚀 Quick Start

```bash
python examples/fastAPI/app_plugin.py
```

### Testing

```bash
# Tests automatically use .env.test
poetry run pytest

# Or with explicit environment variable
ENVIRONMENT=test python your_script.py
```

### Manual Environment Selection

```bash
# Force test environment
ENVIRONMENT=test python your_script.py

# Normal environment (uses .env)
python your_script.py
```

## Configuration Differences

| Setting                 | `.env` (Production) | `.env.test` (Testing) |
| ----------------------- | ------------------- | --------------------- |
| `BEDROCK_CHAT_ENDPOINT` | `/api/chat`         | `/bedrock-chat`       |
| `BEDROCK_ENABLE_UI`     | `true`              | `false`               |
| `BEDROCK_SYSTEM_PROMPT` | Production message  | Test message          |

## Best Practices

1. **Never commit `.env` files** - They contain environment-specific and potentially sensitive data
2. **Always update `.env.example`** - When adding new configuration options
3. **Keep `.env.test` minimal** - Use safe, non-sensitive defaults
4. **Test independence** - Tests should work without requiring a specific `.env` file
5. **Documentation** - Document any new configuration options

## Configuration Override

The `load_config()` function still supports parameter overrides for testing:

```python
from auto_bedrock_chat_fastapi.config import load_config

# Override specific values for testing
config = load_config(
    model_id="test-model",
    temperature=0.5,
    enable_ui=False
)
```

This provides a robust configuration system that supports both environment-based configuration and programmatic overrides while maintaining test isolation.
