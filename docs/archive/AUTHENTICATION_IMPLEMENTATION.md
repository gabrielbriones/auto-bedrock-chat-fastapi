# Tool Call Authentication Implementation Guide

## Architecture Overview

The authentication system is designed with these principles:

1. **Separation of Concerns**: Credentials are kept separate from the LLM logic
2. **Flexible Configuration**: Developers can configure which auth types to support
3. **Automatic Application**: Authentication is automatically applied when executing tool calls
4. **Security First**: Credentials are never logged or sent to the LLM

### Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ Client (WebSocket)                                          │
│ ┌──────────────────┐                                        │
│ │ Auth Message     │                                        │
│ │ {                │                                        │
│ │   type: "auth"   │                                        │
│ │   auth_type: ... │                                        │
│ │   token: ...     │                                        │
│ │ }                │                                        │
│ └──────────────────┘                                        │
└─────────────────────────────────────────────────────────────┘
                    ↓
        ┌───────────────────────┐
        │  WebSocketHandler     │
        │  _handle_auth_message │
        └───────────────────────┘
                    ↓
        ┌───────────────────────┐
        │ ChatSession           │
        │ ├─ credentials        │
        │ └─ auth_handler       │
        └───────────────────────┘
                    ↓
        ┌───────────────────────┐
        │  AuthenticationHandler│
        │  apply_auth_to_headers│
        └───────────────────────┘
                    ↓
        ┌───────────────────────┐
        │ Tool Executor         │
        │ _execute_single_tool_│
        │ call                  │
        └───────────────────────┘
                    ↓
        ┌───────────────────────┐
        │ Protected API         │
        │ (with Auth Header)    │
        └───────────────────────┘
```

## File Organization

### New Files

1. **`auto_bedrock_chat_fastapi/auth_handler.py`**
   - `AuthType` enum: Supported authentication types
   - `Credentials` dataclass: Holds user credentials
   - `AuthenticationHandler` class: Applies authentication to requests

### Modified Files

1. **`auto_bedrock_chat_fastapi/session_manager.py`**

   - Added `credentials` field to `ChatSession`
   - Added `auth_handler` field to `ChatSession`
   - Added `__post_init__` to initialize auth handler

2. **`auto_bedrock_chat_fastapi/tools_generator.py`**

   - Added `_extract_auth_requirements()` method
   - Updated `_create_function_description()` to include auth metadata

3. **`auto_bedrock_chat_fastapi/websocket_handler.py`**

   - Added `_handle_auth_message()` method
   - Updated `_message_loop()` to handle auth messages
   - Updated `_execute_tool_calls()` to accept session parameter
   - Updated `_execute_single_tool_call()` to apply authentication

4. **`auto_bedrock_chat_fastapi/config.py`**
   - Added authentication configuration fields

## Implementation Details

### 1. Authentication Handler (`auth_handler.py`)

```python
class AuthenticationHandler:
    """Manages authentication for API calls"""

    async def apply_auth_to_headers(self, headers, tool_auth_config):
        """Apply authentication to request headers"""
        # Determines which auth method to use based on credentials.auth_type
        # Calls appropriate _apply_* method
        # Returns updated headers
```

**Supported Methods:**

- `_apply_bearer_token()` - Adds `Authorization: Bearer <token>`
- `_apply_basic_auth()` - Base64 encodes and adds `Authorization: Basic`
- `_apply_api_key()` - Adds custom header with API key
- `_apply_oauth2()` - Handles OAuth2 client credentials flow
- `_apply_custom_auth()` - Applies custom headers

### 2. Session Credentials (`session_manager.py`)

```python
@dataclass
class ChatSession:
    # ...
    credentials: Credentials = field(default_factory=lambda: Credentials())
    auth_handler: Optional[AuthenticationHandler] = field(default=None, init=False)

    def __post_init__(self):
        """Initialize auth handler after dataclass initialization"""
        self.auth_handler = AuthenticationHandler(self.credentials)
```

Each session has its own:

- `credentials`: The actual auth data
- `auth_handler`: Instance that applies the auth

### 3. Tool Metadata Extraction (`tools_generator.py`)

```python
def _extract_auth_requirements(self, operation: Dict) -> Optional[Dict[str, Any]]:
    """Extract authentication requirements from OpenAPI operation"""
```

Looks for:

- `security` (standard OpenAPI)
- `x-auth-type` (custom extension)
- `x-bearer-token-header` (custom header name)
- `x-api-key-header` (custom header name)
- `x-oauth2-token-url` (OAuth2 token endpoint)
- `x-custom-auth-headers` (custom headers)

### 4. Authentication Handling (`websocket_handler.py`)

```python
async def _handle_auth_message(self, websocket: WebSocket, data: Dict[str, Any]):
    """Handle authentication message from client"""
    # 1. Parse auth_type from message
    # 2. Create Credentials object with provided data
    # 3. Create AuthenticationHandler
    # 4. Validate credentials
    # 5. Store in session
    # 6. Send confirmation
```

### 5. Tool Execution with Auth (`websocket_handler.py`)

```python
async def _execute_single_tool_call(self, tool_metadata, arguments, session):
    # ... build request ...

    # Apply authentication if available
    if session and session.auth_handler:
        headers = await session.auth_handler.apply_auth_to_headers(
            headers,
            tool_metadata.get("_metadata", {}).get("authentication")
        )

    # Make request with authenticated headers
    response = await self.http_client.get(**request_kwargs)
