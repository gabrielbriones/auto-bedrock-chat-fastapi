# Session artifacts

Session artifacts hold oversized content outside the model context, in
process-local memory. The originals of oversized tool results and user
messages are stored and replaced in the conversation by the same small
[marker](#tool-result-markers) (see also [user messages](#user-message-markers));
a copy of any other oversized message is stored and
the message stays in place. Artifact creation is
enabled by default. It does not impose a WebSocket input-size limit or expose
filesystem paths. The assistant reads artifacts through the
[`read_content_range` and `grep_content` tools](#assistant-tools).

## Graph integration

In the preprocessing node before each primary model invocation, LangGraph estimates each message after the
last assistant message (or after the system prompt for a new conversation). This is the only place the context
budget of pending messages is estimated. A message exceeding `context_budget_safety_fraction * max_input_tokens`
is handled in the same pass:

- An oversized **tool result** is stored as an artifact and replaced by a [marker](#tool-result-markers).
- An oversized **user message** is stored as an artifact and replaced by the same
  [marker](#user-message-markers).
- Any other oversized message is copied into the store and tagged with `metadata.artifact_handle`, keeping its
  content: a tool message with nothing to replace (stored as its results in JSON, once, not the `content` mirror of
  them as well), or a message that a marker would not make smaller.

The threshold uses the effective model, including retries. Each message estimate also lists the estimate of the
parts it is made of (`parts[].estimated_tokens` in `metadata.message_context_budget`): one per result of a tool
message, the text of a user message. The archive step skips messages that carry markers, so nothing is stored twice.

Replacement and copying run before existing truncation to protect original text, so an oversized tool
result or user message is never truncated first.
The primary LLM call reuses those estimates and handles without repeating
the work. Recovery retries still re-evaluate inside the LLM node because
they bypass the preprocessing node and may change model or payload.
Existing truncation behavior is unchanged. Repeated attempts reuse the
existing live handle rather than copying the same content again.
If a payload expires during the primary request, emergency retry preparation
renews its handle on the original history before truncating the retry input,
so the returned checkpoint retains the replacement handle.

A message that is copied, not replaced, still follows the existing logging, checkpoint,
feedback-query, title-generation, and model-input paths. Artifact handles
are also checkpointed in message metadata. Handles do not imply that the
payload is durable. Tool results and user messages replaced by a marker are the exception from the preprocessing
node on: only the marker follows the checkpoint and model-input paths. The WebSocket handler still logs, and uses for
the feedback query and the conversation title, the user text it received, and the checkpoints written before the
preprocessing node (after `init_turn` and RAG) still hold it.

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
| `AUTOCHAT_ARTIFACT_MAX_BYTES_PER_SESSION` | `201326592` | Payload-byte quota per live connection (192 MiB)     |
| `AUTOCHAT_ARTIFACT_MAX_ITEMS_PER_SESSION` | `128`       | Item quota per live connection                       |
| `AUTOCHAT_ARTIFACT_MAX_BYTES`             | `536870912` | Total payload-byte quota per store/process (512 MiB) |
| `AUTOCHAT_ARTIFACT_READ_MAX_LINES`        | `1000`      | Lines per `read_content_range` call (at most 1000)   |
| `AUTOCHAT_ARTIFACT_READ_MAX_BYTES`        | `65536`     | UTF-8 bytes per artifact read/search (at most 65536) |

All numeric limits must be positive. Quotas count UTF-8 payload bytes, not
Python object/index overhead or existing checkpoint copies; budget additional
memory for those and for transient serialization. Each worker owns a separate
store. Check-and-insert operations, expiry, and deletion are synchronized.

Archiving a tool result stores its text once (a text result as is, structured JSON pretty-printed, so a little larger
than its compact form), so keep the per-session quota at least 3x `AUTOCHAT_TOOL_RESPONSE_MAX_BYTES`; the defaults
satisfy this. A user message is stored once as well.

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
`GET {chat_endpoint}/health` response does not include statistics. The store's
only log line, at DEBUG for each new artifact, has its size and id
(`Stored artifact, size <bytes> bytes, id <handle>`); no log line of the store
has ownership identifiers, metadata or content.

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
archiving (oversized tool results then become [flagged previews](#tool-result-markers)) and log that no live
artifact scope is available. A caller wanting
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

The original content is still sent to the model for messages copied this
way. Oversized tool results and user messages are handled differently: they are replaced, as
described next.

## Tool result markers

The preprocessing node replaces an oversized **successful** tool result with a
small marker and stores the original in the artifact store, in the same pass
that estimates the context budget. The tools node itself passes results on as
the tool returned them. From the preprocessing node on, the model input, the
`content` mirror, later checkpoints, the WebSocket `ai_response.tool_results`,
the history frame and the logs hold only the marker. The single checkpoint
written between the tools node and the preprocessing node still holds the
original, for as long as the checkpointer keeps it; it is outside the artifact
quotas.

**When a result is replaced.** The preprocessing node estimates each tool
message once, and each of its results as the model would receive it. A message
over the per-message threshold (`context_budget_safety_fraction *
max_input_tokens` of the turn's effective model, so a runtime `model_id`
override changes the outcome) has its results replaced like this:

1. Each result that alone exceeds the threshold is replaced.
2. If the message as a whole still exceeds it (several mid-sized results), the
   largest remaining results are replaced, one at a time, until it fits. This is
   skipped when the results that are never replaced already exceed the threshold
   on their own, since replacing the rest could not make it fit.

Only the small replacement is measured again, to make sure it is smaller than
the original; the original is not estimated twice. Conversation history is never
rewritten. There is no separate setting; the artifact quotas and
`context_budget_safety_fraction` apply.

**What is never replaced.** Error entries, HTTP error results
(`status_code >= 400`, so a 401 stays visible to
[reactive auth handling](sso.md#session-expired-vs-auth-expiration-handling), which runs first, in the tools node),
results that already carry a marker, and results of
[internal tools](tool-calling.md#internal-tools) such as `read_content_range`
(their output is already sized to the context budget). Those results still
count toward the message total. Calls that bypass the graph, such as
MCP `tools/call`, are not replaced.

**The marker.** The entry keeps `tool_call_id`, `name` and `status_code`, gains
`artifact_marker: true`, and its `result` becomes:

```json
{
  "artifact_handle": "5d0c1c9e-0f4b-4a53-a0a8-2c3d5f5a9b71",
  "message": "Large content stored as a session artifact; only a preview is shown below.",
  "source": "tool_response",
  "tool_name": "get_report",
  "content_type": "application/json",
  "size_bytes": 142057,
  "preview": "{\n  \"rows\": [\n    {\n      \"id\": 0, ...",
  "lifetime": "current connection, at most 24h (subject to quota)",
  "read_hint": "Call read_content_range(handle, start_line, max_lines) or grep_content(handle, pattern)"
}
```

`read_hint` is present only when the [assistant tools](#assistant-tools) are
bound to the model (artifacts enabled, a tool-calling model); otherwise the
marker carries the preview only. The preview is the first 1,000 characters of
the stored text, as they are. The model receives the marker as `str(result)`. The SPA shows it in
the tool call details as formatted JSON.

**What is stored.** The artifact holds the result's text, so it can be read and
searched by line:

- A string result is stored as is.
- An object with one long string (at least 4,096 characters) and otherwise only
  short scalar fields (at most 15 of them, strings up to 512 characters), with
  every field name a string of at most 128 characters, is
  stored as that string, raw and with its real line breaks. This is the shape of
  a file download such as `{"file_content": "..."}`. The marker then names the
  field (`content_field`), lists the remaining fields (`other_fields`), and
  `size_bytes` and `preview` describe the text itself, so `size_bytes` equals the
  size of the downloaded file:

  ```json
  {
    "artifact_handle": "5d0c1c9e-0f4b-4a53-a0a8-2c3d5f5a9b71",
    "message": "Large content stored as a session artifact; only a preview is shown below.",
    "source": "tool_response",
    "tool_name": "download_job_file",
    "content_type": "text/plain",
    "content_field": "file_content",
    "size_bytes": 997942,
    "preview": "cpu0.core0.counter_00000    0    # ...\ncpu1.core1.counter_00001 ...",
    "lifetime": "current connection, at most 24h (subject to quota)",
    "read_hint": "Call read_content_range(handle, start_line, max_lines) or grep_content(handle, pattern)"
  }
  ```

- Any other result (a list, a nested object, several long strings) is stored as
  pretty-printed JSON, so each element sits on its own line.

**When the original cannot be stored.** If artifacts are disabled, there is no
live scope, or the store rejects the item (quota exceeded, scope revoked), the
result is replaced by a flagged preview instead, and the unbounded payload is
never reinserted:

```json
{
  "truncated": true,
  "message": "Tool result is too large for the model context and could not be stored; only a truncated preview is shown below.",
  "source": "tool_response",
  "tool_name": "get_report",
  "content_type": "application/json",
  "size_bytes": 142057,
  "preview": "..."
}
```

A replacement is only made when it is smaller than the original, so a small
result is never inflated by a marker.

**Large responses.** A response over `tool_response_max_bytes` reaches the
preprocessing node as the [flagged truncated result](tool-calling.md#response-size-limit), an
object with a long `text` and short scalar fields, so it is stored as that
text. The marker's `other_fields` carry `truncated`, `bytes_read` and
`limit_bytes`, which tells the model the artifact is incomplete.

**Metrics.** `spilled_artifacts` in the `ai_response` metadata counts the
results replaced by a marker during the turn (`0` when none), taken from the
turn's tool results. `tool_result_sizes` reports the size of each result as the
tool returned it (its compact JSON), not the marker's.

**Reading the logs.** The tools node only logs the sizes; the preprocessing
node's single pass makes the estimate, stores the artifact and replaces the
result. For a replaced result the sequence is:

- `Tool result sizes: [{'name': <tool>, 'bytes': <n>}]` (INFO, tools node): sizes as returned.
- `Message context estimate: model_id=<m> message_index=<i> estimated_tokens=<n> threshold_tokens=<t> exceeds_threshold=<bool> parts=[<label>: <n> tokens (<chars> chars, kind=<kind>, <r> chars/token); ...]`
  (DEBUG): the only estimate, before anything is stored. `parts` lists each part's tokens and what the estimate was
  made from: its characters, content `kind` (`json`, `prose` or `log_tabular`) and the `chars/token` ratio. For a tool
  message there is one part per result, labelled with the tool name.
- `Stored artifact, size <bytes> bytes, id <handle>` (DEBUG, from the store)
- `Spilled tool result <tool>: <bytes> bytes (~<tokens> tokens, threshold <t>)` (INFO)
- `Tool result <tool> is stored as artifact <handle>` (DEBUG)

Other lines: `Tool result <tool> (~<tokens> tokens) was kept: a replacement would not be smaller` (DEBUG),
`Tool result <tool> (<bytes> bytes, ~<tokens> tokens) is too large and was not archived; replaced by a truncated preview`
(WARNING), and `Stored a copy of message <index> (role=<role>, ~<tokens> tokens) as artifact <handle>; the message keeps its content`
(DEBUG) for an oversized message that was copied into the store instead of being replaced.

Handles are kept out of INFO and higher logs. To find the artifact for a result, enable DEBUG: the handle is in the
`tool_results` entry (checkpoint, WebSocket and history frames, and the DEBUG conversation dump), in the store's
`Stored artifact` line and in the DEBUG lines above. The store logs nothing for a put that finds the same content
already stored, so there the DEBUG `... is stored as artifact <handle>` line is the one that names the handle.

**Internal tool results.** Each result of `read_content_range` or `grep_content` is logged at DEBUG as
`Internal tool result <tool> (call <id>, <bytes> bytes): <json>`, right after `Tool result sizes`. Every string in the
result is cut to 500 characters (`...(+N chars)`), so the fields after a long `text` (`match_count`, `total_lines`,
`has_more`, `error`) stay visible. HTTP tool results are logged by size only. These lines contain bounded excerpts of
the stored content, so leave DEBUG off where that content is sensitive.

**History compaction.** [Preprocessing](token-management.md) never truncates or
summarizes a marker entry or a user message marker, so its handle survives, and the archive pass above
skips messages that carry markers. One limitation remains: with AI
summarization off (the default), Stage 2.2 of history compaction can still
drop whole old middle-zone messages, including one that holds a marker, and a
handle can be lost with it. It removes the assistant tool-call message and its
result together (or an old user message and its answer), and history rarely reaches that stage after a
replacement. The user message of the current turn is protected from it.

## User message markers

The same pass replaces an oversized **user message** (a text over the per-message threshold,
`context_budget_safety_fraction * max_input_tokens` of the turn's effective model) with the
[same marker](#tool-result-markers) a tool result gets, so it is neither truncated nor sent whole, and the model can
read it through the handle. The original text is stored once, as is, with its real line breaks. The marker has the
same fields in the same order, with `source` set to `user_message` and no `tool_name`. The message keeps its other
fields and gains `artifact_marker: true`; its `content` becomes the marker as the model receives a tool result's
marker (`str(marker)`):

```text
{'artifact_handle': '5d0c1c9e-0f4b-4a53-a0a8-2c3d5f5a9b71', 'message': 'Large content stored as a session artifact; only a preview is shown below.', 'source': 'user_message', 'content_type': 'text/plain', 'size_bytes': 926304, 'preview': 'analyze this:  stat name,core 0,core 1, ...', 'lifetime': 'current connection, at most 24h (subject to quota)', 'read_hint': 'Call read_content_range(handle, start_line, max_lines) or grep_content(handle, pattern)'}
```

The preview is the beginning of the text only, so a question that ends a pasted text is not in it; the model reads
the end through the handle. `read_hint` is present only when the [assistant tools](#assistant-tools) are bound to
the model. The message's metadata carries no `artifact_handle`, so the model is told the handle once.

From then on the marker is the message in the conversation: in the checkpoint, in the model input on every later
round, and in the history a reloaded conversation shows (the SPA renders the request bubble from what it sent, so
only a reload shows the marker). The WebSocket handler still logs, and uses for the feedback query and the
conversation title, the text it received.

**What is not replaced.** Content that is not a string, a message that is already a marker, and a message that a
marker would not make smaller (it is then copied into the store and tagged with a handle). Without a live artifact
scope (artifacts disabled, or a direct graph caller that passes none) the message is left to the preprocessor's
truncation, as before. If the store rejects it (quota exceeded, scope revoked), the turn fails with an explicit
`ArtifactError` and the model is not called: the message is never silently dropped.

**Reading the logs.** For a replaced message the sequence is:

- `Message context estimate: model_id=<m> message_index=<i> estimated_tokens=<n> threshold_tokens=<t> exceeds_threshold=True parts=[content: <n> tokens (<chars> chars, kind=<kind>, <r> chars/token)]` (DEBUG)
- `Stored artifact, size <bytes> bytes, id <handle>` (DEBUG, from the store)
- `Spilled user message <i>: <bytes> bytes (~<tokens> tokens, threshold <t>)` (INFO)
- `User message <i> is stored as artifact <handle>` (DEBUG)
- no truncation lines (`Oversized message truncation finished: no messages exceeded threshold`)

Other lines: `User message <i> (~<tokens> tokens) was kept: a replacement would not be smaller` (DEBUG), and
`Failed to store message <i> as a session artifact` (ERROR, before the `ArtifactError`).

## Assistant tools

When `AUTOCHAT_ARTIFACT_ENABLED` is true, the plugin registers two in-process
[internal tools](tool-calling.md#internal-tools) that the model can call with
an artifact handle. They are bound to the model next to the OpenAPI tools, but
never listed or callable over [MCP](configuration.md#mcp-model-context-protocol), which has no live chat
session. When artifacts are disabled they are not registered at all.

Both tools read only the caller's own live scope. Without one (MCP, or direct
`chat_graph.ainvoke` callers that do not pass `artifact_store` and
`artifact_scope`) they return `tool_unavailable`.

The model gets handles from three places: the [handle note](#handle-marker-in-model-input) on copied messages, the
[marker](#tool-result-markers) that replaces an oversized tool result, and the
[marker](#user-message-markers) that replaces an oversized user message. Results of these tools are never
replaced by a marker themselves.

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
