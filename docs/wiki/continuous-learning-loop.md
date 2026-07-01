# Continuous Learning Loop

> **Component:** Feedback Collection → Expert Review → Synthesis → RAG
> **Prerequisites:** `AUTOCHAT_FEEDBACK_ENABLED=true`, `AUTOCHAT_ADMIN_ENABLED=true`, a configured KB store

The Continuous Learning Loop turns user 👍 / 👎 ratings and corrections
into KB articles that future RAG retrievals use — so the AI's answers
improve over time without retraining the model.

---

## Overview

### What it is and why it exists

Every AI response has a **feedback button**. When a user rates a
response negatively (or submits a correction), that rating is stored as
a `FeedbackEntry`. A human reviewer inspects the entry, tags it with
semantic labels, and either approves or rejects it. Approved entries
are synthesized (by an LLM call) into structured KB articles that the
RAG retriever surfaces on semantically similar future queries.

The loop closes on the next query: the synthesized article is retrieved,
the system prompt is enriched with the new knowledge, and the AI
produces a better response.

### AI failure modes addressed

| Failure mode             | How the loop helps                                                                                        |
| ------------------------ | --------------------------------------------------------------------------------------------------------- |
| Factually wrong answers  | Approved corrections become KB articles; future retrievals surface the corrected knowledge                |
| Outdated information     | Stale synthesized articles decay (credibility score drops) and are excluded from RAG by default           |
| Missing domain knowledge | Expert reviewers tag entries; the synthesizer groups by tag to produce focused articles                   |
| Repeated errors          | Negative feedback signals lower the credibility of the cited KB article via the feedback-signal mechanism |

---

## Architecture

Data flows in one direction through four stages:

```
User rating / correction
         │
         ▼
┌─────────────────────┐
│  Feedback Queue     │  FeedbackEntry (SQLite / Postgres)
│  (feedback store)   │  status: pending_review
└────────┬────────────┘
         │ Admin PATCH /admin/feedback/{id}
         ▼
┌─────────────────────┐
│  Expert Review Gate │  Reviewer inspects, tags, approves or rejects
│  (admin dashboard)  │  status → approved / rejected
└────────┬────────────┘
         │ POST /admin/synthesis/trigger
         ▼
┌─────────────────────┐
│  Synthesis Pipeline │  LLM groups approved entries by tag;
│  (FeedbackSynth.)   │  creates / updates KB articles
│                     │  article.source = "feedback"
└────────┬────────────┘
         │  semantic_search()
         ▼
┌─────────────────────┐
│  RAG Retrieval      │  Retrieves articles; applies credibility
│  (vector DB)        │  weighting; excludes flagged articles
└─────────────────────┘
         │  enriched system prompt
         ▼
      LLM response
```

> For the full component diagram (WebSocket handler, LangGraph nodes,
> tool manager) see [Architecture](architecture).

---

## Configuration Reference

All variables listed here are part of `ChatConfig` (`autolangchat/config.py`)
and can be set in `.env` or as environment variables.

### Feedback collection

