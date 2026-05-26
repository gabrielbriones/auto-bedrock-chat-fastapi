# Admin API

The **Admin API** is the HTTP control plane for the human-in-the-loop
review workflow that backs the [Feedback Collection](feedback-collection)
feature and the knowledge base. It exposes two route groups under
`/admin/`, plus a capability probe used by the Dashboard UI:

- **Feedback Review** — `/admin/feedback/*` — list, inspect, decide on
  user-submitted 👍 / 👎 corrections.
- **KB Management** — `/admin/kb/documents/*` — list, inspect, edit, and
  delete KB documents (re-embeds on content change).
- **Capability probe** — `GET /admin/_capabilities` — tells the Chat UI
  whether the current caller is an admin; always 200.

The synthesis-control surface (`/admin/synthesis/*`) is **reserved but
unimplemented** in this release; requests return 404. See
[docs/plans/feedback-review-api.md](../plans/feedback-review-api.md) §T7.

> **Security boundary:** hiding admin endpoints behind `admin_enabled` is
> a configuration switch, not a security boundary. Authorization is
> enforced on **every** admin request via the `AdminAuthorizer`. If you
> turn `admin_enabled` on without configuring an authorizer, every
> request is rejected with `403 not_admin` by default — see
> [Choosing an authorizer](#choosing-an-authorizer) below.

---

## Enabling

Set `BEDROCK_ADMIN_ENABLED=true` to register the `/admin/*` routes.
When disabled (the default), the routes are not mounted and clients get
a clean 404 — exactly the same shape as the SSO routes when SSO is off.

```bash
BEDROCK_ADMIN_ENABLED=true
# pick one authorizer (see below); without one, every request is 403
BEDROCK_ADMIN_VERIFICATION_ENDPOINT=https://identity.internal/api/v1/admin/check
# or
BEDROCK_ADMIN_REQUIRED_GROUPS=kb-admins,bedrock-reviewers
```

### Configuration reference

| Setting                       | Env var                               | Default | Description                                                                                                             |
| ----------------------------- | ------------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------- |
| `admin_enabled`               | `BEDROCK_ADMIN_ENABLED`               | `False` | Master switch for the `/admin/*` route block.                                                                           |
| `admin_verification_endpoint` | `BEDROCK_ADMIN_VERIFICATION_ENDPOINT` | `None`  | URL the plugin POSTs identity to on every admin request. Relative paths resolve against `app_base_url`.                 |
| `admin_required_groups`       | `BEDROCK_ADMIN_REQUIRED_GROUPS`       | `[]`    | CSV of group names; user is admin if any of these appears in the SSO `groups` claim. Used only when no endpoint is set. |

---

## Authentication

The `require_admin` dependency tries **two identity sources** in order
on every request:

1. **SSO session cookie** — when `sso_enabled=True` and a valid
   `sso_session_token` cookie is present, the cookie identifies the
   caller. This is the path browsers use.
2. **`auth_verification_endpoint`** — when configured, the caller's
   `Authorization` / `X-API-Key` headers are forwarded to the endpoint
   and the JSON body returned is treated as the admin identity. This is
   the path non-SSO deployments use (Bearer / API key / Basic).

If neither resolves an identity, the request returns
`401 not_authenticated`. If an identity is resolved but the
`AdminAuthorizer` rejects it, the request returns `403 not_admin`.

### CSRF model

The `sso_session_token` cookie is set with `SameSite=lax` + `HttpOnly`,
which blocks the cookie from riding **cross-site** state-mutating
requests at the browser level — the classic CSRF vector. No custom
header check is layered on top: a constant header would only matter
when `SameSite` is weakened, but in that same configuration it's
trivially forgeable by JS once CORS is opened up, so it isn't a real
defense.

If a future deployment needs cross-site admin access (`SameSite=None` +
permissive CORS), it should layer a **per-session CSRF token** at that
point — a stateful token that's bound to the session cookie. A static
custom header would not help.

The header-forwarded path (Authorization / X-API-Key) is **not**
vulnerable to classic CSRF because browsers do not auto-attach those
headers on cross-site requests.

---

## Choosing an authorizer

The plugin ships three built-in `AdminAuthorizer` implementations and
resolves them in this order at construction time (highest wins):

1. Explicit `admin_authorizer=...` constructor argument (test stub or
   the Access Control task's custom impl).
2. `admin_verification_endpoint` is set → `RemoteAdminAuthorizer`.
3. `admin_required_groups` is non-empty → `SSOGroupAdminAuthorizer`.
4. Otherwise → `DenyAllAdminAuthorizer` (every request 403).

The decision is logged at INFO on plugin startup so operators can see
which path is active.

| Authorizer                | When to use                                                                                 | Pros                                                                                       | Cons                                                                                                  |
| ------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `RemoteAdminAuthorizer`   | You have a centralized identity service or an "is this user an admin?" service to call.     | Instant revocation (no caching — every request hits the endpoint). Single source of truth. | One HTTP call per admin request; endpoint outage = no admins can review (returns 403 on errors).      |
| `SSOGroupAdminAuthorizer` | Your IdP (Okta / Azure AD / Cognito / Keycloak) already returns group claims in SSO tokens. | Zero additional infrastructure; works the moment your IdP returns groups.                  | Group membership only refreshes on next SSO login; rotating an admin out requires a session re-issue. |
| `DenyAllAdminAuthorizer`  | Safe default when nothing else is configured.                                               | Misconfiguration manifests as 403, not "anyone can edit the KB."                           | Useless until you wire one of the above.                                                              |
| Custom (constructor arg)  | Access Control task / tests / "users with admin = true in our user table."                  | Full control; can layer caching, multi-factor checks, audit hooks.                         | You own it.                                                                                           |

### `admin_verification_endpoint` contract

The endpoint receives a `POST` with this body:

```json
{
  "user_id": "alice@example.com",
  "email": "alice@example.com",
  "groups": ["kb-admins", "engineering"],
  "claims": {
    "sub": "alice",
    "preferred_username": "alice@example.com",
    "groups": ["kb-admins", "engineering"]
  }
}
```

It must respond with `200 OK` and a JSON body containing
`{"is_admin": true|false}`. Anything else (non-2xx, network error,
malformed body) is treated as **deny** — the caller gets `403 not_admin`.

**Latency budget:** because there's no caching, every admin request
makes this call. Admin traffic is human-paced (a person clicking
through a dashboard, not a hot API path), so p99 around a few hundred
milliseconds is fine; treat it like a database lookup, not like a
synchronous part of a hot request path.

Minimal reference implementation:

```python
from fastapi import FastAPI

app = FastAPI()
ADMINS = {"alice@example.com", "bob@company.com"}

@app.post("/api/v1/admin/check")
async def check(body: dict) -> dict:
    return {"is_admin": body.get("email") in ADMINS}
```

---

## Endpoints

All endpoints sit under `/admin/`. Responses use a flat error envelope:

```json
{ "code": "not_found", "detail": "kb document foo not found" }
```

### Capability probe

| Method | Path                   | Description                                                         |
| ------ | ---------------------- | ------------------------------------------------------------------- |
| GET    | `/admin/_capabilities` | Returns `{is_admin, anonymous}` — **always 200**, never 403 or 401. |

Response shape:

```json
{ "is_admin": true, "anonymous": false }
```

| Field       | Type    | Notes                                                                                                                |
| ----------- | ------- | -------------------------------------------------------------------------------------------------------------------- |
| `is_admin`  | boolean | `true` when the caller is authenticated and authorised as an admin.                                                  |
| `anonymous` | boolean | `true` when `require_tool_auth=false` — the escape hatch is unconditional; identity resolution is bypassed entirely. |

The Chat UI calls this endpoint on page load. If `is_admin=true` it
reveals the Dashboard button in the header; otherwise the button stays
hidden. If `admin_enabled=false` this route is not mounted at all,
returning a clean 404 — the button is not even rendered in that case.

The endpoint **never** returns 403 or 401 so the capability check is
transparent to non-admin users (the button simply stays hidden rather
than triggering a visible error).

### Feedback Review

| Method | Path                    | Description                                                 |
| ------ | ----------------------- | ----------------------------------------------------------- |
| GET    | `/admin/feedback`       | Filterable, paginated list. Returns `FeedbackListResponse`. |
| GET    | `/admin/feedback/stats` | Aggregate counts + `top_tags` + `oldest_pending_hours`.     |
| GET    | `/admin/feedback/{id}`  | Fetch one entry by UUID.                                    |
| PATCH  | `/admin/feedback/{id}`  | Approve / reject; server derives `reviewer_id` from auth.   |

Query parameters for `GET /admin/feedback`:

| Param            | Type                                       | Notes                                                 |
| ---------------- | ------------------------------------------ | ----------------------------------------------------- |
| `status`         | `pending_review` / `approved` / `rejected` | omit = no filter                                      |
| `rating`         | `positive` / `negative`                    |                                                       |
| `tags`           | CSV string                                 | Overlap match; blank entries dropped.                 |
| `has_correction` | `true` / `false`                           | Filter entries that include / lack `correction_text`. |
| `user_id`        | string                                     | Exact match.                                          |
| `date_from`      | ISO-8601                                   | Inclusive.                                            |
| `date_to`        | ISO-8601                                   | Exclusive (matches `created_at < date_to`).           |
| `limit`          | int, default 50, max 200                   | 422 on out-of-bounds.                                 |
| `offset`         | int, default 0                             | 422 on negative.                                      |

PATCH body (`extra='forbid'` — any extra field returns 422):

```json
{
  "review_status": "approved",
  "reviewer_tags": ["ipc", "computation"],
  "reviewer_comment": "Confirmed: IPC = instructions/cycles."
}
```

Tag validation: ≤32 chars, `[A-Za-z0-9_-]+`, deduplicated, ≤20 per
entry. `reviewer_id` and `reviewed_at` in the body are rejected as
`422` — both are derived by the server (the authenticated identity
becomes `reviewer_id`).

### KB Management

| Method | Path                       | Description                                                    |
| ------ | -------------------------- | -------------------------------------------------------------- |
| GET    | `/admin/kb/documents`      | Filterable, paginated list. Returns `KBDocumentListResponse`.  |
| GET    | `/admin/kb/documents/{id}` | Fetch one document. `id` may contain slashes (URL-shaped IDs). |
| PATCH  | `/admin/kb/documents/{id}` | Partial update. Re-embeds when `content` changes.              |
| DELETE | `/admin/kb/documents/{id}` | Hard-delete document + chunks. 204 on success.                 |

PATCH body (all fields optional; `extra='forbid'`):

```json
{
  "content": "...",
  "title": "...",
  "source": "...",
  "source_url": "...",
  "topic": "...",
  "date_published": "2026-01-01",
  "metadata": { "...": "..." },
  "tags": ["alpha", "beta"]
}
```

Concurrency: a per-document `asyncio.Lock` serializes concurrent PATCH

- DELETE for the **same** id so re-embedding doesn't race. Different
  documents update in parallel.

> **URL-shaped IDs:** Web-crawled documents use the source URL as the
> id (e.g. `https://fastapi.tiangolo.com/reference/templating/`).
> Percent-encode it when building the path:
> `/admin/kb/documents/https%3A%2F%2Ffastapi.tiangolo.com%2Freference%2Ftemplating%2F`.
> The route uses FastAPI's `:path` converter so the decoded slashes
> match.

---

## Examples

### Listing pending feedback

```bash
curl -sS -b cookies.txt \
  'https://app.example.com/admin/feedback?status=pending_review&limit=10'
```

### Approving feedback with tags

```bash
curl -sS -b cookies.txt -X PATCH \
  -H 'Content-Type: application/json' \
  -d '{"review_status":"approved","reviewer_tags":["ipc","computation"],"reviewer_comment":"Verified."}' \
  'https://app.example.com/admin/feedback/8c0c3f0e-...'
```

### Editing a KB document (re-embeds on content change)

```bash
curl -sS -b cookies.txt -X PATCH \
  -H 'Content-Type: application/json' \
  -d '{"content":"IPC = instructions / cycles ...","tags":["ipc"]}' \
  'https://app.example.com/admin/kb/documents/https%3A%2F%2Ffastapi.tiangolo.com%2Freference%2Ftemplating%2F'
```

### Non-SSO (header-based) auth

```bash
curl -sS -H 'Authorization: Bearer eyJ...' \
  'https://app.example.com/admin/feedback/stats'
```

The plugin forwards the `Authorization` header to
`auth_verification_endpoint`; the endpoint's response body becomes the
admin identity passed to the authorizer.

---

## Error envelope

All admin errors share a single flat shape:

```json
{
  "code": "<machine_code>",
  "detail": "<human message>",
  "errors": [
    /* optional, per-field */
  ]
}
```

| HTTP | `code`                          | When                                                          |
| ---- | ------------------------------- | ------------------------------------------------------------- |
| 400  | `invalid_filters`               | Bad date window, malformed query value.                       |
| 401  | `not_authenticated`             | No identity source resolved the caller.                       |
| 403  | `not_admin`                     | Identity resolved but `AdminAuthorizer` rejected it.          |
| 404  | `not_found`                     | Target id doesn't exist.                                      |
| 409  | `invalid_status_transition`     | Feedback PATCH attempts a forbidden review-status transition. |
| 422  | (validation error from FastAPI) | Body / path / query failed Pydantic validation.               |

---

## Audit logging

Every state-changing admin request emits a structured log line on the
`bedrock.audit` channel for the host application's log shipper to pick
up. Storage in a dedicated DB table is out of scope; a host that wants
persistence can attach a logging handler.

| Action                   | Where          | Payload                                                                            |
| ------------------------ | -------------- | ---------------------------------------------------------------------------------- |
| `feedback.review.update` | PATCH feedback | `actor_user_id`, `target_id`, full before/after `{status, tags, comment}`, `ts`    |
| `kb.document.update`     | PATCH KB doc   | `actor_user_id`, `target_id`, `content_hash` before/after, `content_changed`, `ts` |
| `kb.document.delete`     | DELETE KB doc  | `actor_user_id`, `target_id`, before-hash + `{title, source, topic, tags}`, `ts`   |

`content_hash` is SHA-256; full content is **not** logged so log lines
stay bounded.

---

## Anonymous admin escape hatch (development only)

When **`require_tool_auth=False`** (the default), every request to an
admin endpoint is accepted as an anonymous admin (`user_id="anonymous"`)
and the authorizer is **bypassed entirely** — regardless of whether
credentials or a resolvable identity are presented.
Every such request emits a `WARNING` on the `bedrock.audit` channel:

```
admin request accepted as anonymous because require_tool_auth=False
(method=PATCH path=/admin/feedback/...); do not use this combination
in production
```

This makes local development without SSO / verification-endpoint
ergonomic, but it is a foot-gun:

> ⚠️ **Production:** set `BEDROCK_REQUIRE_TOOL_AUTH=true` whenever
> `BEDROCK_ADMIN_ENABLED=true`. Otherwise any request to `/admin/*`
> with no identity becomes an unauthenticated admin. Hook the
> `"accepted as anonymous"` log line into your alerting.

---

## Out of scope (intentional)

- `POST /admin/kb/documents` — creation is owned by the populate
  pipeline (and, post-Phase 3, the synthesizer).
- `/admin/synthesis/*` — reserved for Phase 3.
- Rate limiting — operationally enforced upstream (Nginx / ALB).
- Persisting audit logs to a DB table — handled by the host app's log
  shipper.

---

## Related

- [Feedback Collection](feedback-collection) — the user-facing 👍 / 👎
  side; the admin API is the reviewer side of the same loop.
- [Authentication](authentication) — `auth_verification_endpoint`
  contract, used here as one of the two admin identity sources.
- [SSO](sso) — cookie-based identity, used here as the other admin
  identity source.
- [docs/plans/feedback-review-api.md](../plans/feedback-review-api.md)
  — design rationale and task plan for this surface.
