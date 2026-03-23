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
python interactive.py --url ws://localhost:8000/bedrock-chat/ws \
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
        endpoint="ws://localhost:8000/bedrock-chat/ws",
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
    endpoint="ws://localhost:8000/bedrock-chat/ws",
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
  "message": "Here are the products I found...",
  "tool_calls": [...],
  "tool_results": [...],
  "timestamp": "2026-01-01T10:00:05"
}
```

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

| Option   | Default                               | Description                                               |
| -------- | ------------------------------------- | --------------------------------------------------------- |
| `--url`  | `ws://localhost:8000/bedrock-chat/ws` | WebSocket endpoint                                        |
| `--auth` | `none`                                | Auth type: `none`, `bearer`, `api_key`, `basic`, `oauth2` |
| `--demo` | `false`                               | Run demo mode (non-interactive)                           |

---

## Environment Variables

```bash
export BEDROCK_WS_ENDPOINT="ws://localhost:8000/bedrock-chat/ws"
export BEDROCK_AUTH_TYPE="bearer_token"
export BEDROCK_AUTH_TOKEN="your-token"
```

---

## See Also

- [Authentication](authentication.md) — auth types and credential flow
- [Chat UI](chat-ui.md) — built-in web interface
- `examples/websockets/interactive.py` — full source code