| Env var                                 | Type        | Default    | Description                                                                                                                                              |
| --------------------------------------- | ----------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AUTOCHAT_FEEDBACK_ENABLED`             | `bool`      | `False`    | Master switch. Must be `true` for any part of the learning loop to function.                                                                             |
| `AUTOCHAT_FEEDBACK_ALLOW_ANONYMOUS`     | `bool`      | `False`    | Accept feedback from unauthenticated users. Intended for local development only.                                                                         |
| `AUTOCHAT_FEEDBACK_AUTHORIZED_USERS`    | `str` (CSV) | `""`       | If non-empty, only these user IDs / email addresses may submit feedback. Emails normalized to lowercase; opaque SSO sub claims matched case-sensitively. |
| `AUTOCHAT_FEEDBACK_STORAGE_TYPE`        | `str`       | `"sqlite"` | `"sqlite"` (zero-config) or `"postgres"` (requires `AUTOCHAT_FEEDBACK_POSTGRES_URL` or `AUTOCHAT_KB_POSTGRES_URL`).                                      |
| `AUTOCHAT_FEEDBACK_DATABASE_PATH`       | `str`       | `None`     | SQLite file path. When unset, falls back to `KB_DATABASE_PATH` (`kb_database_path`).                                                                     |
| `AUTOCHAT_FEEDBACK_POSTGRES_URL`        | `str`       | `None`     | Postgres connection URL for the feedback schema. Falls back to `AUTOCHAT_KB_POSTGRES_URL`.                                                               |
| `AUTOCHAT_FEEDBACK_POSTGRES_POOL_SIZE`  | `int`       | `5`        | Async pool size for the feedback Postgres backend. Range: 1–100.                                                                                         |
| `AUTOCHAT_FEEDBACK_INIT_SCHEMA`         | `bool`      | `True`     | Apply the feedback DDL on startup. Set `False` when a separate provisioning task owns the schema.                                                        |
| `AUTOCHAT_FEEDBACK_MAX_HISTORY_CONTEXT` | `int`       | `5`        | Number of preceding messages captured alongside the rated response (gives reviewers context). Set `0` to disable.                                        |

### Feedback metadata enrichment

| Env var                                               | Type    | Default | Description                                                                                                                                                           |
| ----------------------------------------------------- | ------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AUTOCHAT_FEEDBACK_METADATA_ENRICHMENT_URL`           | `str`   | `None`  | Optional HTTP endpoint called on every submission; the JSON response is stored in `FeedbackEntry.entry_metadata`. Must use `http` or `https` scheme.                  |
| `AUTOCHAT_FEEDBACK_METADATA_ENRICHMENT_TIMEOUT`       | `float` | `2.0`   | Timeout (seconds) for the enrichment HTTP call. Must be > 0.                                                                                                          |
| `AUTOCHAT_FEEDBACK_METADATA_ENRICHMENT_FAIL_ON_ERROR` | `bool`  | `False` | When `True`, an enrichment failure rejects the feedback submission. When `False` (default), failures are logged and the submission proceeds with `entry_metadata={}`. |

### Synthesis

| Env var                                     | Type  | Default | Description                                                                                                                                                     |
| ------------------------------------------- | ----- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AUTOCHAT_FEEDBACK_SYNTHESIS_SYSTEM_PROMPT` | `str` | `None`  | Override the LLM system prompt used when synthesizing KB articles. See [Customizing the synthesis prompt](feedback-synthesis#customizing-the-synthesis-prompt). |

### Credibility decay (auto-aging)

| Env var                                        | Type    | Default | Description                                                                                     |
| ---------------------------------------------- | ------- | ------- | ----------------------------------------------------------------------------------------------- |
| `AUTOCHAT_KB_CREDIBILITY_DECAY_ENABLED`        | `bool`  | `False` | Enable the background decay task. When `False`, scores stay at `1.0` until manually reset.      |
| `AUTOCHAT_KB_CREDIBILITY_DECAY_RATE`           | `float` | `0.05`  | Amount subtracted from `credibility_score` per cycle. Range: (0, 1).                            |
| `AUTOCHAT_KB_CREDIBILITY_REMOVAL_THRESHOLD`    | `float` | `0.3`   | Score at or below which an article is flagged for removal and excluded from RAG. Range: [0, 1]. |
| `AUTOCHAT_KB_CREDIBILITY_DECAY_INTERVAL_HOURS` | `int`   | `168`   | How often the decay task runs, in hours (default: 1 week). Must be > 0.                         |

### Credibility signals (feedback-driven)

| Env var                                           | Type    | Default | Description                                                                                                                               |
| ------------------------------------------------- | ------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `AUTOCHAT_KB_CREDIBILITY_CITATION_BOOST_ENABLED`  | `bool`  | `False` | When enabled, each time a synthesized article is cited in a RAG response its score increases by `AUTOCHAT_KB_CREDIBILITY_CITATION_BOOST`. |
| `AUTOCHAT_KB_CREDIBILITY_CITATION_BOOST`          | `float` | `0.05`  | Amount added to `credibility_score` per RAG citation. Range: [0, 1].                                                                      |
| `AUTOCHAT_KB_CREDIBILITY_FEEDBACK_SIGNAL_ENABLED` | `bool`  | `False` | When enabled, approving a feedback entry adjusts the credibility of KB articles that were cited in the original AI response.              |
| `AUTOCHAT_KB_CREDIBILITY_POSITIVE_DELTA`          | `float` | `0.5`   | Boost applied to a cited article when the feedback entry is positively rated and approved. Range: [0, 1].                                 |
| `AUTOCHAT_KB_CREDIBILITY_NEGATIVE_DELTA`          | `float` | `0.5`   | Penalty applied to a cited article when the feedback entry is negatively rated and approved. Range: [0, 1].                               |

---

## Expert Reviewer Guide

### Accessing the review queue

1. Open the Dashboard at `/chat/dashboard` (the Dashboard button appears
   in the header when you are logged in as an admin).
2. Navigate to the **Feedback** tab. Entries in `pending_review` status
   are shown first.
3. Click any row to open the **Review drawer**.

For headless / automated access use the [Admin API](admin-api):

```bash
# List pending entries
curl -sS -b cookies.txt \
  'https://app.example.com/admin/feedback?status=pending_review&limit=50'
