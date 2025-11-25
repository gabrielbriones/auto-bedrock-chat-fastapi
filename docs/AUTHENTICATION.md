# Authentication for Tool Calls

This guide explains how to use authentication for tool calls in auto-bedrock-chat-fastapi.

## Overview

When the LLM makes tool calls (API requests), those requests may require authentication depending on the API specification. The authentication system allows you to:

1. **Provide credentials** through the WebSocket connection on session initialization
2. **Automatically apply authentication** to tool calls based on the API specification
3. **Support multiple authentication types** (Bearer Token, Basic Auth, API Key, OAuth2, Custom)
4. **Let developers configure** which authentication types are enabled

## Supported Authentication Types

### 1. Bearer Token

Simple bearer token authentication, commonly used in modern APIs.

**WebSocket Message:**
```json
{
  "type": "auth",
  "auth_type": "bearer_token",
  "token": "your-bearer-token-here"
}
```

**How it works:**
- Token is added to requests as `Authorization: Bearer <token>`

### 2. Basic Authentication

HTTP Basic Authentication using username and password.

**WebSocket Message:**
```json
{
  "type": "auth",
  "auth_type": "basic_auth",
  "username": "user@example.com",
  "password": "password123"
}
```

**How it works:**
- Credentials are base64-encoded and added as `Authorization: Basic <encoded-credentials>`

### 3. API Key

API key authentication with configurable header name.

**WebSocket Message:**
```json
{
  "type": "auth",
  "auth_type": "api_key",
  "api_key": "sk-1234567890abcdef",
  "api_key_header": "X-API-Key"
}
```

**How it works:**
- API key is added to requests as custom header (default: `X-API-Key`)
- You can customize the header name per session

### 4. OAuth2 Client Credentials

OAuth2 client credentials flow for service-to-service authentication.

**WebSocket Message:**
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

**How it works:**
- Automatically requests access tokens from the token endpoint
- Caches tokens and refreshes them when they expire
- Adds token to requests as `Authorization: Bearer <access-token>`

### 5. Custom Authentication

Flexible authentication for non-standard schemes.

**WebSocket Message:**
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

**How it works:**
- Custom headers are added directly to all requests
- Useful for proprietary authentication schemes

## How It Works

### 1. Session Initialization

When a client connects via WebSocket:

```javascript
// Connect to WebSocket
const ws = new WebSocket('ws://localhost:8000/bedrock-chat/ws');

// Wait for connection
ws.onopen = () => {
  // Send authentication
  ws.send(JSON.stringify({
    type: 'auth',
    auth_type: 'bearer_token',
    token: 'your-token'
  }));
};
```

### 2. Authentication Storage

Credentials are stored securely in the session:
- They are **not** sent to the LLM
- They are **only** used by the executor when making tool calls
- They remain in memory for the session duration
- They are cleared when the session ends

### 3. Tool Metadata

The tools generator automatically extracts authentication requirements from OpenAPI specs using these custom extensions:

```yaml
paths:
  /api/users:
    get:
      # Standard OpenAPI security
      security:
        - bearerAuth: []
      # Custom extensions for auth configuration
      x-auth-type: bearer_token
      x-bearer-token-header: Authorization
```

### 4. Automatic Application

When a tool call is executed:

1. The executor checks if the session has credentials configured
2. If credentials exist, it retrieves the tool's auth requirements from metadata
3. It applies the appropriate authentication headers/schemes
4. The request is made with proper authentication

```python
# Inside tool execution
headers = {}
if session.auth_handler and session.credentials.auth_type != "none":
    headers = await session.auth_handler.apply_auth_to_headers(
        headers,
        tool_auth_config
    )
# Now make the request with authenticated headers
```

## API Endpoint Documentation

### Authentication Message Format

**Endpoint:** WebSocket `/bedrock-chat/ws`

**Message Type:** `auth`

**Response on success:**
```json
{
  "type": "auth_configured",
  "message": "Authentication configured: bearer_token",
  "auth_type": "bearer_token",
  "timestamp": "2025-01-15T10:30:45.123456"
}
```

**Response on error:**
```json
{
  "type": "error",
  "message": "Bearer token required",
  "timestamp": "2025-01-15T10:30:45.123456"
}
```

## Configuration

### In your `.env` file:

```bash
# Enable/disable tool authentication
BEDROCK_ENABLE_TOOL_AUTH=true

# Supported authentication types (comma-separated or JSON list)
BEDROCK_SUPPORTED_AUTH_TYPES=bearer_token,basic_auth,api_key,oauth2,custom

# Require authentication before any tool calls
BEDROCK_REQUIRE_TOOL_AUTH=false

# OAuth2 token cache TTL in seconds
BEDROCK_AUTH_TOKEN_CACHE_TTL=3600
```

### Programmatically:

```python
from auto_bedrock_chat_fastapi import add_bedrock_chat

app = FastAPI()

bedrock_chat = add_bedrock_chat(
    app,
    enable_tool_auth=True,
    supported_auth_types=["bearer_token", "oauth2"],
    require_tool_auth=False,
    auth_token_cache_ttl=3600,
)
```

## OpenAPI Specification Extensions

Document authentication requirements in your OpenAPI spec:

### Bearer Token
```yaml
paths:
  /api/data:
    get:
      x-auth-type: bearer_token
      x-bearer-token-header: Authorization
```

### Basic Auth
```yaml
paths:
  /api/admin:
    get:
      x-auth-type: basic_auth
```

### API Key
```yaml
paths:
  /api/search:
    get:
      x-auth-type: api_key
      x-api-key-header: X-API-Key
```

### OAuth2
```yaml
paths:
  /api/secure:
    post:
      x-auth-type: oauth2
      x-oauth2-token-url: https://auth.example.com/token
      x-oauth2-scope: api:write
```

