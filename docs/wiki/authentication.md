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
autolangchat_plugin = add_autolangchat(
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

````http
The current `autolangchat` plugin does not expose a standalone REST `verify-auth` endpoint. Credentials are validated on the first tool call or via your own protected API routes.

---

## Python WebSocket Example

```python
import asyncio
import websockets
import json

async def chat_with_auth():
    uri = "ws://localhost:8000/chat/ws"

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
````

---

## JavaScript WebSocket Example

```javascript
const ws = new WebSocket("ws://localhost:8000/chat/ws");

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

## User Metadata Capture and Propagation

When authenticating, the plugin can capture user-specific metadata (tenant ID, permissions, display name, roles, etc.) from your API's verification endpoint and automatically inject it into all tool call requests. This enables downstream APIs to authorize and audit actions on a per-user basis.

### How It Works

```
1. User authenticates (OAuth2, SSO, etc.)
         │
         ▼
2. Plugin calls auth verification endpoint
         │
         ▼
3. Verification endpoint returns JSON response:
   {
     "user_id": "user-123",
     "tenant_id": "tenant-456",
     "email": "user@example.com",
     "display_name": "John Doe",
     "roles": ["admin", "developer"],
     "permissions": [...]
   }
         │
         ▼
4. Plugin stores in session.metadata["verified_user_info"]
         │
         ▼
5. On every tool call, plugin injects HTTP headers:
   - X-User-ID: user-123
   - X-User-Display-Name: John Doe
   - X-User-Metadata: <base64-encoded full JSON, including tenant_id and all verified fields>
         │
         ▼
6. Your API receives tool calls with user context
```

### Configuring the Verification Endpoint

```python
from autolangchat import add_autolangchat

add_autolangchat(
    app,
    enable_tool_auth=True,
    auth_verification_endpoint="/api/v1/auth/verify",  # Your verification endpoint
)
```

The verification endpoint should:

- Accept authenticated requests (with the user's credentials in headers)
- Return HTTP 2XX on success with a JSON body containing user metadata
- Return HTTP 4XX/5XX on failure

**Example verification endpoint:**

```python
from fastapi import Depends, HTTPException, Header
from typing import Optional

@app.get("/api/v1/auth/verify")
async def verify_credentials(authorization: Optional[str] = Header(None)):
    """Verify user credentials and return user metadata."""
    if not authorization:
        raise HTTPException(status_code=401, detail="No credentials provided")

    # Your auth logic here (validate token, check database, etc.)
    token = authorization.replace("Bearer ", "")
    user = await validate_token(token)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Return user metadata
    return {
        "user_id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "display_name": user.name,
        "roles": user.roles,
        "permissions": user.permissions,
        "access_all_tenants": user.is_admin,
    }
```

### Using Metadata in Your Tool APIs

Your tool API endpoints automatically receive user metadata in request headers:

```python
@app.get("/api/workloads")
async def list_workloads(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
    x_user_metadata: Optional[str] = Header(None, alias="X-User-Metadata"),
):
    """List workloads with user context."""

    # Option 1: Use X-User-ID for simple user-based filtering
    if x_user_id:
        # Filter by user_id
        workloads = db.query(Workload).filter(Workload.user_id == x_user_id).all()

    # Option 2: Decode full metadata for advanced authorization
    if x_user_metadata:
        import base64, json
        metadata = json.loads(base64.b64decode(x_user_metadata))
        verified_info = metadata.get("verified_user_info", {})

        # Access any field from verification endpoint response
        tenant_id = verified_info.get("tenant_id")  # If your app uses tenants
        roles = verified_info.get("roles", [])

        # Apply application-specific authorization logic
        if "admin" not in roles:
            # Filter results based on role
            pass

        if tenant_id:
            # Filter by tenant if your app is multi-tenant
            workloads = db.query(Workload).filter(Workload.tenant_id == tenant_id).all()

    return workloads
```

### Metadata Headers Reference

| Header                | Content                                              | Use Case                                |
| --------------------- | ---------------------------------------------------- | --------------------------------------- |
| `X-User-ID`           | User identifier (from `user_id` field)               | User-specific filtering, audit logs     |
| `X-User-Display-Name` | Human-readable name (from `display_name` or `email`) | UI display, audit trails                |
| `X-User-Metadata`     | Base64-encoded JSON of full `session.metadata` dict  | Complex authorization, custom workflows |

**Note:** All headers are optional. If a field is missing from the verification endpoint response, the corresponding header will not be set.

### SSO with Metadata

When using SSO authentication, the verification endpoint is **also called automatically** after successful SSO login. This ensures SSO users get the same metadata enrichment as OAuth2/API key users.

```python
add_autolangchat(
    app,
    enable_sso=True,
    sso_client_id="...",
    sso_client_secret="...",
    # Verification endpoint will be called after SSO login
    auth_verification_endpoint="/api/v1/auth/verify",
)
```

The SSO flow:

1. User logs in via Identity Provider (Cognito, Okta, Azure AD, etc.)
2. Plugin receives access token + user info from IdP
3. **Plugin calls your verification endpoint** with the IdP access token
4. Your verification endpoint returns application-specific metadata (tenant, permissions, etc.)
5. Metadata is stored in session and injected into tool calls

This allows you to:

- **Map IdP users to application tenants** — the IdP knows user identity, your API knows which tenant they belong to
- **Apply application-level permissions** — beyond what the IdP provides
- **Audit with application context** — not just "user@example.com logged in" but "user@example.com accessed tenant-456 data"

### Display Name in Chat UI

When metadata includes a `display_name` field (or falls back to `name`, `email`, `username`, or `user_id`), the chat UI automatically displays the authenticated user's name in the header:

```
🤖 AI Assistant    Authenticated as: John Doe
```

This provides visual confirmation of who is authenticated in the session.

---

## Including Auth Info in LLM Prompts

You can optionally include authenticated user metadata in the system prompt, allowing the LLM to answer identity questions like "who am I?" or personalize responses based on user attributes.

### Configuration

```python
add_autolangchat(
    app,
    enable_tool_auth=True,
    auth_verification_endpoint="/api/v1/auth/verify",
    include_auth_info_in_prompts=True,  # Enable auth info in prompts
)
```

Or via environment variable:

```bash
AUTOCHAT_ENABLE_TOOL_AUTH=true
AUTOCHAT_AUTH_VERIFICATION_ENDPOINT=http://localhost:8000/api/v1/auth/verify
AUTOCHAT_INCLUDE_AUTH_INFO_IN_PROMPTS=true
```

### How It Works

When `include_auth_info_in_prompts` is enabled:

1. User authenticates (OAuth2, Bearer Token, SSO, etc.)
2. Verification endpoint returns user metadata (user_id, name, email, department, roles, etc.)
3. Metadata is stored in `session.metadata["verified_user_info"]`
4. **On each chat message**, the user info is formatted and prepended to the system prompt
5. LLM receives context about the authenticated user
6. User can now ask "who am I?" and get personalized responses

### Example Interaction

**User authenticates with:**

```json
{
  "user_id": "alice@example.com",
  "name": "Alice Johnson",
  "department": "Engineering",
  "role": "Senior Developer",
  "team": "Platform"
}
```

**User asks:** "Who am I?"

**LLM responds:** "You are Alice Johnson, a Senior Developer in the Engineering department, part of the Platform team."

**User asks:** "What department am I in?"

**LLM responds:** "You are in the Engineering department."

### System Prompt Format

The plugin formats user info into a structured context block that's invisible to the user:

```
AUTHENTICATED USER CONTEXT:
============================================================
You are currently interacting with an authenticated user.
The following information is available about this user:

  user_id: alice@example.com
  name: Alice Johnson
  department: Engineering
  role: Senior Developer
  team: Platform

INSTRUCTIONS:
- This information is provided for context only - the user cannot see it
- Use this information to personalize your responses when appropriate
- If the user asks 'who am I?' or similar identity questions, use this context
- Respect user privacy - only share information when directly asked
- Be natural and conversational when using this information
============================================================

[Your system prompt continues here...]
```

### What Gets Included

The plugin includes only simple field types from the verification endpoint response:

- ✅ **Strings** — `name`, `email`, `user_id`, `department`
- ✅ **Numbers** — `employee_id`, `age`, `years_of_service`
- ✅ **Booleans** — `is_admin`, `is_active`, `verified`
- ✅ **String lists** — `roles: ["admin", "editor"]` → `roles: admin, editor`
- ❌ **Nested objects** — `{"address": {"city": "..."}}` (skipped for simplicity)
- ❌ **Complex lists** — `[{"permission": "read"}, ...]` (skipped)

This ensures the prompt stays clean and readable for the LLM.

### Privacy Considerations

**Important:** This feature exposes user metadata to the LLM (Amazon Bedrock). Before enabling:

- Review what data your verification endpoint returns
- Consider removing sensitive fields (SSN, passwords, tokens) from the response
- Understand your LLM provider's data handling policies
- Comply with GDPR, CCPA, and other privacy regulations
- Document what user data is sent to the LLM

### Combining with RAG

Auth info works seamlessly alongside RAG (Retrieval-Augmented Generation):

```python
add_autolangchat(
    app,
    enable_rag=True,  # Knowledge base context
    include_auth_info_in_prompts=True,  # User context
    auth_verification_endpoint="/api/v1/auth/verify",
)
```

The system prompt will include both:

1. **Knowledge Base Context** — relevant documents/chunks
2. **User Context** — authenticated user metadata
3. **Base System Prompt** — your application instructions

All three are combined into a single enhanced system message.

### When to Use This Feature

✅ **Good use cases:**

- Internal tools where the LLM should know who's asking
- Personalized recommendations based on user role/department
- Context-aware responses ("as an admin, you can...")
- Identity verification workflows

❌ **Not recommended when:**

- User metadata contains PII you don't want to send to LLM provider
- Multi-tenant system where cross-tenant data leakage is a concern
- Compliance prevents sending user data to third-party LLMs
- You want the LLM to remain user-agnostic

---

## See Also

- [SSO (Single Sign-On)](sso.md) — OAuth2/OIDC SSO for automatic authentication via Identity Providers
- [FastAPI Plugin Integration](fastapi-plugin.md)
- [WebSocket Client](websocket-client.md) — client script with all auth examples
- [Configuration](configuration.md) — `enable_tool_auth`, `supported_auth_types`, `include_auth_info_in_prompts`
