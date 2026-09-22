# Chat UI

The plugin includes a built-in React web chat interface. Enable it with `enable_ui=True` (the default) and access it at `/chat/ui`.

---

## Accessing the Chat UI

```python
autolangchat_plugin = add_autolangchat(
    app,
    enable_ui=True,           # default: True
    ui_endpoint="/chat/ui",   # default; the React SPA is served here
)
```

Open your browser to `http://localhost:8000/chat/ui`. The build directory is
`AUTOCHAT_UI_DIST_DIR` (defaults to `<repo>/frontend/dist`). Build it with
`cd frontend && npm ci && npm run build`; the `Dockerfile` does this automatically.

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

| Method | Path                     | Description                                  |
| ------ | ------------------------ | --------------------------------------------- |
| `GET`  | `/chat/ui`, `/chat/ui/*` | Serves the React SPA (deep links fall back to `index.html`) |
| `GET`  | `/chat/config`           | JSON bootstrap payload read by the SPA       |
| `WS`   | `/chat/ws`               | WebSocket chat connection                    |
| `GET`  | `/chat/health`           | Plugin health check                          |
| `GET`  | `/chat/stats`            | Chat statistics                              |
| `GET`  | `/chat/tools`            | Tool metadata and schema stats               |
| `POST` | `/chat/knowledge/search` | Hybrid KB search endpoint (when RAG enabled) |

All paths are configurable. Example with custom paths:

```python
autolangchat_plugin = add_autolangchat(
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

1. Enable tool auth in the plugin: `add_autolangchat(app, enable_tool_auth=True)`
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

The UI is a React + TypeScript + Vite SPA in [`frontend/`](../../frontend/README.md). It reads everything it needs
from `GET {chat_endpoint}/config` at start-up, so titles, preset prompts, auth types and feature flags are all
controlled through the plugin configuration rather than by editing the UI.

To change the UI itself, edit the sources under `frontend/src/` and rebuild. To replace it entirely, serve your own
frontend against the WebSocket endpoint directly. See [WebSocket Client](websocket-client.md) for building a custom client.

---

## Input Lock While Responding

By default the chat input and Send button are disabled from the moment a user sends a message until the assistant's response is fully received. This prevents queuing additional messages while a reply is in flight.

To allow users to send messages at any time (original behavior):

```
AUTOCHAT_UI_LOCK_INPUT_WHILE_RESPONDING=false
```

When locked, the composer shows a "Waiting for response..." state and is disabled. The input is automatically re-enabled on `ai_response`, `error`, `auth_expired`, or WebSocket reconnect.

---

## Disabling the UI

If you only need the WebSocket/REST API (e.g., you have a custom frontend):

```python
autolangchat_plugin = add_autolangchat(app, enable_ui=False)
```

Or via `.env`:

```
AUTOCHAT_ENABLE_UI=false
```

---

## See Also

- [WebSocket Client](websocket-client.md) — build a custom client
- [Authentication](authentication.md) — securing tool calls
- [Configuration](configuration.md) — endpoint path settings
- [Admin Dashboard](dashboard.md) — the Dashboard button shown to admins
