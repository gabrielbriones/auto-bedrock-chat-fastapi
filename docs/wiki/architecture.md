# Architecture

**auto-bedrock-chat-fastapi** is a FastAPI plugin that wires together an Amazon Bedrock LLM, a WebSocket transport layer, automatic tool generation from your OpenAPI spec, session management, and optional RAG (knowledge base) capabilities.

---

## Component Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Your FastAPI App                            │
│                                                                     │
│  add_bedrock_chat(app) ──► registers routes & mounts static UI      │
└──────────────────────────────────────┬──────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      websocket_handler.py                           │
│                       (Transport Layer)                             │
│  • Accept WebSocket connections                                     │
│  • Route message types (chat, auth, ping, history)                  │
│  • Inject auth credentials + RAG context into ChatManager          │
│  • Stream AI responses back to client                               │
└──────────────────────┬───────────────────────────────────────────────┘
                       │ chat_completion()
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        chat_manager.py                              │
│                      (Orchestration Layer)                          │
│  • Drives the LLM conversation loop                                 │
│  • Calls MessagePreprocessor before each LLM call                  │
│  • Handles recursive tool call rounds                               │
│  • Returns full updated history                                     │
└───────────┬──────────────────────────────────┬──────────────────────┘
            │                                  │
            ▼                                  ▼
┌───────────────────────┐         ┌────────────────────────────────────┐
│   bedrock_client.py   │         │      message_preprocessor.py       │
│   (LLM Transport)    │         │     (Token Budget Management)      │
│                       │         │                                    │
│ • Calls Bedrock API   │         │ • Stage 1: per-message truncation  │
│ • Parses responses    │         │ • Stage 2: history total truncation│
│ • Retries + backoff   │         │ • AI summarization (opt-in)        │
│ • Generates embeddings│         └────────────────────────────────────┘
└───────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         tool_manager.py                             │
│  • ToolsGenerator: OpenAPI spec → AI tool descriptions             │
│  • Executes tool calls (HTTP requests to your API)                 │
│  • Applies auth credentials to outbound requests                   │
└──────────────────────────────────────────────────────────────────────┘

Optional RAG Components:
┌────────────────────────┐  ┌─────────────────────┐  ┌────────────────┐
│  content_crawler.py    │  │ embedding_pipeline  │  │  vector_db.py  │
│  Web / local crawler   │→ │  Text → embeddings  │→ │  SQLite-vec    │
└────────────────────────┘  └─────────────────────┘  └────────────────┘
```

---

## Key Modules

| Module                    | Role                                                                        |
| ------------------------- | --------------------------------------------------------------------------- |
| `plugin.py`               | Entry point — `add_bedrock_chat()` and `create_fastapi_with_bedrock_chat()` |
| `config.py`               | `ChatConfig` — all settings via Pydantic + `.env`                           |
| `defaults.py`             | Centralized default values (thresholds, timeouts, ratios)                   |
| `websocket_handler.py`    | WebSocket connection management, message routing                            |
| `chat_manager.py`         | LLM conversation orchestration loop                                         |
| `bedrock_client.py`       | Amazon Bedrock API client with retries and model parsers                    |
| `tool_manager.py`         | Converts OpenAPI spec to AI tools; executes tool calls                      |
| `auth_handler.py`         | Authentication types and credential management                              |
| `session_manager.py`      | In-memory session lifecycle management                                      |
| `message_preprocessor.py` | Two-stage token budget management (truncation + summarization)              |
| `content_crawler.py`      | Web and local file crawler for RAG knowledge base                           |
| `embedding_pipeline.py`   | Text chunking and Bedrock Titan embedding generation                        |
| `vector_db.py`            | SQLite-vec vector store for semantic search                                 |
| `parsers/`                | Per-model request/response parsers (Claude, GPT, Llama)                     |

---

## Request Flow

1. **Client** opens a WebSocket to `/bedrock-chat/ws` (or configured path).
2. **WebSocket Handler** authenticates the session (optional) and awaits messages.
3. **User sends** a chat message → handler optionally performs RAG retrieval and enriches context.
4. **ChatManager** calls `MessagePreprocessor` to ensure the message history fits within token budget.
5. **ChatManager** calls **BedrockClient** with messages + tools.
6. **If the model returns tool calls** → `ToolManager` executes HTTP requests to your API, results are appended, and the loop repeats (up to `MAX_TOOL_CALL_ROUNDS`).
7. **Final response** is streamed back to the client via WebSocket.

---

## Supported Models

| Model Family          | Example Model ID                               | Parser                   |
| --------------------- | ---------------------------------------------- | ------------------------ |
| Claude 4.x            | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | `ClaudeParser`           |
| Claude 3.x            | `us.anthropic.claude-3-5-sonnet-20241022-v2:0` | `ClaudeParser`           |
| GPT OSS (via Bedrock) | `openai.gpt-oss-*`                             | `GPTParser`              |
| Llama                 | `meta.llama3-8b-instruct-v1:0`                 | `LlamaParser`            |
| Titan                 | `amazon.titan-text-express-v1`                 | `ClaudeParser` (default) |

Model routing is based on the `model_id` prefix. Unknown models fall back to `ClaudeParser`. Model-specific parsing is handled by the `parsers/` subpackage.
