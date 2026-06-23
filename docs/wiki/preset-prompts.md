# Preset Prompts

Preset prompts are one-click buttons displayed at the bottom of the chat UI. Clicking a button immediately sends a pre-defined message to the AI — no copy-pasting required. Templates can contain `{{VARIABLE_NAME}}` placeholders (e.g. `{{JOB_ID}}`, `{{PLATFORM}}`, `{{TENANT}}`) that are resolved from a per-session cache or by prompting the user for the missing values.

---

## Quick Start

```python
from autolangchat import add_autolangchat

add_autolangchat(
    app,
    preset_prompts_file="prompts.yaml",   # recommended: YAML file
)
```

Or pass prompts inline:

```python
add_autolangchat(
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

| Key           | Required | Description                                                              |
| ------------- | -------- | ------------------------------------------------------------------------ |
| `label`       | ✅       | Button text shown in the UI                                              |
| `template`    | ✅       | Prompt text sent to the AI; may contain `{{VARIABLE_NAME}}` placeholders |
| `description` | ❌       | Tooltip shown on hover                                                   |

---

## Template Placeholders

Templates may contain any number of `{{VARIABLE_NAME}}` placeholders (double curly braces, SCREAMING_SNAKE_CASE). Each placeholder maps to an input field shown in the variables panel above the message box. Preset buttons are disabled until all required variables pass validation.

### Validation rules

The default validation requires every text field to be **non-empty**. You can customise validation per-variable in the YAML `variables:` section:

| `validate` value | Behaviour                                             |
| ---------------- | ----------------------------------------------------- |
| `"nonempty"`     | Must be non-empty (default when no `validate` is set) |
| Any other string | Treated as a regex — field must match `new RegExp(…)` |

For `number` inputs, the HTML `min`/`max` constraints are also enforced. `select` fields require a non-empty selection; `checkbox` fields always pass.

### Auto-detect

Variables with a `detect_pattern` in their definition will be auto-populated when the user sends a message that matches the pattern. This is configured per-variable in the YAML `variables:` section (see below).

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

When the user clicks "Tenant Report" and neither `TENANT` nor `JOB_ID` has a value, the corresponding input fields will be highlighted and the button remains disabled until they are filled in.

---

## Variable Definitions

Variables can be explicitly defined in the YAML file under a top-level `variables:` key. When no `variables:` section is present, variables are **automatically inferred** from `{{PLACEHOLDER}}` patterns found in the prompt templates — each placeholder becomes a simple text input that must be non-empty.

```yaml
variables:
  - name: JOB_ID
    label: "Job ID"
    placeholder: "Enter job identifier"
    detect_pattern: "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    detect_flags: "i"

  - name: PLATFORM
    label: "Platform"
    input_type: select
    options:
      - linux
      - windows
    default: linux

  - name: VERBOSE
    label: "Verbose output"
    input_type: checkbox
    default: "false"
```

Supported fields per variable:

| Field                  | Description                                                              |
| ---------------------- | ------------------------------------------------------------------------ |
| `name`                 | **Required.** SCREAMING_SNAKE_CASE name matching `{{NAME}}` in templates |
| `label`                | Display label (auto-generated from name if omitted)                      |
| `input_type`           | `text` (default), `number`, `select`, or `checkbox`                      |
| `placeholder`          | Placeholder text for `text`/`number` inputs                              |
| `default`              | Default value                                                            |
| `validate`             | `"nonempty"` or a regex pattern string                                   |
| `detect_pattern`       | Regex to auto-populate the field from user messages                      |
| `detect_flags`         | Regex flags for `detect_pattern` (e.g. `"i"`)                            |
| `options`              | List of choices for `select` type                                        |
| `min` / `max` / `step` | Constraints for `number` type                                            |

---

## Configuration Reference

### `add_autolangchat()` Parameters

| Parameter             | Type         | Description                                                |
| --------------------- | ------------ | ---------------------------------------------------------- |
| `preset_prompts`      | `list[dict]` | Prompts provided inline as Python dicts (highest priority) |
| `preset_prompts_file` | `str`        | Path to a YAML file containing prompt definitions          |

### `ChatConfig` Fields

| Field                 | Env Variable                   | Default | Description                                                           |
| --------------------- | ------------------------------ | ------- | --------------------------------------------------------------------- |
| `preset_prompts`      | —                              | `[]`    | In-memory prompt list; set programmatically or via a pre-built config |
| `preset_prompts_file` | `AUTOCHAT_PRESET_PROMPTS_FILE` | `None`  | Path to YAML file; honoured when `preset_prompts` is empty            |
| `preset_variables`    | —                              | `[]`    | Variable definitions; auto-inferred from templates when not provided  |

### Resolution Priority

The plugin resolves prompts in this order (first non-empty source wins):

1. `preset_prompts=` kwarg to `add_autolangchat()` / `AutoLangChatPlugin()`
2. `config.preset_prompts` — populated by a pre-built `ChatConfig` or env var
3. YAML file — `preset_prompts_file=` kwarg or `AUTOCHAT_PRESET_PROMPTS_FILE` env var

---

## Using a YAML File with `importlib.resources`

When the YAML file is shipped as package data (i.e., bundled inside a Python package), resolve the path with `importlib.resources` so it works correctly whether the package is installed as a wheel or in editable mode:

```python
import importlib.resources as pkg_resources

prompts_path = str(
    pkg_resources.files("my_package").joinpath("prompts.yaml")
)

add_autolangchat(app, preset_prompts_file=prompts_path)
```

---

## Environment Variable

You can also point to a YAML file via the environment without changing code:

```bash
AUTOCHAT_PRESET_PROMPTS_FILE=/etc/myapp/prompts.yaml
```

---

## Error Handling

`load_preset_prompts_from_yaml()` never raises — on any failure it returns `[]` and logs a warning, so the chat UI starts normally:

| Condition                         | Behaviour                   |
| --------------------------------- | --------------------------- |
| `pyyaml` not installed            | `WARNING` log; returns `[]` |
| File not found                    | `DEBUG` log; returns `[]`   |
| Invalid YAML / parse error        | `WARNING` log; returns `[]` |
| File present but no `prompts` key | `INFO` log; returns `[]`    |

---

## Security Notes

- Button labels and descriptions are set as `textContent` / `title` attributes — no HTML injection risk.
- Prompt templates are passed through `marked.parse()` then sanitized with **DOMPurify** before being rendered in the chat bubble, preventing XSS from raw HTML in templates.
- Placeholder substitution uses `String.replaceAll()` on the resolved plain-text template before it is rendered as markdown — there is no risk of a placeholder expanding into executable code.

---

## See Also

- [Chat UI](chat-ui.md) — general UI features and endpoints
- [Configuration](configuration.md) — full settings reference
