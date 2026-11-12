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
| `turn_in_progress`                  | A `clear` arrived while a chat turn is still running on that conversation; wait for its `ai_response` and send `clear` again                      |

### In-progress and interrupted turns

`conversation_loaded` (and `GET .../messages`) carry `in_progress: bool` —
`true` while a chat turn for that conversation is running in this server
process, or while the history ends mid tool-loop and its last message is
less than 10 minutes old (the turn may be running on another worker).
Clients should show a "still working" state and re-poll only while it is
`true`.

If a turn fails or is cancelled (WebSocket teardown, shutdown) after the
model started calling tools, the server appends a closing assistant message
to the checkpoint so the history no longer ends mid tool-loop. It has
`metadata.interrupted: true`, and any tool call left without a result first
gets a synthetic error result (Bedrock rejects a `toolUse` with no matching
`toolResult` on the next turn).

`conversation_load` applies the same repair to a conversation that still
ends mid tool-loop with no turn running (e.g. the process was killed, or the
conversation got stuck before this behavior existed), persisting the closing
message before returning the history. The bundled UI shows such a message as
an error notice in place of the cut-off tool calls.

Checkpoints are shared across workers but the running-turn registry is per
process, so the load-time repair only touches a tool-loop tail older than
10 minutes (`TOOL_LOOP_LEASE` in `history.py`) — longer than any gap between
checkpoint writes in a live turn. Within a process, the repair and the start
of a turn take a per-conversation lock, so a new turn never races the write.
The trade-off: after a crash, a conversation keeps reporting `in_progress`
until that lease expires.

### Running turns in other tabs

Every connection of the user that has a conversation open follows its running
turn exactly like the connection that sent the message:

- A connection that already has it open gets `turn_started` (with the user's
  `message`) when the turn starts, then every `typing`, `tool_call_start`,
  `tool_call_end` and the final `ai_response`.
- A connection that loads it mid-turn gets `conversation_loaded` with
  `live: true` and the history _without_ the running turn, then `turn_started`
  and a replay of the turn's frames so far (consecutive `typing` frames merged to
  the latest), then the rest live. Loading and the turn's frames take the same
  per-conversation lock, so no frame is missed or sent twice.
- The connection that sent the message gets no `turn_started`; it already shows
  its message.
- A following connection that takes more than a few seconds to accept a frame is
  closed (code `1013`) rather than left to hold up the turn; the client reconnects
  and reloads. The connection that sent the message, and one that is being sent the
  history it asked for, get longer (30 seconds) before the same happens to them.
- Only a signed-in connection of the user is sent the frames of a turn it did not
  start. Any other connection (a `user_id` that was never verified, a connection
  that has signed out) loading the conversation mid-turn gets `live: false` with
  what is checkpointed so far and polls, as it would for a turn on another worker.
- Only a turn sent by a signed-in connection with a verified `user_id` is followed
  by other connections. A turn sent by a connection that merely claims a `user_id`
  (the `?user_id=` parameter, never verified) stays on that connection.
- Following connections are sent to at the same time, so several that stop reading
  hold the turn up for one timeout between them, not one each.
- If the connection that sent the message reloads the conversation after the answer
  is checkpointed, the answer is in the history it gets and no `ai_response` follows.

Only one turn runs per conversation: a `chat` into a conversation with a running
turn is refused with an `error` frame, followed by `turn_started` and the turn's
frames so far (as for a load), because a client that sent at the same moment may
have dropped the first `turn_started` for its own pending message. Like
`in_progress`, this is per process — a connection served by another worker sees
`live: false` and polls.

### Conversation list across tabs

The user's other connections hear about every change to the list made in one of
them, so no tab needs a refresh:

- `conversation_updated` carries one conversation as `conversation_list` lists it
  (`id`, `title`, `updated_at`, `message_count`). It is sent to the user's other
  connections when a conversation is created and after each of its turns. Merge it
  into the list without opening it; an empty `title` means it is not titled yet.
- `conversation_titled` and `conversation_renamed` go to all of the user's
  connections.
- `conversation_deleted`, `conversation_bulk_deleted` and `conversation_all_deleted`
  go to all of the user's connections. A connection that had a deleted conversation
  open is detached from it, as the one that deleted it is, and its
  `conversation_bulk_deleted` says so in `active_conversation_deleted`.
- The `conversation_bulk_deleted` sent to the _other_ connections carries
  `from_other_connection: true`. A client waiting for the reply to its own
  `conversation_delete_bulk` must not take such a frame for that reply.
- The other connections are told before the connection that made the change gets
  its reply, and a delete is announced before any of the user's connections can
  load another conversation, so `active_conversation_deleted` always describes the
  conversation the client still shows. Updates, titles and renames are serialised
  with deletes too: none arrives for a conversation after its delete.

- The reply to the connection that made the change is ordered with the notices
  too: a `conversation_renamed` reply never arrives after the notice of a later
  delete of that conversation, nor a `conversation_all_deleted` reply after the
  `conversation_updated` of a conversation created since.
