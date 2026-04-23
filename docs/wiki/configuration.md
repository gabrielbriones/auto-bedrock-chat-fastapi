# Configuration

All plugin settings are managed through `ChatConfig` (a Pydantic `BaseSettings` model). Values can be set via `.env` file, environment variables, or passed directly in code.

---

## Quick Start

1. Copy the example env file and edit it:

```bash
cp .env.example .env
```

1. In your app, most settings load automatically from `.env`. List-type fields must be set in code:

```python
from fastapi import FastAPI
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI()

bedrock_chat = add_bedrock_chat(
    app,
    allowed_paths=["/api/products", "/api/users"],   # must be in code
    excluded_paths=["/docs", "/admin"]               # must be in code
)
```

> **Why code for lists?** Pydantic v2 does not support list parsing from `.env` files.

---

## Full Configuration Reference

### AWS / Bedrock

| Env Variable            | Default                                        | Description                      |
| ----------------------- | ---------------------------------------------- | -------------------------------- |
| `AWS_REGION`            | `us-east-1`                                    | AWS region                       |
| `AWS_ACCESS_KEY_ID`     | —                                              | AWS access key (or use IAM role) |
| `AWS_SECRET_ACCESS_KEY` | —                                              | AWS secret key                   |
| `BEDROCK_MODEL_ID`      | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Model identifier                 |
| `BEDROCK_TEMPERATURE`   | `0.7`                                          | Response randomness (0.0–1.0)    |
| `BEDROCK_MAX_TOKENS`    | `4096`                                         | Max tokens in model response     |
| `BEDROCK_TOP_P`         | `0.9`                                          | Top-p sampling parameter         |
| `BEDROCK_SYSTEM_PROMPT` | `None`                                         | Custom system prompt             |
| `BEDROCK_TIMEOUT`       | `30`                                           | Bedrock API timeout (seconds)    |

### Endpoints

| Env Variable                 | Default            | Description             |
| ---------------------------- | ------------------ | ----------------------- |
| `BEDROCK_CHAT_ENDPOINT`      | `/bedrock-chat`    | REST API base path      |
| `BEDROCK_WEBSOCKET_ENDPOINT` | `/bedrock-chat/ws` | WebSocket endpoint      |
| `BEDROCK_UI_ENDPOINT`        | `/bedrock-chat/ui` | Chat UI path            |
| `BEDROCK_ENABLE_UI`          | `true`             | Enable built-in chat UI |

### Tool Calling

| Env Variable                   | Default | Description                        |
| ------------------------------ | ------- | ---------------------------------- |
| `BEDROCK_MAX_TOOL_CALLS`       | `10`    | Max tool calls per turn            |
| `BEDROCK_MAX_TOOL_CALL_ROUNDS` | `10`    | Max recursive tool call rounds     |
| `BEDROCK_OPENAPI_SPEC_FILE`    | `None`  | Path to external OpenAPI spec file |

### Session Management

| Env Variable                        | Default | Description                                |
| ----------------------------------- | ------- | ------------------------------------------ |
| `BEDROCK_MAX_SESSIONS`              | `1000`  | Max concurrent sessions                    |
| `BEDROCK_SESSION_TIMEOUT`           | `3600`  | Session timeout (seconds)                  |
| `BEDROCK_MAX_CONVERSATION_MESSAGES` | `20`    | Max messages in history (count-based trim) |

### Error Handling & Retries

| Env Variable                   | Default | Description                   |
| ------------------------------ | ------- | ----------------------------- |
| `BEDROCK_MAX_RETRIES`          | `3`     | Max retry attempts            |
| `BEDROCK_RETRY_DELAY`          | `1.0`   | Initial retry delay (seconds) |
| `BEDROCK_EXPONENTIAL_BACKOFF`  | `true`  | Use exponential backoff       |
| `BEDROCK_GRACEFUL_DEGRADATION` | `true`  | Degrade gracefully on errors  |

### Token Budget / Truncation

| Env Variable                             | Default  | Description                               |
| ---------------------------------------- | -------- | ----------------------------------------- |
| `BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD`    | `500000` | Chars that trigger per-message truncation |
| `BEDROCK_SINGLE_MSG_TRUNCATION_TARGET`   | `425000` | Target chars after per-message truncation |
| `BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD` | `650000` | Total history chars that trigger Stage 2  |
| `BEDROCK_HISTORY_MSG_LENGTH_THRESHOLD`   | `100000` | Per-message threshold in Stage 2          |
| `BEDROCK_HISTORY_MSG_TRUNCATION_TARGET`  | `85000`  | Per-message target in Stage 2             |
| `BEDROCK_MAX_TRUNCATION_RECURSION`       | `3`      | Max recursion for safety-net halving      |

