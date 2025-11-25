# Tool Call Authentication Quick Start

## Overview

The authentication system automatically manages credentials for API tool calls. Your users can provide credentials once via WebSocket, and they'll be automatically applied to all subsequent API calls.

## Quick Start (5 minutes)

### 1. Enable Authentication

```python
from auto_bedrock_chat_fastapi import add_bedrock_chat
from fastapi import FastAPI

app = FastAPI()

# Enable authentication support
bedrock_chat = add_bedrock_chat(
    app,
    enable_tool_auth=True,
)
```

### 2. Client Sends Credentials

```javascript
const ws = new WebSocket("ws://localhost:8000/bedrock-chat/ws");

ws.onopen = () => {
  // Send authentication
  ws.send(
    JSON.stringify({
      type: "auth",
      auth_type: "bearer_token",
      token: "your-api-token",
    }),
  );
};
```

### 3. System Automatically Applies Auth

```
User Message: "Get my data"
     ↓
LLM: "I'll call /api/data"
     ↓
Executor: Applies bearer token to request
     ↓
GET /api/data
Authorization: Bearer your-api-token
     ↓
API Response: Data returned
     ↓
LLM: "Here's your data: ..."
```

## Supported Authentication Methods

| Method           | Use Case            | Example                                     |
| ---------------- | ------------------- | ------------------------------------------- |
| **Bearer Token** | Modern APIs, JWTs   | `Authorization: Bearer token123`            |
| **Basic Auth**   | Legacy APIs         | `Authorization: Basic dXNlcjpwYXNz`         |
| **API Key**      | Simplified APIs     | `X-API-Key: sk-123456`                      |
| **OAuth2**       | Enterprise APIs     | Client credentials flow with token endpoint |
| **Custom**       | Proprietary schemes | Any custom headers                          |

## Configuration

### Environment Variables

```bash
# Enable tool authentication (default: true)
BEDROCK_ENABLE_TOOL_AUTH=true

# Supported auth types (comma-separated)
BEDROCK_SUPPORTED_AUTH_TYPES=bearer_token,basic_auth,api_key,oauth2,custom

# Require auth before tool calls (default: false)
BEDROCK_REQUIRE_TOOL_AUTH=false

# OAuth2 token cache duration in seconds (default: 3600)
BEDROCK_AUTH_TOKEN_CACHE_TTL=3600
```

### Programmatic Configuration

```python
bedrock_chat = add_bedrock_chat(
    app,
    enable_tool_auth=True,
    supported_auth_types=["bearer_token", "oauth2"],
    require_tool_auth=False,
    auth_token_cache_ttl=3600,
)
```

## Authentication Messages

### Bearer Token

```json
{
  "type": "auth",
  "auth_type": "bearer_token",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### Basic Authentication

```json
{
  "type": "auth",
  "auth_type": "basic_auth",
  "username": "user@example.com",
  "password": "password123"
}
```

### API Key

```json
{
  "type": "auth",
  "auth_type": "api_key",
  "api_key": "sk-1234567890abcdef",
  "api_key_header": "X-API-Key"
}
```

### OAuth2 Client Credentials

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

### Custom Headers

```json
{
  "type": "auth",
  "auth_type": "custom",
  "custom_headers": {
    "X-Custom-Auth": "custom-value",
    "X-Request-ID": "12345"
  }
}
```

## Full JavaScript Example

```javascript
class AuthenticatedChat {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.setupHandlers();
  }

  setupHandlers() {
    this.ws.onopen = () => console.log("Connected");
    this.ws.onmessage = (e) => this.onMessage(JSON.parse(e.data));
  }

  authenticate(authType, credentials) {
    this.ws.send(
      JSON.stringify({
        type: "auth",
        auth_type: authType,
        ...credentials,
      }),
    );
  }

  sendMessage(text) {
    this.ws.send(
      JSON.stringify({
        type: "chat",
        message: text,
      }),
    );
  }

  onMessage(msg) {
    if (msg.type === "auth_configured") {
      console.log("✅ Authenticated with " + msg.auth_type);
      this.sendMessage("What can you help me with?");
    } else if (msg.type === "ai_response") {
      console.log("🤖 " + msg.message);
    } else if (msg.type === "error") {
      console.error("❌ " + msg.message);
    }
  }
}

