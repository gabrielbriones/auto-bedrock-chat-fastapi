# Token Management

The plugin includes a two-stage token budget management system that prevents context window overflow when tool responses or conversation history grow too large. Truncation is the default behavior; AI-based summarization is an opt-in alternative.

---

## The Problem

Large tool responses can quickly fill the model's context window:

```
User query
→ Tool call 1: 40KB response
→ Tool call 2: 40KB response
→ Tool call 3: 40KB response
Total: ~150KB+ — exceeds 200K token window → API error
```

The `MessagePreprocessor` handles this automatically before every LLM call.

---

## Two-Stage Pipeline

```
preprocess_messages(history)
│
├── Stage 1: Per-Message Truncation
│   ├── Scans every message in history
│   ├── If message > SINGLE_MSG_LENGTH_THRESHOLD (500K chars)
│   └── Truncates to SINGLE_MSG_TRUNCATION_TARGET (425K chars)
│       ├── Tool messages: proportional truncation per content entry
│       └── Text messages: head (80%) + tail (20%) preservation
│           └── Optional: AI summarization instead of truncation
│
└── Stage 2: History Total Truncation
    ├── If total history > HISTORY_TOTAL_LENGTH_THRESHOLD (650K chars)
    ├── Step 2.1: Truncate large middle-zone messages individually
    ├── Step 2.2: (without AI) Wipe middle-zone messages
    ├── Step 2.3: (with AI) Summarize oldest messages
    └── Step 2.4: Safety-net recursive halving (up to MAX_TRUNCATION_RECURSION)
```

---

## Configuration

> The five threshold/target settings below
> (`AUTOCHAT_SINGLE_MSG_LENGTH_THRESHOLD`, `AUTOCHAT_SINGLE_MSG_TRUNCATION_TARGET`,
> `AUTOCHAT_HISTORY_TOTAL_LENGTH_THRESHOLD`, `AUTOCHAT_HISTORY_MSG_LENGTH_THRESHOLD`,
> `AUTOCHAT_HISTORY_MSG_TRUNCATION_TARGET`) are **no longer configurable**. They are
> computed automatically from `AUTOCHAT_MODEL_ID`'s `max_input_tokens` (via
> `langchain_aws.data._profiles`), scaled proportionally from the package's
> original defaults so a smaller-context model automatically gets a smaller
> char budget instead of risking a Bedrock "Input is too long" overflow. To
> change truncation behavior, change `AUTOCHAT_MODEL_ID` instead — there is no
> other lever.

| Setting                                     | Default (1M-token model) | Description                                       |
| ------------------------------------------- | ------------------------ | ------------------------------------------------- |
| `single_msg_length_threshold` (computed)    | `500000`                 | Chars that trigger Stage 1 per-message truncation |
| `single_msg_truncation_target` (computed)   | `425000`                 | Target chars after Stage 1                        |
| `history_total_length_threshold` (computed) | `650000`                 | Total history chars that trigger Stage 2          |
| `history_msg_length_threshold` (computed)   | `100000`                 | Per-message threshold during Stage 2              |
| `history_msg_truncation_target` (computed)  | `85000`                  | Per-message target during Stage 2                 |
| `AUTOCHAT_MAX_TRUNCATION_RECURSION`         | `3`                      | Max recursion depth for safety-net halving        |
| `AUTOCHAT_ENABLE_AI_SUMMARIZATION`          | `false`                  | Use AI summarization instead of plain truncation  |

The computed values scale linearly with the selected model's context window
(`max_input_tokens / 1,000,000`). For example, a 200K-token model gets
100000/85000/130000/20000/17000 respectively — 20% of the 1M-token defaults.

### Safety-net retry on context-window errors

If a Bedrock call still fails with a context-window/"Input is too long" error
despite the above (e.g. unusually dense JSON), `llm_call_node` retries once
with all thresholds temporarily halved (`threshold_factor=0.5`) against the
same model before falling back to `fallback_model` (if configured). See
`autolangchat/graph/nodes/llm_call.py`.

---

## How Oversize Is Decided

