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
- `autolangchat/defaults.py` — all default values
