# Configuration

All plugin settings are managed through `ChatConfig` (a Pydantic `BaseSettings` model). Values can be set via `.env` file, environment variables, or passed directly in code.

---

## Quick Start

1. Copy the example env file and edit it:

```bash
cp .env.example .env
```

1. In your app, most settings load automatically from `.env`. List-type fields must be set in code:

```python
from fastapi import FastAPI
from autolangchat import add_autolangchat

app = FastAPI()

autolangchat_plugin = add_autolangchat(
    app,
    allowed_paths=["/api/products", "/api/users"],   # must be in code
    excluded_paths=["/docs", "/admin"]               # must be in code
)
```

> **Why code for lists?** Pydantic v2 does not support list parsing from `.env` files.

---

## Full Configuration Reference

### AWS / Bedrock

| Env Variable             | Default                        | Description                      |
| ------------------------ | ------------------------------ | -------------------------------- |
| `AWS_REGION`             | `us-east-1`                    | AWS region                       |
| `AWS_ACCESS_KEY_ID`      | —                              | AWS access key (or use IAM role) |
| `AWS_SECRET_ACCESS_KEY`  | —                              | AWS secret key                   |
| `AUTOCHAT_MODEL_ID`      | `us.anthropic.claude-sonnet-5` | Model identifier                 |
| `AUTOCHAT_TEMPERATURE`   | `0.7`                          | Response randomness (0.0–1.0)    |
| `AUTOCHAT_MAX_TOKENS`    | `4096`                         | Max tokens in model response     |
| `AUTOCHAT_TOP_P`         | `0.9`                          | Top-p sampling parameter         |
| `AUTOCHAT_SYSTEM_PROMPT` | `None`                         | Custom system prompt             |

> **Per-model capabilities.** `AUTOCHAT_TEMPERATURE`, `AUTOCHAT_TOP_P` and
> `AUTOCHAT_MAX_TOKENS` are applied according to what the selected model
> actually supports (from `langchain_aws.data._profiles`): parameters a model
> rejects are dropped, `max_tokens` is clamped down to the model's
> `max_output_tokens` cap, and only one of `temperature`/`top_p` is ever sent
> (Bedrock Converse rejects requests carrying both). So a value that's valid
> for one model won't break another — it's simply ignored where unsupported.

### Endpoints

| Env Variable                              | Default    | Description                                             |
| ----------------------------------------- | ---------- | ------------------------------------------------------- |
| `AUTOCHAT_CHAT_ENDPOINT`                  | `/chat`    | Chat route base path                                    |
| `AUTOCHAT_WEBSOCKET_ENDPOINT`             | `/chat/ws` | WebSocket endpoint                                      |
| `AUTOCHAT_UI_ENDPOINT`                    | `/chat/ui` | Chat UI path                                            |
| `AUTOCHAT_SSO_ALLOWED_RETURN_PREFIXES`    | UI path    | Comma-separated allowed post-SSO return path prefixes   |
| `AUTOCHAT_ENABLE_UI`                      | `true`     | Enable built-in chat UI                                 |
| `AUTOCHAT_UI_LOCK_INPUT_WHILE_RESPONDING` | `true`     | Disable chat input while waiting for assistant response |

### Tool Calling

| Env Variable                 | Default       | Description                                                |
| ---------------------------- | ------------- | ---------------------------------------------------------- |
| `AUTOCHAT_MAX_TOOL_CALLS`    | _(unlimited)_ | Max tool calls per turn (omit or leave unset for no limit) |
| `AUTOCHAT_OPENAPI_SPEC_FILE` | `None`        | Path to external OpenAPI spec file                         |

### Dynamic Parameter Overrides

