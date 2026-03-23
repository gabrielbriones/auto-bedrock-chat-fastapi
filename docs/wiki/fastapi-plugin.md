# FastAPI Plugin Integration

`add_bedrock_chat()` is the primary way to add AI chat to an existing FastAPI application. It registers WebSocket, REST, and (optionally) UI endpoints on your app.

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
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI(title="My API")

# Your existing routes...
@app.get("/products")
async def list_products():
    return [{"id": 1, "name": "Widget", "price": 9.99}]

# Add AI chat — reads settings from .env
bedrock_chat = add_bedrock_chat(
    app,
    allowed_paths=["/products"],
    excluded_paths=["/docs"]
)
```

That's it. The plugin registers:

| Endpoint                             | Description                      |
| ------------------------------------ | -------------------------------- |
| `GET /bedrock-chat/health`           | Health check                     |
| `POST /bedrock-chat/chat`            | REST chat endpoint               |
| `WS /bedrock-chat/ws`                | WebSocket chat                   |
| `GET /bedrock-chat/ui`               | Built-in Chat UI (if enabled)    |
| `POST /bedrock-chat/semantic-search` | RAG semantic search (if enabled) |
| `POST /bedrock-chat/verify-auth`     | Auth verification endpoint       |

---

## Modern Lifespan Approach (Recommended for New Projects)

```python
from auto_bedrock_chat_fastapi import create_fastapi_with_bedrock_chat

app, plugin = create_fastapi_with_bedrock_chat(
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
bedrock_chat = add_bedrock_chat(
    app,
    # Model
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    temperature=0.5,
    max_tokens=8192,
    system_prompt="You are a helpful customer support assistant.",

    # Endpoints
    chat_endpoint="/bedrock-chat",
    websocket_endpoint="/bedrock-chat/ws",
    ui_endpoint="/bedrock-chat/ui",
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
from auto_bedrock_chat_fastapi import add_bedrock_chat

security = HTTPBearer()
app = FastAPI()

@app.get("/secure-data")
async def get_data(token = Depends(security)):
    return {"data": "sensitive value"}

# Enable tool auth so the AI passes credentials when calling your endpoints
bedrock_chat = add_bedrock_chat(
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
from auto_bedrock_chat_fastapi import add_bedrock_chat
from auto_bedrock_chat_fastapi.vector_db import VectorDB
from auto_bedrock_chat_fastapi.embedding_pipeline import EmbeddingPipeline

app = FastAPI()
db = VectorDB("knowledge_base.db")

bedrock_chat = add_bedrock_chat(
    app,
    vector_db=db,           # enables RAG retrieval on every message
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
