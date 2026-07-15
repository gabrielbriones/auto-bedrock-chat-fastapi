# Conversation Persistence

Per-user, named, persisted conversations for the chat plugin. LangGraph's
checkpoint remains the single source of truth for message history — this
feature adds a lightweight `conversations` metadata table (id, user_id,
title, timestamps) that maps user identities to their LangGraph threads,
plus a sidebar UI, WebSocket protocol extensions, auto-titling, and a REST
API.

> **Conversation id = LangGraph `thread_id`.** There is no separate
> `messages` table. Loading a conversation's history always goes through
> `chat_graph.aget_state(thread_id=conversation_id)` — never through the
> conversation store.

---

## Enabling

Conversation persistence is **off by default**. Enable it with:

```bash
AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED=true
```

The storage backend defaults to **SQLite**, so no database setup is
required for local development. To use **Postgres** instead, configure:

```bash
AUTOCHAT_CONVERSATION_STORAGE_TYPE=postgres
AUTOCHAT_CONVERSATION_POSTGRES_URL=postgresql://conversations:secret@db:5432/conversations
```

If `AUTOCHAT_CONVERSATION_POSTGRES_URL` is unset, the store falls back to
`AUTOCHAT_FEEDBACK_POSTGRES_URL`, then `AUTOCHAT_KB_POSTGRES_URL`, so a
single Postgres instance can host every schema. Likewise, when
`conversation_storage_type=sqlite` and `AUTOCHAT_CONVERSATION_DB_PATH` is
unset, the SQLite backend reuses `AUTOCHAT_FEEDBACK_DATABASE_PATH`, then
`KB_DATABASE_PATH`.

### Configuration reference

| Setting                            | Env var                                     | Default  | Notes                                                                                      |
| ---------------------------------- | ------------------------------------------- | -------- | ------------------------------------------------------------------------------------------ |
| `conversation_persistence_enabled` | `AUTOCHAT_CONVERSATION_PERSISTENCE_ENABLED` | `false`  | Master switch                                                                              |
| `conversation_storage_type`        | `AUTOCHAT_CONVERSATION_STORAGE_TYPE`        | `sqlite` | `sqlite` (zero-config) or `postgres`                                                       |
| `conversation_db_path`             | `AUTOCHAT_CONVERSATION_DB_PATH`             | `None`   | SQLite file path; falls back to `AUTOCHAT_FEEDBACK_DATABASE_PATH`, then `KB_DATABASE_PATH` |
| `conversation_postgres_url`        | `AUTOCHAT_CONVERSATION_POSTGRES_URL`        | `None`   | Falls back to `AUTOCHAT_FEEDBACK_POSTGRES_URL`, then `AUTOCHAT_KB_POSTGRES_URL`            |
| `max_conversations_per_user`       | `AUTOCHAT_MAX_CONVERSATIONS_PER_USER`       | `100`    | Oldest (by `updated_at`) conversations beyond this cap are pruned on creation              |
| `conversation_title_model_id`      | `AUTOCHAT_CONVERSATION_TITLE_MODEL_ID`      | `None`   | Bedrock model used to auto-generate titles; falls back to the main chat `model_id`         |

The optional `[postgres]` extra is required for the Postgres backend:

```bash
pip install "autolangchat[postgres]"
```

### The LangGraph checkpointer is a recommendation, not a hard precondition

