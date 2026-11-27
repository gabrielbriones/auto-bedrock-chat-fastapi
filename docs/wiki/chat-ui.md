# Chat UI

The plugin includes a built-in React web chat interface. Enable it with `enable_ui=True` (the default) and access it at `/chat/ui`.

---

## Accessing the Chat UI

```python
autolangchat_plugin = add_autolangchat(
    app,
    enable_ui=True,           # default: True
    ui_endpoint="/chat/ui",   # default; the React SPA is served here
)
```

Open your browser to `http://localhost:8000/chat/ui`. The build directory is
`AUTOCHAT_UI_DIST_DIR` (defaults to `<repo>/frontend/dist`, else the build bundled into
release wheels by `.github/workflows/release.yml`). Build it with
`cd frontend && npm ci && npm run build`; the `Dockerfile` does this automatically.

---

## UI Features

- **Real-time chat** — messages stream via WebSocket
- **Typing indicator** — shows when the AI is generating a response
- **Tool call visibility** — displays which API endpoints the AI is calling and the results
- **Conversation history** — persists within the session
- **Authentication modal** — UI for providing credentials (bearer token, API key, basic auth, OAuth2)
- **Session management** — clear history, ping, logout
- **Preset prompt buttons** — one-click buttons that send pre-defined prompts, hidden while skills are active; see [Preset Prompts](preset-prompts.md)
- **Slash commands** — type `/` to pick a [skill](skills); see [below](#slash-commands)
- **Per-message usage label** — token usage of each answer, with its estimated price on hover; see [below](#per-message-usage-label)

### Per-message usage label

Every assistant answer whose `ai_response.metadata.usage` (or persisted history `metadata.usage`) is present shows a
small muted line under the bubble, `Tokens 1.2k in / 340 out`, summing **every** AI call of the turn (see
[Token Usage Tracking](token-usage-tracking.md#per-turn-usage-and-cost)). While `final` is `false` it is suffixed
`· pending`.

- **Pending → final**: on the turn that creates a conversation, the title call runs after the answer, so the label
  stays pending until the [`usage_updated`](websocket-client.md#usage_updated) frame with the same `message_id`
  replaces it. A `usage_updated` for an answer the client does not hold is ignored. Reloading the conversation shows
  the persisted final totals.
- **Hover card**: hovering or clicking/tapping the label opens a card headed by the estimate (`Estimated $0.0123`, or
  "No price estimate for this model" when `estimated_cost` is null — never $0), then the input/output split by
  purpose (chat, summarization, retries, title, embedding — from `usage.by_purpose`), cache read and cache write
  tokens and the exact total. Screen readers get the same breakdown as the label's accessible name.
- **Hidden** for user messages, failed or interrupted turns, and answers without `usage` (e.g. older servers).
- The value is an **estimate**, so the UI never labels it a "cost". Sub-cent amounts keep four decimals; a positive
  amount below `0.0001` reads `<$0.0001`.

### Conversation usage

Once any answer in the open conversation has reported usage, the composer toolbar shows a compact summary beside the
model picker, e.g. `13% context $0.3124`, updated as each answer (and each `usage_updated`) arrives:

- **Estimate**: the sum of the answers' estimates. Answers without a price (or priced in another currency) are left
  out and counted in the card; with no priced answer it shows the token total instead (`162k tokens`).
- **Context**: how full the model's input window is after the latest answer (`usage.context`), coloured amber from
  75% and red from 90%. Shown only when the server knows the model's window; it is taken from the latest answer alone,
  so switching to a model with an unknown window hides it rather than showing a stale reading.
- **Card** (hover or click): the estimate, total input/output, cache read/write, the number of answers counted and the
  context meter with exact token counts.
- Answers loaded from history count too, as long as they were stored with usage.

### Slash commands

Typing `/` at the start of the composer opens a menu of the enabled [skills](skills#slash-commands), filtered as the
name is typed (name prefixes first, then names or titles containing the text). Each entry shows the command, its
title, description and usage.

- **Menu keys**: ↑/↓ move the highlight, Enter or Tab picks the skill, Escape closes the menu until the text changes.
  While the menu is open Enter never sends. Clicking an entry picks it too.
- **Parameter fields**: a picked skill (or one typed in full, `/<name>` plus a space) becomes a chip above the message
  box with one field per parameter, so nobody types `key=value`:
  - text and number parameters get a text box (placeholder from the skill's `placeholder`, else `example`),
    `select` parameters a drop-down of their options, `checkbox` parameters a checkbox;
  - required parameters are marked `*`, and Send stays disabled until they are filled;
  - fields (and the message box, for `input`) start at the parameter's default; a blank field is left out, so the
    server applies its own default; a required checkbox without a default starts unchecked, sent as `false`;
  - the first field gets focus; Enter in a field sends, Escape or the chip's remove button drops the skill.
- **Free text**: for a skill with an `input` parameter the message box stays and fills it (its placeholder is the
  parameter's); Backspace in an empty box drops the skill. A skill without `input` hides the message box, and one
  without any parameter just runs on Enter.
- **What is sent**: the plain `chat` frame the server parses, e.g. `/job-triage job_id="job 42" format=txt`. Values
  that are not a single plain token are double-quoted with `\` and `"` escaped; free text goes as `input="..."`.
- A command typed or pasted with its pairs (`/job-triage job_id=1`) is sent as typed, with the skill's usage line
  shown above the message box.
- `//` and text that does not start with `/` never open the menu.
- The list comes from a [`skill_catalog`](websocket-client.md#skill_catalog) request on the chat socket (so it works
  with any sign-in method), read when input becomes available and again on composer focus once it is a minute old,
  and only when the bootstrap reports `skillsEnabled`. If it cannot be read, typing
  `/` shows "Skills could not be loaded. You can still send a command." instead of the menu; commands still work
  because the server parses them.
- **Refused commands**: a [`skill_error`](websocket-client.md) (unknown or disabled skill, bad parameters) is shown
  as a system notice in the transcript, the command is removed from the transcript (no turn was started) and put
  back in the message box to be corrected.
- **Presets**: while skills are active (`skillsEnabled` and a non-empty catalog) the preset bar and variables panel
  are hidden; they stay hidden until the catalog has been read and return if it is empty or unreadable.
- Screen readers: while the menu is open the composer keeps focus and points at the highlighted entry
  (`aria-activedescendant`), and the number of matches is announced. The fields are a labelled group.

### Deep links

A link can run a skill as soon as the chat is usable:

```
https://your-app.example.com/chat/ui?skill=job-triage&job_id=abc123&format=txt
```

- `skill` names the skill; any other key matching one of its parameters fills it (an `input` key fills the free
  text). The command is sent as `/job-triage job_id=abc123 format=txt`.
- `autosend=0` or `autosend=false` puts the command in the message box instead of sending it.
- A legacy `?prompt=<id>&VAR=value` link runs the preset's default skill (same name, `VAR` lower-cased); one that
  no enabled skill answers to falls back to the [preset deep link](preset-prompts.md#deep-linking--auto-start-via-query-string),
  and the preset bar then stays visible so its variables can be filled in.
- The link waits for the skill catalog and for the chat to be idle (signed in, connected, no answer pending), runs
  once, and its keys (including a `prompt` a `skill` link took priority over) are then removed from the address
  bar. Authentication works as for preset deep links.

---

## Endpoints Registered by the Plugin

| Method | Path                     | Description                                                 |
| ------ | ------------------------ | ----------------------------------------------------------- |
| `GET`  | `/chat/ui`, `/chat/ui/*` | Serves the React SPA (deep links fall back to `index.html`) |
| `GET`  | `/chat/config`           | JSON bootstrap payload read by the SPA                      |
| `WS`   | `/chat/ws`               | WebSocket chat connection                                   |
| `GET`  | `/chat/health`           | Plugin health check                                         |
| `GET`  | `/chat/stats`            | Chat statistics (admin only; 404 unless admin enabled)      |
| `GET`  | `/chat/tools`            | Tool metadata and schema stats                              |
| `POST` | `/chat/knowledge/search` | Hybrid KB search endpoint (when RAG enabled)                |

All paths are configurable. Example with custom paths:

```python
autolangchat_plugin = add_autolangchat(
    app,
    chat_endpoint="/ai",
    websocket_endpoint="/ai/ws",
    ui_endpoint="/ai/chat"
)
```

---

## UI Authentication

The UI shows an **authentication modal** when `enable_tool_auth=True`. Users can log in before chatting.

To use auth in the UI:

1. Enable tool auth in the plugin: `add_autolangchat(app, enable_tool_auth=True)`
2. Open the chat UI and click the **Login** button
3. Select auth type and enter credentials
4. All tool calls made by the AI will automatically include those credentials

The auth credentials are sent once as a WebSocket message:

```json
{
  "type": "auth",
  "auth_type": "bearer_token",
  "token": "your-token-here"
}
```

See [Authentication](authentication.md) for all supported auth types.

---

## UI Customization

The UI is a React + TypeScript + Vite SPA in [`frontend/`](../../frontend/README.md). It reads everything it needs
from `GET {chat_endpoint}/config` at start-up, so titles, preset prompts, auth types and feature flags are all
controlled through the plugin configuration rather than by editing the UI.

To change the UI itself, edit the sources under `frontend/src/` and rebuild. To replace it entirely, serve your own
frontend against the WebSocket endpoint directly. See [WebSocket Client](websocket-client.md) for building a custom client.

---

## Input Lock While Responding

By default the chat input and Send button are disabled from the moment a user sends a message until the assistant's response is fully received. This prevents queuing additional messages while a reply is in flight.

To allow users to send messages at any time (original behavior):

```
AUTOCHAT_UI_LOCK_INPUT_WHILE_RESPONDING=false
```

When locked, the composer shows a "Waiting for response..." state and is disabled. The input is automatically re-enabled on `ai_response`, `error`, `auth_expired`, or WebSocket reconnect.

---

## Disabling the UI

If you only need the WebSocket/REST API (e.g., you have a custom frontend):

```python
autolangchat_plugin = add_autolangchat(app, enable_ui=False)
```

Or via `.env`:

```
AUTOCHAT_ENABLE_UI=false
```

---

## See Also

- [WebSocket Client](websocket-client.md) — build a custom client
- [Authentication](authentication.md) — securing tool calls
- [Configuration](configuration.md) — endpoint path settings
- [Admin Dashboard](dashboard.md) — the Dashboard button shown to admins
