# WebSocket Client

The `examples/websockets/interactive.py` script is a ready-to-use Python WebSocket client for the AI chat endpoint. It supports all authentication methods and can be used interactively or programmatically.

---

## Installation

```bash
pip install websockets
```

---

## Quick Start

### Interactive Chat (No Auth)

```bash
python examples/websockets/interactive.py
```

### Interactive Chat (Bearer Token)

```bash
python examples/websockets/interactive.py --auth bearer --token YOUR_TOKEN
```

### Demo Mode (Predefined Messages)

```bash
python examples/websockets/interactive.py --demo --auth bearer --token YOUR_TOKEN
```

---

## Authentication Examples

### Bearer Token

```bash
python interactive.py --url ws://localhost:8000/chat/ws \
  --auth bearer --token "sk-1234567890"
```

### API Key

```bash
python interactive.py --auth api_key \
  --api-key "your-api-key" \
  --api-key-header "Authorization"
```

### Basic Auth

```bash
python interactive.py --auth basic \
  --username user@example.com \
  --password "password123"
```

### OAuth2 Client Credentials

```bash
python interactive.py \
  --auth oauth2 \
  --client-id "your-client-id" \
  --client-secret "your-client-secret" \
  --token-url "https://oauth.example.com/token" \
  --scope "read write"
```

---

## Interactive Commands

Once connected, type any of these commands:

| Command     | Description                   |
| ----------- | ----------------------------- |
| `<message>` | Send a chat message           |
| `/history`  | Retrieve conversation history |
| `/clear`    | Clear conversation history    |
| `/ping`     | Test connection               |
| `/logout`   | Log out                       |
| `/quit`     | Disconnect and exit           |

---

## Programmatic Usage

### Basic Client

```python
import asyncio
from interactive import WebSocketConfig, WebSocketChatClient, AuthType

async def main():
    config = WebSocketConfig(
        endpoint="ws://localhost:8000/chat/ws",
        auth_type=AuthType.BEARER_TOKEN,
        token="your-token"
    )
    client = WebSocketChatClient(config)

    if await client.connect():
        await client.send_chat_message("Hello!")
        await asyncio.sleep(2)
        await client.disconnect()

asyncio.run(main())
```

### With Message Handlers

```python
def on_message(message):
    msg_type = message.get("type")
    if msg_type == "ai_response":
        print(f"Assistant: {message['message']}")
    elif msg_type == "typing":
        print("Assistant is typing...")

def on_error(error):
    print(f"Error: {error}")

def on_connected(session_id):
    print(f"Connected with session: {session_id}")

config = WebSocketConfig(
    endpoint="ws://localhost:8000/chat/ws",
    auth_type=AuthType.BEARER_TOKEN,
    token="your-token"
)

client = WebSocketChatClient(
    config,
    on_message=on_message,
    on_error=on_error,
    on_connected=on_connected
)
```

---

## WebSocket Message Protocol

### Messages You Send

**Chat message:**

```json
{ "type": "chat", "message": "Your question here" }
```

**Authenticate session:**

```json
{ "type": "auth", "auth_type": "bearer_token", "token": "your-token" }
{ "type": "auth", "auth_type": "api_key", "api_key": "key", "api_key_header": "X-API-Key" }
{ "type": "auth", "auth_type": "basic_auth", "username": "user", "password": "pass" }
{ "type": "auth", "auth_type": "oauth2", "client_id": "id", "client_secret": "secret", "token_url": "https://..." }
```

**Other commands:**

```json
{ "type": "ping" }
{ "type": "get_history" }
{ "type": "clear_history" }
{ "type": "logout" }
```

**Conversation management** (only when [conversation persistence](conversation-persistence) is enabled and the session is authenticated — see that page for the full protocol, auth model, and REST equivalent):

```json
{ "type": "conversation_list", "limit": 50, "offset": 0 }
{ "type": "conversation_new" }
{ "type": "conversation_load", "conversation_id": "b16d7f4e-…" }
{ "type": "conversation_delete", "conversation_id": "b16d7f4e-…" }
{ "type": "conversation_delete_all" }
{ "type": "conversation_rename", "conversation_id": "b16d7f4e-…", "title": "Renamed chat" }
```

### Messages You Receive

**Connection established:**

```json
{
  "type": "connection_established",
  "session_id": "session-abc123",
  "message": "Connected to AI assistant",
  "timestamp": "2026-01-01T10:00:00"
}
```

**AI response:**

```json
{
  "type": "ai_response",
  "message_id": "f1c2…",
  "message": "Here are the products I found...",
  "tool_calls": [...],
  "tool_results": [...],
  "timestamp": "2026-01-01T10:00:05",
  "metadata": {
    "message_id": "f1c2…",
    "model_id": "us.anthropic.claude-sonnet-5",
    "usage": { "input_tokens": 120, "output_tokens": 240 },
    "timestamp": "2026-01-01T10:00:05",
    "tool_call_rounds": 1,
    "total_tool_calls": 2,
    "preprocessing_applied": false,
    "input_tokens": 120,
    "output_tokens": 240,
    "kb_used": true,
    "kb_chunks": 3,
    "kb_sources": [
      { "title": "Doc A", "source": "kb://a", "url": "https://…", "score": 0.91 }
    ]
  }
}
```

#### `ai_response` metadata schema