### Custom Headers
```yaml
paths:
  /api/custom:
    get:
      x-auth-type: custom
      x-custom-auth-headers:
        X-Custom-Auth: internal
        X-Version: v2
```

## Client Examples

### JavaScript/WebSocket

```javascript
class AuthenticatedChatClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.setupHandlers();
  }

  setupHandlers() {
    this.ws.onopen = () => console.log('Connected');
    this.ws.onmessage = (event) => this.handleMessage(JSON.parse(event.data));
    this.ws.onerror = (error) => console.error('WebSocket error:', error);
  }

  authenticate(authType, credentials) {
    const message = {
      type: 'auth',
      auth_type: authType,
      ...credentials,
    };
    this.ws.send(JSON.stringify(message));
  }

  sendMessage(content) {
    this.ws.send(JSON.stringify({
      type: 'chat',
      message: content,
    }));
  }

  handleMessage(message) {
    if (message.type === 'auth_configured') {
      console.log('Authentication configured:', message.auth_type);
    } else if (message.type === 'error') {
      console.error('Error:', message.message);
    } else if (message.type === 'ai_response') {
      console.log('AI Response:', message.message);
    }
  }
}

// Usage
const client = new AuthenticatedChatClient('ws://localhost:8000/bedrock-chat/ws');

// Authenticate with bearer token
client.authenticate('bearer_token', {
  token: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...'
});

// Send message
setTimeout(() => {
  client.sendMessage('Get me the user data');
}, 1000);
```

### Python

```python
import asyncio
import json
import websockets

async def chat_with_auth():
    uri = "ws://localhost:8000/bedrock-chat/ws"
    
    async with websockets.connect(uri) as websocket:
        # Authenticate
        auth_message = {
            "type": "auth",
            "auth_type": "bearer_token",
            "token": "your-token-here",
        }
        await websocket.send(json.dumps(auth_message))
        
        # Wait for auth confirmation
        response = json.loads(await websocket.recv())
        print(f"Auth response: {response}")
        
        # Send a message
        chat_message = {
            "type": "chat",
            "message": "Get me the user data",
        }
        await websocket.send(json.dumps(chat_message))
        
        # Receive response
        response = json.loads(await websocket.recv())
        print(f"AI Response: {response}")

# Run
asyncio.run(chat_with_auth())
```

### cURL/REST Client

```bash
# WebSocket with Bearer Token (using wscat)
wscat -c ws://localhost:8000/bedrock-chat/ws

# Then send:
{"type":"auth","auth_type":"bearer_token","token":"your-token"}
{"type":"chat","message":"Get my data"}
```

## Security Considerations

### Best Practices

1. **Never log credentials**: Credentials are automatically excluded from logs
2. **Use HTTPS/WSS**: Always use secure WebSocket connections (wss://) in production
3. **Token rotation**: Regularly rotate tokens and OAuth2 credentials
4. **Scope limitation**: Use minimal scopes for OAuth2 credentials
5. **Session timeout**: Configure appropriate session timeouts

### What Gets Sent Where

```
Client → WebSocket → Server Memory (Session)
                   ↓
                   (Not sent to LLM)
                   ↓
                   Tool Executor → API with Auth Headers
```

- **Credentials storage**: Session memory only
- **Credentials to LLM**: Never
- **Tool calls**: Always authenticated with proper headers
- **Logs**: Credentials excluded, only `has_*` flags logged

### Token Caching

OAuth2 tokens are cached in memory:
- Automatically refreshed at 90% of expiry time
- No sensitive data in logs
- Tokens cleared when session ends

## Troubleshooting

### Issue: "Bearer token required"

**Solution**: Ensure token is provided in the auth message:
```json
{
  "type": "auth",
  "auth_type": "bearer_token",
  "token": "actual-token-value"
}
```

### Issue: "Authentication failed: Request failed"

**Possible causes**:
- OAuth2 token URL is unreachable
- Client credentials are invalid
- Scopes are insufficient

**Solution**: Verify credentials and token URL.

### Issue: Tool calls failing with 401 Unauthorized

**Possible causes**:
- Authentication not configured for the session
- Wrong authentication type for the API
- Token has expired

**Solution**: Send auth message before making requests, or check token expiry.

### Issue: "OAuth2 token URL not provided"

**Solution**: Include `token_url` in OAuth2 auth message:
```json
{
  "type": "auth",
  "auth_type": "oauth2",
  "client_id": "...",
  "client_secret": "...",
  "token_url": "https://auth.example.com/token"
}
```

## Advanced Usage

### Custom Header Authentication

For proprietary authentication schemes:

```json
{
  "type": "auth",
  "auth_type": "custom",
  "custom_headers": {
    "X-API-Version": "v2",
    "X-Secret-Key": "secret-key-value",
    "X-Client-ID": "client-123"
  },
  "metadata": {
    "auth_scheme": "proprietary",
    "version": "2.0"
  }
}
```

### Multiple API Credentials

Create separate sessions if you need to call different APIs:

```javascript
// Session 1: API A with Bearer Token
const session1 = new AuthenticatedChatClient('ws://localhost:8000/bedrock-chat/ws');
session1.authenticate('bearer_token', { token: 'api-a-token' });

// Session 2: API B with OAuth2
const session2 = new AuthenticatedChatClient('ws://localhost:8000/bedrock-chat/ws');
session2.authenticate('oauth2', {
  client_id: '...',
  client_secret: '...',
  token_url: '...',
});
```

## See Also

- [Configuration Guide](./CONFIGURATION.md)
- [OpenAPI Specification](../README.md)
- [WebSocket Protocol](./GITHUB_ACTIONS.md)
