# Feedback Collection

> This feature _collects_ feedback on AI responses; it does **not** improve
> responses on its own. Quality improvements become visible once approved
> feedback is synthesized back into the knowledge base.

The feedback collection backend lets authorized users submit ratings,
comments, and corrections on AI responses through the existing chat
WebSocket. Submissions are persisted with full provenance (original query,
AI response, KB sources used, model id) so an expert reviewer can later
approve, reject, and tag them.

When the built-in chat UI is enabled, users see thumbs-up / thumbs-down
buttons under each assistant message; 👎 opens an inline correction form
(optional correction text + comment).

---

## Enabling

Feedback collection is **off by default**. Enable it by setting:

```bash
AUTOCHAT_FEEDBACK_ENABLED=true
```

The storage backend defaults to **SQLite** so no database setup is
required for local development. To use Postgres in production, set:

```bash
AUTOCHAT_FEEDBACK_STORAGE_TYPE=postgres
AUTOCHAT_FEEDBACK_POSTGRES_URL=postgresql://feedback:secret@db:5432/feedback
```

If `AUTOCHAT_FEEDBACK_POSTGRES_URL` is not set, the store falls back to
`AUTOCHAT_KB_POSTGRES_URL` so a single Postgres instance can host both
schemas. Likewise, when `feedback_storage_type=sqlite` and
`AUTOCHAT_FEEDBACK_DATABASE_PATH` is unset, the SQLite backend reuses
`KB_DATABASE_PATH`.

The optional [postgres] extra is required for the Postgres backend:

```bash
pip install "autolangchat[postgres]"
```

When enabled, the FastAPI plugin opens the backend on startup and closes
it on shutdown. If the host app uses Starlette `lifespan=` instead of
`on_event`, ensure the feedback store is explicitly opened during app
startup, because the plugin's startup hook may not run in that setup. If
open fails (DB unreachable), feedback is disabled in-place and the rest
of the app keeps working — clients that submit feedback messages receive
a `feedback_unavailable` error rather than a crash.

### Configuration reference

| Setting                       | Env var                                | Default  | Notes                                                                     |
| ----------------------------- | -------------------------------------- | -------- | ------------------------------------------------------------------------- |
| `feedback_enabled`            | `AUTOCHAT_FEEDBACK_ENABLED`            | `false`  | Master switch                                                             |
| `feedback_allow_anonymous`    | `AUTOCHAT_FEEDBACK_ALLOW_ANONYMOUS`    | `false`  | Render the UI and accept submissions even without a `user_id` (dev/local) |
| `feedback_storage_type`       | `AUTOCHAT_FEEDBACK_STORAGE_TYPE`       | `sqlite` | `sqlite` (zero-config) or `postgres`                                      |
| `feedback_database_path`      | `AUTOCHAT_FEEDBACK_DATABASE_PATH`      | `None`   | SQLite file path; falls back to `KB_DATABASE_PATH`                        |
| `feedback_postgres_url`       | `AUTOCHAT_FEEDBACK_POSTGRES_URL`       | `None`   | Falls back to `AUTOCHAT_KB_POSTGRES_URL`                                  |
| `feedback_postgres_pool_size` | `AUTOCHAT_FEEDBACK_POSTGRES_POOL_SIZE` | `5`      | Async pool max size (Postgres only)                                       |
| `feedback_init_schema`        | `AUTOCHAT_FEEDBACK_INIT_SCHEMA`        | `true`   | Set `false` if a separate provisioning task owns the DDL lifecycle        |

---

## UI rendering gate

The built-in chat template renders rating controls only when the server
injects `window.CONFIG.feedbackEnabled = true`. The gate is computed in
[`plugin.py`](../../autolangchat/plugin.py) at every chat-UI
request and is **feature-only** — it answers "could this deployment
accept a feedback submission?", not "is _this user_ allowed to submit?":

```
feedbackEnabled = feedback_enabled
               AND feedback_store_is_initialized
               AND ( sso_enabled
                  OR auth_verification_endpoint is set
                  OR feedback_allow_anonymous )
```

The last clause suppresses the UI when no `user_id` can ever be produced
(no SSO + no auth-verification endpoint) and anonymous submissions are
disallowed, so users never see controls that would always error.

