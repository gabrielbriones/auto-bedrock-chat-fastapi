# AI-Based Summarization

## Overview

The `MessagePreprocessor` supports **AI-based summarization** as an alternative to plain-text truncation. When enabled, oversized messages are condensed by an LLM that preserves key facts, data points, IDs, URLs, and structure — instead of losing information to head/tail truncation.

AI summarization is **opt-in** (`BEDROCK_ENABLE_AI_SUMMARIZATION=true`) and **always falls back** to plain-text truncation on error or when the LLM produces an over-budget result.

---

## Configuration

| Environment Variable                     | Config Field                     | Default   | Description                                                          |
| ---------------------------------------- | -------------------------------- | --------- | -------------------------------------------------------------------- |
| `BEDROCK_ENABLE_AI_SUMMARIZATION`        | `enable_ai_summarization`        | `False`   | Master switch — enables AI summarization across both pipeline stages |
| `BEDROCK_SYSTEM_PROMPT`                  | `system_prompt`                  | `None`    | System prompt injected as context into summarization LLM calls       |
| `BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD`    | `single_msg_length_threshold`    | `500,000` | Per-message size (chars) that triggers Stage 1 truncation            |
| `BEDROCK_SINGLE_MSG_TRUNCATION_TARGET`   | `single_msg_truncation_target`   | `425,000` | Target size (chars) after Stage 1 truncation                         |
| `BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD` | `history_total_length_threshold` | `650,000` | Total conversation size (chars) that triggers Stage 2                |
| `BEDROCK_HISTORY_MSG_LENGTH_THRESHOLD`   | `history_msg_length_threshold`   | `100,000` | Per-message threshold during Stage 2 history truncation              |
| `BEDROCK_HISTORY_MSG_TRUNCATION_TARGET`  | `history_msg_truncation_target`  | `85,000`  | Per-message target during Stage 2 history truncation                 |
| `BEDROCK_MAX_TRUNCATION_RECURSION`       | `max_truncation_recursion`       | `3`       | Max recursion depth for Stage 2.4 safety-net halving                 |

### Internal Constants (defaults.py)

| Constant                               | Value   | Description                                      |
| -------------------------------------- | ------- | ------------------------------------------------ |
| `DEFAULT_SUMMARIZATION_TEMPERATURE`    | `0.7`   | LLM temperature for summarization calls          |
| `DEFAULT_SUMMARIZATION_MIN_MAX_TOKENS` | `1,024` | Minimum `max_tokens` for summarization LLM calls |
| `DEFAULT_SUMMARIZATION_MIN_CHUNKS`     | `3`     | Minimum chunk count when splitting content       |
| `TRUNCATION_HEAD_RATIO`                | `0.8`   | Plain-text fallback: fraction of budget for head |
| `TRUNCATION_TAIL_RATIO`                | `0.2`   | Plain-text fallback: fraction of budget for tail |
| `MIN_PROPORTIONAL_BUDGET`              | `100`   | Minimum chars per content-list item              |

### Quick Start

```bash
# Enable AI summarization
BEDROCK_ENABLE_AI_SUMMARIZATION=true

# Optional: provide system prompt for summarization context
BEDROCK_SYSTEM_PROMPT="You are a helpful engineering assistant."
```

```python
from auto_bedrock_chat_fastapi import ChatConfig

config = ChatConfig(
    BEDROCK_ENABLE_AI_SUMMARIZATION=True,
    BEDROCK_SYSTEM_PROMPT="You are a helpful engineering assistant.",
    BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=500_000,
    BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=425_000,
)
```

---

## Pipeline Architecture

The `MessagePreprocessor.preprocess_messages()` method runs a two-stage pipeline:

```
preprocess_messages(messages, on_progress=callback)
│
├── Stage 1: Single-message truncation
│   └── _truncate_oversized_messages()
│       └── For each message > single_msg_length_threshold:
│           └── _truncate_single_message(msg, target)
│               ├── tool message  → _truncate_result_entries() [proportional per-entry]
│               ├── list content  → _truncate_list_content_items() [proportional per-item]
│               ├── dict content  → _truncate_text(inner, target)
│               └── str content   → _truncate_text(text, target)
│                                       ├── AI: _try_ai_summarize() → _ai_summarize_message()
│                                       │       └── Rolling map-reduce via _summarize_with_llm()
│                                       └── Fallback: _truncate_plain_text() [head + tail]
│
└── Stage 2: History-total truncation
    └── _truncate_history_total()
        ├── 2.1: Truncate middle-zone messages individually
        ├── 2.2: (AI OFF only) Wipe middle-zone messages
        ├── 2.3: Truncate ALL user/tool messages exceeding threshold
        └── 2.4: Recurse with halved thresholds (up to max_truncation_recursion)
```

