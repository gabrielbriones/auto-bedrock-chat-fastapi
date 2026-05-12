# Feedback Collection

> **Phase 2 of the Continuous Learning Loop.** This phase _collects_ feedback
> on AI responses; it does **not** improve responses on its own. Quality
> improvements become visible in Phase 3 once approved feedback is synthesized
> back into the knowledge base.

The feedback collection backend lets authorized users submit ratings,
comments, and corrections on AI responses through the existing chat
WebSocket. Submissions are persisted with full provenance (original query,
AI response, KB sources used, model id) so an expert reviewer can later
approve, reject, and tag them.

Tracked under [XMGPLAT-10417](https://jira.devtools.intel.com/browse/XMGPLAT-10417).

---

## Enabling

Feedback collection is **off by default**. Enable it by setting:

```bash
BEDROCK_FEEDBACK_ENABLED=true
BEDROCK_FEEDBACK_POSTGRES_URL=postgresql://feedback:secret@db:5432/feedback
```

If `BEDROCK_FEEDBACK_POSTGRES_URL` is not set, the store falls back to
`BEDROCK_KB_POSTGRES_URL` so a single Postgres instance can host both
schemas.

The optional [postgres] extra is required:

```bash
pip install "auto-bedrock-chat-fastapi[postgres]"
```

When enabled, the FastAPI plugin opens an async connection pool on startup
and closes it on shutdown. If the pool fails to open (DB unreachable),
feedback is disabled in-place and the rest of the app keeps working — clients
that submit feedback messages receive a `feedback_unavailable` error rather
than a crash.

### Configuration reference

| Setting                       | Env var                               | Default | Notes                                                              |
| ----------------------------- | ------------------------------------- | ------- | ------------------------------------------------------------------ |
| `feedback_enabled`            | `BEDROCK_FEEDBACK_ENABLED`            | `false` | Master switch                                                      |
| `feedback_postgres_url`       | `BEDROCK_FEEDBACK_POSTGRES_URL`       | `None`  | Falls back to `BEDROCK_KB_POSTGRES_URL`                            |
| `feedback_postgres_pool_size` | `BEDROCK_FEEDBACK_POSTGRES_POOL_SIZE` | `5`     | Async pool max size                                                |
| `feedback_init_schema`        | `BEDROCK_FEEDBACK_INIT_SCHEMA`        | `true`  | Set `false` if a separate provisioning task owns the DDL lifecycle |

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
  "rating": "correction",
  "score": 2,
  "correction_text": "IPC = instructions / cycles, not cycles / instructions",
  "user_comment": "This calculation was inverted"
}
```

| Field             | Type   | Required    | Notes                                                                         |
| ----------------- | ------ | ----------- | ----------------------------------------------------------------------------- |
| `type`            | string | yes         | Must be `"feedback"`                                                          |
| `message_id`      | string | yes         | The `message_id` echoed by a previous `ai_response`                           |
| `rating`          | enum   | yes         | `positive` \| `negative` \| `correction`                                      |
| `score`           | int    | no          | 1–5                                                                           |
| `correction_text` | string | conditional | Required when `rating == "correction"`; forbidden when `rating == "positive"` |
| `user_comment`    | string | no          | Free-text                                                                     |

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
  "feedback_id": "8c0c3f0e-...",
  "status": "pending_review",
  "timestamp": "2026-05-11T12:34:56.789012"
}
```

### Error envelope

Any failure returns a uniform error message. `code` enables clients to
branch on the cause:

```json
{
  "type": "error",
  "code": "invalid_feedback",
  "detail": "correction_text is required when rating is 'correction'",
  "timestamp": "2026-05-11T12:34:56.789012"
}
```

| `code`                  | When it fires                                                                                                                               |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `feedback_unavailable`  | Backend disabled or pool failed to open                                                                                                     |
| `unauthorized_feedback` | Authorizer rejected the user                                                                                                                |
| `invalid_feedback`      | Missing/unknown `message_id`, missing/unknown `rating`, validator failure (e.g. `correction` without `correction_text`, `score` out of 1–5) |
| `feedback_error`        | Persistence failed downstream                                                                                                               |

---

## Authorization

Submission is gated by a pluggable `FeedbackAuthorizer`:

```python
class FeedbackAuthorizer(Protocol):
    def can_submit(self, user_id: Optional[str]) -> bool: ...
```

The default `AuthenticatedUserAuthorizer` accepts any non-empty `user_id`.
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

The async data-access class is exposed for the upcoming admin-API and
Phase 3 synthesizer tasks:

```python
from auto_bedrock_chat_fastapi.feedback_store import FeedbackStore

store = FeedbackStore(connection_url="postgresql://...", pool_max_size=5)
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
| `approved`       | `rejected`             |
| `rejected`       | `approved`             |

`pending_review` cannot be set as a target via `update_review` — once a
decision is made, the entry stays in a decided state.

---

## Schema

The DDL lives at
[`auto_bedrock_chat_fastapi/sql/feedback_schema.sql`](../../auto_bedrock_chat_fastapi/sql/feedback_schema.sql).
All statements are idempotent; the file can be applied directly with
`psql -f` by the database-provisioning task or auto-bootstrapped by the
store on startup (`feedback_init_schema=true`).

The schema enforces the same validation rules as the Pydantic
[`FeedbackEntry`](../../auto_bedrock_chat_fastapi/models.py) model via
`CHECK` constraints, so direct DB writes that bypass the application can't
introduce invalid rows.

---

## Testing

Unit tests run without a database:

```bash
poetry run pytest tests/test_feedback_store.py -v
```

Integration tests against a live Postgres are gated on
`TEST_FEEDBACK_PG_URL`:

```bash
export TEST_FEEDBACK_PG_URL="postgresql://feedback:feedback@localhost:5432/feedback_test"
poetry run pytest tests/test_feedback_store_integration.py -v
```

---

## Scope reminder

Phase 2 deliberately does not change AI behavior. Stored feedback feeds
Phase 3, where an LLM synthesizer turns approved entries into KB articles
that the RAG retriever then uses on subsequent queries.
