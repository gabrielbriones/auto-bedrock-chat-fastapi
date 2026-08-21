# Token Usage Tracking

> This feature _records_ per-turn token counts for observability/billing; it
> does not affect model behavior, context-window budgeting, or truncation.
> See [Token Management](token-management) for the context-window budget
> system.

Every chat turn already reports `input_tokens` and `output_tokens` — accumulated
across any LLM tool-call rounds — in the `ai_response` WebSocket payload's
`metadata`. When enabled, the plugin additionally persists those counts per
turn to a `TokenUsageStore` backend (SQLite or Postgres), so they can be
queried later for usage dashboards, cost attribution, or auditing.

---

## Enabling

Token usage recording is **off by default**. Enable it by setting:

```bash
AUTOCHAT_TOKEN_USAGE_ENABLED=true
```

The storage backend defaults to **SQLite** so no database setup is required
for local development. To use Postgres in production, set:

```bash
AUTOCHAT_TOKEN_USAGE_STORAGE_TYPE=postgres
AUTOCHAT_TOKEN_USAGE_POSTGRES_URL=postgresql://token_usage:secret@db:5432/token_usage
```

If `AUTOCHAT_TOKEN_USAGE_POSTGRES_URL` is not set, the store falls back to
`AUTOCHAT_FEEDBACK_POSTGRES_URL`, then `AUTOCHAT_KB_POSTGRES_URL`, so a single
Postgres instance can host all three schemas. Likewise, when
`token_usage_storage_type=sqlite` and `AUTOCHAT_TOKEN_USAGE_DATABASE_PATH` is
unset, the SQLite backend falls back to `AUTOCHAT_FEEDBACK_DATABASE_PATH`,
then `KB_DATABASE_PATH`.

The optional [postgres] extra is required for the Postgres backend:

```bash
pip install "autolangchat[postgres]"
```

When enabled, the FastAPI plugin opens the backend on startup and closes it
on shutdown. If open fails (DB unreachable), token usage recording is
disabled in-place and the rest of the app keeps working — chat responses are
still delivered normally, just without the persisted record for that turn.

### Configuration reference

| Setting                     | Env var                              | Default  | Notes                                                                             |
| --------------------------- | ------------------------------------ | -------- | --------------------------------------------------------------------------------- |
| `token_usage_enabled`       | `AUTOCHAT_TOKEN_USAGE_ENABLED`       | `false`  | Master switch                                                                     |
| `token_usage_storage_type`  | `AUTOCHAT_TOKEN_USAGE_STORAGE_TYPE`  | `sqlite` | `sqlite` (zero-config) or `postgres`                                              |
| `token_usage_database_path` | `AUTOCHAT_TOKEN_USAGE_DATABASE_PATH` | `None`   | SQLite file path; falls back to `feedback_database_path`, then `KB_DATABASE_PATH` |
| `token_usage_postgres_url`  | `AUTOCHAT_TOKEN_USAGE_POSTGRES_URL`  | `None`   | Falls back to `AUTOCHAT_FEEDBACK_POSTGRES_URL`, then `AUTOCHAT_KB_POSTGRES_URL`   |

---

## What gets recorded

For each chat turn, one row is written with:

- `id` — the stable message id also sent to the client as `ai_response.message_id`
- `session_id`, `user_id` — turn context (`user_id` is `None` for anonymous sessions)
- `model_id` — the model that actually produced the response (reflects fallback-model substitution, if triggered)
- `input_tokens`, `output_tokens` — accumulated across all tool-call rounds for that turn
- `turn_ts` — when the turn completed

Writes are idempotent (`INSERT OR IGNORE` / `ON CONFLICT DO NOTHING` keyed on
`id`), and persistence failures are logged and swallowed rather than
propagated — a token-usage write failure never prevents the chat response
from being delivered.

## Recording mechanism

Recording happens inside the compiled LangGraph `chat_graph` itself, in a
terminal `token_usage_node` (runs after `citation_boost`, before `END`) —
not in the WebSocket transport layer. This means _any_ caller of
`chat_graph.ainvoke()` gets its usage recorded, not just
`WebSocketChatHandler` (e.g. a standalone script, a batch job, or a future
REST endpoint).

The node is a no-op unless all of the following are present in
`config["configurable"]`:

| Key                 | Required | Notes                                                    |
| ------------------- | -------- | -------------------------------------------------------- |
| `token_usage_store` | required | A `BaseTokenUsageStore` instance. Absent/`None` ⇒ no-op. |
| `chat_config`       | required | Must have `token_usage_enabled=True`.                    |
| `session_id`        | optional | Falls back to `thread_id` when omitted.                  |
| `user_id`           | optional | Falls back to `None` (anonymous) when omitted.           |

`token_usage_store` and `chat_config` don't need to be passed on every
`ainvoke()` call: `build_chat_graph(config, tool_manager, token_usage_store)`
accepts the store once at graph-build time and injects it into every node
call automatically (the same mechanism already used for `chat_config` and
`tool_manager`). `AutoLangChatPlugin` wires its own
`self._token_usage_store` in this way, so **any** app built on
`plugin.chat_graph` — the WebSocket handler, or another component invoking
`plugin.chat_graph.ainvoke()` directly — gets usage recorded with no extra
wiring. `session_id`/`user_id` are still per-turn and must come from each
call's own `configurable` dict (or are left to fall back to `thread_id`/
`None`). A caller building its own bare graph via `build_chat_graph(config)`
(e.g. a standalone script with no plugin) can still override
`token_usage_store` per-call via `config["configurable"]["token_usage_store"]`
— see `autolangchat/graph/nodes/token_usage.py` and `autolangchat/graph/graph.py`.

## Querying the recorded data

Once enabled, recorded rows can be queried through the
`/admin/tokens/*` endpoints (per-model summary, per-user history,
per-day aggregates, and top-users ranking) — see
[Admin API — Token Usage Analytics](admin-api#token-usage-analytics).
Those routes are only registered when a token-usage store is configured,
mirroring the Feedback/KB admin route groups.

## Out of scope

Embedding-token tracking is not implemented: the Bedrock Titan and Cohere
embedding APIs used by the [RAG feature](rag-feature) don't return a token
count in their response bodies. Only LLM chat-turn tokens are tracked.