### Stage 1 — Single-Message Truncation

**Trigger**: Any individual message with `get_content_size(msg) > single_msg_length_threshold`.

**Method**: `_truncate_oversized_messages()` iterates every message. Oversized messages are dispatched through `_truncate_single_message()` which handles format-specific routing:

| Message Format                                    | Handler                          | Strategy                                        |
| ------------------------------------------------- | -------------------------------- | ----------------------------------------------- |
| ChatManager tool (`role="tool"` + `tool_results`) | `_truncate_result_entries()`     | Proportional budget per tool result entry       |
| Claude list content (`content: [...]`)            | `_truncate_list_content_items()` | Proportional budget per content block           |
| Dict content (`content: {...}`)                   | `_truncate_text()`               | AI summarize or plain-text truncate inner value |
| String content (`content: "..."`)                 | `_truncate_text()`               | AI summarize or plain-text truncate             |

### Stage 2 — History-Total Truncation

**Trigger**: `sum(get_content_size(m) for m in messages) > history_total_length_threshold`.

**Zone layout**:

```
[system prompt]  ← Protected (index 0 if role="system")
[old assistant]  ← Middle zone
[old tool]       ← Middle zone
[old user]       ← Middle zone
[old assistant]  ← Middle zone
[last user msg]  ← Protected (trailing)
[assistant]      ← Protected (trailing)
[tool results]   ← Protected (trailing)
```

**Steps** (applied in order until under budget):

| Step | Scope                     | Action                                                             |
| ---- | ------------------------- | ------------------------------------------------------------------ |
| 2.1  | Middle zone               | Truncate/summarize each message to `history_msg_truncation_target` |
| 2.2  | Middle zone (AI OFF only) | Remove all middle-zone messages                                    |
| 2.3  | All user/tool messages    | Truncate messages exceeding `history_msg_length_threshold`         |
| 2.4  | Full history              | Recurse with halved thresholds (safety net)                        |

---

## AI Summarization Algorithm

### Rolling Map-Reduce (`_ai_summarize_message`)

When a message needs AI summarization, it is processed through a rolling map-reduce:

1. **Split** — `split_into_chunks(content, chunk_size)` divides the content into chunks. Chunk size is `single_msg_length_threshold // 2`. A minimum of 3 chunks is enforced when content exceeds chunk size.

2. **Chunk 1** — Summarize the first chunk via `_summarize_with_llm()`.

3. **Chunks 2..N** — For each subsequent chunk, concatenate the previous summary with the next chunk and re-summarize. The final iteration uses `target_size` as the budget; intermediate iterations use `chunk_size`.

4. **Result** — The final summary is wrapped with an `[AI SUMMARY]` marker including original/reduced sizes.

```
Content: 1.95M chars
  ↓ split into 8 chunks (~244K each)
  ↓ summarize chunk 1 → 200K summary
  ↓ summarize(200K summary + chunk 2) → 200K summary
  ↓ ... (rolling)
  ↓ summarize(200K summary + chunk 8) → target_size summary
Result: 425K chars with [AI SUMMARY] marker
```

### Chunk Splitting (`split_into_chunks`)

Content is split at natural boundaries in priority order:

1. `\n\n` — paragraph break
2. `\n` — line break
3. `.` followed by space or `\n` — sentence end
4. Space character — word boundary
5. Hard cut — last resort

All chunks concatenated reproduce the original content exactly (no gaps or overlaps).

### LLM Call (`_summarize_with_llm`)

Each summarization call uses a dedicated system prompt:

```
You are a summarization assistant. Condense the content below
while preserving ALL key facts, data points, names, numbers,
error messages, IDs, URLs, and actionable details.

[Optional: system prompt context from config]

RULES:
- Summary MUST be under {target_size} characters
- Preserve specific data: names, numbers, URLs, error messages, IDs, dates
- Preserve structure (lists, key-value pairs, tables) where possible
- If content is a tool/API response, keep the result data
- If content is a user message, keep the core request and details
- Omit: redundant context, verbose formatting, boilerplate
- Do NOT add commentary — output ONLY the summarized content
```

**Parameters**:

- `temperature`: `0.7` (from `DEFAULT_SUMMARIZATION_TEMPERATURE`)
- `max_tokens`: `max(target_size // 4, 1024)`

### Fallback Behavior

AI summarization falls back to plain-text truncation when:

- `enable_ai_summarization` is `False`
- `llm_client` is `None`
- The LLM raises an exception
- The AI summary exceeds `target_size` (logged as warning)

Plain-text fallback produces a structured head + tail preview:

```
[MESSAGE CONTENT TRUNCATED - Original size: 1,950,000 chars, 45,230 lines]

BEGINNING:
{first 80% of content budget}

... (1,520,000 chars omitted) ...

ENDING:
{last 20% of content budget}

RECOMMENDATION: Use filtering or pagination to get specific data.
```

---

## Progress Notifications

When `on_progress` is provided to `preprocess_messages()`, the preprocessor emits granular status updates during AI summarization:

| Phase                 | Message                         | Condition                                |
| --------------------- | ------------------------------- | ---------------------------------------- |
| Stage 1 start         | `"Summarizing conversation..."` | Any oversized message + AI enabled       |
| Stage 1 per-message   | `"Summarizing message 2/5..."`  | Multiple oversized messages + AI enabled |
| Tool result per-entry | `"Summarizing result 1/3..."`   | Tool result entries being AI-summarized  |

Notifications are `{"type": "typing", "message": "..."}` dicts passed to the async `on_progress` callback. When `on_progress` is `None`, no notifications are emitted and no overhead is incurred.

### Example: WebSocket Integration

```python
async def on_progress(msg: dict):
    await websocket.send_json(msg)

result = await chat_manager.chat_completion(
    messages=messages,
    on_progress=on_progress,
)
```

---

## Tool Result Handling

Tool messages (`role="tool"` with `tool_results` list) receive **proportional per-entry truncation**:

1. Each entry's payload size is measured
2. Budget is distributed proportionally: `share = target × (entry_size / total_size)`
3. Entries within their share are left untouched
4. Oversized entries are AI-summarized (or plain-text truncated) to their share

This ensures small results are preserved intact while large results absorb the reduction.

```python
# Example: 3 tool results, target = 100K
# entry_0: 500K chars → share = 83K → AI summarize to 83K
# entry_1: 50K chars  → share = 8K  → left untouched (under 50K? no, truncated)
# entry_2: 50K chars  → share = 8K  → truncated
```

---

## Testing

```bash
# All AI summarization tests
poetry run pytest tests/test_ai_single_message_summarization.py \
                  tests/test_ai_summarization_comprehensive.py \
                  tests/test_ai_history_summarization.py -v --no-cov

# Single-message truncation (plain + AI paths)
poetry run pytest tests/test_single_message_truncation.py -v --no-cov

# History-total truncation
poetry run pytest tests/test_history_total_truncation.py -v --no-cov

# Full suite
poetry run pytest tests/ -q --no-cov
```

### Test Coverage

| Test File                                 | Covers                                                                                                                                        |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_ai_single_message_summarization.py` | `split_into_chunks`, `_summarize_with_llm`, `_ai_summarize_message`, `_truncate_text`, `_truncate_oversized_messages`, `on_progress` callback |
| `test_ai_summarization_comprehensive.py`  | Multi-tool proportional truncation, Claude format, system prompt end-to-end, fallback logging                                                 |
| `test_ai_history_summarization.py`        | Stage 2 AI path, zone detection, recursive halving with AI                                                                                    |
| `test_single_message_truncation.py`       | Plain-text truncation, format dispatch, `ChatManager` wiring                                                                                  |
| `test_history_total_truncation.py`        | Stage 2 plain-text path, zone detection, wipe step, `ChatManager` wiring                                                                      |

---

## Production Behavior

### Log Messages

When AI summarization runs, expect log output like:

```
DEBUG  AI summarization: splitting 1,950,000 chars into 8 chunks (chunk_size=250,000)
DEBUG  AI summarization chunk 1/8: 250,000 → 13,000 chars
DEBUG  AI summarization chunk 2/8: 263,000 → 14,500 chars
...
DEBUG  AI summarization complete: 1,950,000 → 18,800 chars in 8 LLM call(s)
INFO   Truncated oversized tool message: 1,950,000 → 18,800 chars
INFO   Oversized message truncation finished: 3 message(s) truncated
```

### Failure Modes

| Scenario                 | Behavior                            | Log Level |
| ------------------------ | ----------------------------------- | --------- |
| LLM timeout/error        | Falls back to plain-text truncation | `WARNING` |
| Summary exceeds target   | Falls back to plain-text truncation | `WARNING` |
| LLM returns empty        | Falls back to plain-text truncation | `WARNING` |
| Max recursion in Stage 2 | Returns best-effort result          | `ERROR`   |

### Cost Considerations

AI summarization increases LLM token usage. For a single 2M-char tool result:

- **Chunks**: ~8 LLM calls (at `chunk_size = 250K`)
- **Input tokens per call**: ~60K–65K tokens
- **Output tokens per call**: ~3K–5K tokens
- **Total**: ~500K input + ~30K output tokens

Multiply by the number of oversized messages per conversation turn. Monitor usage when enabling in production.
