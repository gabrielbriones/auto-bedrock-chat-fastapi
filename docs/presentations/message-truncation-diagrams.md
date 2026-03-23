# Message Truncation & AI Summarization — Feature Overview

> 📊 This document summarizes the interactive visual diagrams in [`message-truncation-diagrams.html`](../message-truncation-diagrams.html). Open that file in a browser for the full visual comparison including performance metrics.

---

## The Problem

Modern LLMs have fixed context window limits. When tool responses are large (e.g., a 600K-character API response), they can exceed the model's capacity and cause API errors:

```
ContextWindowExceededError: Total tokens exceed limit of 200,000
```

Common LLM context limits:

| Model             | Context Window            |
| ----------------- | ------------------------- |
| Claude 3.5 Sonnet | 200K tokens (~800K chars) |
| Claude 3 Haiku    | 200K tokens               |
| GPT-4o            | 128K tokens (~512K chars) |
| Llama 3 8B        | 8K tokens                 |

---

## Two Approaches

### 1. Plain-Text Truncation (Default)

Oversized messages are split, preserving the beginning and end:

```
Original message (600K chars)
         │
         ▼
HEAD: first 80% of budget (~340K chars)
[... truncated: 175K chars omitted ...]
TAIL: last 20% of budget (~85K chars)
```

**Characteristics:**

- Fast (no LLM call)
- Deterministic
- Information loss guaranteed (omitted section)
- Best when middle content is less important

### 2. AI Summarization (Opt-In)

The LLM compresses the oversized message, preserving key facts:

```
Original message (600K chars)
         │
         ▼
Summarization LLM call
(preserves: IDs, URLs, key data points, structure)
         │
         ▼
Summary (~85K chars, within budget)
```

**Characteristics:**

- Higher quality: key facts preserved
- Uses an extra LLM call (cost + latency)
- Falls back to plain-text if summary exceeds budget
- Best for tool responses with structured data (lists, IDs, values)

---

## Processing Flow

```
preprocess_messages(history)
│
├── Stage 1: Per-Message Truncation
│   For each message > 500K chars:
│   ├── Tool messages: proportional per-entry truncation
│   └── Text messages:
│       ├── [AI ON]  → summarize with LLM → fallback to plain if needed
│       └── [AI OFF] → head (80%) + tail (20%)
│
└── Stage 2: History Total Truncation
    If total history > 650K chars:
    ├── 2.1: Truncate large messages in the middle zone
    ├── 2.2: [AI OFF] Wipe middle-zone messages entirely
    ├── 2.3: [AI ON]  Summarize oldest messages
    └── 2.4: Safety-net recursive halving (up to 3 iterations)
```

---

## Performance Metrics

For a 600K character tool response (illustrative):

| Strategy         | Output Size | Information Preserved | Extra LLM Calls   |
| ---------------- | ----------- | --------------------- | ----------------- |
| Plain truncation | ~425K chars | ~71% (head+tail)      | 0                 |
| AI summarization | ~85K chars  | ~95% (key facts)      | 1                 |
| No truncation    | 600K chars  | 100%                  | 0 (but may error) |

---

## Configuration

```bash
# .env
BEDROCK_ENABLE_AI_SUMMARIZATION=true   # opt-in
BEDROCK_SINGLE_MSG_LENGTH_THRESHOLD=500000
BEDROCK_SINGLE_MSG_TRUNCATION_TARGET=425000
BEDROCK_HISTORY_TOTAL_LENGTH_THRESHOLD=650000
BEDROCK_HISTORY_MSG_LENGTH_THRESHOLD=100000
BEDROCK_HISTORY_MSG_TRUNCATION_TARGET=85000
BEDROCK_MAX_TRUNCATION_RECURSION=3
```

---

## Key Takeaways

- **Default (plain truncation):** Zero overhead, always safe, some information loss
- **AI summarization:** Better quality, opt-in, minor extra cost per truncation event
- Both strategies prevent context window errors transparently
- All thresholds are configurable to match your model's actual context window
- The system always falls back safely — a truncation error never causes a crash

---

## See Also

- [Token Management wiki page](wiki/token-management.md) — full configuration reference
- [Configuration](wiki/configuration.md) — all `BEDROCK_*` threshold settings
- `auto_bedrock_chat_fastapi/message_preprocessor.py` — implementation
- `auto_bedrock_chat_fastapi/defaults.py` — default values
