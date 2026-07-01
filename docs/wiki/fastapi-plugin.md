# FastAPI Plugin Integration

`add_autolangchat()` is the primary way to add AI chat to an existing FastAPI application. It registers WebSocket, utility REST endpoints, and (optionally) UI endpoints on your app.

---

## Installation

```bash
# From GitHub (current method)
pip install git+https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git

# Editable install for development
git clone https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git
cd auto-bedrock-chat-fastapi
pip install -e .
```

**Requirements:** Python 3.9+, FastAPI 0.100+, AWS credentials configured.

---

## Basic Usage

```python
from fastapi import FastAPI
from autolangchat import add_autolangchat

app = FastAPI(title="My API")

# Your existing routes...
@app.get("/products")
async def list_products():
    return [{"id": 1, "name": "Widget", "price": 9.99}]

# Add AI chat — reads settings from .env
autolangchat_plugin = add_autolangchat(
    app,
    allowed_paths=["/products"],
    excluded_paths=["/docs"]
)
```

That's it. The plugin registers:

| Endpoint                      | Description                                   |
| ----------------------------- | --------------------------------------------- |
| `GET /chat/health`            | Health check                                  |
| `GET /chat/stats`             | Chat statistics                               |
| `GET /chat/tools`             | Exposed tool metadata                         |
| `WS /chat/ws`                 | WebSocket chat                                |
| `GET /chat/ui`                | Built-in Chat UI (if enabled)                 |
| `POST /chat/knowledge/search` | Hybrid knowledge-base search (if RAG enabled) |
| `GET /chat/auth/sso/login`    | SSO login redirect (if SSO enabled)           |

---

## Modern Lifespan Approach (Recommended for New Projects)

```python
from autolangchat import create_fastapi_with_autolangchat

app, plugin = create_fastapi_with_autolangchat(
    title="My API",
    description="An API with AI chat",
    version="1.0.0",
    allowed_paths=["/products", "/orders"],
    system_prompt="You are a helpful e-commerce assistant."
)

@app.get("/products")
async def list_products():
    return []
```

---

## Full Configuration Example

```python
autolangchat_plugin = add_autolangchat(
    app,
    # Model
    model_id="us.anthropic.claude-sonnet-5",
    temperature=0.5,
    max_tokens=8192,
    system_prompt="You are a helpful customer support assistant.",

    # Endpoints
    chat_endpoint="/chat",
    websocket_endpoint="/chat/ws",
    ui_endpoint="/chat/ui",
    enable_ui=True,

    # Tool access control
    allowed_paths=["/api/products", "/api/users", "/api/orders"],
    excluded_paths=["/docs", "/redoc", "/openapi.json", "/admin"],

    # Auth
    enable_tool_auth=True,
    supported_auth_types=["bearer_token", "api_key"],

    # Session
    max_sessions=500,
    session_timeout=1800,
    max_conversation_messages=30,

    # Tool limits
    max_tool_calls=15,
    max_tool_call_rounds=10,

    # CORS
    cors_origins=["https://myapp.com"],

    # Logging
    log_level="INFO"
)
```

---

## Auth Plugin Example (`app_auth.py`)

```python
from fastapi import FastAPI, Depends
from fastapi.security import HTTPBearer
from autolangchat import add_autolangchat

security = HTTPBearer()
app = FastAPI()

@app.get("/secure-data")
async def get_data(token = Depends(security)):
    return {"data": "sensitive value"}

# Enable tool auth so the AI passes credentials when calling your endpoints
autolangchat_plugin = add_autolangchat(
    app,
    enable_tool_auth=True,
    allowed_paths=["/secure-data"]
)
```

The client authenticates once via WebSocket:

```json
{ "type": "auth", "auth_type": "bearer_token", "token": "your-token" }
```

All subsequent AI tool calls to `/secure-data` will include `Authorization: Bearer your-token`.

---

## RAG Plugin Example (`app_rag.py`)

```python
from fastapi import FastAPI
from autolangchat import add_autolangchat

app = FastAPI()

autolangchat_plugin = add_autolangchat(
    app,
    enable_rag=True,
    kb_database_path="data/knowledge_base.db",
    allowed_paths=["/api"]
)
```

See [RAG Feature](rag-feature.md) for full setup.

---

## See Also

- [Configuration](configuration.md) — full settings reference
- [OpenAPI Integration](openapi-integration.md) — non-FastAPI frameworks
- [Authentication](authentication.md) — auth methods
- [Tool Calling](tool-calling.md) — how tools are generated and called