Bedrock Converse has no way to count tokens before a call, so "will this
payload fit?" is decided from an **estimate**. `autolangchat/context_budget.py`
provides that estimate as pure functions (no I/O, no network). It does not
change the pipeline above yet; it is the building block for deciding when a
tool result or user message is too large to inline. The preprocessor
thresholds and the safety-net retry stay in place as the backstop when an
estimate is wrong.

### Per-message evaluation before model calls

The LangGraph preprocessing node builds `ContextBudget.for_turn` before
each primary model invocation, including tool-loop calls. The primary LLM
call reuses its estimates without repeating estimation or archiving.
Recovery retries bypass that node and re-evaluate inside the LLM node.
The fallback invocation rebuilds its model input from the prepared messages,
so any markers created for the fallback model's threshold reach that model.
It estimates each message **after the most recent assistant message**, or
after the system prompt for a new conversation. An assistant tool-call
message is also a boundary: the next call evaluates the new tool results.
If there is no assistant or system message, all messages are evaluated.

Each message's token estimate, including message framing and structured
tool results, is compared independently with
`context_budget_safety_fraction * max_input_tokens` (default fraction:
`0.10`). This comparison does not use the remaining aggregate context
budget. Retries use the model and messages actually being submitted.

The latest attempt's estimates are stored in graph metadata under
`message_context_budget`, with the model ID, threshold, and per-message
indices, token estimates, and `exceeds_threshold` flags. Each message also
lists the estimate of the parts it is made of (`parts`): one per result of a
tool message, the text of a user message. With a live artifact
scope, an oversized user message is replaced by the same
[marker](session-artifacts.md#user-message-markers) a tool result gets (so the
preprocessor does not truncate it), and any other oversized message
that is not a tool result is copied to the
[session artifact store](session-artifacts.md) and tagged with a handle,
without replacing its content. Both happen before preprocessing
truncation to preserve original payloads.

#### Tool results are replaced in the same pass

