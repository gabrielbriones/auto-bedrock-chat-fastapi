# Session artifacts

Session artifacts are ephemeral copies of oversized chat messages held in
process-local memory. Artifact creation is enabled by default. It does not
replace message content, impose a WebSocket input-size limit, or expose
filesystem paths. The assistant reads artifacts through the
[`read_content_range` and `grep_content` tools](#assistant-tools).

## Graph integration

In the preprocessing node before each primary model invocation, LangGraph estimates each message after the
last assistant message (or after the system prompt for a new conversation).
A message exceeding `context_budget_safety_fraction * max_input_tokens`
is archived and tagged with `metadata.artifact_handle`. This includes new
tool results, whose structured content is stored as JSON; user text is
stored verbatim. The threshold uses the effective model, including retries.

Archiving runs before existing truncation to protect original text.
The primary LLM call reuses those estimates and handles without repeating
the work. Recovery retries still re-evaluate inside the LLM node because
they bypass the preprocessing node and may change model or payload.
Existing truncation behavior is unchanged. Repeated attempts reuse the
existing live handle rather than copying the same content again.
If a payload expires during the primary request, emergency retry preparation
renews its handle on the original history before truncating the retry input,
so the returned checkpoint retains the replacement handle.

The original message still follows the existing logging, checkpoint,
feedback-query, title-generation, and model-input paths. Artifact handles
are also checkpointed in message metadata. Handles do not imply that the
payload is durable.

## Ownership and lifetime

Artifacts belong to a server-generated connection instance and originating
conversation/thread. Ordinary conversation switches retain the payload,
but revoke the old active scope. Switching back on the same live connection
restores access while the artifact exists. Reconnecting, even with the same
session ID, does not restore access.

Disconnect, idle expiry, capacity eviction, identity change, logout, and
shutdown purge the affected connection's artifacts. Explicit history or
conversation deletion purges affected artifacts, including deletion through
the conversation REST API. Revoked in-flight scopes cannot recreate deleted
content. Unavailable or foreign handles return the same `ArtifactError`.

The maximum age is an upper bound even on a live connection. Expired
payloads are removed on store access and during the session cleanup sweep
(normally every five minutes). Restarting the process loses all payloads.

### Deployment limitations

The store is process-local: each live socket must stay on its owning worker,
and artifact handles cannot be transferred to another worker or replica.
Multiple workers may each serve their own connections, but their stores,
quotas and statistics are independent. A reconnect, even if routed to the same
worker, creates a new connection scope and cannot recover the old artifacts.

Sharing artifacts across workers or replicas would require a shared backend,
which is not supported today. If that capability is needed, create a separate
ticket for the backend and any necessary abstraction; the current concrete
store and single-module layout remain unchanged.

## Configuration

| Environment variable                      | Default     | Meaning                                              |
| ----------------------------------------- | ----------- | ---------------------------------------------------- |
| `AUTOCHAT_ARTIFACT_ENABLED`               | `true`      | Enable automatic archiving for WebSocket graph turns |
| `AUTOCHAT_ARTIFACT_MAX_AGE_SECONDS`       | `86400`     | Maximum payload age                                  |
| `AUTOCHAT_ARTIFACT_MAX_BYTES_PER_SESSION` | `16777216`  | Payload-byte quota per live connection (16 MiB)      |
| `AUTOCHAT_ARTIFACT_MAX_ITEMS_PER_SESSION` | `128`       | Item quota per live connection                       |
| `AUTOCHAT_ARTIFACT_MAX_BYTES`             | `134217728` | Total payload-byte quota per store/process (128 MiB) |
| `AUTOCHAT_ARTIFACT_READ_MAX_LINES`        | `1000`      | Lines per `read_content_range` call (at most 1000)   |
| `AUTOCHAT_ARTIFACT_READ_MAX_BYTES`        | `65536`     | UTF-8 bytes per artifact read/search (at most 65536) |

All numeric limits must be positive. Quotas count UTF-8 payload bytes, not
Python object/index overhead or existing checkpoint copies; budget additional
memory for those and for transient serialization. Each worker owns a separate
store. Check-and-insert operations, expiry, and deletion are synchronized.

Quota exhaustion fails archiving explicitly with `ArtifactError`, logs the
failure, and stops the invocation rather than pretending the payload was
stored. It is a storage-capacity failure, not an input-size rejection.
Disabling artifacts preserves the ordinary processing path.

## Store statistics

`ArtifactStore.statistics()` returns an atomic, aggregate-only snapshot:

| Field              | Meaning                                                                        |
| ------------------ | ------------------------------------------------------------------------------ |
| `active_artifacts` | Current number of unexpired artifacts                                          |
| `bytes`            | Current UTF-8 payload bytes, excluding object overhead                         |
| `puts`             | Successful insertions, excluding deduplication hits                            |
| `expiries`         | Artifacts removed by TTL expiry                                                |
| `quota_rejections` | Put calls rejected by any storage quota                                        |
| `failed_reads`     | Unavailable-handle or scope failures in `read_region`, `get_meta` and `search` |

Taking a snapshot expires overdue artifacts first. Lifetime counters are
per store instance and survive explicit deletion and `close()`; the live
gauges reflect the remaining contents. A failed read counts once per
`read_region`, `get_meta` or `search` call, including revocation during scanning and access after
closure. `contains()` is an availability probe used to decide whether to
re-archive a message, so it never counts as a failed read. Invalid range
or search arguments are not counted as failed reads.
Snapshots contain no handles, ownership identifiers, metadata or payloads.
`ChatSessionManager.get_statistics()` includes the snapshot under `artifacts`.
`WebSocketChatHandler.get_statistics()` and `GET {chat_endpoint}/stats` expose
it under `sessions.artifacts`. `GET {chat_endpoint}/stats` requires an admin
caller (the same authorization as `{chat_endpoint}/admin/*`) and returns 404
unless `AUTOCHAT_ADMIN_ENABLED=true`. The unauthenticated
`GET {chat_endpoint}/health` response does not include statistics. Artifact
storage logs include only aggregate size information, not handles or ownership
identifiers.

## Internal access and non-WebSocket callers

`ArtifactStore.put(scope, text, metadata)` returns an opaque handle.
`read_region(scope, handle, start_line=1, max_lines=100)` supports internal
bounded inspection, capped at 1,000 lines and 64 KiB of UTF-8 output.
The `truncated` flag indicates that the requested region was not returned
in full. The assistant reaches it through `read_content_range` (below).
Reads scan line boundaries without allocating a list of every line or
encoding the entire selected region. Output assembly is byte-bounded;
`total_lines` still requires scanning the artifact, outside the store lock.

`get_meta(scope, handle)` returns `size_bytes`, `total_lines` (counted once
at `put`), `expires_in_seconds`, `source` and a copy of the metadata without
reading the text.

`search(scope, handle, pattern, context_lines=0, max_matches=50, regex=False,
ignore_case=True)` matches each line on its own, without its terminator, so a
match never spans lines. Patterns are literal and case-insensitive by default.
Regexes are opt-in and limited to 200 characters; invalid patterns or limits
raise `ValueError`. `context_lines` is capped at 20 and `max_matches` at 200.
The result lists 1-based matching and context lines in order without
duplicates (`line`, `text`, `match`, `clipped`, and `column` for matches),
plus `match_count`, `total_lines`, `truncated` and `metadata`. Lines longer
than 500 characters are clipped to a window around the first match, and the
total output is capped at 64 KiB of UTF-8. `truncated` means more matching
lines exist than were returned. Like `read_region`, the scan runs outside the
store lock and the handle is revalidated afterwards, so expiry or revocation
during a search fails without a partial result.

All three methods share the same scope, expiry and `ArtifactError` behavior.

WebSocket turns receive the live store and scope through LangGraph's
`configurable` runtime values, never as checkpointed store objects.
Direct/background graph callers without those values continue without
archiving and log that no live artifact scope is available. A caller wanting
archiving must explicitly register and activate its own scope and arrange
its cleanup; it must not borrow another session's scope.

### Handle marker in model input

When the artifact tools are bound to the model (artifacts enabled, a
tool-calling model, and `read_content_range` registered), each message that
carries `metadata.artifact_handle` gets a trailing note in the model input:
`[Archived: the full message is stored as artifact handle <uuid>. ...]`
(`tool output of this step` for tool results, repeated on each result of that
step). The note is added when the model input is built, after truncation, so it
is never truncated away and stored/checkpointed content is unchanged. This is
how the model learns the handle to pass to the [assistant tools](#assistant-tools).

The original content is still sent to the model; replacing oversized content
with a preview plus handle (spilling it out of the context window) remains
follow-up work.

## Assistant tools

When `AUTOCHAT_ARTIFACT_ENABLED` is true, the plugin registers two in-process
[internal tools](tool-calling.md#internal-tools) that the model can call with
an artifact handle. They are bound to the model next to the OpenAPI tools, but
never listed or callable over [MCP](configuration.md#mcp-model-context-protocol), which has no live chat
session. When artifacts are disabled they are not registered at all.

Both tools read only the caller's own live scope. Without one (MCP, or direct
`chat_graph.ainvoke` callers that do not pass `artifact_store` and
`artifact_scope`) they return `tool_unavailable`.

### `read_content_range`

| Argument     | Default | Notes                                                          |
| ------------ | ------- | -------------------------------------------------------------- |
| `handle`     | —       | Required                                                       |
| `start_line` | `1`     | 1-based                                                        |
| `max_lines`  | `200`   | Up to `AUTOCHAT_ARTIFACT_READ_MAX_LINES`                       |
| `start_byte` | —       | Byte-paging mode, for giant single lines such as minified JSON |
| `max_bytes`  | —       | Byte-paging mode, up to `AUTOCHAT_ARTIFACT_READ_MAX_BYTES`     |

Line mode returns `text` as line-numbered lines (`12: ...`), plus
`start_line`, `end_line`, `total_lines`, `has_more` and `next_start_line`
(`null` on the last page). If a single line is longer than one page, the page
stops inside it with `line_truncated: true`, `next_start_byte` and a `hint`.
Byte mode returns `text`, `start_byte`, `end_byte`, `total_bytes`, `has_more`
and `next_start_byte`; offsets always fall on UTF-8 character boundaries. Line
and byte arguments can't be combined.

An empty artifact returns an empty page (`total_lines: 0`, `has_more: false`).
Text that was stored with invalid UTF-8 or binary bytes comes back with the
replacement characters and control characters that were stored, escaped like
any JSON string.

### `grep_content`

| Argument        | Default | Notes                                      |
| --------------- | ------- | ------------------------------------------ |
| `handle`        | —       | Required                                   |
| `pattern`       | —       | Required                                   |
| `context_lines` | `0`     | 0–20 lines before and after each match     |
| `max_matches`   | `50`    | 1–200                                      |
| `regex`         | `false` | Opt-in; patterns of at most 200 characters |

Matching is a case-insensitive literal substring by default. Regexes use
[RE2](https://github.com/google/re2/wiki/Syntax) syntax (via `google-re2`), so
they run in linear time and cannot backtrack catastrophically; backreferences
and lookaround are rejected as invalid. Regexes are matched one line at a time
and only against the first 8,192 characters of each line;
`partially_searched_lines` and a `hint` report lines that were longer. The
result has grep-style `text` (`12: match`,
`12- context`, `--` between groups), `match_count`, `total_lines` and
`truncated` (more matches exist than were returned).

### Output caps

Each result is capped by `AUTOCHAT_ARTIFACT_READ_MAX_LINES`,
`AUTOCHAT_ARTIFACT_READ_MAX_BYTES` and the turn's
[`ContextBudget`](token-management.md) allowance, measured on the JSON-escaped
text the model actually receives. When too little context is left to return a
useful page, the tools return `context_exhausted` without reading.

### Errors

Expected failures are returned as `{"error": code, "message": ...}`:

| Code                   | When                                                                                                                     |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `artifact_unavailable` | Unknown, expired, revoked or other-scope handle. The message is the same in every case and never reveals other sessions. |
| `invalid_arguments`    | Unknown, mistyped or out-of-range arguments, or a range past the end                                                     |
| `context_exhausted`    | Not enough context budget left for a useful page                                                                         |
| `tool_unavailable`     | No live chat session (MCP, or graph callers without an artifact scope)                                                   |

`artifact_unavailable` failures count toward the store's `failed_reads`;
argument errors do not.

### Aliases

`read_artifact_range` and `grep_artifact` share the same handlers and are
dispatched by name, but they are never bound to the model or listed anywhere.
All four names are reserved: an OpenAPI operation with the same cleaned name is
logged and skipped while artifacts are enabled.
