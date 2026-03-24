# Chat UI

The plugin includes a built-in web chat interface. Enable it with `enable_ui=True` (the default) and access it at `/bedrock-chat/ui`.

---

## Accessing the Chat UI

```python
bedrock_chat = add_bedrock_chat(
    app,
    enable_ui=True,           # default: True
    ui_endpoint="/bedrock-chat/ui"  # default
)
```

Open your browser to `http://localhost:8000/bedrock-chat/ui`.

---

## UI Features

- **Real-time chat** — messages stream via WebSocket
- **Typing indicator** — shows when the AI is generating a response
- **Tool call visibility** — displays which API endpoints the AI is calling and the results
- **Conversation history** — persists within the session
- **Authentication modal** — UI for providing credentials (bearer token, API key, basic auth, OAuth2)
- **Session management** — clear history, ping, logout
- **Preset prompt buttons** — one-click buttons that send pre-defined prompts; see [Preset Prompts](preset-prompts.md)

---

## Endpoints Registered by the Plugin

| Method | Path                            | Description                        |
| ------ | ------------------------------- | ---------------------------------- |
| `GET`  | `/bedrock-chat/ui`              | Serves the chat HTML page          |
| `WS`   | `/bedrock-chat/ws`              | WebSocket chat connection          |
| `POST` | `/bedrock-chat/chat`            | REST chat (non-streaming)          |
| `GET`  | `/bedrock-chat/health`          | Plugin health check                |
| `POST` | `/bedrock-chat/verify-auth`     | Test auth credentials              |
| `POST` | `/bedrock-chat/semantic-search` | RAG semantic search (when enabled) |

All paths are configurable. Example with custom paths:

```python
bedrock_chat = add_bedrock_chat(
    app,
    chat_endpoint="/ai",
    websocket_endpoint="/ai/ws",
    ui_endpoint="/ai/chat"
)
```

---

## UI Authentication

The UI shows an **authentication modal** when `enable_tool_auth=True`. Users can log in before chatting.

To use auth in the UI:

1. Enable tool auth in the plugin: `add_bedrock_chat(app, enable_tool_auth=True)`
2. Open the chat UI and click the **Login** button
3. Select auth type and enter credentials
4. All tool calls made by the AI will automatically include those credentials

The auth credentials are sent once as a WebSocket message:

```json
{
  "type": "auth",
  "auth_type": "bearer_token",
  "token": "your-token-here"
}
```

See [Authentication](authentication.md) for all supported auth types.

---

## UI Customization

The UI templates are in `auto_bedrock_chat_fastapi/templates/`:

- `chat.html` — main chat interface
- `auth_modal.html` — authentication modal

Static assets are in `auto_bedrock_chat_fastapi/static/`:

- `styles.css` — UI styles
- `chat-client.js` — WebSocket client logic
- `auth.js` — authentication handling
- `app.js` — application entry point

To customize the UI, override the templates or serve your own frontend using the WebSocket endpoint directly. See [WebSocket Client](websocket-client.md) for building a custom client.

---

## Disabling the UI

If you only need the WebSocket/REST API (e.g., you have a custom frontend):

```python
bedrock_chat = add_bedrock_chat(app, enable_ui=False)
```

Or via `.env`:

```
BEDROCK_ENABLE_UI=false
```

---

## See Also

- [WebSocket Client](websocket-client.md) — build a custom client
- [Authentication](authentication.md) — securing tool calls
- [Configuration](configuration.md) — endpoint path settings