```

## Data Flow

### Authentication Setup

```
1. Client connects via WebSocket
2. Connection accepted, session created
3. Client sends auth message
4. _handle_auth_message() processes it
5. Credentials stored in session.credentials
6. AuthenticationHandler created and stored in session.auth_handler
7. Confirmation sent to client
```

### Tool Execution

```
1. LLM requests tool call
2. _execute_tool_calls() called with session
3. For each tool call:
   a. Get tool metadata (includes auth requirements)
   b. Build request headers
   c. Call session.auth_handler.apply_auth_to_headers()
   d. Method adds auth headers based on auth_type
   e. Request sent with auth headers
4. Response returned to LLM
```

## Configuration

### Environment Variables

```bash
# Enable tool authentication
BEDROCK_ENABLE_TOOL_AUTH=true

# Supported auth types
BEDROCK_SUPPORTED_AUTH_TYPES=bearer_token,basic_auth,api_key,oauth2,custom

# Require auth before any tool calls
BEDROCK_REQUIRE_TOOL_AUTH=false

# OAuth2 token cache TTL
BEDROCK_AUTH_TOKEN_CACHE_TTL=3600
```

### Programmatic Configuration

```python
from auto_bedrock_chat_fastapi import add_bedrock_chat

bedrock_chat = add_bedrock_chat(
    app,
    enable_tool_auth=True,
    supported_auth_types=["bearer_token", "oauth2"],
    require_tool_auth=False,
)
```

## Security Implementation

### Credential Storage

- **In Memory Only**: Credentials stored in `ChatSession` object
- **Session-Scoped**: Each session has its own credentials
- **Automatic Cleanup**: Cleared when session ends

### OAuth2 Token Handling

```python
# Tokens are cached with expiry
_cached_access_token: Optional[str] = None
_token_expiry: Optional[float] = None

# Refreshed at 90% of expiry
if time.time() < self.credentials._token_expiry:
    # Use cached token
else:
    # Request new token
    expires_in = token_data.get("expires_in", 3600)
    self.credentials._token_expiry = time.time() + (expires_in * 0.9)
```

### Logging

Credentials are excluded from logs:

- Username/password: Not logged
- Tokens: Not logged
- Only flags logged: `has_bearer_token`, `has_credentials`, etc.

## Testing

### Unit Tests

```python
async def test_bearer_token_auth():
    """Test bearer token application"""
    creds = Credentials(
        auth_type=AuthType.BEARER_TOKEN,
        bearer_token="test-token"
    )
    handler = AuthenticationHandler(creds)
    headers = await handler.apply_auth_to_headers({})
    assert headers["Authorization"] == "Bearer test-token"

async def test_basic_auth():
    """Test basic auth encoding"""
    creds = Credentials(
        auth_type=AuthType.BASIC_AUTH,
        username="user",
        password="pass"
    )
    handler = AuthenticationHandler(creds)
    headers = await handler.apply_auth_to_headers({})
    # Base64 of "user:pass" is "dXNlcjpwYXNz"
    assert headers["Authorization"] == "Basic dXNlcjpwYXNz"
```

### Integration Tests

```python
async def test_authenticated_tool_call():
    """Test tool call with authentication"""
    # 1. Create session with auth
    # 2. Call tool that requires auth
    # 3. Verify auth header was added
    # 4. Verify request succeeded
```

## Error Handling

### Invalid Credentials

```python
if not auth_handler.validate_credentials():
    await self._send_error(
        websocket,
        "Invalid credentials provided"
    )
    return
```

### Missing Required Fields

```python
if not token:
    await self._send_error(
        websocket,
        "Bearer token required"
    )
    return
```

### OAuth2 Failures

```python
try:
    access_token = await self._get_oauth2_token(token_url)
except httpx.HTTPError as e:
    return {"error": f"OAuth2 token request failed: {str(e)}"}
```

## Advanced Features

### Multiple Auth Types per Tool

Some tools might support multiple auth methods. The system can:

1. Try primary auth method first
2. Fall back to secondary if 401
3. Log which method succeeded

```python
# Tool metadata can specify multiple options
"_metadata": {
    "authentication": {
        "primary": "bearer_token",
        "fallback": "api_key",
        "token_url": "https://auth.example.com/token"
    }
}
```

### Custom Auth Validation

Developers can extend the `AuthenticationHandler`:

```python
class CustomAuthHandler(AuthenticationHandler):
    async def _apply_custom_auth(self, headers, tool_auth_config):
        # Custom implementation
        return headers
```

### Session Auth Updates

Credentials can be updated mid-session:

```python
# Client sends new auth message
ws.send(json.dumps({
    "type": "auth",
    "auth_type": "oauth2",
    # ... new credentials
}))
```

## Performance Considerations

### OAuth2 Token Caching

- Tokens cached in memory
- Automatic refresh at 90% expiry
- Reduces token endpoint calls
- ~50KB per cached token

### Header Application

- Headers applied during request building
- Minimal overhead (header copy + modification)
- No blocking operations (except OAuth2 token request)

### Credential Validation

- Happens only during auth setup
- Not repeated for every request
- ~1ms per validation

## Future Enhancements

### Possible Additions

1. **JWT Token Parsing**: Extract expiry from JWT tokens
2. **MTLS Support**: Certificate-based authentication
3. **SAML/OIDC**: Enterprise authentication
4. **Key Rotation**: Automatic credential refresh
5. **Audit Logging**: Track auth usage without logging credentials
6. **Per-Tool Auth**: Different credentials for different tools
7. **Auth Middleware**: Pluggable auth providers

## References

- [OpenAPI Security Schemes](https://spec.openapis.org/oas/v3.0.3#security-scheme-object)
- [RFC 7617: Basic HTTP Authentication](https://tools.ietf.org/html/rfc7617)
- [RFC 6750: Bearer Token Usage](https://tools.ietf.org/html/rfc6750)
- [OAuth 2.0 Client Credentials](https://tools.ietf.org/html/rfc6749#section-4.4)
