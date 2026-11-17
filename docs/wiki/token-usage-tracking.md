# Token Usage Tracking

> This feature _records_ per-turn token counts for observability/billing; it
> does not affect model behavior, context-window budgeting, or truncation.
> See [Token Management](token-management) for the context-window budget
> system.

Every chat turn reports its usage in the `ai_response` WebSocket payload's
`metadata.usage` — the sum of **every** AI call the turn caused (chat rounds,
retries, summarization chunks and the conversation title) plus an estimated
cost — whether or not persistence is enabled. When enabled, the plugin
additionally persists those counts per turn and per call to a
`TokenUsageStore` backend (SQLite or Postgres), so they can be queried later
for usage dashboards, cost attribution, or auditing.

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

| Setting                     | Env var                              | Default  | Notes                                                                                          |
| --------------------------- | ------------------------------------ | -------- | ---------------------------------------------------------------------------------------------- |
| `token_usage_enabled`       | `AUTOCHAT_TOKEN_USAGE_ENABLED`       | `false`  | Master switch                                                                                  |
| `token_usage_storage_type`  | `AUTOCHAT_TOKEN_USAGE_STORAGE_TYPE`  | `sqlite` | `sqlite` (zero-config) or `postgres`                                                           |
| `token_usage_database_path` | `AUTOCHAT_TOKEN_USAGE_DATABASE_PATH` | `None`   | SQLite file path; falls back to `feedback_database_path`, then `KB_DATABASE_PATH`              |
| `token_usage_postgres_url`  | `AUTOCHAT_TOKEN_USAGE_POSTGRES_URL`  | `None`   | Falls back to `AUTOCHAT_FEEDBACK_POSTGRES_URL`, then `AUTOCHAT_KB_POSTGRES_URL`                |
| `token_usage_write_timeout` | `AUTOCHAT_TOKEN_USAGE_WRITE_TIMEOUT` | `5.0`    | Seconds before a usage write is abandoned and queued for retry (turn still completes normally) |
| `pricing_table`             | `AUTOCHAT_PRICING_TABLE`             | `None`   | JSON pricing table replacing the packaged one (see [Pricing](#pricing))                         |

---

## Per-turn usage and cost

Every plugin-owned AI call caused by a turn is recorded as one **usage event**
in a per-turn `UsageLedger` (`autolangchat/usage/ledger.py`), passed to the
graph as `config["configurable"]["usage_ledger"]`:

| `purpose`       | Recorded by                                                                    |
| --------------- | ------------------------------------------------------------------------------ |
| `chat`          | `llm_call_node`, once per tool-call round                                      |
| `retry`         | `llm_call_node`, for emergency re-truncation, fallback-model and inference-profile retries (with the model that actually answered) |
| `summarization` | `MessagePreprocessor`, once per AI summarization chunk                         |
| `title`         | The WebSocket handler's conversation auto-titling                              |

Each event carries `turn_id`, `provider`, `model_id`, `input_tokens`,
`output_tokens`, `cache_read_tokens`, `cache_write_tokens` and a timestamp.
Measurement happens regardless of `token_usage_enabled`; that setting only
controls persistence. Callers that invoke the graph without a ledger get a
single `chat` event built from the turn's accumulated metadata.

`token_usage_node` sums the ledger, prices it and attaches the result to the
final assistant message as `metadata.usage`, which the WebSocket handler sends
in `ai_response.metadata.usage`:

```json
{ "input_tokens": 1320, "output_tokens": 280, "cache_read": 400, "cache_write": 0,
  "estimated_cost": 0.00792, "currency": "USD", "final": false }
```

### Final vs pending

The conversation title is generated after `ai_response` is sent, so on the
turn that creates a conversation the live summary is **pending**
(`final: false`). Once the title call finishes (or is skipped because the
conversation already has a title), the handler records the `title` event,
upserts the turn's row with `final = true` and sends a
[`usage_updated`](websocket-client.md#usage_updated) frame carrying the final
totals. Every other turn is `final: true` straight away.

Stand-alone admin synthesis (`FeedbackSynthesizer`) is never recorded into a
chat turn: each synthesis or ingestion operation collects its calls in its own
short-lived ledger, logs the totals when it ends and then releases it.

### Pricing

`autolangchat/usage/pricing.py` prices each event with its own model, so a
fallback-model retry is billed at the fallback model's rate. Rates come from
the packaged, versioned `autolangchat/usage/pricing.json`, or from
`AUTOCHAT_PRICING_TABLE` when set:

```json
{
  "pricing_revision": "2026-11-17-v2",
  "currency": "USD",
  "models": {
    "anthropic:anthropic.claude-sonnet-4-5-20250929-v1:0": {
      "input_per_million": 3.0,
      "output_per_million": 15.0,
      "cache_read_per_million": 0.3,
      "cache_write_per_million": 3.75,
      "cache_write_1h_per_million": 6.0
    }
  }
}
```

Keys are `provider:model_id`; a region-prefixed inference-profile id
(`us.anthropic.…`) falls back to the bare model's entry when it has no entry of
its own. If **any** event in a turn uses a model missing from the table, the
estimate is unknown: `estimated_cost` is `null` (never `0`) and
`estimated_cost_micros` is stored as `NULL`. Uncached input is priced as
`input_tokens - cache_read_tokens - cache_write_tokens` (see
[Prompt-cache tokens](#prompt-cache-tokens)). Cache writes made with the
one-hour TTL (`prompt_cache_ttl="1h"`) are priced at
`cache_write_1h_per_million`; the estimate is unknown when a table entry lacks
that rate and the turn wrote one-hour cache entries. The packaged table only lists
models with published rates; set `AUTOCHAT_PRICING_TABLE` to price others.

---

## What gets recorded

For each chat turn, one `token_usage` row is written with:

- `id` — the stable message id also sent to the client as `ai_response.message_id`
- `session_id`, `user_id` — turn context (`user_id` is `None` for anonymous sessions)
- `model_id` — the model that actually produced the response (reflects fallback-model substitution, if triggered)
- `input_tokens`, `output_tokens` — summed over every usage event of the turn
- `cache_read_tokens`, `cache_write_tokens` — prompt-cache tokens for that turn (see [Prompt-cache tokens](#prompt-cache-tokens)); `0` when nothing was cached
- `estimated_cost_micros`, `currency`, `pricing_revision` — the cost estimate in millionths of `currency`; `NULL` when any model was unpriced
- `final` — `false` while the title call is still pending
- `turn_ts` — when the turn's first AI call happened

and one `token_usage_events` row per usage event (`id` is the event id,
plus `turn_id`, `purpose`, `provider`, `model_id`, the four token counts and
`event_ts`).

`record_turn` is an upsert keyed on `id`: a pending row is replaced by the
final one, while a final row is never overwritten, so retried writes are
idempotent. Event writes ignore event ids that already exist. A failed write
is logged and kept in a bounded in-memory retry queue (oldest dropped when
full), and one queued write is replayed after each successful write — a token-usage write failure
never prevents the chat response from being delivered.

## Prompt-cache tokens

With [prompt caching](configuration.md#prompt-caching) enabled, part of a
turn's input is read from or written to the Bedrock prompt cache, and each of
those is billed at its own rate. Bedrock's Converse API reports them
separately (`inputTokens` excludes cached tokens), while LangChain's
`usage_metadata` folds all three into `input_tokens` and breaks the cached part
out in `input_token_details`. The chat graph keeps that convention:

| Field                | Meaning                                                                            |
| -------------------- | ---------------------------------------------------------------------------------- |
| `input_tokens`       | **All** prompt tokens: uncached + cache read + cache write                         |
| `cache_read_tokens`  | Prompt tokens served from the cache (`input_token_details.cache_read`)             |
| `cache_write_tokens` | Prompt tokens written to the cache (`cache_creation`, or the per-TTL breakdown)    |
| `output_tokens`      | Generated tokens                                                                   |

So uncached input is `input_tokens - cache_read_tokens - cache_write_tokens`,
and an estimated cost is:

```text
uncached × input price + cache_read × cache-read price
  + cache_write × cache-write price + output_tokens × output price
```

Totals that add `input_tokens + output_tokens` (e.g. the top-users ranking)
already include cached tokens, so the cache columns must not be added on top.
The streaming and non-streaming paths report identical counts. The counts appear
in the `ai_response` metadata (top-level totals and the per-call `usage` object,
see [WebSocket Client](websocket-client.md)), in the stored rows, in every
`/admin/tokens/*` response, and as separate **Cache read** / **Cache write**
columns on the admin usage dashboard.

Existing token-usage tables gain the new columns automatically the next time
the store opens: SQLite always migrates on open (`ALTER TABLE ... ADD COLUMN`
for each missing column); Postgres applies `ADD COLUMN IF NOT EXISTS` with the
rest of its schema bootstrap. Rows written before `final` existed default to
`final = true`. The `token_usage_events` table is created by the schema
bootstrap (`init_schema=True`); deployments that provision DDL themselves must
apply the updated `token_usage_schema*.sql`.

## Recording mechanism

Recording happens inside the compiled LangGraph `chat_graph` itself, in a
terminal `token_usage_node` (runs after `citation_boost`, before `END`) —
not in the WebSocket transport layer. This means _any_ caller of
`chat_graph.ainvoke()` gets its usage summarized and recorded, not just
`WebSocketChatHandler` (e.g. a standalone script, a batch job, or a future
REST endpoint). The usage summary is always attached; persistence is a no-op
unless all of the following are present in `config["configurable"]`:

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
Per-turn rows include `turn_id`, `estimated_cost_micros`, `currency`,
`pricing_revision` and `final`; aggregates include the summed
`estimated_cost_micros` of priced turns, its `currency` and
`unpriced_turn_count`. The summed cost and `currency` are `null` when the
priced turns in a row span more than one currency.
Those routes are only registered when a token-usage store is configured,
mirroring the Feedback/KB admin route groups.

## Out of scope

Embedding-token tracking is not implemented: the Bedrock Titan and Cohere
embedding APIs used by the [RAG feature](rag-feature) don't return a token
count in their response bodies, so no `embedding` events are recorded yet.
