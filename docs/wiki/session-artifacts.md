# Session artifacts

Session artifacts are ephemeral copies of oversized chat messages held in
process-local memory. Artifact creation is enabled by default. It does not
replace message content, impose a WebSocket input-size limit, or expose
filesystem paths or assistant tools.

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

## Configuration

| Environment variable                      | Default     | Meaning                                              |
| ----------------------------------------- | ----------- | ---------------------------------------------------- |
| `AUTOCHAT_ARTIFACT_ENABLED`               | `true`      | Enable automatic archiving for WebSocket graph turns |
| `AUTOCHAT_ARTIFACT_MAX_AGE_SECONDS`       | `86400`     | Maximum payload age                                  |
| `AUTOCHAT_ARTIFACT_MAX_BYTES_PER_SESSION` | `16777216`  | Payload-byte quota per live connection (16 MiB)      |
| `AUTOCHAT_ARTIFACT_MAX_ITEMS_PER_SESSION` | `128`       | Item quota per live connection                       |
| `AUTOCHAT_ARTIFACT_MAX_BYTES`             | `134217728` | Total payload-byte quota per store/process (128 MiB) |

All numeric limits must be positive. Quotas count UTF-8 payload bytes, not
Python object/index overhead or existing checkpoint copies; budget additional
memory for those and for transient serialization. Each worker owns a separate
store. Check-and-insert operations, expiry, and deletion are synchronized.

Quota exhaustion fails archiving explicitly with `ArtifactError`, logs the
failure, and stops the invocation rather than pretending the payload was
stored. It is a storage-capacity failure, not an input-size rejection.
Disabling artifacts preserves the ordinary processing path.

## Internal access and non-WebSocket callers

`ArtifactStore.put(scope, text, metadata)` returns an opaque handle.
`read_region(scope, handle, start_line=1, max_lines=100)` supports internal
bounded inspection, capped at 1,000 lines and 64 KiB of UTF-8 output.
The `truncated` flag indicates that the requested region was not returned
in full. This internal method is not registered as an assistant tool.
Reads scan line boundaries without allocating a list of every line or
encoding the entire selected region. Output assembly is byte-bounded;
`total_lines` still requires scanning the artifact, outside the store lock.

WebSocket turns receive the live store and scope through LangGraph's
`configurable` runtime values, never as checkpointed store objects.
Direct/background graph callers without those values continue without
archiving and log that no live artifact scope is available. A caller wanting
archiving must explicitly register and activate its own scope and arrange
its cleanup; it must not borrow another session's scope.

Assistant-facing marker replacement and region/search tool registration
remain follow-up work.
