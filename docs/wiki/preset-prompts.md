# Preset Prompts

Preset prompts are one-click buttons displayed at the bottom of the chat UI. Clicking a button immediately sends a pre-defined message to the AI — no copy-pasting required. Templates can contain `{{VARIABLE_NAME}}` placeholders (e.g. `{{JOB_ID}}`, `{{PLATFORM}}`, `{{TENANT}}`) that are resolved from a per-session cache or by prompting the user for the missing values.

---

## Quick Start

```python
from auto_bedrock_chat_fastapi import add_bedrock_chat

add_bedrock_chat(
    app,
    preset_prompts_file="prompts.yaml",   # recommended: YAML file
)
```

Or pass prompts inline:

```python
add_bedrock_chat(
    app,
    preset_prompts=[
        {
            "label": "Health Check",
            "description": "Ask the AI to summarise API health",
            "template": "Please summarise the current API health status.",
        }
    ],
)
```

---

## YAML File Format

The recommended approach is a YAML file with a top-level `prompts` list:

```yaml
prompts:
  - label: "Workload Analysis"
    description: "Full CPU workload characterization for a job"
    template: |
      Perform a full workload characterization for job {{JOB_ID}}.

      Include:
      - CPU utilization breakdown
      - Memory and I/O patterns
      - Bottleneck identification

  - label: "Health Check"
    description: "Summarise API health"
    template: "Please summarise the current API health status."
```

Each entry supports these keys:

| Key           | Required | Description                                                  |
| ------------- | -------- | ------------------------------------------------------------ |
| `label`       | ✅        | Button text shown in the UI                                            |
| `template`    | ✅        | Prompt text sent to the AI; may contain `{{VARIABLE_NAME}}` placeholders |
| `description` | ❌        | Tooltip shown on hover                                                 |

---

## Template Placeholders

Templates may contain any number of `{{VARIABLE_NAME}}` placeholders (double curly braces, SCREAMING\_SNAKE\_CASE). The client resolves each placeholder from a per-session **prompt cache** (`currentPromptCache`) in one of two ways:

1. **Auto-detected (JOB\_ID only)** — the UI scans every user message for a UUID (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`) and caches the most recent one under the key `JOB_ID`. When a preset button is clicked, any `{{JOB_ID}}` is substituted automatically from this cache.

2. **Inline panel** — for each placeholder whose value is not yet in the cache, a small input panel appears below the message box with one labelled field per missing variable. Entering a value stores it in the cache so that subsequent prompts requiring the same placeholder do not ask again.

### Validation rules

The panel validates each field before sending:

| Variable name pattern | Validation           |
| --------------------- | -------------------- |
| Ends with `_ID`       | Must be a valid UUID |
| Any other name        | Must be non-empty    |

Invalid fields are highlighted with a red border; focus moves to the first failing field.

### Adding new variable types

You can use any `{{VARIABLE_NAME}}` in a template — the system is generic. For example:

```yaml
prompts:
  - label: "Platform Analysis"
    description: "Platform resource utilization breakdown"
    template: |
      Give me a resource utilization breakdown for platform {{PLATFORM}}.

  - label: "Tenant Report"
    description: "Generate report for a tenant"
    template: |
      Generate a full report for tenant {{TENANT}} (job {{JOB_ID}}).
```

When the user clicks "Tenant Report" and neither `TENANT` nor `JOB_ID` is in the cache, the panel will show two input rows — one for each missing value — and cache both on submit.

---

## Configuration Reference

### `add_bedrock_chat()` Parameters

| Parameter             | Type            | Description                                                                                     |
| --------------------- | --------------- | ----------------------------------------------------------------------------------------------- |
| `preset_prompts`      | `list[dict]`    | Prompts provided inline as Python dicts (highest priority)                                      |
| `preset_prompts_file` | `str`           | Path to a YAML file containing prompt definitions                                               |

### `ChatConfig` Fields

| Field                  | Env Variable                    | Default  | Description                                                              |
| ---------------------- | ------------------------------- | -------- | ------------------------------------------------------------------------ |
| `preset_prompts`       | —                               | `[]`     | In-memory prompt list; set programmatically or via a pre-built config    |
| `preset_prompts_file`  | `BEDROCK_PRESET_PROMPTS_FILE`   | `None`   | Path to YAML file; honoured when `preset_prompts` is empty               |

### Resolution Priority

The plugin resolves prompts in this order (first non-empty source wins):

1. `preset_prompts=` kwarg to `add_bedrock_chat()` / `BedrockChatPlugin()`
2. `config.preset_prompts` — populated by a pre-built `ChatConfig` or env var
3. YAML file — `preset_prompts_file=` kwarg or `BEDROCK_PRESET_PROMPTS_FILE` env var

---

## Using a YAML File with `importlib.resources`

When the YAML file is shipped as package data (i.e., bundled inside a Python package), resolve the path with `importlib.resources` so it works correctly whether the package is installed as a wheel or in editable mode:

```python
import importlib.resources as pkg_resources

prompts_path = str(
    pkg_resources.files("my_package").joinpath("prompts.yaml")
)

add_bedrock_chat(app, preset_prompts_file=prompts_path)
```

---

## Environment Variable

You can also point to a YAML file via the environment without changing code:

```bash
BEDROCK_PRESET_PROMPTS_FILE=/etc/myapp/prompts.yaml
```

---

## Error Handling

`load_preset_prompts_from_yaml()` never raises — on any failure it returns `[]` and logs a warning, so the chat UI starts normally:

| Condition                        | Behaviour                                   |
| -------------------------------- | ------------------------------------------- |
| `pyyaml` not installed           | `WARNING` log; returns `[]`                 |
| File not found                   | `DEBUG` log; returns `[]`                   |
| Invalid YAML / parse error       | `WARNING` log; returns `[]`                 |
| File present but no `prompts` key | `INFO` log; returns `[]`                   |

---

## Security Notes

- Button labels and descriptions are set as `textContent` / `title` attributes — no HTML injection risk.
- Prompt templates are passed through `marked.parse()` then sanitized with **DOMPurify** before being rendered in the chat bubble, preventing XSS from raw HTML in templates.
- Placeholder substitution uses `String.replaceAll()` on the resolved plain-text template before it is rendered as markdown — there is no risk of a placeholder expanding into executable code.

---

## See Also

- [Chat UI](chat-ui.md) — general UI features and endpoints
- [Configuration](configuration.md) — full settings reference