### AI Summarization

| Env Variable                      | Default | Description                                          |
| --------------------------------- | ------- | ---------------------------------------------------- |
| `BEDROCK_ENABLE_AI_SUMMARIZATION` | `false` | Enable LLM-based summarization instead of truncation |

### Authentication

| Env Variable                   | Default   | Description                                  |
| ------------------------------ | --------- | -------------------------------------------- |
| `BEDROCK_ENABLE_TOOL_AUTH`     | `false`   | Enable authentication for tool call requests |
| `BEDROCK_SUPPORTED_AUTH_TYPES` | all types | Auth types accepted (list, set in code)      |
| `BEDROCK_DEFAULT_AUTH_TYPE`    | _(none)_  | Pre-select this auth type in the UI modal    |

### Logging

| Env Variable        | Default | Description                             |
| ------------------- | ------- | --------------------------------------- |
| `BEDROCK_LOG_LEVEL` | `INFO`  | Log level (DEBUG, INFO, WARNING, ERROR) |

### Knowledge Base / RAG

| Env Variable              | Default                      | Description                                               |
| ------------------------- | ---------------------------- | --------------------------------------------------------- |
| `ENABLE_RAG`              | `false`                      | Enable Retrieval-Augmented Generation with knowledge base |
| `KB_SOURCES_CONFIG`       | `kb_sources.yaml`            | Path to YAML file defining KB content sources             |
| `KB_POPULATE_ON_STARTUP`  | `false`                      | Auto-populate KB on startup (dev only)                    |
| `KB_ALLOW_EMPTY`          | `false`                      | Allow app to start with empty KB                          |
| `KB_EMBEDDING_MODEL`      | `amazon.titan-embed-text-v1` | Bedrock model for generating embeddings                   |
| `KB_CHUNK_SIZE`           | `512`                        | Token size for text chunks                                |
| `KB_CHUNK_OVERLAP`        | `100`                        | Token overlap between chunks                              |
| `KB_TOP_K_RESULTS`        | `5`                          | Number of top chunks to retrieve per query                |
| `KB_SIMILARITY_THRESHOLD` | `0.0`                        | Minimum similarity score for results                      |
| `KB_SEMANTIC_WEIGHT`      | `0.7`                        | Weight for semantic (embedding) score in hybrid search    |
| `KB_KEYWORD_WEIGHT`       | `0.3`                        | Weight for keyword (FTS) score in hybrid search           |

### KB Storage Backend

| Env Variable                      | Default                  | Description                                       |
| --------------------------------- | ------------------------ | ------------------------------------------------- |
| `BEDROCK_KB_STORAGE_TYPE`         | `sqlite`                 | Storage backend: `sqlite` or `pgvector`           |
| `KB_DATABASE_PATH`                | `data/knowledge_base.db` | SQLite database file path (sqlite backend only)   |
| `BEDROCK_KB_POSTGRES_URL`         | _(none)_                 | PostgreSQL connection URL (pgvector backend only) |
| `BEDROCK_KB_POSTGRES_POOL_SIZE`   | `5`                      | Connection pool size for PostgreSQL               |
| `BEDROCK_KB_EMBEDDING_DIMENSIONS` | `1536`                   | Embedding vector dimensions (must match model)    |

> See [RAG Feature](rag-feature) for storage backend details, Docker Compose setup, and production recommendations.

---

## Code-Only Settings

These must be passed directly to `add_bedrock_chat()`:

| Parameter              | Type        | Description                        |
| ---------------------- | ----------- | ---------------------------------- |
| `allowed_paths`        | `List[str]` | API paths the AI can call as tools |
| `excluded_paths`       | `List[str]` | API paths to hide from AI          |
| `cors_origins`         | `List[str]` | CORS allowed origins               |
| `supported_auth_types` | `List[str]` | Auth types allowed                 |

---

## Overriding .env Values in Code

```python
bedrock_chat = add_bedrock_chat(
    app,
    model_id="us.anthropic.claude-3-5-sonnet-20241022-v2:0",  # overrides BEDROCK_MODEL_ID
    temperature=0.3,
    max_tokens=8192,
    system_prompt="You are a helpful customer support assistant.",
    allowed_paths=["/api/products", "/api/orders"],
    enable_ui=True
)
```

---

## Environment Files

The plugin automatically selects the `.env` file:

- Under `pytest`: uses `.env.test`
- Otherwise: uses `.env`
