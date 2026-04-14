# SSO (Single Sign-On)

The plugin supports SSO authentication via the **OAuth2 Authorization Code flow with PKCE**. When enabled, users are redirected to an Identity Provider (IdP) to authenticate and their access token is stored for the duration of the session. Tool calls made by the AI automatically include the user's token — no manual credential entry required.

---

## Overview

```
       ┌──────────────┐
       │ Chat UI      │
       │ (browser)    │
       └──────┬───────┘
              │ 1. Click "Login with SSO"
              ▼
       ┌──────────────┐         ┌──────────────────┐
       │ Plugin       │──2──►   │ Identity Provider │
       │ /sso/login   │  302    │ (Cognito, Okta…)  │
       └──────────────┘         └────────┬─────────┘
                                         │ 3. User authenticates
              ┌──────────────────────────┘
              │ 4. Redirect with auth code
              ▼
       ┌──────────────┐
       │ Plugin       │  5. Exchange code for tokens
       │ /callback    │  6. Validate id_token
       └──────┬───────┘  7. Create SSO session
              │
              ▼
       ┌──────────────┐
       │ WebSocket    │  8. Auto-authenticate on connect
       │ session      │  9. Tool calls include
       └──────────────┘     Authorization: Bearer <access_token>
```

---

## Quick Start

### 1. Enable SSO in `add_bedrock_chat()`

```python
from auto_bedrock_chat_fastapi import add_bedrock_chat

plugin = add_bedrock_chat(
    app,
    # ... other params ...
    enable_tool_auth=True,
    supported_auth_types=["sso"],
    require_tool_auth=True,
    # SSO configuration
    sso_enabled=True,
    sso_provider="cognito",           # or "okta", "azure_ad", "auth0", "keycloak", "generic"
    sso_discovery_url="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_EXAMPLE/.well-known/openid-configuration",
    sso_client_id="your-client-id",
    sso_scopes="openid profile email",
    sso_callback_path="/chat/auth/callback",
    sso_session_secret="your-secret-key",
    sso_public_base_url="https://your-app.example.com",  # optional; see below
)
```

### 2. Register the callback URL with your IdP

The callback URL must be registered as an allowed redirect URI in your Identity Provider. The URL is:

```
<base_url><sso_callback_path>
```

For example: `https://your-app.example.com/chat/auth/callback`

### 3. Access the chat UI

Navigate to `/bedrock-chat/ui`. Click **"Login with SSO"**. After authenticating with the IdP, you'll be redirected back and the chat session will be authenticated automatically.

---

## Configuration Reference

All SSO settings can be passed as keyword arguments to `add_bedrock_chat()` or set via environment variables.

### Required Settings

