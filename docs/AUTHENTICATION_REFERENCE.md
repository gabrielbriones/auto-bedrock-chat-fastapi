# Authentication System - Quick Reference

## What Was Built

A complete, production-ready authentication system for securing tool calls (API requests) in auto-bedrock-chat-fastapi.

**Key Points:**
- Clients provide credentials once via WebSocket
- Credentials automatically applied to all API calls
- LLM never sees the credentials
- Supports 5 authentication types
- Fully configurable

## Authentication Types

| Type | Use Case | Header Example |
|------|----------|---|
| **Bearer Token** | Modern APIs, JWTs | `Authorization: Bearer abc123...` |
| **Basic Auth** | Legacy systems | `Authorization: Basic dXNlcjpwYXNz` |
| **API Key** | Simplified APIs | `X-API-Key: sk-123456` |
| **OAuth2** | Enterprise, token endpoint | Auto-fetches & caches tokens |
| **Custom** | Proprietary schemes | Any custom headers |

## 5-Minute Setup

### 1. Enable Authentication
```python
from auto_bedrock_chat_fastapi import add_bedrock_chat

bedrock_chat = add_bedrock_chat(app, enable_tool_auth=True)
```

### 2. Client Authenticates
```javascript
ws.send(JSON.stringify({
  type: 'auth',
  auth_type: 'bearer_token',
  token: 'your-api-token'
}));
```

### 3. System Applies Auth Automatically
When the LLM makes a tool call, the executor:
1. Retrieves credentials from session
2. Applies auth headers to request
3. Makes API call with authentication
4. Returns result to LLM

## File Structure

### New Files (1,500+ lines of code)
- **`auth_handler.py`** (430 lines) - Core authentication logic
- **`AUTHENTICATION_QUICK_START.md`** - Quick start guide
- **`AUTHENTICATION.md`** - Full reference (500+ lines)
- **`AUTHENTICATION_IMPLEMENTATION.md`** - Technical details (400+ lines)
- **`examples/fastAPI/app_auth.py`** - Multi-auth working example (1,100+ lines)

### Modified Files
- **`session_manager.py`** - Added credentials storage
- **`websocket_handler.py`** - Added auth handling and application
- **`tools_generator.py`** - Added auth metadata extraction
- **`config.py`** - Added auth configuration
- **`__init__.py`** - Exported auth components

## WebSocket API

### Send Authentication
```json
{
  "type": "auth",
  "auth_type": "bearer_token",
  "token": "your-token"
}
```

### Receive Confirmation
```json
{
  "type": "auth_configured",
  "message": "Authentication configured: bearer_token",
  "auth_type": "bearer_token"
}
```

## Configuration

### Environment Variables
```bash
BEDROCK_ENABLE_TOOL_AUTH=true
BEDROCK_SUPPORTED_AUTH_TYPES=bearer_token,basic_auth,api_key,oauth2,custom
BEDROCK_REQUIRE_TOOL_AUTH=false
BEDROCK_AUTH_TOKEN_CACHE_TTL=3600
```

### Programmatic
```python
add_bedrock_chat(
    app,
    enable_tool_auth=True,
    supported_auth_types=["bearer_token", "oauth2"],
    require_tool_auth=False,
    auth_token_cache_ttl=3600
)
```

## Data Flow

```
Client WebSocket
    ↓
    ├─ "type": "auth"
    │  ├─ "auth_type": "bearer_token"
    │  └─ "token": "xxx"
    ↓
WebSocketHandler._handle_auth_message()
    ↓
ChatSession.credentials = Credentials(...)
ChatSession.auth_handler = AuthenticationHandler(...)
    ↓
Client sends chat message
    ↓
LLM generates tool call
    ↓
Tool Executor
    ├─ Gets session credentials
    ├─ Calls auth_handler.apply_auth_to_headers()
    └─ Adds Authorization/X-API-Key/etc.
    ↓
HTTP Request with Auth
    ↓
API responds
    ↓
Result to LLM
```

## Security Features

✅ **Credentials isolated per session**
- Each session has its own credentials
- Automatically cleaned up on disconnect

✅ **Never sent to LLM**
- Only applied when making API calls
- LLM only sees results

✅ **Not logged**
- Credentials excluded from logs
- Only `has_token` flags logged

✅ **OAuth2 token caching**
- Tokens cached in memory
- Auto-refreshed at 90% expiry
- Reduces auth endpoint calls

## Code Examples

### JavaScript
```javascript
class AuthChat {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.ws.onmessage = e => this.onMsg(JSON.parse(e.data));
  }

  auth(type, creds) {
    this.ws.send(JSON.stringify({
      type: 'auth',
      auth_type: type,
      ...creds
    }));
  }

  send(msg) {
    this.ws.send(JSON.stringify({
      type: 'chat',
      message: msg
    }));
  }
}

const chat = new AuthChat('ws://localhost:8000/bedrock-chat/ws');
chat.auth('bearer_token', { token: 'my-token' });
```

### Python
```python
import asyncio
import json
import websockets

async def main():
    async with websockets.connect('ws://localhost:8000/bedrock-chat/ws') as ws:
        await ws.send(json.dumps({
            'type': 'auth',
            'auth_type': 'bearer_token',
            'token': 'my-token'
        }))
        
        msg = json.loads(await ws.recv())
        print(f'✅ {msg["message"]}')
        
        await ws.send(json.dumps({
            'type': 'chat',
            'message': 'Get my data'
        }))

asyncio.run(main())
```

