# Admin Dashboard UI

The Admin Dashboard is a browser-based control surface for expert
reviewers and KB administrators. It is served at
`{chat_endpoint}/dashboard` (default: `/bedrock-chat/dashboard`) when
both `admin_enabled=True` and `enable_ui=True` are set.

The dashboard is a pure client-side application: the HTML shell is
server-rendered by FastAPI at `GET {chat_endpoint}/dashboard`, but all
data is loaded via XHR calls to the existing [Admin API](admin-api.md).
The only new Admin API endpoint introduced is the capability probe
(`GET /admin/_capabilities`) used to show or hide the Dashboard button
in the Chat UI header.

---

## Enabling the Dashboard

```bash
BEDROCK_ADMIN_ENABLED=true
BEDROCK_ENABLE_UI=true
# pick one authorizer (see admin-api.md)
BEDROCK_ADMIN_REQUIRED_GROUPS=kb-admins
```

The Chat UI automatically displays a **Dashboard** button in the header
when both conditions are met:

1. `admin_enabled=True` (the button element is rendered in the HTML).
2. `GET /admin/_capabilities` returns `{"is_admin": true, ...}`.

Non-admin users never see the button. Visiting `/bedrock-chat/dashboard`
directly without admin access renders an "Access Denied" empty state —
the page itself loads no data until capability is confirmed.

---

## Views

### Feedback Queue

The primary triage surface for expert reviewers.

- **Table** of `FeedbackEntry` records from `GET /admin/feedback`.
- **Filter chips**: status (pending / approved / rejected), rating
  (positive / negative), has correction, tags CSV, date range.
- Filters compose with AND and are applied on "Apply". Reset clears all
  filters.
- Pagination shows 50 entries per page (API cap: 200). Shows
  `Showing X–Y of Z` with Prev / Next buttons.
- **Pending badge** next to the "Feedback Queue" nav item shows the
  count of `pending_review` entries; refreshed on every approve/reject.