| Env Variable                         | Default               | Description                                                                                                                                                                  |
| ------------------------------------ | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES`  | `false`               | Master switch allowing end users to override LLM params/feature toggles per message or per session via WebSocket metadata                                                    |
| `AUTOCHAT_ALLOWED_DYNAMIC_OVERRIDES` | _(none)_              | Comma-separated allowlist of overridable parameter names. When unset and the master switch is on, all overridable params are allowed                                         |
| `AUTOCHAT_ENABLE_CONFIG_SIDEBAR`     | `false`               | Whether to show the dynamic parameter overrides settings sidebar in the chat UI                                                                                              |
| `AUTOCHAT_AVAILABLE_MODELS`          | tool-capable profiles | Comma-separated model IDs offered in the sidebar's `model_id` dropdown                                                                                                       |
| `AUTOCHAT_PROVIDERS`                 | _(all providers)_     | Comma-separated allowlist of model providers (e.g. `Anthropic,Meta,OpenAI`) offered in the sidebar's `model_id` dropdown, matched case-insensitively against provider labels |
| `AUTOCHAT_MODEL_DISCOVERY_ENABLED`   | `true`                | Filter the dropdown to models this AWS account can actually invoke in this region, by querying the Bedrock control plane once at startup                                     |

> When `AUTOCHAT_AVAILABLE_MODELS` is unset, the dropdown is derived from `langchain_aws.data._profiles._PROFILES` and includes every model profile that supports tool calling. Models are grouped by provider and labeled with each profile's human-readable `name`. Set `AUTOCHAT_AVAILABLE_MODELS` to restrict the dropdown to a comma-separated subset.
>
> `AUTOCHAT_PROVIDERS` filters whichever model list is already in effect (the `AUTOCHAT_AVAILABLE_MODELS` override, or the full built-in catalog) down to models from the given providers only — e.g. `AUTOCHAT_PROVIDERS=Anthropic,Meta,OpenAI` limits the sidebar to those three vendors regardless of how many models each has. Provider names are matched case-insensitively against the labels produced by `get_model_provider()`/`PROVIDER_DISPLAY_NAMES`, and unrecognized names are rejected at startup (server refuses to start, same as an unknown `model_id`).
>
> `AUTOCHAT_MODEL_DISCOVERY_ENABLED` addresses a gap the profile table can't: `_PROFILES` is a **static table compiled into the installed langchain-aws release**, so it describes models that exist somewhere in AWS rather than models _this_ account can invoke in _this_ region. Selecting one of the gaps fails at call time with `ValidationException: The provided model identifier is invalid` — in `us-west-2` this affected 25 of 65 catalog entries: other regions' inference profiles (`eu.`/`jp.`/`au.` prefixes), models not offered in the account (`openai.gpt-5.4`, `openai.gpt-5.5`), and ids whose invocable form carries a version suffix the profile table omits (`openai.gpt-oss-120b` vs `openai.gpt-oss-120b-1:0`). When enabled, the plugin calls `ListFoundationModels` + `ListInferenceProfiles` once during startup and intersects the result with the catalog. Requires the `bedrock:ListFoundationModels` IAM action (and `bedrock:ListInferenceProfiles` for region-prefixed ids — if only that one is denied, foundation-model ids are still used and a warning is logged). Any failure, denial or timeout degrades to the unfiltered catalog, so discovery never blocks startup. Note that `ListFoundationModels` reports models _offered_ in the region and does not reflect per-model access grants, so a listed model can still fail with `AccessDeniedException` until access is requested in the Bedrock console.
>
> `model_id`, `fallback_model`, and every entry in `AUTOCHAT_AVAILABLE_MODELS` must be a model ID known to `_PROFILES` — the server refuses to start otherwise. The backend continues to use the raw `model_id` internally. The `temperature` and `top_p` sliders are shown or hidden together based on the selected model's `_PROFILES[...]["temperature"]` flag (there's no separate `top_p` flag, and models that disable temperature sampling generally don't accept `top_p` either). The `max_tokens` control's upper bound is capped to the selected model's `_PROFILES[...]["max_output_tokens"]`; switching models or resetting to defaults clamps the current value down (and persists the clamped value) if it would otherwise exceed the new model's limit. The `kb_top_k_results` and `kb_similarity_threshold` controls are only shown while `enable_rag` is on.
>
> Overridable parameters: `model_id`, `temperature`, `max_tokens`, `top_p`, `enable_ai_summarization`, `enable_rag`, `kb_top_k_results`, `kb_similarity_threshold`. `max_tool_calls` and `preserve_system_message` are intentionally excluded for now.

### Persisted User Settings

| Env Variable                                 | Default | Description                                                                                                            |
| -------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------- |
| `AUTOCHAT_USER_SETTINGS_PERSISTENCE_ENABLED` | `true`  | Persist each authenticated user's sidebar configuration so it survives refresh, reconnect, a new session and redeploys |

> This is the only setting. The backend and database are inferred from whatever the app already uses: Postgres when an enabled conversation/feedback/token-usage store is on `postgres` (or the KB is on `pgvector`), otherwise the SQLite file resolved from `AUTOCHAT_CONVERSATION_DB_PATH` → `AUTOCHAT_FEEDBACK_DATABASE_PATH` → `KB_DATABASE_PATH`.
>
> Requires `AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES=true`. One row per user (`user_id` primary key) holding an opaque JSON document of the overridable parameters above — there is deliberately no typed schema, since the meaningful parameter set varies per model. The row is created empty on the first authenticated connect (race safe) and only ever stores the parameters the user actually changed, so later changes to the global defaults still reach existing users. On every connect it is loaded, reconciled against the current config (a model that is no longer available, or a `max_tokens` above the selected model's cap, is dropped and reported in the `config_updated` message's `rejected_overrides`), and pushed to the client. `config_update` writes the active set back; `config_reset` empties the row rather than deleting it.
>
> Anonymous (unauthenticated) connections are unaffected — nothing is written and overrides stay session-scoped. If no usable database can be resolved, or the store cannot be opened at startup, the feature is disabled in place; chat keeps working with session-only overrides.

### Session Management

| Env Variable                         | Default | Description                                |
| ------------------------------------ | ------- | ------------------------------------------ |
| `AUTOCHAT_MAX_SESSIONS`              | `1000`  | Max concurrent sessions                    |
| `AUTOCHAT_SESSION_TIMEOUT`           | `3600`  | Session timeout (seconds)                  |
| `AUTOCHAT_MAX_CONVERSATION_MESSAGES` | `20`    | Max messages in history (count-based trim) |

### Error Handling & Retries

| Env Variable                   | Default | Description                   |
| ------------------------------ | ------- | ----------------------------- |
| `AUTOCHAT_MAX_RETRIES`         | `3`     | Max retry attempts            |
| `AUTOCHAT_RETRY_DELAY`         | `1.0`   | Initial retry delay (seconds) |
| `AUTOCHAT_EXPONENTIAL_BACKOFF` | `true`  | Use exponential backoff       |

### Token Budget / Truncation

> These thresholds are **no longer configurable** via env
> var — they are computed automatically from `AUTOCHAT_MODEL_ID`'s
> `max_input_tokens` (via `langchain_aws.data._profiles`), scaled
> proportionally from the values below (tuned for a 1,000,000-token model).
> To change truncation behavior, change `AUTOCHAT_MODEL_ID` instead. See
> [token-management.md](token-management.md#configuration) for details.

| Setting (computed, not an env var)  | Default (1M-token model) | Description                                             |
| ----------------------------------- | ------------------------ | ------------------------------------------------------- |
| `single_msg_length_threshold`       | `500000`                 | Chars that trigger per-message truncation               |
| `single_msg_truncation_target`      | `425000`                 | Target chars after per-message truncation               |
| `history_total_length_threshold`    | `650000`                 | Total history chars that trigger Stage 2                |
| `history_msg_length_threshold`      | `100000`                 | Per-message threshold in Stage 2                        |
| `history_msg_truncation_target`     | `85000`                  | Per-message target in Stage 2                           |
| `AUTOCHAT_MAX_TRUNCATION_RECURSION` | `3`                      | Max recursion for safety-net halving (still an env var) |

### AI Summarization

| Env Variable                         | Default | Description                                                                                                                                                              |
| ------------------------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `AUTOCHAT_ENABLE_AI_SUMMARIZATION`   | `false` | Enable LLM-based summarization instead of truncation                                                                                                                     |
| `AUTOCHAT_SUMMARIZATION_MODEL_ID`    | `None`  | Bedrock model id for summarization LLM calls. Falls back to `AUTOCHAT_MODEL_ID` when unset                                                                               |
| `AUTOCHAT_SUMMARIZATION_TEMPERATURE` | `None`  | Sampling temperature for summarization (0.0–1.0). When unset (and `AUTOCHAT_SUMMARIZATION_TOP_P` is also unset), falls back to `DEFAULT_SUMMARIZATION_TEMPERATURE` (0.7) |
| `AUTOCHAT_SUMMARIZATION_MAX_TOKENS`  | `None`  | Max tokens for summarization responses. Falls back to `AUTOCHAT_MAX_TOKENS` when unset                                                                                   |
| `AUTOCHAT_SUMMARIZATION_TOP_P`       | `None`  | Top-p sampling for summarization (0.0–1.0). Only applied when no summarization temperature is in effect                                                                  |

### Authentication

| Env Variable                    | Default   | Description                                  |
| ------------------------------- | --------- | -------------------------------------------- |
| `AUTOCHAT_ENABLE_TOOL_AUTH`     | `false`   | Enable authentication for tool call requests |
| `AUTOCHAT_SUPPORTED_AUTH_TYPES` | all types | Auth types accepted (list, set in code)      |
| `AUTOCHAT_DEFAULT_AUTH_TYPE`    | _(none)_  | Pre-select this auth type in the UI modal    |

### MCP (Model Context Protocol)

| Env Variable            | Default     | Description                                                                                                               |
| ----------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------- |
| `AUTOCHAT_MCP_ENABLED`  | `false`     | Master switch for the MCP Streamable HTTP endpoint. Requires the optional `[mcp]` extra (`pip install autolangchat[mcp]`) |
| `AUTOCHAT_MCP_ENDPOINT` | `/chat/mcp` | Endpoint path for the MCP server, mounted when `AUTOCHAT_MCP_ENABLED` is true                                             |

Exposes the same OpenAPI-derived tools already available to the LangGraph/Bedrock chat loop to MCP clients (Claude Desktop, VS Code Copilot, etc.) as a pure `tools/list`/`tools/call` provider — MCP clients bring their own LLM and tool-calling loop, so the chat loop itself is not reused for this endpoint.

**Auth** is extracted per-request from the incoming HTTP headers (no separate env vars — it mirrors `AUTOCHAT_SUPPORTED_AUTH_TYPES`), detected in this order:

| Auth type                 | Header(s)                                                                                       | Notes                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------- | ----------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Bearer token              | `Authorization: Bearer <token>`                                                                 | Forwarded as-is to the target API                                                                                                                                                                                                                                                                                                                                                                           |
| Basic auth                | `Authorization: Basic <base64(user:pass)>`                                                      | Standard HTTP Basic                                                                                                                                                                                                                                                                                                                                                                                         |
| OAuth2 Client Credentials | `X-OAuth2-Client-Id`, `X-OAuth2-Client-Secret`, `X-OAuth2-Token-Url`, optional `X-OAuth2-Scope` | All three required headers must be present together; a fresh token exchange happens on every request (no cross-request caching, since the MCP session manager is stateless)                                                                                                                                                                                                                                 |
| API key                   | `X-API-Key`                                                                                     |                                                                                                                                                                                                                                                                                                                                                                                                             |
| Custom headers            | `X-Forward-<Name>`                                                                              | Forwarded verbatim as `<Name>` (prefix stripped)                                                                                                                                                                                                                                                                                                                                                            |
| SSO                       | `Authorization: Bearer <session_token>`                                                         | Only recognized when `AUTOCHAT_SSO_ENABLED=true`. The `session_token` comes from the existing `/chat/auth/sso/login` web flow (unchanged) — the MCP layer swaps in the underlying IdP access token for the actual tool call. When enabled, `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` are also published so spec-compliant MCP clients can auto-discover the IdP |

If none of the above match, the tool call executes unauthenticated (unless `AUTOCHAT_REQUIRE_TOOL_AUTH=true`, in which case the request is rejected with an error instead).

**Example MCP client configs** (VS Code `mcp.json` — see [Add and manage MCP servers](https://code.visualstudio.com/docs/agent-customization/mcp-servers) for other clients/formats):

```jsonc
// Bearer token
{
  "servers": {
    "my-api": {
      "type": "http",
      "url": "http://localhost:8000/chat/mcp",
      "headers": { "Authorization": "Bearer ${input:my_api_token}" }
    }
  },
  "inputs": [{ "id": "my_api_token", "type": "promptString", "description": "Bearer token", "password": true }]
}
```

```jsonc
// Basic auth
{
  "servers": {
    "my-api": {
      "type": "http",
      "url": "http://localhost:8000/chat/mcp",
      "headers": { "Authorization": "Basic ${input:my_api_basic_b64}" }
    }
  },
  "inputs": [{ "id": "my_api_basic_b64", "type": "promptString", "description": "base64(user:pass)", "password": true }]
}
```

```jsonc
// API key
{
  "servers": {
    "my-api": {
      "type": "http",
      "url": "http://localhost:8000/chat/mcp",
      "headers": { "X-API-Key": "${input:my_api_key}" }
    }
  },
  "inputs": [{ "id": "my_api_key", "type": "promptString", "description": "API key", "password": true }]
}
```

```jsonc
// OAuth2 Client Credentials
{
  "servers": {
    "my-api": {
      "type": "http",
      "url": "http://localhost:8000/chat/mcp",
      "headers": {
        "X-OAuth2-Client-Id": "${input:my_api_client_id}",
        "X-OAuth2-Client-Secret": "${input:my_api_client_secret}",
        "X-OAuth2-Token-Url": "https://idp.example.com/oauth2/token"
      }
    }
  },
  "inputs": [
    { "id": "my_api_client_id", "type": "promptString", "description": "OAuth2 client_id" },
    { "id": "my_api_client_secret", "type": "promptString", "description": "OAuth2 client_secret", "password": true }
  ]
}
```

```jsonc
// Custom headers (forwarded as Tenant-Id, Region)
{
  "servers": {
    "my-api": {
      "type": "http",
      "url": "http://localhost:8000/chat/mcp",
      "headers": { "X-Forward-Tenant-Id": "acme", "X-Forward-Region": "us-east" }
    }
  }
}
```

```jsonc
// SSO — session_token obtained by visiting http://localhost:8000/chat/auth/sso/login
// in a browser first, then presented the same way as a bearer token
{
  "servers": {
    "my-api": {
      "type": "http",
      "url": "http://localhost:8000/chat/mcp",
      "headers": { "Authorization": "Bearer ${input:my_api_sso_session_token}" }
    }
  },
  "inputs": [
    { "id": "my_api_sso_session_token", "type": "promptString", "description": "SSO session_token", "password": true }
  ]
}
```

> Since `${input:...}` values are cached by the MCP client after the first prompt (not re-validated against expiry), rotate the input `id` (e.g. append a version/date suffix) to force a fresh prompt when a credential is rotated — or hardcode the header values directly in `mcp.json` if your rotation cadence makes that simpler, keeping in mind the file is plaintext and may be swept up by the client's own settings-sync feature if enabled.

| Env Variable         | Default | Description                             |
| -------------------- | ------- | --------------------------------------- |
| `AUTOCHAT_LOG_LEVEL` | `INFO`  | Log level (DEBUG, INFO, WARNING, ERROR) |

### Knowledge Base / RAG

| Env Variable              | Default                      | Description                                               |
| ------------------------- | ---------------------------- | --------------------------------------------------------- |
| `ENABLE_RAG`              | `false`                      | Enable Retrieval-Augmented Generation with knowledge base |
| `KB_SOURCES_CONFIG`       | `kb_sources.yaml`            | Path to YAML file defining KB content sources             |
| `KB_POPULATE_ON_STARTUP`  | `false`                      | Auto-populate KB on startup (dev only)                    |
| `KB_ALLOW_EMPTY`          | `false`                      | Allow app to start with empty KB                          |
| `KB_EMBEDDING_MODEL`      | `amazon.titan-embed-text-v1` | Bedrock model for generating embeddings                   |
| `KB_CHUNK_SIZE`           | `512`                        | Token size for text chunks                                |
| `KB_CHUNK_OVERLAP`        | `100`                        | Token overlap between chunks                              |
| `KB_TOP_K_RESULTS`        | `5`                          | Number of top chunks to retrieve per query                |
| `KB_SIMILARITY_THRESHOLD` | `0.3`                        | Minimum similarity score for results                      |
| `KB_SEMANTIC_WEIGHT`      | `0.7`                        | Weight for semantic (embedding) score in hybrid search    |
| `KB_KEYWORD_WEIGHT`       | `0.3`                        | Weight for keyword (FTS) score in hybrid search           |

### KB Storage Backend

| Env Variable                       | Default                  | Description                                       |
| ---------------------------------- | ------------------------ | ------------------------------------------------- |
| `AUTOCHAT_KB_STORAGE_TYPE`         | `sqlite`                 | Storage backend: `sqlite` or `pgvector`           |
| `KB_DATABASE_PATH`                 | `data/knowledge_base.db` | SQLite database file path (sqlite backend only)   |
| `AUTOCHAT_KB_POSTGRES_URL`         | _(none)_                 | PostgreSQL connection URL (pgvector backend only) |
| `AUTOCHAT_KB_POSTGRES_POOL_SIZE`   | `5`                      | Connection pool size for PostgreSQL               |
| `AUTOCHAT_KB_EMBEDDING_DIMENSIONS` | `1536`                   | Embedding vector dimensions (must match model)    |

> See [RAG Feature](rag-feature) for storage backend details, Docker Compose setup, and production recommendations.

---

## Code-Only Settings

These must be passed directly to `add_autolangchat()`:

| Parameter              | Type        | Description                        |
| ---------------------- | ----------- | ---------------------------------- |
| `allowed_paths`        | `List[str]` | API paths the AI can call as tools |
| `excluded_paths`       | `List[str]` | API paths to hide from AI          |
| `cors_origins`         | `List[str]` | CORS allowed origins               |
| `supported_auth_types` | `List[str]` | Auth types allowed                 |

---

## Overriding .env Values in Code

```python
autolangchat_plugin = add_autolangchat(
    app,
    model_id="us.anthropic.claude-sonnet-5",  # overrides AUTOCHAT_MODEL_ID
    temperature=0.3,
    max_tokens=8192,
    system_prompt="You are a helpful customer support assistant.",
    allowed_paths=["/api/products", "/api/orders"],
    enable_ui=True
)
```

---

## Environment Files

The plugin automatically selects the `.env` file:

- Under `pytest`: uses `.env.test`
- Otherwise: uses `.env`