- Only a change made by a signed-in connection with a verified `user_id` reaches
  the user's other connections. A connection that merely claims a `user_id` (the
  `?user_id=` parameter, never verified) or has signed out still gets its own
  reply, but no other connection is told or detached. Signing in over SSO on an
  open connection (`auth` with `auth_type: "sso"`) makes its identity the verified
  one, whatever it claimed before; if that is a different user, the connection is
  detached from the conversation it had open.
- A connection that has signed out is told nothing, but is still detached from a
  deleted conversation, so that signing in again on it does not send its next
  `chat` into that conversation.

These also only reach connections on the same server process.

### Connection identity vs. conversation identity

The WebSocket connection's `session_id` (from `connection_established`,
reusable via `?session_id=` to resume the same connection across a
reconnect) is **not** the same thing as a conversation's id once this
feature is enabled:

- `session_id` is the connection's checkpoint/thread identity. It's
  reusable across a reconnect by design (that's the point of `?session_id=`),
  so it does not uniquely identify one specific live socket — the server
  also tracks a private, server-generated `connection_id` per live socket
  internally (never sent to clients) to distinguish "this instance of the
  connection" from the `session_id` carried across a reconnect; see
  "Reconnect takeover rules" below for why that distinction matters.
- The active conversation is tracked separately per connection
  (`session.metadata["conversation_id"]` server-side); it's `null` until
  the client sends a chat message (lazy creation) or a `conversation_load`.

This lets a single connection switch between conversations via
`conversation_load`/`conversation_new` without reconnecting, and keeps
`conversation_load` behind a message-level ownership check rather than
trusting a client-supplied id at connect time.

**Reconnect takeover rules.** A second connection can present a
`?session_id=` that's already bound to a live connection (e.g. a browser
tab reopening before its old socket's disconnect has been noticed). The
server resolves this by comparing the live session's verified identity
against the new connection's:

- **Same verified user (or both unauthenticated):** the stale connection
  is evicted — its WebSocket is closed server-side and its bookkeeping
  removed — then the new connection registers cleanly under the same
  `session_id`.
- **Different verified user:** the takeover is refused. The new connection
  receives an `error` frame and is closed by the server; the original
  connection and its session are left untouched.

Only a server-verified identity (an authenticated SSO session) counts as
"the same principal" here — the `user_id` the WebSocket route also accepts
as a plain query parameter is client-supplied and is never by itself
sufficient to claim or evict someone else's live, authenticated connection.

A disconnect of the superseded socket arriving after the takeover is a
no-op: it cannot remove the replacement connection's session.

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

| Method   | Path                                                       | Description                                                                                                       |
| -------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `GET`    | `/conversations?user_id=&limit=&offset=`                   | List a user's conversations (paginated)                                                                           |
| `POST`   | `/conversations`                                           | Create a conversation — body `{user_id, title?}`                                                                  |
| `DELETE` | `/conversations?user_id=`                                  | Delete all of a user's conversations                                                                              |
| `GET`    | `/conversations/{conversation_id}`                         | Get one conversation's metadata                                                                                   |
| `PATCH`  | `/conversations/{conversation_id}`                         | Update `title` and/or `metadata` (at least one required)                                                          |
| `DELETE` | `/conversations/{conversation_id}`                         | Delete one conversation                                                                                           |
| `GET`    | `/conversations/{conversation_id}/messages?limit=&before=` | Read history via `aget_state` — `before` is a `message_id` cursor for pagination; response includes `in_progress` |

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
> metadata row; the LangGraph checkpoint row is left for the checkpoint
> TTL sweep (`AUTOCHAT_CHECKPOINT_TTL_SECONDS`) to clean up. The reverse
> also holds: when the TTL sweep purges an expired checkpoint, it deletes
> the matching `conversations` row too, so a conversation never lingers
> forever pointing at a checkpoint that no longer exists.

---

## Chat UI sidebar

When enabled and the connection is authenticated, the built-in chat UI
(see [Chat UI](chat-ui.md)) shows a collapsible conversation sidebar with
a "+ New Chat" button, per-conversation rename/delete actions on hover,
and active-conversation highlighting. On mobile it becomes an off-canvas
drawer. The sidebar is populated purely from the WebSocket messages above
— it re-requests the list and re-loads the last-active conversation
automatically on every reconnect.

The gate is `conversationPersistenceEnabled && authenticated`, read from the
`GET {chat_endpoint}/config` bootstrap payload the SPA fetches at start-up
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

A manual rename always wins over auto-titling. A `conversation_rename` on
the same connection cancels the in-flight title generation. Regardless of
where the rename comes from (WebSocket, `PATCH /chat/conversations/{id}`, or
another worker), the generated title is only written if the conversation is
still untitled. If it isn't, `conversation_titled` is not sent. Conversations
that already have a title are never auto-titled.

---

## See Also

- [WebSocket Client](websocket-client.md) — full message protocol reference
- [Admin API](admin-api.md) — the analogous HTTP surface for the reviewer/admin role
- [Chat UI](chat-ui.md) — built-in web interface
- [FastAPI Plugin Integration](fastapi-plugin.md) — checkpointer configuration