| Parameter / Env Var                                 | Description                                                                                                   |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `sso_enabled` / `BEDROCK_SSO_ENABLED`               | Master switch, set to `True`                                                                                  |
| `sso_client_id` / `BEDROCK_SSO_CLIENT_ID`           | OAuth2 app client ID registered with the IdP                                                                  |
| `sso_session_secret` / `BEDROCK_SSO_SESSION_SECRET` | Secret for signing session tokens (generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`) |

### Endpoint Discovery

Provide **either** a discovery URL (recommended) **or** individual endpoint URLs:

| Parameter / Env Var                                       | Description                                                                                        |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `sso_discovery_url` / `BEDROCK_SSO_DISCOVERY_URL`         | OIDC discovery endpoint (`.well-known/openid-configuration`). Auto-configures all other endpoints. |
| `sso_authorization_url` / `BEDROCK_SSO_AUTHORIZATION_URL` | Manual override for the authorization endpoint                                                     |
| `sso_token_url` / `BEDROCK_SSO_TOKEN_URL`                 | Manual override for the token endpoint                                                             |
| `sso_userinfo_url` / `BEDROCK_SSO_USERINFO_URL`           | Manual override for the userinfo endpoint                                                          |
| `sso_jwks_url` / `BEDROCK_SSO_JWKS_URL`                   | JWKS endpoint for ID token signature validation                                                    |

### Optional Settings

| Parameter / Env Var                                   | Default                  | Description                                                                                           |
| ----------------------------------------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------- |
| `sso_provider` / `BEDROCK_SSO_PROVIDER`               | `None`                   | Provider hint for preset defaults: `cognito`, `okta`, `azure_ad`, `auth0`, `keycloak`, `generic`      |
| `sso_client_secret` / `BEDROCK_SSO_CLIENT_SECRET`     | `None`                   | Client secret for confidential clients (public clients with PKCE don't need this)                     |
| `sso_scopes` / `BEDROCK_SSO_SCOPES`                   | `"openid profile email"` | Space-separated OAuth2 scopes to request                                                              |
| `sso_callback_path` / `BEDROCK_SSO_CALLBACK_PATH`     | `"/chat/auth/callback"`  | Path on this server for the IdP callback                                                              |
| `sso_public_base_url` / `BEDROCK_SSO_PUBLIC_BASE_URL` | auto-detected            | Public-facing base URL for redirect URI (see [Public Base URL](#public-base-url-sso_public_base_url)) |
| `sso_session_ttl` / `BEDROCK_SSO_SESSION_TTL`         | `3600`                   | SSO session lifetime in seconds                                                                       |

---

## How It Works

### Login Flow

1. User visits `/bedrock-chat/ui` and clicks **"Login with SSO"**
2. Plugin generates a PKCE `code_verifier` + `code_challenge`, stores them server-side keyed by a random `state` parameter
3. User is redirected to the IdP's authorization endpoint
4. User authenticates with the IdP (e.g., corporate credentials via Azure AD)
5. IdP redirects back to `sso_callback_path` with an authorization `code` and the `state` parameter
6. Plugin validates the `state` (CSRF protection), then exchanges the code for tokens (`access_token`, `id_token`, `refresh_token`)
7. The `id_token` is validated (signature, expiry, audience, issuer, `at_hash`)
8. An SSO session is created; a signed session JWT is set as an `HttpOnly` cookie and the browser is redirected to the chat UI

### WebSocket Authentication

When the WebSocket connects, the plugin automatically:

1. Reads the session JWT from the `HttpOnly` cookie (sent automatically by the browser on the WebSocket handshake)
2. Looks up the SSO session and retrieves the stored `access_token`
3. Sets the session's `Credentials` with `auth_type=SSO` and `bearer_token=<access_token>`
4. All subsequent tool calls include `Authorization: Bearer <access_token>`

No manual auth message is needed — the session is authenticated transparently.

> **Note:** The session token is never exposed to JavaScript — it is stored exclusively in an `HttpOnly` cookie.

### Token Storage

The SSO session stores:

| Field             | Description                                               |
| ----------------- | --------------------------------------------------------- |
| `access_token`    | Used for API calls (set as bearer token on tool requests) |
| `id_token`        | Contains user identity claims (email, name, etc.)         |
| `refresh_token`   | Can be used to obtain new access tokens before expiry     |
| `id_token_claims` | Decoded claims from the ID token                          |
| `user_info`       | User profile from the userinfo endpoint (if available)    |

---

## Public Base URL (`sso_public_base_url`)

The `sso_public_base_url` controls the **redirect URI** sent to the IdP — this is the URL the browser navigates to after IdP authentication. It must match a callback URL registered in your IdP.

**When is it needed?**

- When your app runs behind a reverse proxy, load balancer, or in a container where the internal hostname differs from the public hostname
- When tool calls use `localhost` (internal) but users access the app via a public URL

**Example:** Your app runs as `http://localhost:8000` internally, but users access it at `https://myapp.example.com`:

```python
sso_public_base_url="https://myapp.example.com"
```

The redirect URI becomes: `https://myapp.example.com/chat/auth/callback`

If not set, it defaults to `api_base_url` (auto-detected from the running server).

---

## Provider Examples

### AWS Cognito

```python
plugin = add_bedrock_chat(
    app,
    sso_enabled=True,
    sso_provider="cognito",
    sso_discovery_url="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_POOLID/.well-known/openid-configuration",
    sso_client_id="your-cognito-app-client-id",
    sso_scopes="openid profile email",
    sso_callback_path="/chat/auth/callback",
    sso_session_secret="your-secret",
)
```

**Cognito setup:**

1. Create an App Client in your User Pool (or reuse an existing one)
2. Enable **Authorization code grant** under Allowed OAuth Flows
3. Add your callback URL to **Allowed callback URLs**: `https://your-app.example.com/chat/auth/callback`
4. Select the IdP under **Allowed identity providers**

### Okta

```python
plugin = add_bedrock_chat(
    app,
    sso_enabled=True,
    sso_provider="okta",
    sso_discovery_url="https://your-org.okta.com/oauth2/default/.well-known/openid-configuration",
    sso_client_id="your-okta-client-id",
    sso_scopes="openid profile email",
    sso_callback_path="/chat/auth/callback",
    sso_session_secret="your-secret",
)
```

### Azure AD

```python
plugin = add_bedrock_chat(
    app,
    sso_enabled=True,
    sso_provider="azure_ad",
    sso_discovery_url="https://login.microsoftonline.com/TENANT_ID/v2.0/.well-known/openid-configuration",
    sso_client_id="your-azure-app-client-id",
    sso_scopes="openid profile email",
    sso_callback_path="/chat/auth/callback",
    sso_session_secret="your-secret",
)
```

### Generic OIDC Provider

```python
plugin = add_bedrock_chat(
    app,
    sso_enabled=True,
    sso_provider="generic",
    sso_discovery_url="https://your-idp.example.com/.well-known/openid-configuration",
    sso_client_id="your-client-id",
    sso_scopes="openid profile email",
    sso_callback_path="/chat/auth/callback",
    sso_session_secret="your-secret",
)
```

Or without OIDC discovery (manual endpoints):

```python
plugin = add_bedrock_chat(
    app,
    sso_enabled=True,
    sso_authorization_url="https://idp.example.com/authorize",
    sso_token_url="https://idp.example.com/token",
    sso_jwks_url="https://idp.example.com/.well-known/jwks.json",
    sso_client_id="your-client-id",
    sso_scopes="openid profile email",
    sso_callback_path="/chat/auth/callback",
    sso_session_secret="your-secret",
)
```

---

## Combining SSO with Other Auth Types

SSO can coexist with manual auth methods. When `supported_auth_types` includes both `"sso"` and other types, users can either:

- Authenticate via SSO (automatic, no manual step)
- Send a manual auth message over WebSocket (bearer_token, api_key, etc.)

```python
plugin = add_bedrock_chat(
    app,
    enable_tool_auth=True,
    supported_auth_types=["sso", "bearer_token", "api_key"],
    default_auth_type="sso",  # pre-select SSO in the auth modal
    sso_enabled=True,
    # ... SSO config ...
)
```

When `require_tool_auth=True` and only `"sso"` is in `supported_auth_types`, users **must** authenticate via SSO before they can chat.

---

## Endpoints Added by SSO

| Route                              | Method | Description                                         |
| ---------------------------------- | ------ | --------------------------------------------------- |
| `{chat_endpoint}/auth/sso/login`   | GET    | Redirects to the IdP authorization endpoint         |
| `{chat_endpoint}/auth/sso/refresh` | POST   | Refreshes SSO tokens using the stored refresh token |
| `{chat_endpoint}/auth/sso/logout`  | POST   | Invalidates the SSO session and clears the cookie   |
| `<sso_callback_path>`              | GET    | Handles the IdP callback, exchanges code for tokens |

The login URL is also exposed in the UI as a "Login with SSO" button.

---

## Troubleshooting

### `invalid_scope` error on callback

The scopes requested by the plugin must be a subset of the scopes allowed by the IdP app client. Check your IdP's app client configuration and ensure all scopes in `sso_scopes` are allowed.

### `redirect_mismatch` error

The redirect URI sent to the IdP doesn't match any registered callback URL. Ensure:

1. The callback URL is registered in your IdP (`<base_url><sso_callback_path>`)
2. `sso_public_base_url` is set correctly if your app runs behind a proxy

### `at_hash` validation failure

The `access_token` must be passed when validating the `id_token`. This is handled automatically by the plugin. If you see this error, ensure your IdP returns both `access_token` and `id_token` in the token response.

### Token rejected by downstream API (401)

If tool calls receive 401 from the downstream API:

- Verify the Cognito app client ID is accepted by the API's authorizer
- Check that the requested scopes match what the API expects
- Ensure the token hasn't expired (`access_token` lifetime is configured in the IdP)

### Session expired

SSO sessions expire after `sso_session_ttl` seconds (default 3600). Users need to re-authenticate by clicking "Login with SSO" again.

---

## Security Considerations

- **PKCE** is used by default (no client secret needed for public clients)
- **State parameter** prevents CSRF attacks during the OAuth2 flow
- **ID token validation** verifies signature (via JWKS), expiry, audience, and `at_hash`
- **Session tokens** are signed JWTs — the signing key (`sso_session_secret`) must be kept secret
- **Access tokens** are stored server-side in the session store, never exposed to the chat UI JavaScript

---

## See Also

- [Authentication](authentication.md) — manual auth methods (bearer token, API key, etc.)
- [Configuration](configuration.md) — full settings reference
- [FastAPI Plugin Integration](fastapi-plugin.md) — `add_bedrock_chat()` setup