```

### Reviewing a feedback entry

Each entry exposes:

| Field                  | Meaning                                                          |
| ---------------------- | ---------------------------------------------------------------- |
| `rating`               | `positive` (👍) or `negative` (👎) — the user's original rating  |
| `ai_response`          | The exact AI answer that was rated                               |
| `correction_text`      | User-provided correction (optional)                              |
| `conversation_history` | Up to `AUTOCHAT_FEEDBACK_MAX_HISTORY_CONTEXT` preceding messages |
| `user_id`              | Submitting user (for de-duplication / credibility weighting)     |
| `created_at`           | Submission timestamp                                             |

A reviewer should:

1. Read the `ai_response` in context (use `conversation_history`).
2. Check whether `correction_text` is accurate and complete; if it is,
   note that in `reviewer_comment`.
3. Assign one or more `reviewer_tags` that describe the knowledge domain
   (e.g. `"ipc"`, `"memory-bandwidth"`, `"numa-topology"`).
4. Decide: **approve** or **reject** (see criteria below).

### Approve vs. reject

| Decision    | When to use                                                                                                                                        |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Approve** | The AI response was wrong or incomplete, or the correction is validated and adds value. The entry should be synthesized into KB knowledge.         |
| **Reject**  | The rating was noise (user clicked wrong button), the correction is incorrect, duplicate, out of scope, or already covered by existing KB content. |

Rejected entries are never synthesized and are excluded from the review
queue on subsequent views. They can be re-opened by PATCHing
`review_status` back to `pending_review` if necessary.

### How tags determine synthesis grouping

The synthesizer groups approved entries by `reviewer_tags`. All entries
sharing the same tag are passed together in one LLM call, and the output
is one KB article (create or update) per tag group.

**Tagging conventions** — see [Best Practices](#best-practices) below.

Entries with **no** tags are routed to a special `"untagged"` group and
produce an `"untagged"` KB article. This prevents them from blocking the
queue but produces lower-quality synthesis; always tag when possible.

---

## Synthesis Pipeline

The synthesis pipeline is described in full (triggers, run statuses,
per-entry trigger, rollback) on the [Feedback Synthesis](feedback-synthesis)
page. This section covers the operational view only.

### What triggers synthesis

| Trigger          | How                                                                                                                          |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **Manual batch** | `POST /admin/synthesis/trigger` — processes all eligible approved entries                                                    |
| **Per-entry**    | `POST /admin/synthesis/trigger/{feedback_id}` — synthesizes one entry immediately (Dashboard **"Integrate into KB"** button) |
| **Automatic**    | Not implemented. The batch trigger can be run on a schedule via cron / CI job.                                               |

An entry is eligible when `review_status = 'approved'` AND
`integrated_into_kb_id IS NULL`.

### What synthesis produces

- A KB article (`source='feedback'`) in the vector store with the tag
  group's combined knowledge.
- The article's `credibility_score` is initialized to `1.0`.
- Each contributing `FeedbackEntry` is stamped with `integrated_into_kb_id`.

### Monitoring a synthesis run

```bash
# Poll for completion after triggering a batch
curl -sS -b cookies.txt 'https://app.example.com/admin/synthesis/status'
```

`phase` transitions: `idle` → `running` → `completed` (or `failed`).
Non-empty `errors` means some tag groups failed while others succeeded —
partial success is possible.

> See [Feedback Synthesis — API reference](feedback-synthesis#api-reference)
> for the full endpoint spec, response schemas, and HTTP status codes.

---

## Effectiveness Tracking

### How credibility scoring works

Every synthesized KB article starts at `credibility_score = 1.0`. The
score can move in three ways:

| Event                                                                             | Effect                                                                                                                                             |
| --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Decay cycle** (when `AUTOCHAT_KB_CREDIBILITY_DECAY_ENABLED=true`)               | Score reduced by `AUTOCHAT_KB_CREDIBILITY_DECAY_RATE` each cycle                                                                                   |
| **Citation boost** (when `AUTOCHAT_KB_CREDIBILITY_CITATION_BOOST_ENABLED=true`)   | Score increased by `AUTOCHAT_KB_CREDIBILITY_CITATION_BOOST` each time the article is returned in a RAG result                                      |
| **Feedback signal** (when `AUTOCHAT_KB_CREDIBILITY_FEEDBACK_SIGNAL_ENABLED=true`) | Score boosted (`POSITIVE_DELTA`) or penalized (`NEGATIVE_DELTA`) when an approved feedback entry references an AI response that cited this article |

Scores are clamped to `[0.0, 1.0]` after every update.

### What triggers auto-removal

When `credibility_score` falls to or below
`AUTOCHAT_KB_CREDIBILITY_REMOVAL_THRESHOLD` (default `0.3`), the article
is **flagged** (`removal_flagged = true`).

Flagged articles are:

- **Excluded** from `semantic_search` by default (`exclude_flagged=True`)
- Still visible in `GET /admin/kb/documents?removal_flagged=true`
- Recoverable via `POST /admin/kb/documents/reset-credibility/{id}` — percent-encode URL-shaped IDs (see [Admin API — Credibility reset](admin-api#credibility-reset))

Flagging is a **soft-delete**: the article and its source feedback history
are preserved for audit and rollback purposes.

### Reading credibility metrics in the admin API

```bash
# List flagged articles
curl -sS -b cookies.txt \
  'https://app.example.com/admin/kb/documents?removal_flagged=true'