The estimate above is the only one: an oversized tool message is not estimated
a second time anywhere else. From the per-part estimates, the pass replaces
each successful result that alone exceeds the threshold with a
[marker](session-artifacts.md#tool-result-markers), and, if the message as a
whole still exceeds it, the largest remaining results until it fits. Errors,
HTTP error results and results of internal tools are never replaced. This
happens before preprocessing truncation, so the preprocessor only sees the
marker; the archive step skips marker messages. The `Message context estimate`
debug line lists each part's tokens, characters, content kind and chars/token
ratio, for a user message as well as a tool message, and appears before the
artifact is stored.

There is no application-level WebSocket input-message size cap. Deployment
WebSocket server/proxy limits still apply independently, and the existing
preprocessor and context-window recovery behavior are unchanged.

### Remaining budget per turn

`ContextBudget` computes how many input tokens are still free for the model
that will actually be called:

```
remaining = max_input_tokens            (model profile; 16,384 if unknown)
          - reserved output             (max_tokens clamped to the model's output cap)
          - bound tool schemas          (0 if the model can't call tools)
          - current messages            (content + 8 tokens overhead per message)
          - safety margin               (AUTOCHAT_CONTEXT_BUDGET_SAFETY_FRACTION × max_input_tokens)
```

- `max_input_tokens` comes from the model profile, including region-prefixed
  inference-profile ids and `CAPABILITY_OVERRIDES`. If the cap is unknown,
  16,384 tokens is assumed (below every model in the catalog) and a warning
  is logged once per model id. It never raises.
- If `max_tokens` is unset, the model's full output cap is reserved, since
  Bedrock may use all of it.
- Messages are sized the way they are sent: each tool result counts as its
  own message, and assistant tool calls are counted as JSON.
- `remaining_tokens` is never negative.

```python
from autolangchat.context_budget import ContextBudget

budget = ContextBudget.for_turn(chat_config, messages)   # effective model of this turn
budget.remaining_tokens                                   # tokens still free
budget.fits(tool_result_text)                             # True if it fits as a new message
budget.allowance_for("json")                              # chars of JSON that would fit

# Re-check against another model, e.g. before a fallback retry
ContextBudget.for_model(chat_config.fallback_model, max_tokens=chat_config.max_tokens, messages=messages)
```

`for_turn` uses `chat_config.model_id`, which already includes per-turn
model overrides, and reserves the tools actually bound to the model
(`chat_config.langchain_tools`).

### Content kind and chars/token ratios

Tokens per character depend on the content and on the model's tokenizer, so
the estimate is `ceil(chars / ratio)` with a ratio per **provider** and
**content kind**:

| Kind          | Detected when                                                                          | Typical chars/token |
| ------------- | -------------------------------------------------------------------------------------- | ------------------- |
| `json`        | Starts with `{`, or with `[` followed by a JSON value (log lines like `[INFO]` aren't) | 1.7–2.5             |
| `log_tabular` | Many digits/punctuation, many short lines, or binary-like text                         | 1.0–1.55            |
| `prose`       | Everything else (including empty input)                                                | 3.4–5.05            |

- Only the first 64 KB is inspected, so classifying multi-MB payloads stays
  cheap.
- Ratios are in `CONTEXT_BUDGET_CHARS_PER_TOKEN` in `autolangchat/defaults.py`,
  keyed by the Bedrock provider in the model id (`anthropic`, `amazon`,
  `openai`, `qwen`, ...). Anthropic prose is notably denser (3.40) than most
  other providers (~5.0).
- A provider without calibrated ratios uses the lowest ratio of any provider
  for each kind, so it is over- rather than under-estimated.
- Digit-heavy CSV is the densest content: some tokenizers split every digit
  (about 1 char/token). `log_tabular` ratios are set to cover it, so
  ordinary logs are over-estimated by up to about 50%.

### Calibration

The ratios were measured against real Bedrock `input_tokens` for 40 models
across 15 providers (2026-10-15, `langchain-aws` 1.8.1). Each ratio is the
densest model of that provider, minus 5% headroom, so estimates run high
rather than low. Raw measurements and fixtures are in
`tests/fixtures/context_budget/`, and `tests/test_context_budget.py` checks
that no measured model is underestimated by more than 5%.

### Safety margin

| Env Variable                              | Default | Description                                                            |
| ----------------------------------------- | ------- | ---------------------------------------------------------------------- |
| `AUTOCHAT_CONTEXT_BUDGET_SAFETY_FRACTION` | `0.10`  | Share of `max_input_tokens` held back for estimate error (`0.0`–`0.5`) |

---

## Plain-Text Truncation

Default behavior. Oversized text is split preserving beginning and end:

```
Original: [40KB tool response...]

Truncated:
  HEAD (80% of budget): first ~34KB
  [... truncated: X chars omitted ...]
  TAIL (20% of budget): last ~8.5KB
```

This preserves both the structure at the beginning and recent/summary information at the end.

---

## AI Summarization (Opt-In)

When `AUTOCHAT_ENABLE_AI_SUMMARIZATION=true`, the plugin uses the LLM to compress oversized messages instead of cutting them:

```python
add_autolangchat(
    app,
    enable_ai_summarization=True,
    system_prompt="You are a helpful engineering assistant."
)
```

```bash
# .env
AUTOCHAT_ENABLE_AI_SUMMARIZATION=true
AUTOCHAT_SYSTEM_PROMPT="You are a helpful engineering assistant."
```

**How it works:**

1. The `MessagePreprocessor` detects a message exceeding the threshold
2. It calls the LLM with a summarization prompt asking to preserve key facts, IDs, URLs, and data points
3. If the summary fits within budget, it replaces the original message
4. If the LLM fails or produces an over-budget result, it falls back to plain-text truncation

**Separate model/params for summarization (optional):** by default, the summarizer reuses the main chat `model_id`/`max_tokens` and a fixed temperature. You can override any of these independently so summarization uses a cheaper/faster model or different sampling params than the main chat model:

```bash
# .env
AUTOCHAT_SUMMARIZATION_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
AUTOCHAT_SUMMARIZATION_TEMPERATURE=0.2
AUTOCHAT_SUMMARIZATION_MAX_TOKENS=512
AUTOCHAT_SUMMARIZATION_TOP_P=0.4
```

- `AUTOCHAT_SUMMARIZATION_MODEL_ID` — falls back to `AUTOCHAT_MODEL_ID` when unset.
- `AUTOCHAT_SUMMARIZATION_MAX_TOKENS` — falls back to `AUTOCHAT_MAX_TOKENS` when unset.
- `AUTOCHAT_SUMMARIZATION_TEMPERATURE` — when unset (and `AUTOCHAT_SUMMARIZATION_TOP_P` is also unset), falls back to `DEFAULT_SUMMARIZATION_TEMPERATURE` (`0.7`).
- `AUTOCHAT_SUMMARIZATION_TOP_P` — only takes effect when no summarization temperature is in effect (an explicit `AUTOCHAT_SUMMARIZATION_TEMPERATURE` always wins), since Bedrock Converse rejects requests specifying both simultaneously.

**AI Summarization defaults** (from `defaults.py`):

| Constant                            | Value | Description                           |
| ----------------------------------- | ----- | ------------------------------------- |
| `DEFAULT_SUMMARIZATION_TEMPERATURE` | `0.7` | LLM temperature for summarization     |
| `DEFAULT_SUMMARIZATION_MIN_CHUNKS`  | `3`   | Minimum chunks when splitting content |

---

## Count-Based Message Trimming

In addition to character-based truncation, the plugin trims old messages by count:

```python
add_autolangchat(app, max_conversation_messages=20)  # default: 20
```

This removes the oldest messages (preserving the system message) when the history exceeds the count limit. It runs before the character-based truncation pipeline.

---

## Context Window Recovery Re-Preprocessing

Even after the two-stage preprocessing pipeline runs, multi-round tool calls can accumulate enough content to exceed the model's token limit. JSON-heavy tool results tokenize at roughly 3.0 characters per token — worse than the ~3.3 chars/token the default character thresholds assume. This means a conversation that fits under the character thresholds can still exceed the 200K token limit.

When this happens, `ChatManager` applies a two-step recovery:

1. **Aggressive message reduction** — drops all but the system message and last 4 messages
2. **Re-preprocessing with tightened thresholds** — re-runs the full two-stage pipeline with a `threshold_factor` of `0.8`, multiplying every threshold and target by that factor

```
Normal thresholds                  After threshold_factor=0.8
─────────────────                  ──────────────────────────
Single msg threshold:  500K chars  →  400K chars
Single msg target:     425K chars  →  340K chars
History total:         650K chars  →  520K chars
History msg threshold: 100K chars  →   80K chars
History msg target:     85K chars  →   68K chars
```

This catches oversized tool results that slipped under the normal Stage 1 threshold. For example, a 450K tool response passes the default 500K threshold, but after recovery it exceeds the tightened 400K threshold and gets truncated to 340K.

```
ContextWindowExceededError (Layer 1)
│
├── 1. Aggressive message reduction
│      Keep: system msg + last 4 messages
│      Drop: everything else
│
├── 2. Re-preprocess with threshold_factor=0.8
│      Stage 1: catches tool results between 400K–500K
│      Stage 2: catches total history between 520K–650K
│
└── 3. Retry LLM call (Layer 2)
```

The `threshold_factor` is internal and not user-configurable — it is hardcoded in `ChatManager._call_llm_with_recovery()`. Users can influence the effective thresholds indirectly by adjusting the base thresholds documented above.

---

## Monitoring Truncation

Set `log_level="DEBUG"` to see truncation decisions in logs:

```
DEBUG - Stage 1: message at index 3 truncated: 520000 → 425000 chars
DEBUG - Stage 2: total history 700000 chars exceeds threshold 650000
DEBUG - Stage 2.1: message at index 1 truncated: 120000 → 85000 chars
```

---

## See Also

- [Configuration](configuration.md) — full settings reference
- [Architecture](architecture.md) — where `MessagePreprocessor` fits
- [Token Usage Tracking](token-usage-tracking.md) — recording per-turn `input_tokens`/`output_tokens` for observability/billing (a separate concern from the budget/truncation system on this page)
- `docs/message-truncation-diagrams.html` — visual diagrams of the truncation pipeline
- `autolangchat/message_preprocessor.py` — implementation
- `autolangchat/context_budget.py` — input-token estimator (`ContextBudget`)
- `autolangchat/defaults.py` — all default values
