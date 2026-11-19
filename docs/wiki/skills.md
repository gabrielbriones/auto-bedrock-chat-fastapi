# Skills

A **skill** is a named, admin-managed instruction set stored in the database. Admins create and edit skills
at runtime through the [Admin API](admin-api#skill-management), and chat users can list the enabled ones
through a [catalog endpoint](#user-catalog).

> **Status:** this release delivers the skill **store, the admin REST API and the user catalog** only.
> Nothing in the chat flow loads a skill yet (no `load_skill` model tool and no slash-command invocation), and
> there is no management UI. Skills are intended to eventually replace [Preset Prompts](preset-prompts), which
> are a separate mechanism and are **unchanged** by this feature: no migration, no shared ids, no seeding.

---

## Skill model

| Field          | Type               | Notes                                                                                           |
| -------------- | ------------------ | ----------------------------------------------------------------------------------------------- |
| `name`         | string             | Stable unique id. Must match `^[a-z0-9][a-z0-9_-]{0,63}$`. Immutable: a skill cannot be renamed |
| `display_name` | string             | Required, 1–100 characters                                                                      |
| `description`  | string             | Short, user-visible summary; limited by `skill_description_max_chars` (default 300)             |
| `instructions` | string             | The instruction text; required, not blank; limited by `skill_instructions_max_chars` (32000)    |
| `parameters`   | list of parameters | Ordered; at most `skill_max_parameters` (default 20); names unique within the skill             |
| `enabled`      | boolean            | Disabled skills are hidden from the catalog. Default `true`                                     |
| `created_at`   | timestamp          | Set on creation, never changed                                                                  |
| `updated_at`   | timestamp          | Set on every write                                                                              |
| `updated_by`   | string             | The admin identity (`user_id`) of the last writer; `anonymous` when `require_tool_auth=false`   |

### Parameters

A parameter declares an input the skill accepts. Everything except `name` and `required` is a **presentation
hint** for a future composer UI; runtime values are always strings and the server performs **no type
validation** against `input_type` or `options`.

| Field         | Type            | Default  | Notes                                                 |
| ------------- | --------------- | -------- | ----------------------------------------------------- |
| `name`        | string          | —        | Required. Same pattern as skill names                 |
| `label`       | string          | `""`     | Up to 100 characters                                  |
| `help`        | string          | `""`     | Up to 500 characters                                  |
| `required`    | boolean         | `false`  |                                                       |
| `default`     | string or null  | `null`   | Up to 1000 characters                                 |
| `input_type`  | string          | `"text"` | One of `text`, `number`, `select`, `checkbox`         |
| `options`     | list of strings | `[]`     | At most 50 options of 1–200 characters (for `select`) |
| `placeholder` | string          | `""`     | Up to 200 characters                                  |
| `example`     | string          | `""`     | Up to 500 characters                                  |

There is no field for numeric bounds (`min`/`max`/`step`), validation patterns or auto-detection patterns. A
presentation-hints object for them is planned for a later release.

---

## Storage

Skills live in a single `skills` table (DDL: `autolangchat/db/sql/skill_schema.sql` for Postgres and
`skill_schema_sqlite.sql` for SQLite, applied automatically at startup).

- **Backend and database are inferred**, exactly like [persisted user settings](configuration#persisted-user-settings):
  Postgres when an enabled conversation/feedback/token-usage store is on `postgres` (or the KB is on `pgvector`),
  otherwise the SQLite file resolved from `AUTOCHAT_CONVERSATION_DB_PATH` → `AUTOCHAT_FEEDBACK_DATABASE_PATH` →
  `AUTOCHAT_TOKEN_USAGE_DATABASE_PATH` → `KB_DATABASE_PATH`. There are no skill-specific storage settings.
- **Opt out** with `AUTOCHAT_SKILLS_PERSISTENCE_ENABLED=false`.
- **Fail soft:** if no usable database can be resolved, or the store cannot be opened at startup, skills are
  disabled (an error is logged and `skills_enabled` is `false`). Chat is unaffected.
- **No versioning:** an update replaces the whole skill content and the last write wins. There are no
  revisions, rollback or optimistic-concurrency checks yet; they are planned as a follow-up.

### Configuration

| Env Variable                            | Default | Description                            |
| --------------------------------------- | ------- | -------------------------------------- |
| `AUTOCHAT_SKILLS_PERSISTENCE_ENABLED`   | `true`  | Master switch for the skill store      |
| `AUTOCHAT_SKILL_INSTRUCTIONS_MAX_CHARS` | `32000` | Maximum length of `instructions`       |
| `AUTOCHAT_SKILL_DESCRIPTION_MAX_CHARS`  | `300`   | Maximum length of `description`        |
| `AUTOCHAT_SKILL_MAX_PARAMETERS`         | `20`    | Maximum number of parameters per skill |

The three limits must be positive and are applied on every create and replace. Lowering a limit does not
modify existing skills; they are only checked the next time they are written.

---

## Admin API

All routes require an admin; see [Admin API](admin-api#skill-management) for the full reference. They are
registered only when `AUTOCHAT_ADMIN_ENABLED=true` and the skill store is configured.

| Method | Path                           | Description                                                    |
| ------ | ------------------------------ | -------------------------------------------------------------- |
| GET    | `/admin/skills`                | List all skills, enabled or not, **without** `instructions`    |
| POST   | `/admin/skills`                | Create a skill (`409 skill_exists` if the name is taken)       |
| GET    | `/admin/skills/{name}`         | One skill, including `instructions`                            |
| PUT    | `/admin/skills/{name}`         | Replace the whole content; `enabled` and `created_at` are kept |
| DELETE | `/admin/skills/{name}`         | Permanently delete a skill (`204`)                             |
| POST   | `/admin/skills/{name}/enable`  | Enable a skill                                                 |
| POST   | `/admin/skills/{name}/disable` | Disable a skill                                                |

```bash
curl -sS -X POST https://app.example.com/chat/admin/skills \
  -H 'Content-Type: application/json' -b cookies.txt \
  -d '{
        "name": "job-triage",
        "display_name": "Job triage",
        "description": "Triage a failed job",
        "instructions": "Investigate job {{job_id}} and summarise the root cause.",
        "parameters": [{"name": "job_id", "label": "Job ID", "required": true}]
      }'
```

---

## User catalog

`GET {chat_endpoint}/skills/catalog` (for example `/chat/skills/catalog`) lists the **enabled** skills so a chat
client can offer them. It is a regular user feature and is registered whenever the skill store exists,
independent of `admin_enabled`.

```json
{
  "items": [
    {
      "name": "job-triage",
      "display_name": "Job triage",
      "description": "Triage a failed job",
      "parameters": [
        {
          "name": "job_id",
          "label": "Job ID",
          "required": true,
          "input_type": "text"
        }
      ]
    }
  ]
}
```

- Each parameter is returned with all its [fields](#parameters); the example above omits the empty ones for brevity.
- It never returns `instructions` and never returns a disabled skill. The response model has no `instructions`
  field at all.
- **Authentication:** the caller's identity is resolved like the [conversation endpoints](conversation-persistence)
  (SSO session cookie, then `auth_verification_endpoint`); `401` when none can be resolved. When
  `AUTOCHAT_REQUIRE_TOOL_AUTH=false` no authentication is configured, so no identity is required, mirroring the
  anonymous-admin behaviour of the Admin API.
- Hiding `instructions` from the catalog does not make them secret from the model or from admins: anything a
  skill says should be treated as visible to whoever can run it.

---

## Capability flag

`GET /admin/_capabilities` includes `skills_enabled`: `true` when the skill store is configured **and** opened
successfully. A dashboard can use it to hide skill management when skills are unavailable.

## Unavailable store

Routes are registered while the plugin is constructed, before the store is opened at startup. If the store is
disabled afterwards (for example because opening it failed), the skill routes remain mounted but answer
`503 skills_unavailable`, and `skills_enabled` is `false`.

## Auditing

Skill writes emit `bedrock.audit` records (`skill.create`, `skill.replace`, `skill.enable`, `skill.disable`,
`skill.delete`) carrying the actor and the skill name, never the instructions. See
[Audit logging](admin-api#audit-logging).

---

## Not included yet

- Loading a skill into a conversation (a model `load_skill` tool, slash-command invocation, rendering of
  `{{PLACEHOLDER}}` parameters).
- Skill versioning: revisions, rollback and `expected_revision` checks.
- A presentation-hints object for numeric bounds, validation and detection patterns.
- Importing [Preset Prompts](preset-prompts) as skills. Create skills manually through the Admin API.
- A management UI.
