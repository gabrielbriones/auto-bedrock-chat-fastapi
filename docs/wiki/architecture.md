# Architecture

**autolangchat** is a FastAPI plugin that wires together an Amazon Bedrock LLM, a WebSocket transport layer, automatic tool generation from your OpenAPI spec, LangGraph-based state management, and optional RAG (knowledge base) capabilities.

---

## Component Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Your FastAPI App                            │
│                                                                     │
│  add_autolangchat(app) ──► registers routes & mounts static UI      │
└──────────────────────────────────────┬──────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      websocket_handler.py                           │
│                       (Transport Layer)                             │
│  • Accept WebSocket connections                                     │
│  • Route message types (chat, auth, ping, history)                  │
│  • Restore checkpointed conversation state                          │
│  • Inject auth context + runtime callbacks into LangGraph           │
│  • Stream AI responses back to client                               │
└──────────────────────┬───────────────────────────────────────────────┘
                       │ ainvoke()
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        LangGraph StateGraph                         │
│                     (Orchestration Layer)                           │
│  • rag node        → optional KB retrieval + system prompt update   │
│  • preprocess node → token budget enforcement                       │
│  • llm node        → ChatBedrockConverse call + streaming           │
│  • tools node      → OpenAPI-backed tool execution loop             │
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
| `plugin.py`               | Entry point — `add_autolangchat()` and `create_fastapi_with_autolangchat()` |
| `config.py`               | `ChatConfig` — all settings via Pydantic + `.env`                           |
| `defaults.py`             | Centralized default values (thresholds, timeouts, ratios)                   |
| `websocket_handler.py`    | WebSocket connection management, message routing                            |
| `graph/`                  | LangGraph state machine, nodes, routing, and checkpointing                  |
| `tool_manager.py`         | Converts OpenAPI spec to AI tools; executes tool calls                      |
| `auth_handler.py`         | Authentication types and credential management                              |
| `session_manager.py`      | In-memory session lifecycle management                                      |
| `message_preprocessor.py` | Two-stage token budget management (truncation + summarization)              |
| `rag/`                    | Web and local file crawler, chunking, and embedding generation              |
| `db/`                     | SQLite / Postgres stores for KB + feedback                                  |

---

## Request Flow

1. **Client** opens a WebSocket to `/chat/ws` (or configured path).
2. **WebSocket Handler** authenticates the session (optional) and awaits messages.
3. **User sends** a chat message → handler restores checkpointed history and appends the current user turn.
4. **LangGraph** optionally performs RAG retrieval and enriches the system prompt.
5. **preprocess node** ensures the message history fits within token budget.
6. **llm node** calls `ChatBedrockConverse` with messages + tools.
7. **If the model returns tool calls** → `ToolManager` executes HTTP requests to your API, results are appended, and the loop repeats (up to `max_tool_call_rounds`; individual-turn call count is unlimited by default but can be capped via `max_tool_calls`).
8. **Final response** is streamed back to the client via WebSocket.

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
