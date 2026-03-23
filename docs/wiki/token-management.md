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

All thresholds are configurable via `.env` or code:

| Env Variable                             | Default  | Description                                       |
| ---------------------------------------- | -------- | ------------------------------------------------- |
| `BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD`    | `500000` | Chars that trigger Stage 1 per-message truncation |
| `BEDROCK_SINGLE_MSG_TRUNCATION_TARGET`   | `425000` | Target chars after Stage 1                        |
| `BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD` | `650000` | Total history chars that trigger Stage 2          |
| `BEDROCK_HISTORY_MSG_LENGTH_THRESHOLD`   | `100000` | Per-message threshold during Stage 2              |
| `BEDROCK_HISTORY_MSG_TRUNCATION_TARGET`  | `85000`  | Per-message target during Stage 2                 |
| `BEDROCK_MAX_TRUNCATION_RECURSION`       | `3`      | Max recursion depth for safety-net halving        |
| `BEDROCK_ENABLE_AI_SUMMARIZATION`        | `false`  | Use AI summarization instead of plain truncation  |

### Example: Tighter Limits for Smaller Models

```python
bedrock_chat = add_bedrock_chat(
    app,
    single_msg_length_threshold=200_000,
    single_msg_truncation_target=170_000,
    history_total_length_threshold=300_000,
    history_msg_length_threshold=50_000,
    history_msg_truncation_target=42_500
)
```

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

When `BEDROCK_ENABLE_AI_SUMMARIZATION=true`, the plugin uses the LLM to compress oversized messages instead of cutting them:

```python
add_bedrock_chat(
    app,
    enable_ai_summarization=True,
    system_prompt="You are a helpful engineering assistant."
)
```

```bash
# .env
BEDROCK_ENABLE_AI_SUMMARIZATION=true
BEDROCK_SYSTEM_PROMPT="You are a helpful engineering assistant."
```

**How it works:**

1. The `MessagePreprocessor` detects a message exceeding the threshold
2. It calls the LLM with a summarization prompt asking to preserve key facts, IDs, URLs, and data points
3. If the summary fits within budget, it replaces the original message
4. If the LLM fails or produces an over-budget result, it falls back to plain-text truncation

**AI Summarization defaults** (from `defaults.py`):

| Constant                               | Value  | Description                                |
| -------------------------------------- | ------ | ------------------------------------------ |
| `DEFAULT_SUMMARIZATION_TEMPERATURE`    | `0.7`  | LLM temperature for summarization          |
| `DEFAULT_SUMMARIZATION_MIN_MAX_TOKENS` | `1024` | Minimum max_tokens for summarization calls |
| `DEFAULT_SUMMARIZATION_MIN_CHUNKS`     | `3`    | Minimum chunks when splitting content      |

---

## Count-Based Message Trimming

In addition to character-based truncation, the plugin trims old messages by count:

```python
add_bedrock_chat(app, max_conversation_messages=20)  # default: 20
```

This removes the oldest messages (preserving the system message) when the history exceeds the count limit. It runs before the character-based truncation pipeline.

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
- `docs/message-truncation-diagrams.html` — visual diagrams of the truncation pipeline
- `auto_bedrock_chat_fastapi/message_preprocessor.py` — implementation
- `auto_bedrock_chat_fastapi/defaults.py` — all default values