// Usage
const chat = new AuthenticatedChat("ws://localhost:8000/bedrock-chat/ws");

// Authenticate with bearer token
chat.authenticate("bearer_token", {
  token: "your-api-token",
});
```

## Python Example

```python
import asyncio
import json
import websockets

async def main():
    async with websockets.connect('ws://localhost:8000/bedrock-chat/ws') as ws:
        # Send authentication
        await ws.send(json.dumps({
            'type': 'auth',
            'auth_type': 'bearer_token',
            'token': 'your-api-token'
        }))

        # Wait for auth confirmation
        response = json.loads(await ws.recv())
        print(f'✅ {response["message"]}')

        # Send message
        await ws.send(json.dumps({
            'type': 'chat',
            'message': 'Get my user data'
        }))

        # Receive response
        response = json.loads(await ws.recv())
        print(f'🤖 {response["message"]}')

asyncio.run(main())
```

## OpenAPI Specification (Documenting Auth)

Mark which API endpoints require authentication using OpenAPI extensions:

```yaml
paths:
  /api/users:
    get:
      summary: List users
      description: Get all users (requires authentication)
      # Standard OpenAPI security
      security:
        - bearerAuth: []
      # Custom extension for auth type
      x-auth-type: bearer_token
      parameters:
        - name: limit
          in: query
          schema:
            type: integer
      responses:
        "200":
          description: List of users
        "401":
          description: Unauthorized

components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
```

## Security Notes

✅ **What's Secure:**

- Credentials stored in memory only (not persisted)
- Not sent to LLM or logged
- Per-session isolation
- Automatic cleanup on disconnect

⚠️ **Best Practices:**

- Always use HTTPS/WSS in production
- Rotate tokens regularly
- Use minimal OAuth2 scopes
- Never hardcode credentials in frontend
- Clear credentials after session

## Troubleshooting

| Issue                           | Solution                                  |
| ------------------------------- | ----------------------------------------- |
| "Bearer token required"         | Include `token` field in auth message     |
| "HTTP 401 Unauthorized"         | Check token validity and API requirements |
| "OAuth2 token URL not provided" | Include `token_url` for OAuth2            |
| Tool calls failing silently     | Enable logging: `BEDROCK_LOG_LEVEL=DEBUG` |

## Advanced Features

### OAuth2 Token Caching

Tokens are automatically cached and refreshed:

- Cached in session memory
- Refreshed at 90% of expiry
- Reduces token endpoint calls

### Multiple Sessions

Create separate sessions for different APIs:

```javascript
// Session 1: API A with Bearer Token
const session1 = new AuthenticatedChat("ws://localhost:8000/bedrock-chat/ws");
session1.authenticate("bearer_token", { token: "api-a-token" });

// Session 2: API B with OAuth2
const session2 = new AuthenticatedChat("ws://localhost:8000/bedrock-chat/ws");
session2.authenticate("oauth2", {
  client_id: "client-id",
  client_secret: "client-secret",
  token_url: "https://auth.example.com/token",
});
```

## Complete Example

See `examples/fastAPI/app_auth.py` for a complete working example with:

- Protected API endpoints
- Multiple auth types
- Web UI with authentication
- Real tool calls with auto-applied auth

Run it with:

```bash
python examples/fastAPI/app_auth.py
```

Then open http://localhost:8000 in your browser.

## Further Reading

- [Full Authentication Guide](./AUTHENTICATION.md)
- [Implementation Details](./AUTHENTICATION_IMPLEMENTATION.md)
- [OpenAPI Specification](https://spec.openapis.org/oas/v3.0.3)
- [OAuth 2.0 RFC](https://tools.ietf.org/html/rfc6749)
