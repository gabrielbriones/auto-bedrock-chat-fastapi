# Feedback Synthesis

> Synthesis closes the continuous learning loop: approved feedback is turned
> into KB articles that the RAG retriever uses on future queries, so AI
> answers improve over time without retraining.

The synthesis engine (`FeedbackSynthesizer`) takes every approved
`FeedbackEntry` that has not yet been integrated into the knowledge base,
groups them by `reviewer_tags`, calls the LLM once per group, and
creates or updates a KB article with the synthesized knowledge. Each
contributing entry is then marked as integrated.

---

## Prerequisites

Synthesis requires all three of the following to be enabled and
configured:

- `BEDROCK_FEEDBACK_ENABLED=true` — feedback collection backend
- `BEDROCK_ADMIN_ENABLED=true` — admin API (synthesis routes live here)
- A wired KB store (`KB_STORAGE_TYPE` / `KB_POSTGRES_URL` etc.) — see
  [RAG Feature](rag-feature)

---

## Configuration reference

| Setting                            | Env var                                    | Default | Description                                                                                                                                                  |
| ---------------------------------- | ------------------------------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `feedback_synthesis_system_prompt` | `BEDROCK_FEEDBACK_SYNTHESIS_SYSTEM_PROMPT` | `None`  | Override the default LLM system prompt used when synthesizing KB articles (see [Customizing the synthesis prompt](#customizing-the-synthesis-prompt) below). |

All other synthesis behaviour (model, embedding model, chunk size, etc.)
is inherited from the existing KB and Bedrock settings — no extra
configuration is required.

---

## How it works

### Entry eligibility

An entry is eligible for synthesis when:

- `review_status = 'approved'`
- `integrated_into_kb_id IS NULL` (not yet synthesized)

`correction_text` is **not** required. The synthesizer uses whichever
information is available, in priority order:

1. `reviewer_comment` — expert reviewer guidance (primary signal)
2. `correction_text` — user-proposed fix (secondary, supplementary)
3. `ai_response` + `rating` — minimum baseline when neither of the above
   is provided

### Tag routing

Entries are grouped by `reviewer_tags`. Each group generates one LLM
call and produces one KB article (create or update). An entry tagged
with multiple tags participates in each group; it is marked integrated
after the first successful synthesis and skipped in subsequent groups
within the same run to avoid double-marking.

Entries with **no** `reviewer_tags` are routed to a fixed internal
tag (`"untagged"`) so they can still be synthesized. The
resulting KB document is stored with `metadata.tags = ["untagged"]`;
this ensures the next synthesis run can find and update the document
(via `KBDocumentListFilters(tags=["untagged"])`) instead of creating
duplicate articles on every pass.

### KB article metadata

| Field  | Value                                                                                          |
| ------ | ---------------------------------------------------------------------------------------------- |
| source | `"feedback"`                                                                                   |
| topic  | `"Feedback review"` (admin can change it later via the [KB Management API](admin-api))         |
| tags   | Union of all `reviewer_tags` across the contributing entries; `["untagged"]` when none are set |
| title  | LLM-generated concise title                                                                    |

### LLM action semantics

The LLM is given the feedback entries plus any existing KB article that
matches the same tag and source. It responds with an `action` field:

| Action   | Meaning                                                                         |
| -------- | ------------------------------------------------------------------------------- |
| `create` | No existing article — write a new one.                                          |
| `update` | Existing article found — the new feedback adds or corrects information in it.   |
| `skip`   | Existing article found — the new feedback only confirms what's already written. |

On `skip`, no KB write is made but the entry is still marked as
integrated (it confirmed the existing knowledge).

---

## Triggers

### Manual (full batch) — `POST /admin/synthesis/trigger`

Processes all eligible entries. Returns `202 Accepted` immediately with
`phase: "running"`. Poll `GET /admin/synthesis/status` for completion.
Returns `409` if a run is already in progress.

```bash
curl -sS -b cookies.txt -X POST \
  'https://app.example.com/admin/synthesis/trigger'
```

### Per-entry (on-demand) — `POST /admin/synthesis/trigger/{feedback_id}`

Synthesizes a single approved entry immediately. Used by the Dashboard's
**"Integrate into KB"** button on each reviewed entry.

```bash
curl -sS -b cookies.txt -X POST \
  'https://app.example.com/admin/synthesis/trigger/8c0c3f0e-...'
```

Response:

```json
{
  "tag": "ipc-computation",
  "action": "create",
  "kb_doc_id": "synthesis-ipc-computation-a1b2c3d4",
  "feedback_ids_marked": ["8c0c3f0e-..."]
}
```

---

## API reference

All synthesis endpoints sit under `/admin/synthesis/` and require the
same admin authorization as the rest of the admin API. See
[Admin API — Choosing an authorizer](admin-api#choosing-an-authorizer).

### `GET /admin/synthesis/status`

Returns the in-memory state of the most recent **batch** run. Per-entry triggers
(`POST /admin/synthesis/trigger/{feedback_id}`) return their result synchronously
and do not update this endpoint's state.

```json
{
  "phase": "completed",
  "started_at": "2026-06-01T00:00:00Z",
  "finished_at": "2026-06-01T00:00:47Z",
  "total_integrated": 12,
  "errors": [],
  "feedback_id": null
}
```

| Field              | Notes                                                                              |
| ------------------ | ---------------------------------------------------------------------------------- |
| `phase`            | `idle` / `running` / `completed` / `failed`                                        |
| `total_integrated` | Number of feedback entries marked as integrated in the last batch run.             |
| `errors`           | Per-tag error messages; non-empty means some groups failed while others succeeded. |
| `feedback_id`      | Reserved field; always `null` in the current implementation.                       |

> **State is ephemeral.** The in-memory state resets on every process
> restart. Entries mid-integration when the process dies are retried on
> the next run (their `integrated_into_kb_id` is still `NULL`).

### `POST /admin/synthesis/trigger`

Trigger a full batch run. `202 Accepted` returns immediately with the
`running` state. Returns `409` if already running.

### `POST /admin/synthesis/trigger/{feedback_id}`

Synthesize one entry now. Blocking — returns `200 OK` with the result
when synthesis completes (typically a few seconds for one entry).

| HTTP | `code`                          | When                                      |
| ---- | ------------------------------- | ----------------------------------------- |
| 200  | —                               | Synthesis completed.                      |
| 404  | `not_found`                     | No entry with that UUID.                  |
| 409  | `already_integrated`            | Entry is already linked to a KB document. |
| 422  | `synthesis_precondition_failed` | Entry is not in `approved` state.         |

---

## Customizing the synthesis prompt

By default the synthesizer uses a generic system prompt that instructs
the LLM to produce a structured KB article in a fixed JSON schema.

To tailor it to your domain — add domain terminology, tone requirements,
or output constraints — set `BEDROCK_FEEDBACK_SYNTHESIS_SYSTEM_PROMPT`
in your `.env`:

```bash
BEDROCK_FEEDBACK_SYNTHESIS_SYSTEM_PROMPT="You are an expert knowledge-base curator for a cloud infrastructure platform. ..."
```

The prompt **must** instruct the LLM to respond with a single JSON object
containing exactly these fields (the synthesizer parses them directly):

| Field                 | Type             | Purpose                                         |
| --------------------- | ---------------- | ----------------------------------------------- |
| `title`               | string           | Concise, searchable article title               |
| `problem`             | string           | What the AI was doing wrong or what gap exists  |
| `correct_methodology` | string           | Validated correct approach, with examples       |
| `key_terms`           | array of strings | Terms likely to appear in related user queries  |
| `examples`            | string           | 1–2 concrete worked examples                    |
| `source_feedback_ids` | array of strings | UUIDs of the feedback entries being synthesized |
| `action`              | string           | One of `"create"`, `"update"`, `"skip"`         |

The default prompt is defined as `_SYNTHESIS_SYSTEM_PROMPT` in
[`auto_bedrock_chat_fastapi/synthesizer.py`](../../auto_bedrock_chat_fastapi/synthesizer.py).
It describes the three-level content hierarchy
(`reviewer_comment` → `correction_text` → `ai_response + rating`) and
the `action` rules; copy it as a starting point when writing a custom
prompt.

> If you deploy from a framework like workload-analyzer that has its own
> `Settings` class, define `feedback_synthesis_system_prompt` there and
> pass it to `add_bedrock_chat(...)` — or simply set the env var and
> `ChatConfig` will pick it up automatically.

---

## Error handling

Individual tag-group failures are caught and recorded in `errors` rather
than aborting the whole batch. A failed group is surfaced in the status
response and logged at `ERROR`. The feedback entries in that group are
**not** marked as integrated and will be retried on the next run.

Per-entry triggers propagate errors synchronously as HTTP error responses
(see error codes above) so the Dashboard button can show the user a
meaningful message.

---

## Related

- [Feedback Collection](feedback-collection) — how feedback entries are
  collected and stored.
- [Admin API](admin-api) — Full admin API reference, including
  authorizers, the Dashboard, and KB management.
- [RAG Feature](rag-feature) — How synthesized KB articles are retrieved
  and injected into future queries.