# Inspect a specific article
curl -sS -b cookies.txt \
  'https://app.example.com/admin/kb/documents/synthesis-ipc-computation-a1b2c3d4'
```

`KBDocument` response fields:

| Field               | Notes                                                          |
| ------------------- | -------------------------------------------------------------- |
| `credibility_score` | Float in `[0.0, 1.0]`; `1.0` = full credibility                |
| `removal_flagged`   | `true` when score has fallen to or below the removal threshold |
| `source`            | `"feedback"` for synthesized articles                          |

---

## Admin Operations

### Managing KB documents

Use the Dashboard's **KB** tab or the [Admin API](admin-api) KB Management
endpoints (`GET`, `PATCH`, `DELETE` on `/admin/kb/documents/`).

Synthesized articles (`source='feedback'`) can be edited like any other
KB document; edits are re-embedded automatically on content change.

### Rolling back a synthesized article

If synthesis produced a bad or incorrect article:

1. Identify the article's `id` from `GET /admin/kb/documents?source=feedback`.
2. Roll it back via the synthesis API — this deletes the article and
   resets the source feedback entries to `pending_review` so they can
   be corrected and re-synthesized.

> See [Feedback Synthesis — Rollback](feedback-synthesis#post-adminsynthesisrollbackarticle_id)
> for the full endpoint spec and example `curl` commands.

### Resetting article credibility

When an article has been flagged by the decay task but you've determined
it's still valid, reset its score manually:

```bash
curl -sS -b cookies.txt -X POST \
  'https://app.example.com/admin/kb/documents/reset-credibility/synthesis-ipc-computation-a1b2c3d4'