The plugin's LangGraph checkpointer is either `MemorySaver` (default,
in-process only) or `AsyncPostgresSaver` (when `AUTOCHAT_POSTGRES_URL` is
set — see [FastAPI Plugin Integration](fastapi-plugin)). Conversation
**metadata** (titles, the sidebar list) always persists independently of
this choice. Conversation **history**, however, only survives a process
restart when the checkpointer is `AsyncPostgresSaver` — `MemorySaver` loses
it. You can enable conversation persistence with either checkpointer; with
`MemorySaver` the plugin logs a one-time startup warning, and
`conversation_load` / `GET .../messages` return
`409 conversation_history_unavailable` (not an empty history) for any
conversation that has recorded turns (`message_count > 0`) whose
checkpoint didn't survive. A brand-new conversation that has never had a
turn recorded (e.g. just created via `POST /conversations`, or the
WebSocket's lazy-create failed before its first `ainvoke`) instead loads
normally with an empty message list — that's a legitimately empty
conversation, not lost history.

---

## WebSocket protocol

Conversation management piggybacks on the existing chat WebSocket. See
[WebSocket Client](websocket-client.md#websocket-message-protocol) for the
full message shapes; a summary:

| Client → Server            | Server → Client            | Purpose                                                                             |
| -------------------------- | -------------------------- | ----------------------------------------------------------------------------------- |
| `conversation_list`        | `conversation_list`        | List the caller's conversations (paginated)                                         |
| `conversation_new`         | _(none)_                   | Detach from the active conversation; the next chat message lazily creates a new one |
| `conversation_load`        | `conversation_loaded`      | Switch this connection to an existing conversation and fetch its history            |
| `conversation_delete`      | `conversation_deleted`     | Delete one conversation's metadata row                                              |
| `conversation_delete_all`  | `conversation_all_deleted` | Delete all of the caller's conversations                                            |
| `conversation_rename`      | `conversation_renamed`     | Set an explicit title                                                               |
| _(automatic)_              | `conversation_created`     | Sent the first time a connection sends a chat message with no active conversation   |
| _(automatic)_              | `conversation_titled`      | Sent once, in the background, after the first turn of a new conversation            |
| any of the above, on error | `conversation_error`       | `{code, message}` — see below                                                       |

`conversation_error` codes:

| Code                                | Meaning                                                                                                                                           |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `conversation_persistence_disabled` | The feature isn't enabled or no store is configured                                                                                               |
| `unauthorized_conversation`         | The session has no authenticated `user_id` (conversations require auth — see below)                                                               |
| `conversation_not_found`            | The id doesn't exist **or** belongs to another user (deliberately not distinguished — see [Auth model](#auth-model))                              |
| `conversation_history_unavailable`  | The conversation has recorded turns (`message_count > 0`) but the checkpoint has no values (process restarted with a non-persistent checkpointer) |
| `invalid_conversation_request`      | Malformed payload (missing/invalid `conversation_id`, `title`, `limit`, `offset`)                                                                 |

### Connection identity vs. conversation identity

The WebSocket connection's `session_id` (from `connection_established`,
reusable via `?session_id=` to resume the same connection across a
reconnect) is **not** the same thing as a conversation's id once this
feature is enabled:

- `session_id` identifies the live **connection**.
- The active conversation is tracked separately per connection
  (`session.metadata["conversation_id"]` server-side); it's `null` until
  the client sends a chat message (lazy creation) or a `conversation_load`.

This lets a single connection switch between conversations via
`conversation_load`/`conversation_new` without reconnecting, and keeps
`conversation_load` behind a message-level ownership check rather than
trusting a client-supplied id at connect time.

### Auth model

Every `conversation_*` message requires `session.user_id` to be set
(401-equivalent `unauthorized_conversation` otherwise) — conversations are
a per-user feature with no anonymous bucket. Anonymous connections
(no SSO, no verified `user_id`) never get a persisted conversation even
when chatting normally; they keep the legacy behavior of using the
connection's `session_id` as the LangGraph `thread_id` for that session
only.

Ownership is enforced on every id-addressed operation
(`conversation_load`/`delete`/`rename`, and the REST equivalents below).
A mismatch returns the **same** `conversation_not_found` response as a
nonexistent id, rather than a distinguishing "403 forbidden" — this
prevents a client from using the response to enumerate which conversation
ids exist for other users.

---

## REST API

Registered at `{chat_endpoint}/conversations` (e.g. `/chat/conversations`)
whenever `conversation_persistence_enabled=true` and the store is
configured. Independent of `admin_enabled` — this is a regular user-facing
feature, not part of the [Admin API](admin-api.md).

| Method   | Path                                                       | Description                                                                      |
| -------- | ---------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `GET`    | `/conversations?user_id=&limit=&offset=`                   | List a user's conversations (paginated)                                          |
| `POST`   | `/conversations`                                           | Create a conversation — body `{user_id, title?}`                                 |
| `DELETE` | `/conversations?user_id=`                                  | Delete all of a user's conversations                                             |
| `GET`    | `/conversations/{conversation_id}`                         | Get one conversation's metadata                                                  |
| `PATCH`  | `/conversations/{conversation_id}`                         | Update `title` and/or `metadata` (at least one required)                         |
| `DELETE` | `/conversations/{conversation_id}`                         | Delete one conversation                                                          |
| `GET`    | `/conversations/{conversation_id}/messages?limit=&before=` | Read history via `aget_state` — `before` is a `message_id` cursor for pagination |

### Authentication

Every route requires a resolvable caller identity, from the same two
sources as the [Admin API](admin-api.md#authentication):

1. **SSO session cookie** (`sso_enabled=True` + a valid `sso_session_token` cookie).
2. **`auth_verification_endpoint`** — the caller's `Authorization` /
   `X-API-Key` headers are forwarded and the JSON body's `user_id` (or
   `email`/`sub`/`username`) is used.

No identity resolves → `401 not_authenticated`. Every route then enforces
that the resolved identity's `user_id` matches the resource being
accessed:

- `user_id`-query endpoints (list, delete-all) → `403 forbidden` on mismatch.
- Path-addressed conversations (get/patch/delete/messages) → `404 conversation_not_found`
  on mismatch, for the same non-enumerable-404 reason described above —
  **not** `403`.

Error bodies are a plain FastAPI `HTTPException` with a `{code, message}`
`detail` — e.g. `{"detail": {"code": "conversation_not_found", "message": "Conversation not found"}}`.
This is deliberately simpler than the [Admin API](admin-api.md)'s
centrally-registered `{code, detail}` envelope, which is only wired when
`admin_enabled=True`.

### Examples

```bash
# List (paginated)
curl -H "Authorization: Bearer $TOKEN" \
  "https://host/chat/conversations?user_id=alice&limit=20&offset=0"

# Create
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"user_id": "alice", "title": "Optional title"}' \
  https://host/chat/conversations

# Rename
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title": "Renamed"}' \
  https://host/chat/conversations/b16d7f4e-a956-412f-aae4-b6cc433c7658

# Read history, newest 50 messages
curl -H "Authorization: Bearer $TOKEN" \
  "https://host/chat/conversations/b16d7f4e-a956-412f-aae4-b6cc433c7658/messages?limit=50"

# Read older messages before a cursor message_id
curl -H "Authorization: Bearer $TOKEN" \
  "https://host/chat/conversations/b16d7f4e-a956-412f-aae4-b6cc433c7658/messages?before=msg-42&limit=50"

# Delete all of a user's conversations
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  "https://host/chat/conversations?user_id=alice"
```

The `409 conversation_history_unavailable` response (see
[The LangGraph checkpointer is a recommendation](#the-langgraph-checkpointer-is-a-recommendation-not-a-hard-precondition)
above) applies to `GET .../messages` exactly as it does to the WebSocket
`conversation_load` handler.

> `delete_conversation`/`DELETE /conversations/{id}` only remove the
> metadata row. The LangGraph checkpoint row is cleaned up separately by
> the existing checkpoint TTL sweep (`AUTOCHAT_CHECKPOINT_TTL_SECONDS`).

---

## Chat UI sidebar

When enabled and the connection is authenticated, the built-in chat UI
(see [Chat UI](chat-ui.md)) shows a collapsible conversation sidebar with
a "+ New Chat" button, per-conversation rename/delete actions on hover,
and active-conversation highlighting. On mobile it becomes an off-canvas
drawer. The sidebar is populated purely from the WebSocket messages above
— it re-requests the list and re-loads the last-active conversation
automatically on every reconnect.

The gate is `window.CONFIG.conversationPersistenceEnabled && authenticated`
— hiding the UI is not a security boundary; the same auth/ownership checks
described above apply regardless of what the client renders.

---

## Auto-titling

The first time a connection creates a new conversation, the plugin
generates a short title (5–8 words) from the first user message + assistant
reply in the background, using `conversation_title_model_id` (or the main
`model_id`). This never delays the `ai_response` — the title arrives later
via `conversation_titled`. If the LLM call fails or isn't configured, the
title falls back to a truncated prefix of the first user message.

---

## See Also

- [WebSocket Client](websocket-client.md) — full message protocol reference
- [Admin API](admin-api.md) — the analogous HTTP surface for the reviewer/admin role
- [Chat UI](chat-ui.md) — built-in web interface
- [FastAPI Plugin Integration](fastapi-plugin.md) — checkpointer configuration