- Clicking a row opens the [Review Drawer](#review-drawer).

### Feedback Stats

Summary statistics sourced from `GET /admin/feedback/stats`.

- **Stat cards**: Total, Pending Review (with oldest-pending-hours),
  Approved, Rejected, Positive, Negative, With Correction.
- **Top-tags bar chart**: horizontal bars for the top 10 reviewer tags,
  proportional to count.

### KB Browser

Browse and manage Knowledge Base documents from
`GET /admin/kb/documents`.

- **Table** of `KBDocument` records: title (with ID subtitle), source,
  topic, tags, chunk count, created date.
- **Filter chips**: source, topic, tags CSV, date-published range.
- Clicking a row opens the [KB Document Editor](#kb-document-editor).

---

## Review Drawer

Opened by clicking a row in the Feedback Queue. Fetches the full entry
from `GET /admin/feedback/{id}`.

### Read-only header

- User ID, rating chip, review status chip, created date.
- Model ID and list of KB sources used.

### Content panels

Side-by-side display:

| Panel       | Content                                     |
| ----------- | ------------------------------------------- |
| Query       | The user's original question.               |
| AI Response | The assistant's answer that the user rated. |

Below the side-by-side panels:

- **User Correction** (blue-bordered, shown only when `correction_text`
  is present) — the user's proposed fix.
- **User Comment** — free-text note from the user (if any).

If the entry was previously reviewed:

- Previous reviewer ID, reviewed-at timestamp.
- Reviewer tags and reviewer comment from the prior decision.

### Decision form

| Field              | Notes                                                                                              |
| ------------------ | -------------------------------------------------------------------------------------------------- |
| Decision (radio)   | `approved` or `rejected` — required.                                                               |
| Tags (chip input)  | Max 20, ≤32 chars, `[A-Za-z0-9_-]+`. Type + Enter or `,` to add; Backspace to remove the last tag. |
| Comment (textarea) | Optional reviewer comment.                                                                         |

**Save** sends `PATCH /admin/feedback/{id}`. On success the drawer
closes and the table row refreshes.

Error handling:

| Status         | Behaviour                                                                                                                                                                                          |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 200            | Drawer closes, table reloads, pending badge updates.                                                                                                                                               |
| 409 conflict   | Inline error with the API `detail` for `invalid_status_transition` (e.g. attempted to set `pending_review`). Currently unreachable via normal HTTP — blocked upstream by request validation (422). |
| 422 validation | Inline error with the API detail.                                                                                                                                                                  |
| Other          | Toast notification.                                                                                                                                                                                |

---

## KB Document Editor

Opened by clicking a row in the KB Browser. Fetches the document from
`GET /admin/kb/documents/{id}` (id is URL-encoded).

### Read-only header

- Document ID (may be a full URL for web-crawled docs), source, source
  URL, created date, chunk count.

### Re-embed warning

A yellow banner appears when the **Content** textarea is edited:
"Saving will re-embed this document and may take several seconds."

### Editable fields

| Field          | Notes                                                                         |
| -------------- | ----------------------------------------------------------------------------- |
| Title          | Free text.                                                                    |
| Topic          | Free text.                                                                    |
| Tags           | Chip input — same validation as reviewer tags.                                |
| Date Published | Date picker (ISO date).                                                       |
| Content        | Multi-line monospace textarea. Changing triggers re-embedding on save.        |
| Metadata       | JSON textarea. Must be valid JSON; an inline error is shown on parse failure. |

**Save** sends `PATCH /admin/kb/documents/{id}` with only changed
fields. If no fields changed a toast informs the user without calling
the API.

**Delete** opens a confirmation modal ("This will permanently delete …
and all its chunks. This cannot be undone."). Confirmed → `DELETE
/admin/kb/documents/{id}` → 204 → toast, drawer closes, table reloads.

---

## KB Synthesis section (Review Drawer)

Displayed for every `approved` feedback entry inside the Review Drawer.

### Not yet synthesized

- A hint text explains the entry has not been integrated into the KB.
- **Synthesize into KB** button calls `POST /admin/synthesis/trigger/{entry_id}`.
  - On success: toast + drawer closes.
  - On 409: inline error "Already synthesized — reload to refresh the entry."
  - On 422: inline error from the API `detail`.
  - On other errors: inline "Synthesis failed: …" message.

### Already synthesized

When `integrated_into_kb_id` is set, the section shows:

| Field  | Value                               |
| ------ | ----------------------------------- |
| Status | "Synthesized ✓"                     |
| At     | `integrated_at` formatted timestamp |
| KB Doc | Truncated document ID (36 chars)    |

A **Roll Back** button is also shown. Clicking it opens a confirmation
modal that prompts for an optional reason. Confirmed →
`POST /admin/synthesis/rollback/{article_id}` with `{"reason": "…"}`.

- On success: toast showing the count of reverted entries + drawer closes + table reloads.
- On 422: inline error (article is not a synthesized document).
- On 500: inline error with a note to check server logs.
- On other errors: inline error message.

---

## KB Document Editor — Roll Back button

When the KB Document Editor is opened on a document with `source = "feedback"`
(i.e. a synthesized article), a **Roll Back Article** button appears in the
footer alongside Delete and Save.

This is the primary way to roll back a synthesized article: navigate to the
**KB Browser**, find the article, open it, and click **Roll Back Article**.

The button prompts for an optional reason, then calls
`POST /admin/synthesis/rollback/{article_id}`. On success: toast with the count
of reverted feedback entries, drawer closes, KB table reloads. Error handling is
the same as the Review Drawer rollback button above.

---

## Dev-mode banner

When the anonymous-admin escape hatch is active
(`require_tool_auth=false` and `admin_enabled=true`), the capability
probe returns `{"is_admin": true, "anonymous": true}`. This fires
unconditionally — regardless of whether SSO or an auth verification
endpoint is configured, and regardless of whether the caller presents
credentials. The dashboard renders a yellow banner:

> **Dev mode — anonymous admin** — `require_tool_auth=false` is active;
> every request is treated as admin regardless of credentials. Do not
> use this in production.

This banner is also the signal to reviewers that they are operating in a
development environment.

---

## Authentication

All XHR calls include `credentials: 'include'` so the
`sso_session_token` HttpOnly cookie is forwarded automatically. The
server's `require_admin` dependency validates every request; a 401 or
403 response (e.g. after session expiry) causes a toast error and the
user is advised to reload or log in again.

No separate dashboard login is needed — the same SSO session used for
the Chat UI grants admin access.

---

## Security notes

- The Dashboard button is **hidden** (not disabled) for non-admins and
  for unauthenticated users. Hiding is a UX courtesy only; server-side
  authorization is the enforced boundary.
- Manually visiting `/bedrock-chat/dashboard` without admin access
  renders the "Access Denied" empty state. No data is leaked because
  every XHR call is gated by `require_admin`.
- All user-supplied strings (feedback content, KB content, user IDs) are
  set via `textContent`/`createTextNode`, never via `innerHTML`, to
  prevent stored-XSS from API content reaching other admins' browsers.
- The JSON metadata editor validates client-side before sending but the
  server is the authoritative validator.

---

## Cross-links

- [Admin API](admin-api.md) — the HTTP API that backs the dashboard.
- [Chat UI](chat-ui.md) — how the Dashboard button is rendered and probed.
- [SSO](sso.md) — the authentication mechanism used for both the Chat UI and the dashboard.
- [Feedback Collection](feedback-collection.md) — how feedback entries are created by users.