```

Response: the updated `KBDocument` with `credibility_score=1.0` and
`removal_flagged=false`. See [Admin API — Credibility Reset](admin-api#credibility-reset)
for the full spec.

### Viewing analytics

```bash
# Feedback queue health
curl -sS -b cookies.txt \
  'https://app.example.com/admin/feedback/stats'
```

Returns aggregate counts, `top_tags`, and `oldest_pending_hours` — useful
for detecting reviewer bottlenecks.

---

## Troubleshooting

### Expert review becoming a bottleneck

**Symptom:** `oldest_pending_hours` in `/admin/feedback/stats` grows large;
synthesis runs produce few new articles.

**Mitigations:**

- Add more reviewers via `AUTOCHAT_ADMIN_REQUIRED_GROUPS` or the
  verification endpoint.
- Set `AUTOCHAT_FEEDBACK_AUTHORIZED_USERS` to restrict who can submit
  feedback so only high-signal entries enter the queue.
- Lower `AUTOCHAT_FEEDBACK_MAX_HISTORY_CONTEXT` to reduce reviewer
  reading time per entry.
- Reject noise entries in bulk using the `status=pending_review` filter
  and the PATCH endpoint.

### Synthesizer generates a bad article

**Symptom:** A synthesis run completes but the produced article contains
incorrect, hallucinated, or incoherent content.

**Response:**

1. Roll back the article immediately (see [Rolling back](#rolling-back-a-synthesized-article)). This restores the source entries to `pending_review`.
2. Re-review the source entries and add or correct `reviewer_comment` before re-approving.
3. Consider setting `AUTOCHAT_FEEDBACK_SYNTHESIS_SYSTEM_PROMPT` to add
   domain constraints or output requirements.

### Low feedback volume

**Symptom:** Feedback queue is sparse; synthesis runs find few eligible entries.

**Mitigations:**

- Ensure `AUTOCHAT_FEEDBACK_ALLOW_ANONYMOUS=false` is not blocking
  unauthenticated users from submitting if your deployment lacks auth.
- Check that the feedback UI buttons are visible to users — the buttons
  are only rendered when `AUTOCHAT_FEEDBACK_ENABLED=true`.
- Review `AUTOCHAT_FEEDBACK_AUTHORIZED_USERS`: if set to a small list,
  it limits the submitter pool.

### Feedback fatigue

**Symptom:** Users stop rating responses; queue volume drops over time.

**Mitigations:**

- Ensure feedback is acknowledged (e.g. a confirmation toast after
  submission).
- Avoid exposing the feedback UI on queries where it adds no value
  (error states, greetings, ping messages).
- Verify that positive feedback ratings are also captured (not just
  negative) — `rating='positive'` entries with reviewer-confirmed tags
  still strengthen KB credibility via the feedback-signal mechanism.

### KB pollution scenarios

**Symptom:** RAG answer quality degrades; the KB browser shows many low-quality synthesized articles; `removal_flagged` entries multiply.

**Mitigations:**

- Enable credibility decay (`AUTOCHAT_KB_CREDIBILITY_DECAY_ENABLED=true`)
  to age out stale articles automatically.
- Enable feedback signals (`AUTOCHAT_KB_CREDIBILITY_FEEDBACK_SIGNAL_ENABLED=true`)
  so negatively-rated responses reduce the score of their cited articles.
- Use the `removal_flagged=true` filter to find and review flagged
  articles; roll back any that were synthesized from bad source data.
- Tighten reviewer tag discipline so synthesis groups entries by
  distinct topic rather than producing catch-all articles.

### Effectiveness tracking false signals

**Symptom:** Articles are being flagged despite being valid; or flagged
articles are not being auto-excluded.

**Checks:**

- Confirm `AUTOCHAT_KB_CREDIBILITY_DECAY_ENABLED=true` (decay is opt-in).
- Verify `AUTOCHAT_KB_CREDIBILITY_DECAY_RATE` is not set too high — a
  rate of `0.5` with an interval of `168 h` reaches the default threshold
  in ~2 cycles (~2 weeks).
- Check `AUTOCHAT_KB_CREDIBILITY_REMOVAL_THRESHOLD`: if set too high
  (e.g. `0.9`) most articles will be flagged quickly after any decay.
- For articles excluded when they should be included: use
  `POST /admin/kb/documents/reset-credibility/{id}` to restore them and
  then lower `DECAY_RATE` or raise `DECAY_INTERVAL_HOURS`.
- Confirm that `semantic_search` is not being called with
  `exclude_flagged=False` explicitly in a custom rag node override.

### No internal documentation available

**Symptom:** The `"No internal documentation available"` fallback
appears in AI responses even after synthesis runs complete.

**Checks:**

1. Confirm the synthesis run actually completed (`GET /admin/synthesis/status`,
   `phase: "completed"`, `total_integrated > 0`).
2. Check whether the synthesized articles were subsequently flagged:
   `GET /admin/kb/documents?removal_flagged=true&source=feedback`.
3. Verify `KB_SIMILARITY_THRESHOLD` is not set so high that synthesized
   articles fall below the retrieval cutoff.
4. Check `KB_TOP_K_RESULTS` — synthesized articles compete with crawled
   documents; lower `kb_similarity_threshold` or raise `kb_top_k_results`
   if the KB is large.

---

## Best Practices

### Choosing synthesis threshold values

**Decay rate and interval:**

- A rate of `0.05` with the default `168 h` interval means an article
  that never receives positive signals reaches `removal_threshold=0.3`
  after approximately 14 weeks (14 cycles × 0.05 = 0.7 decay from 1.0).
- Adjust based on your domain's rate of change. Fast-moving domains
  (e.g. performance regression patterns) warrant a higher decay rate or
  shorter interval. Stable reference knowledge warrants a lower rate or
  longer interval.

**Citation boost vs. feedback signal:**

- Citation boost rewards articles that are _retrieved_ — even if the
  downstream AI answer is never rated. Good signal for frequently-queried
  topics.
- Feedback signal rewards articles that contributed to _rated_ responses.
  Stronger signal (direct human feedback) but less frequent.
- Enable both for the richest credibility picture; start with one if
  tuning complexity is a concern.

**Removal threshold:**

- Default `0.3` means an article must lose 70% of its score before being
  flagged. This is deliberately conservative — only truly stale articles
  are auto-excluded.
- Do not set it above `0.7` unless you are also enabling citation boost
  to counteract decay; otherwise most articles will be flagged within a
  few cycles.

### Tagging conventions

Tags directly determine how synthesized KB articles are structured. Good
tagging practice:

- Use **domain-specific noun phrases** (`"ipc-computation"`,
  `"memory-bandwidth-l3"`, `"numa-topology"`), not generic labels
  (`"bug"`, `"wrong"`, `"fix"`).
- Keep tags **consistent across reviewers** — a misspelled tag
  (`"ipc_compute"` vs `"ipc-computation"`) creates duplicate articles
  for the same topic.
- Use **multiple tags** when an entry spans topics; the entry will
  participate in each group's synthesis.
- Avoid tags that are too broad (`"performance"`) or too narrow
  (`"query-7a-2026-05-01"`); aim for a granularity where 2–10 entries
  per tag is the typical batch size.
- Document your tag taxonomy somewhere accessible to all reviewers (a
  pinned note in your team wiki works well).

### Reviewer onboarding

New reviewers should:

1. Read this guide in full before reviewing their first entry.
2. Shadow an experienced reviewer on 5–10 entries before approving
   independently.
3. Agree on the team's tag taxonomy before their first solo session.
4. Understand the rollback path — mistakes are recoverable; err on the
   side of approving borderline entries and refining via rollback rather
   than over-rejecting.
5. Check `GET /admin/feedback/stats` regularly to monitor queue health.
