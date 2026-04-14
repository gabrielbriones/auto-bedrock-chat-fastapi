# Authentication

The plugin supports authenticating AI tool calls (HTTP requests to your API) using five methods. Credentials are provided once per WebSocket session and applied automatically to all outbound tool requests — the LLM never sees them.

---

## How It Works

```
Client ──auth message──► WebSocket Handler ──stores──► Credentials (per session)
                                                              │
                               AI triggers tool call         │
                                        │                    ▼
                                        └──► ToolManager applies auth headers
                                                              │
                                                              ▼
                                                    Your API receives request
                                                    with correct auth headers
```

---

## Enabling Authentication

```python
bedrock_chat = add_bedrock_chat(
    app,
    enable_tool_auth=True,
    # Optionally restrict which auth types are accepted:
    supported_auth_types=["bearer_token", "api_key"],
    # Optionally pre-select an auth type in the UI modal:
    default_auth_type="bearer_token",
)
```

---

## Authentication Methods

### 1. Bearer Token

```json
{
  "type": "auth",
  "auth_type": "bearer_token",
  "token": "your-bearer-token"
}
```

Adds `Authorization: Bearer <token>` to all tool call requests.

### 2. Basic Authentication

```json
{
  "type": "auth",
  "auth_type": "basic_auth",
  "username": "user@example.com",
  "password": "password123"
}
```

Adds `Authorization: Basic <base64(user:pass)>` to all requests.

### 3. API Key

```json
{
  "type": "auth",
  "auth_type": "api_key",
  "api_key": "sk-1234567890",
  "api_key_header": "X-API-Key"
}
```

Adds a custom header (default `X-API-Key`) to all requests. The header name is configurable per session.

### 4. OAuth2 Client Credentials

```json
{
  "type": "auth",
  "auth_type": "oauth2",
  "client_id": "your-client-id",
  "client_secret": "your-client-secret",
  "token_url": "https://auth.example.com/oauth/token",
  "scope": "api:read api:write"
}
```

The plugin automatically:

- Requests an access token from `token_url`
- Caches the token until it expires
- Refreshes automatically before expiry
- Adds `Authorization: Bearer <access-token>` to requests

### 5. Custom Headers

```json
{
  "type": "auth",
  "auth_type": "custom",
  "custom_headers": {
    "X-Custom-Auth": "custom-value",
    "X-Request-ID": "12345"
  },
  "metadata": {
    "client_type": "internal"
  }
}
```

Adds any custom headers to all requests. Useful for proprietary authentication schemes.

---

## Auth Verification Endpoint

Test credentials without starting a chat:

```http
POST /bedrock-chat/verify-auth
Content-Type: application/json

{
  "auth_type": "bearer_token",
  "token": "your-token",
  "test_url": "https://your-api.com/protected-endpoint"
}
```

The endpoint will attempt a test request and return success/failure, making it easy to validate credentials before a session starts.

---

## Python WebSocket Example

```python
import asyncio
import websockets
import json

async def chat_with_auth():
    uri = "ws://localhost:8000/bedrock-chat/ws"

    async with websockets.connect(uri) as ws:
        # Step 1: Authenticate
        await ws.send(json.dumps({
            "type": "auth",
            "auth_type": "bearer_token",
            "token": "your-api-token"
        }))
        auth_response = json.loads(await ws.recv())
        print(f"Auth: {auth_response['message']}")

        # Step 2: Chat — tool calls will include auth automatically
        await ws.send(json.dumps({
            "type": "chat",
            "message": "Show me all products under $50"
        }))

        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "ai_response":
                print(f"AI: {msg['message']}")
                break
            elif msg["type"] == "typing":
                print("...")

asyncio.run(chat_with_auth())
```

---

## JavaScript WebSocket Example

```javascript
const ws = new WebSocket("ws://localhost:8000/bedrock-chat/ws");

ws.onopen = () => {
  // Authenticate first
  ws.send(
    JSON.stringify({
      type: "auth",
      auth_type: "bearer_token",
      token: "your-api-token",
    }),
  );
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);

  if (msg.type === "auth_response" && msg.success) {
    // Now send a chat message
    ws.send(
      JSON.stringify({
        type: "chat",
        message: "Show me all products under $50",
      }),
    );
  }

  if (msg.type === "ai_response") {
    console.log("AI:", msg.message);
  }
};
```

---

## Supported Auth Types Reference

| Type           | Header Added                           | Use Case                   |
| -------------- | -------------------------------------- | -------------------------- |
| `bearer_token` | `Authorization: Bearer <token>`        | Modern APIs, JWTs          |
| `basic_auth`   | `Authorization: Basic <encoded>`       | Legacy systems             |
| `api_key`      | `<custom-header>: <key>`               | Simple API keys            |
| `oauth2`       | `Authorization: Bearer <access-token>` | Enterprise, token endpoint |
| `sso`          | `Authorization: Bearer <access-token>` | SSO via Identity Provider  |
| `custom`       | Any custom headers                     | Proprietary schemes        |

---

## See Also

- [SSO (Single Sign-On)](sso.md) — OAuth2/OIDC SSO for automatic authentication via Identity Providers
- [FastAPI Plugin Integration](fastapi-plugin.md)
- [WebSocket Client](websocket-client.md) — client script with all auth examples
- [Configuration](configuration.md) — `enable_tool_auth`, `supported_auth_types`