The `metadata` object carries per-turn information about the model, token usage,
tool-calling, and any knowledge-base (KB) retrieval. Keys are assembled from the
final LLM message (`message_id`, `usage`, `timestamp`) and the WebSocket handler
(everything else).

**Always present:**

| Key                     | Type                     | Description                                                                                                                                                                                |
| ----------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `message_id`            | `string` (UUID)          | Stable per-turn ID, also present at the top level of the `ai_response` payload.                                                                                                            |
| `model_id`              | `string`                 | Active model ID. Written directly from server config and overwrites any value returned by the LLM.                                                                                         |
| `usage`                 | `object`                 | Token usage for the **final** LLM call only: `{ "input_tokens": int\|null, "output_tokens": int\|null }`. For multi-round totals use the top-level `input_tokens` / `output_tokens` below. |
| `timestamp`             | `string` (ISO 8601)      | Timestamp of the final LLM call.                                                                                                                                                           |
| `tool_call_rounds`      | `int` (default `0`)      | Number of tool-call loop iterations for this turn.                                                                                                                                         |
| `total_tool_calls`      | `int` (default `0`)      | Total individual tool calls executed across all rounds.                                                                                                                                    |
| `preprocessing_applied` | `bool` (default `false`) | Whether message preprocessing ran on the user input.                                                                                                                                       |

**Conditional — token totals (only when the model returns usage):**

These are accumulated across all tool-call rounds. Note they are distinct from the
nested `usage` dict, which reflects only the final LLM call.

| Key             | Type  | Condition                            | Description                                                      |
| --------------- | ----- | ------------------------------------ | ---------------------------------------------------------------- |
| `input_tokens`  | `int` | Present when the model returns usage | Total input tokens consumed across all LLM calls in this turn.   |
| `output_tokens` | `int` | Present when the model returns usage | Total output tokens generated across all LLM calls in this turn. |

**Conditional — KB results (only when the knowledge base is queried):**

| Key          | Type                   | Condition                        | Description                                                                                                 |
| ------------ | ---------------------- | -------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `kb_used`    | `bool` (always `true`) | Present only when KB was queried | Indicates the knowledge base was consulted.                                                                 |
| `kb_chunks`  | `int`                  | Present only when KB was queried | Number of KB chunks retrieved.                                                                              |
| `kb_sources` | array of `object`      | Present only when KB was queried | Each entry: `title` (`string\|null`), `source` (`string\|null`), `url` (`string\|null`), `score` (`float`). |

> **Server-side-only keys.** The graph state also tracks `fallback_model_used`
> (`bool`), `fallback_model` (`string`, present only when `fallback_model_used` is
> `true`), and `context_window_retries` (`int`). These are intentionally **not**
> forwarded in the `ai_response` payload — clients will never receive them.

**Conversation lifecycle** (only sent when [conversation persistence](conversation-persistence) is enabled — see that page for the full protocol):

```json
{ "type": "conversation_created", "conversation_id": "b16d7f4e-…", "timestamp": "..." }
{ "type": "conversation_list", "conversations": [ { "id": "...", "title": "...", "updated_at": "...", "message_count": 3 } ], "timestamp": "..." }
{ "type": "conversation_loaded", "conversation_id": "b16d7f4e-…", "conversation": { ... }, "messages": [ ... ], "timestamp": "..." }
{ "type": "conversation_deleted", "conversation_id": "b16d7f4e-…", "timestamp": "..." }
{ "type": "conversation_all_deleted", "deleted_count": 3, "timestamp": "..." }
{ "type": "conversation_titled", "conversation_id": "b16d7f4e-…", "title": "Configuring Okta SSO", "timestamp": "..." }
{ "type": "conversation_renamed", "conversation_id": "b16d7f4e-…", "title": "My renamed chat", "timestamp": "..." }
{ "type": "conversation_error", "code": "conversation_not_found", "message": "Conversation not found", "conversation_id": "b16d7f4e-…", "timestamp": "..." }
```

Note that `ai_response` also gains a `conversation_id` field (nullable) once
this feature is enabled — see [Conversation Persistence](conversation-persistence)
for the full lifecycle (lazy creation on first message, auto-titling, and the
`conversation_error` codes).

**Typing indicator:**

```json
{ "type": "typing", "message": "AI is thinking...", "timestamp": "..." }
```

**Auth response:**

```json
{ "type": "auth_response", "success": true, "message": "Authentication successful" }
```

**Error:**

```json
{ "type": "error", "message": "Error description" }
```

---

## Connection Options

| Option   | Default                       | Description                                               |
| -------- | ----------------------------- | --------------------------------------------------------- |
| `--url`  | `ws://localhost:8000/chat/ws` | WebSocket endpoint                                        |
| `--auth` | `none`                        | Auth type: `none`, `bearer`, `api_key`, `basic`, `oauth2` |
| `--demo` | `false`                       | Run demo mode (non-interactive)                           |

---

## Environment Variables

```bash
export AUTOCHAT_WS_ENDPOINT="ws://localhost:8000/chat/ws"
export AUTOCHAT_AUTH_TYPE="bearer_token"
export AUTOCHAT_AUTH_TOKEN="your-token"
```

---

## See Also

- [Authentication](authentication.md) — auth types and credential flow
- [Chat UI](chat-ui.md) — built-in web interface
- [Conversation Persistence](conversation-persistence.md) — per-user named conversations, sidebar, REST API
- `examples/websockets/interactive.py` — full source code
