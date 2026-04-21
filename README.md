# auto-bedrock-chat-fastapi

🤖 **Automatically add AI chat capabilities to your FastAPI application with Amazon Bedrock integration**

Transform any FastAPI app into an intelligent AI assistant. The plugin reads your OpenAPI spec, generates AI-callable tools from your endpoints, and provides a real-time WebSocket chat interface powered by Amazon Bedrock — all with a single decorator.

[![PyPI version](https://badge.fury.io/py/auto-bedrock-chat-fastapi.svg)](https://badge.fury.io/py/auto-bedrock-chat-fastapi)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## ✨ Key Features

- **Zero-config tool generation** — OpenAPI spec → AI tools automatically
- **Framework-agnostic** — works with Express.js, Flask, Django, or any framework via an OpenAPI spec file
- **Real-time WebSocket chat** with a built-in web UI
- **Amazon Bedrock** support for Claude, GPT OSS, Llama, Titan, and other models
- **5 authentication methods** for securing AI tool calls (Bearer, Basic, API Key, OAuth2, Custom)
- **RAG support** — web crawler, vector DB, and embedding pipeline for knowledge-base-grounded responses
- **Smart token management** — automatic truncation and optional AI summarization to prevent context overflow

---

## 🚀 Quick Start

### Install

```bash
pip install git+https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git
```

### Add to Your App

```python
from fastapi import FastAPI
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI(title="My API")

@app.get("/products")
async def list_products():
    return [{"id": 1, "name": "Widget", "price": 9.99}]

# One line adds AI chat + WebSocket + built-in UI
add_bedrock_chat(app, allowed_paths=["/products"])
```

### Configure AWS (`.env`)

```bash
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
```

### Run

```bash
uvicorn app:app --reload
```

Open `http://localhost:8000/bedrock-chat/ui` and start chatting with your API.

---

## 🏗️ Architecture

```
Your FastAPI App
       │
       └── add_bedrock_chat(app)
               │
               ├── WebSocket Handler (/bedrock-chat/ws)
               │       └── session auth, RAG retrieval, message routing
               │
               ├── Chat Manager
               │       └── LLM conversation loop + tool call rounds
               │
               ├── Bedrock Client
               │       └── AWS Bedrock API (Claude, Llama, Titan…)
               │
               ├── Tool Manager
               │       └── OpenAPI spec → AI tools → HTTP calls to your API
               │
               ├── Message Preprocessor
               │       └── Token budget management (truncation / AI summarization)
               │
               └── Optional RAG Stack
                       ├── ContentCrawler  (web + local file ingestion)
                       ├── EmbeddingPipeline  (Bedrock Titan embeddings)
                       └── VectorDB  (SQLite-vec semantic search)
```

**Request flow:** User message → RAG retrieval (optional) → token budget check → Bedrock LLM → tool calls (as needed, up to N rounds) → final response streamed via WebSocket.

---

## 📁 Source Code Structure

```
auto_bedrock_chat_fastapi/
├── plugin.py               # Entry point: add_bedrock_chat(), create_fastapi_with_bedrock_chat()
├── config.py               # ChatConfig — all settings via Pydantic + .env
├── defaults.py             # Centralized default values (thresholds, timeouts, ratios)
├── websocket_handler.py    # WebSocket connection lifecycle and message routing
├── chat_manager.py         # LLM conversation orchestration loop
├── bedrock_client.py       # Amazon Bedrock API client (retries, model routing)
├── tool_manager.py         # ToolsGenerator (OpenAPI→tools) + tool execution
├── auth_handler.py         # Authentication types and credential management
├── session_manager.py      # In-memory session lifecycle
├── message_preprocessor.py # Two-stage token budget pipeline
├── content_crawler.py      # Web and local file crawler for RAG
├── embedding_pipeline.py   # Text chunking + Bedrock Titan embeddings
├── vector_db.py            # SQLite-vec vector store
├── parsers/                # Per-model request/response parsers (Claude, GPT, Llama)
├── templates/              # Chat UI HTML templates
└── static/                 # Chat UI CSS and JS assets

examples/
├── fastAPI/                # FastAPI integration examples (plugin, auth, RAG)
├── expressjs/              # Express.js + OpenAPI spec integration demo
└── websockets/             # Python WebSocket client script

tests/                      # Unit tests (~204 tests, no AWS required)
integration_testing/        # Integration tests (require AWS credentials)
.github/workflows/          # CI/CD: tests, code quality, build, deploy
docs/wiki/                  # Full documentation wiki
```

---

## 📖 Documentation

Full documentation is in [`docs/wiki/`](docs/wiki/):

| Topic                        | Page                                                       |
| ---------------------------- | ---------------------------------------------------------- |
| System architecture          | [architecture.md](docs/wiki/architecture.md)               |
| All configuration settings   | [configuration.md](docs/wiki/configuration.md)             |
| FastAPI plugin integration   | [fastapi-plugin.md](docs/wiki/fastapi-plugin.md)           |
| OpenAPI / framework-agnostic | [openapi-integration.md](docs/wiki/openapi-integration.md) |
| Tool calling & generation    | [tool-calling.md](docs/wiki/tool-calling.md)               |
| Built-in Chat UI             | [chat-ui.md](docs/wiki/chat-ui.md)                         |
| WebSocket client script      | [websocket-client.md](docs/wiki/websocket-client.md)       |
| Authentication methods       | [authentication.md](docs/wiki/authentication.md)           |
| RAG (crawler + vector DB)    | [rag-feature.md](docs/wiki/rag-feature.md)                 |
| Token management             | [token-management.md](docs/wiki/token-management.md)       |
| CI pipelines                 | [ci-pipelines.md](docs/wiki/ci-pipelines.md)               |
| CD / deployment              | [cd-pipelines.md](docs/wiki/cd-pipelines.md)               |

---

## Dependency Management

The canonical source of truth for dependencies is [`pyproject.toml`](pyproject.toml), managed via [Poetry](https://python-poetry.org/).

[`requirements.txt`](requirements.txt) is a pip-compatible export derived from `poetry.lock`. It is used by CI pipelines and consumers (e.g. other repositories that install this package via pip). **Do not edit `requirements.txt` manually.**

After adding, removing, or updating any dependency in `pyproject.toml`, regenerate `requirements.txt`:

```bash
poetry export -f requirements.txt --without-hashes --output requirements.txt
```

A CI check in the `Code Quality` workflow will fail if `requirements.txt` is out of sync with `poetry.lock`.

---

## 🔧 Requirements

- Python >=3.10, <4.0
- FastAPI 0.100+
- AWS account with Bedrock access enabled