> **Hiding the UI is not a security boundary.** Per-user authorization
> is enforced at WebSocket-message time by `FeedbackAuthorizer.can_submit`
> (see [Authorization](#authorization)). Anyone editing the rendered HTML
> in dev tools can still send a `feedback` frame; the server will reject
> it with `unauthorized_feedback` if the authorizer says so.

---

## WebSocket message protocol

The chat client submits a `feedback` message over the existing chat
WebSocket. The server replies with a `feedback_ack` (success) or an `error`
(any failure path).

### Submitting feedback (client → server)

```json
{
  "type": "feedback",
  "message_id": "msg-uuid-of-the-rated-response",
  "rating": "negative",
  "score": 2,
  "correction_text": "IPC = instructions / cycles, not cycles / instructions",
  "user_comment": "This calculation was inverted"
}
```

| Field             | Type   | Required    | Notes                                                         |
| ----------------- | ------ | ----------- | ------------------------------------------------------------- |
| `type`            | string | yes         | Must be `"feedback"`                                          |
| `message_id`      | string | yes         | The `message_id` echoed by a previous `ai_response`           |
| `rating`          | enum   | yes         | `positive` \| `negative`                                      |
| `score`           | int    | no          | 1–5                                                           |
| `correction_text` | string | conditional | Optional proposed fix; only valid when `rating == "negative"` |
| `user_comment`    | string | no          | Free-text                                                     |

The server resolves the original `query`, `ai_response`, `kb_sources_used`,
and `model_id` from session history using `message_id`, so the client does
not need to repeat them.

> **Note:** Outgoing `ai_response` payloads now include a `message_id`
> field. Clients must capture and echo it on the corresponding `feedback`
> message.

### Acknowledgement (server → client)

```json
{
  "type": "feedback_ack",
  "message_id": "msg-uuid-of-the-rated-response",
  "feedback_id": "8c0c3f0e-...",
  "status": "pending_review",
  "timestamp": "2026-05-11T12:34:56.789012"
}
```

The `message_id` is echoed back so the client can reconcile optimistic UI
state without bookkeeping a request-id.

### Error envelope

Any failure returns a dedicated `feedback_error` envelope so the client
can route it separately from generic chat errors. `code` enables
programmatic branching; `message` is human-readable and safe to display:

```json
{
  "type": "feedback_error",
  "code": "invalid_feedback",
  "message": "correction_text is only allowed when rating is 'negative'",
  "message_id": "msg-uuid-of-the-rated-response",
  "timestamp": "2026-05-11T12:34:56.789012"
}
```

`message_id` is included whenever the failing payload carried one (which
is every realistic case besides a malformed JSON frame). The chat-client
uses it to locate and revert the optimistic "✓ Feedback submitted"
indicator on the corresponding message.

| `code`                  | When it fires                                                                                                                               |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `feedback_unavailable`  | Backend disabled or pool failed to open                                                                                                     |
| `unauthorized_feedback` | Authorizer rejected the user                                                                                                                |
| `invalid_feedback`      | Missing/unknown `message_id`, missing/unknown `rating`, validator failure (e.g. `correction_text` on a positive rating, `score` out of 1–5) |
| `feedback_error`        | Persistence failed downstream                                                                                                               |

---

## Authorization

Submission is gated by a pluggable `FeedbackAuthorizer`:

```python
class FeedbackAuthorizer(Protocol):
    def can_submit(self, user_id: Optional[str]) -> bool: ...
```

The default `AuthenticatedUserAuthorizer` accepts any non-empty `user_id`.
When `AUTOCHAT_FEEDBACK_ALLOW_ANONYMOUS=true`, the plugin constructs the
authorizer with `allow_anonymous=True` so submissions without a resolved
user identity are accepted (intended for local development and
standalone deployments without SSO / auth-verification). Anonymous rows
are persisted with `user_id = "anonymous"` (sentinel) — never an empty
string — so audit and history queries can distinguish them from real
user identifiers.

The dedicated access-control task swaps in a role/group-aware
implementation by passing it to `WebSocketChatHandler`:

```python
handler = WebSocketChatHandler(
    ...,
    feedback_store=store,
    feedback_authorizer=MyRoleAwareAuthorizer(),
)
```

---

## `FeedbackStore` API

> **Looking for the production review workflow?** The HTTP-level
> review surface — list pending feedback, approve / reject with tags,
> view stats — is documented on the [Admin API](admin-api) page. The
> `FeedbackStore` API below is the underlying async data-access layer,
> primarily of interest to plugin embedders and the synthesizer.

The async data-access classes are exposed for the admin-API and
synthesizer. Both backends implement the same
[`BaseFeedbackStore`](../../autolangchat/db/feedback_base.py)
interface; pick one via the factory:

```python
from autolangchat.db import create_feedback_store

store = create_feedback_store(config)  # returns SQLite or Postgres impl
async with store:
    pending = await store.list_pending(limit=20)
    await store.update_review(
        pending[0].id,
        ReviewStatus.APPROVED,
        reviewer_id="alice@example.com",
        tags=["perf", "ipc"],
        comment="Verified",
    )
    summary = await store.stats()
```

Direct instantiation is also available:

```python
from autolangchat.db import SQLiteFeedbackStore, PostgresFeedbackStore

sqlite_store = SQLiteFeedbackStore(db_path="data/feedback.db")
pg_store = PostgresFeedbackStore(connection_url="postgresql://…", pool_max_size=5)
```

| Method                                                             | Purpose                                                               |
| ------------------------------------------------------------------ | --------------------------------------------------------------------- |
| `create(entry)`                                                    | Insert a `FeedbackEntry`; returns the persisted row                   |
| `get(feedback_id)`                                                 | Lookup by id; returns `None` if missing                               |
| `list_pending(limit=50, offset=0)`                                 | Pending entries oldest-first (admin queue)                            |
| `list_by_tags(tags)`                                               | Entries whose `reviewer_tags` overlap with `tags`                     |
| `list_by_date_range(start, end, status=None, limit=200, offset=0)` | Date-range query, newest-first                                        |
| `update_review(id, status, reviewer_id, tags, comment)`            | Apply a reviewer decision; transactional with status-transition check |
| `stats()`                                                          | Aggregate counts by status and rating                                 |

Status transitions (enforced by both `update_review` and the DB):

| From             | Allowed targets        |
| ---------------- | ---------------------- |
| `pending_review` | `approved`, `rejected` |
| `approved`       | `approved`, `rejected` |
| `rejected`       | `approved`, `rejected` |

Once a decision is recorded, admins may update it freely — including
changing only the tags or comment while keeping the same decision.
`pending_review` can never be set as a target via `update_review`.

Future work: if review entries are later linked to KB articles, the store
can add a guard (for example, via a `kb_article_id` field) to block further
updates until that KB linkage is rolled back.

---

## Schema

The DDL ships in two flavors:

- Postgres: [`autolangchat/db/sql/feedback_schema.sql`](../../autolangchat/db/sql/feedback_schema.sql)
- SQLite: [`autolangchat/db/sql/feedback_schema_sqlite.sql`](../../autolangchat/db/sql/feedback_schema_sqlite.sql)

All statements are idempotent; either file can be applied directly with
`psql -f` / `sqlite3 /path/to/feedback.db < feedback_schema_sqlite.sql` by the
database-provisioning task or auto-bootstrapped by the store on startup
(`feedback_init_schema=true`).

The schema enforces the same validation rules as the Pydantic
[`FeedbackEntry`](../../autolangchat/models.py) model via
`CHECK` constraints, so direct DB writes that bypass the application can't
introduce invalid rows.

### Migration notes

The `Rating` enum used to include a third value `"correction"` that was
retired in favor of the orthogonal `correction_text` field. The current
schema (and Pydantic model) only allow `"positive"` / `"negative"`.

Long-lived dev databases that still contain rows with
`rating='correction'` are handled defensively on store init:

- **Read path** — `FeedbackEntry` has a `mode="before"` validator on
  `rating` that coerces `"correction"` → `"negative"`, so hydration
  never fails.
- **Write path** — both `SQLiteFeedbackStore.__init__` and
  `PostgresFeedbackStore.open` run an idempotent
  `UPDATE feedback SET rating='negative' WHERE rating='correction'`
  on startup and log a `WARNING` with the affected row count when
  any rows are rewritten.

The Postgres `feedback_rating` enum value is **not** dropped — keeping
it in the type lets the migration succeed and avoids a downtime-prone
`ALTER TYPE` dance. Fresh deployments get the new 2-value enum from
the `IF NOT EXISTS`-guarded DDL.

---

## Testing

Unit tests run without a database:

```bash
poetry run pytest tests/test_feedback_store.py -v
```

The SQLite backend has its own test suite (no external service required):

```bash
poetry run pytest tests/test_sqlite_feedback_store.py -v
```

Server-side rendering gate and WebSocket payload contract:

```bash
poetry run pytest tests/test_feedback_ui_flag.py tests/test_feedback_ws_payload_contract.py -v
```

Integration tests against a live Postgres are gated on
`TEST_FEEDBACK_PG_URL`:

```bash
export TEST_FEEDBACK_PG_URL="postgresql://feedback:feedback@localhost:5432/feedback_test"
poetry run pytest tests/test_feedback_store_integration.py -v
```

---

## Scope reminder

This feature deliberately does not change AI behavior. Stored feedback feeds
the synthesizer, which turns approved entries into KB articles that the RAG
retriever then uses on subsequent queries. See
[Feedback Synthesis](feedback-synthesis) for details.