## Protected API Example

```python
@app.get("/api/users")
async def list_users(x_api_key: str = Header(None)):
    if not x_api_key or x_api_key != "valid-key":
        raise HTTPException(status_code=401)
    
    return {"users": [...]}
```

When LLM calls this tool, system:
1. Retrieves API key from session
2. Adds `X-API-Key: valid-key` header
3. Calls the endpoint successfully
4. Returns data to LLM

## Documentation Files

📖 **Start with these:**

1. **AUTHENTICATION_QUICK_START.md** (5 min)
   - Quick setup
   - All auth types
   - Code examples

2. **AUTHENTICATION.md** (20 min)
   - Complete guide
   - Best practices
   - Troubleshooting

3. **AUTHENTICATION_IMPLEMENTATION.md** (30 min)
   - Technical deep dive
   - Architecture diagrams
   - Implementation details

4. **examples/fastAPI/app_auth.py**
   - Complete working app
   - Protected endpoints
   - Web UI demo

## Key Classes

### `AuthType` (Enum)
```python
class AuthType(str, Enum):
    NONE = "none"
    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"
    OAUTH2_CLIENT_CREDENTIALS = "oauth2_client_credentials"
    API_KEY = "api_key"
    CUSTOM = "custom"
```

### `Credentials` (Dataclass)
```python
@dataclass
class Credentials:
    auth_type: AuthType
    bearer_token: Optional[str]
    username: Optional[str]
    password: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]
    api_key: Optional[str]
    token_url: Optional[str]  # OAuth2
    # ... more fields
```

### `AuthenticationHandler` (Class)
```python
class AuthenticationHandler:
    async def apply_auth_to_headers(
        self,
        headers: Dict[str, str],
        tool_auth_config: Optional[Dict]
    ) -> Dict[str, str]:
        # Applies authentication to headers
        # Returns updated headers
```

## Integration Points

### 1. Session Manager
```python
session.credentials  # Stores creds
session.auth_handler  # Applies auth
```

### 2. Tools Generator
```python
# Extracts auth requirements from OpenAPI specs
x-auth-type: bearer_token
x-api-key-header: X-API-Key
x-oauth2-token-url: https://...
```

### 3. WebSocket Handler
```python
# Receives auth messages
# Applies auth to tool calls
```

### 4. Configuration
```python
enable_tool_auth
supported_auth_types
require_tool_auth
auth_token_cache_ttl
```

## Testing the System

### Run the Example
```bash
cd /home/gbriones/auto-bedrock-chat-fastapi
python examples/fastAPI/app_auth.py
# Open http://localhost:8000 in browser
```

### Test Different Auth Types
1. **API Key**: Use one of the test credentials
2. **Bearer Token**: Test JWT tokens
3. **Try prompts**: "List all orders", "Get user profile", etc.

## Deployment Checklist

- ✅ Enable HTTPS/WSS in production
- ✅ Rotate tokens regularly
- ✅ Use minimal OAuth2 scopes
- ✅ Monitor authentication failures
- ✅ Configure session timeouts
- ✅ Enable logging (without credentials)
- ✅ Test with multiple auth types
- ✅ Document required credentials for users

## Common Tasks

### Enable Only Bearer Token
```python
add_bedrock_chat(
    app,
    supported_auth_types=["bearer_token"]
)
```

### Require Auth Before Tool Calls
```python
add_bedrock_chat(
    app,
    require_tool_auth=True  # Must auth first
)
```

### Custom OAuth2 Setup
```json
{
  "type": "auth",
  "auth_type": "oauth2",
  "client_id": "your-id",
  "client_secret": "your-secret",
  "token_url": "https://auth.example.com/oauth/token",
  "scope": "api:read api:write"
}
```

### Multiple Sessions
```javascript
// Session 1: API A
const chat1 = new AuthChat('ws://...');
chat1.auth('bearer_token', { token: 'token-a' });

// Session 2: API B
const chat2 = new AuthChat('ws://...');
chat2.auth('api_key', { api_key: 'key-b' });
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Bearer token required" | Send `token` field in auth message |
| "HTTP 401 Unauthorized" | Check token validity |
| "OAuth2 token URL not provided" | Include `token_url` in message |
| Credentials not applied | Ensure auth message sent before chat |
| Tool calls fail silently | Enable debug logging |

## What's Next?

The authentication system is production-ready and includes:

✅ 5 authentication methods  
✅ Automatic credential application  
✅ Security best practices  
✅ Comprehensive documentation  
✅ Working examples  
✅ Full configuration options  

You can:
1. Use the example as a starting point
2. Integrate with existing FastAPI apps
3. Configure for your specific auth needs
4. Customize auth types as needed

## Get Started

1. **Read**: AUTHENTICATION_QUICK_START.md (5 min)
2. **Run**: examples/fastAPI/app_auth.py
3. **Integrate**: Add `enable_tool_auth=True` to your app
4. **Test**: Send auth message from WebSocket client

Happy authenticating! 🔐
