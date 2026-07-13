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
from autolangchat import add_autolangchat

app = FastAPI()

autolangchat_plugin = add_autolangchat(
    app,
    allowed_paths=["/api/products", "/api/users"],   # must be in code
    excluded_paths=["/docs", "/admin"]               # must be in code
)
```

> **Why code for lists?** Pydantic v2 does not support list parsing from `.env` files.

---

## Full Configuration Reference

### AWS / Bedrock

| Env Variable             | Default                          | Description                      |
| ------------------------ | -------------------------------- | -------------------------------- |
| `AWS_REGION`             | `us-east-1`                      | AWS region                       |
| `AWS_ACCESS_KEY_ID`      | —                                | AWS access key (or use IAM role) |
| `AWS_SECRET_ACCESS_KEY`  | —                                | AWS secret key                   |
| `AUTOCHAT_MODEL_ID`      | `us.anthropic.claude-sonnet-5` | Model identifier                 |
| `AUTOCHAT_TEMPERATURE`   | `0.7`                            | Response randomness (0.0–1.0)    |
| `AUTOCHAT_MAX_TOKENS`    | `4096`                           | Max tokens in model response     |
| `AUTOCHAT_TOP_P`         | `0.9`                            | Top-p sampling parameter         |
| `AUTOCHAT_SYSTEM_PROMPT` | `None`                           | Custom system prompt             |

### Endpoints

| Env Variable                              | Default    | Description                                             |
| ----------------------------------------- | ---------- | ------------------------------------------------------- |
| `AUTOCHAT_CHAT_ENDPOINT`                  | `/chat`    | Chat route base path                                    |
| `AUTOCHAT_WEBSOCKET_ENDPOINT`             | `/chat/ws` | WebSocket endpoint                                      |
| `AUTOCHAT_UI_ENDPOINT`                    | `/chat/ui` | Chat UI path                                            |
| `AUTOCHAT_ENABLE_UI`                      | `true`     | Enable built-in chat UI                                 |
| `AUTOCHAT_UI_LOCK_INPUT_WHILE_RESPONDING` | `true`     | Disable chat input while waiting for assistant response |

### Tool Calling

| Env Variable                 | Default       | Description                                                |
| ---------------------------- | ------------- | ---------------------------------------------------------- |
| `AUTOCHAT_MAX_TOOL_CALLS`    | _(unlimited)_ | Max tool calls per turn (omit or leave unset for no limit) |
| `AUTOCHAT_OPENAPI_SPEC_FILE` | `None`        | Path to external OpenAPI spec file                         |

### Session Management

| Env Variable                         | Default | Description                                |
| ------------------------------------ | ------- | ------------------------------------------ |
| `AUTOCHAT_MAX_SESSIONS`              | `1000`  | Max concurrent sessions                    |
| `AUTOCHAT_SESSION_TIMEOUT`           | `3600`  | Session timeout (seconds)                  |
| `AUTOCHAT_MAX_CONVERSATION_MESSAGES` | `20`    | Max messages in history (count-based trim) |

### Error Handling & Retries

| Env Variable                   | Default | Description                   |
| ------------------------------ | ------- | ----------------------------- |
| `AUTOCHAT_MAX_RETRIES`         | `3`     | Max retry attempts            |
| `AUTOCHAT_RETRY_DELAY`         | `1.0`   | Initial retry delay (seconds) |
| `AUTOCHAT_EXPONENTIAL_BACKOFF` | `true`  | Use exponential backoff       |

### Token Budget / Truncation

| Env Variable                              | Default  | Description                               |
| ----------------------------------------- | -------- | ----------------------------------------- |
| `AUTOCHAT_SINGLE_MSG_LENGTH_THRESHOLD`    | `500000` | Chars that trigger per-message truncation |
| `AUTOCHAT_SINGLE_MSG_TRUNCATION_TARGET`   | `425000` | Target chars after per-message truncation |
| `AUTOCHAT_HISTORY_TOTAL_LENGTH_THRESHOLD` | `650000` | Total history chars that trigger Stage 2  |
| `AUTOCHAT_HISTORY_MSG_LENGTH_THRESHOLD`   | `100000` | Per-message threshold in Stage 2          |
| `AUTOCHAT_HISTORY_MSG_TRUNCATION_TARGET`  | `85000`  | Per-message target in Stage 2             |
| `AUTOCHAT_MAX_TRUNCATION_RECURSION`       | `3`      | Max recursion for safety-net halving      |

### AI Summarization

| Env Variable                       | Default | Description                                          |
| ---------------------------------- | ------- | ---------------------------------------------------- |
| `AUTOCHAT_ENABLE_AI_SUMMARIZATION` | `false` | Enable LLM-based summarization instead of truncation |

### Authentication

| Env Variable                    | Default   | Description                                  |
| ------------------------------- | --------- | -------------------------------------------- |
| `AUTOCHAT_ENABLE_TOOL_AUTH`     | `false`   | Enable authentication for tool call requests |
| `AUTOCHAT_SUPPORTED_AUTH_TYPES` | all types | Auth types accepted (list, set in code)      |
| `AUTOCHAT_DEFAULT_AUTH_TYPE`    | _(none)_  | Pre-select this auth type in the UI modal    |

### Logging

| Env Variable         | Default | Description                             |
| -------------------- | ------- | --------------------------------------- |
| `AUTOCHAT_LOG_LEVEL` | `INFO`  | Log level (DEBUG, INFO, WARNING, ERROR) |

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

| Env Variable                       | Default                  | Description                                       |
| ---------------------------------- | ------------------------ | ------------------------------------------------- |
| `AUTOCHAT_KB_STORAGE_TYPE`         | `sqlite`                 | Storage backend: `sqlite` or `pgvector`           |
| `KB_DATABASE_PATH`                 | `data/knowledge_base.db` | SQLite database file path (sqlite backend only)   |
| `AUTOCHAT_KB_POSTGRES_URL`         | _(none)_                 | PostgreSQL connection URL (pgvector backend only) |
| `AUTOCHAT_KB_POSTGRES_POOL_SIZE`   | `5`                      | Connection pool size for PostgreSQL               |
| `AUTOCHAT_KB_EMBEDDING_DIMENSIONS` | `1536`                   | Embedding vector dimensions (must match model)    |

> See [RAG Feature](rag-feature) for storage backend details, Docker Compose setup, and production recommendations.

---

## Code-Only Settings

These must be passed directly to `add_autolangchat()`:

| Parameter              | Type        | Description                        |
| ---------------------- | ----------- | ---------------------------------- |
| `allowed_paths`        | `List[str]` | API paths the AI can call as tools |
| `excluded_paths`       | `List[str]` | API paths to hide from AI          |
| `cors_origins`         | `List[str]` | CORS allowed origins               |
| `supported_auth_types` | `List[str]` | Auth types allowed                 |

---

## Overriding .env Values in Code

```python
autolangchat_plugin = add_autolangchat(
    app,
    model_id="us.anthropic.claude-sonnet-5",  # overrides AUTOCHAT_MODEL_ID
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
