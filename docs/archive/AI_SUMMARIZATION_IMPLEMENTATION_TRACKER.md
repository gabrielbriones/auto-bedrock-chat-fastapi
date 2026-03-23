# AI-Based Summarization — Feature Implementation Tracker

**Project**: Auto Bedrock Chat FastAPI — AI-Based Message Summarization
**Created**: February 27, 2026
**Last Updated**: February 27, 2026
**Status**: Design Phase
**Parent Tracker**: [CHAT_MANAGER_IMPLEMENTATION_TRACKER.md](CHAT_MANAGER_IMPLEMENTATION_TRACKER.md)
**Architecture Reference**: [CHAT_MANAGER_ARCHITECTURE.md](CHAT_MANAGER_ARCHITECTURE.md)

---

## 📋 Feature Overview

Currently, oversized messages are handled with plain text truncation (head + tail with omission marker). This works but loses information. The AI-based summarization feature uses an LLM to intelligently condense oversized messages while preserving key facts, data, and context.

**Two summarization modes:**

| Mode                             | Trigger                                                         | Method                                                       |
| -------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------ |
| **Single-message summarization** | One message exceeds `message_truncation_threshold` (500k chars) | Rolling map-reduce: split into chunks, summarize iteratively |
| **History summarization**        | Total history exceeds `history_total_threshold` (650k chars)    | Summarize middle-zone conversation into a single message     |

Both modes are **opt-in** via `enable_ai_summarization = True` and fall back to plain text truncation when disabled or on error.

---

## ⚙️ Configuration

| Setting                        | Env Var                            | Default  | Description                                         |
| ------------------------------ | ---------------------------------- | -------- | --------------------------------------------------- |
| `enable_ai_summarization`      | `BEDROCK_ENABLE_AI_SUMMARIZATION`  | `False`  | Master switch for AI summarization                  |
| `summarization_chunk_size`     | `BEDROCK_SUMMARIZATION_CHUNK_SIZE` | `200000` | Max chars per chunk in rolling summarization        |
| `message_truncation_threshold` | `BEDROCK_MSG_TRUNCATION_THRESHOLD` | `500000` | Per-message size that triggers truncation           |
| `message_truncation_target`    | `BEDROCK_MSG_TRUNCATION_TARGET`    | `425000` | Target size after single-message truncation         |
| `history_total_threshold`      | `BEDROCK_HISTORY_TOTAL_THRESHOLD`  | `500000` | Total history size that triggers history truncation |
| `history_msg_threshold`        | `BEDROCK_HISTORY_MSG_THRESHOLD`    | `100000` | Per-message threshold during history truncation     |
| `history_msg_target`           | `BEDROCK_HISTORY_MSG_TARGET`       | `85000`  | Per-message target during history truncation        |
| `max_truncation_recursion`     | `BEDROCK_MAX_TRUNCATION_RECURSION` | `3`      | Max recursion depth for safety-net halving          |

### Model-Specific Guidance

| Model                        | Context Window            | Max `summarization_chunk_size` | Recommended       |
| ---------------------------- | ------------------------- | ------------------------------ | ----------------- |
| Claude 3.5 Sonnet / Claude 4 | 200k tokens (~800k chars) | 200,000                        | 200,000 (default) |
| Llama 3.x (128k context)     | 128k tokens (~512k chars) | 120,000                        | 100,000           |
| GPT-4o (128k context)        | 128k tokens (~512k chars) | 120,000                        | 100,000           |

**Formula**: `max_chunk_size ≈ (model_context_tokens × 4 chars/token - overhead) / 3`
The `/3` accounts for: previous summary input + current chunk input + output budget.

---

## 🔬 Algorithm Details

### A. Single-Message AI Summarization (Rolling Map-Reduce)

**When**: A single message's content exceeds `message_truncation_threshold` (500k chars) AND `enable_ai_summarization = True`.

**Goal**: Condense the message to ≤ `message_truncation_target` (425k chars) while preserving key information.

#### Algorithm

```
Input:  message with content of size S (S > message_truncation_threshold)
Output: message with summarized content of size ≤ message_truncation_target

1. EXTRACT content string from message (handle all formats: str, list, dict)

2. CALCULATE chunk_size:
   - base = summarization_chunk_size (200k)
   - num_chunks = ceil(S / base)
   - if num_chunks < 3:
       chunk_size = ceil(S / 3)   # Ensure minimum 3 chunks
   - else:
       chunk_size = base

3. SPLIT content into chunks [C₁, C₂, ..., Cₙ] of size ≤ chunk_size
   - Split on natural boundaries (paragraphs, then lines, then words)
   - No overlap needed (summarization preserves context via rolling accumulation)

4. ROLLING SUMMARIZATION:
   - summary = await summarize_with_llm(C₁, target=chunk_size, context="first chunk")
   - for i in 2..n:
       combined = summary + "\n\n---NEXT SECTION---\n\n" + Cᵢ
       summary = await summarize_with_llm(combined, target=chunk_size, context="chunk i of n")

5. VALIDATE:
   - if len(summary) ≤ message_truncation_target: ✅ use summary
   - if len(summary) > message_truncation_target:
       ⚠️ apply plain text truncation to message_truncation_target as fallback

6. RECONSTRUCT message with summarized content, preserving role + metadata
   - Prepend: "[AI SUMMARY - Original: {S:,} chars, reduced to: {len(summary):,} chars]"
```

#### Chunk Splitting Strategy

```python
def split_into_chunks(content: str, chunk_size: int) -> List[str]:
    """
    Split content into chunks, preferring natural boundaries.

    Priority order for split points:
    1. Double newline (paragraph break) — \n\n
    2. Single newline — \n
    3. Sentence end — ". " or ".\n"
    4. Word boundary — " "
    5. Hard cut (last resort)
    """
```

This is the same strategy as `context_aware_chunk` in `message_chunker.py`.

#### Summarization LLM Call

```python
async def _summarize_with_llm(
    self,
    content: str,
    target_size: int,
    system_prompt: str,
    iteration_context: str,  # e.g., "chunk 2 of 5"
    llm_client: Any,
    bedrock_params: Optional[Dict] = None,
) -> str:
    """
    Call LLM to summarize content.

    Returns: Summary string
    Raises: Falls back to plain text on any error
    """
```

**Prompt engineering**:

```
SYSTEM: You are a summarization assistant. Condense the content below while
preserving ALL key facts, data points, names, numbers, error messages, IDs,
URLs, and actionable details.

The main conversation uses this system context:
---
{system_prompt}
---

RULES:
- Your summary MUST be under {target_size} characters
- Preserve specific data: names, numbers, URLs, error messages, IDs, dates
- Preserve structure (lists, key-value pairs, tables) where possible
- If content is a tool/API response, keep the result data that answers the query
- If content is a user message, keep the core request and any provided details
- Omit: redundant context, verbose formatting, boilerplate, repeated headers
- Do NOT add commentary — output ONLY the summarized content
- This is {iteration_context}

USER: {content}
```

**LLM call parameters** for summarization:

- `temperature`: 0.1 (low — we want faithful reproduction, not creativity)
- `max_tokens`: `target_size / 4` (approximate char→token conversion)
- `model_id`: Same as main conversation model (uses existing client)

#### Token Budget Analysis

For `summarization_chunk_size = 200,000`:

| Scenario              | Chunk size | Input (prev summary + chunk) | Output (summary) | Total | Fits Claude 200k? | Fits 128k models? |
| --------------------- | ---------- | ---------------------------- | ---------------- | ----- | ----------------- | ----------------- |
| Normal iteration      | 200k chars | ~100k tokens                 | ~50k tokens      | ~150k | ✅ Yes            | ⚠️ Tight          |
| First iteration       | 200k chars | ~50k tokens                  | ~50k tokens      | ~100k | ✅ Yes            | ✅ Yes            |
| Reduced chunks (100k) | 100k chars | ~50k tokens                  | ~25k tokens      | ~75k  | ✅ Yes            | ✅ Yes            |

#### Cost Analysis

| Message Size | Chunks (200k) | LLM Calls | Est. Input Tokens | Est. Output Tokens | Est. Cost (Claude Sonnet) |
| ------------ | ------------- | --------- | ----------------- | ------------------ | ------------------------- |
| 500k chars   | 3             | 3         | ~250k             | ~150k              | ~$1.20                    |
| 800k chars   | 4             | 4         | ~400k             | ~200k              | ~$1.80                    |
| 1M chars     | 5             | 5         | ~500k             | ~250k              | ~$2.25                    |
| 2M chars     | 10            | 10        | ~1M               | ~500k              | ~$4.50                    |

These are per-message costs. History summarization is typically a single call.

---

### B. History AI Summarization (Conversation Compression)

**When**: Total history size exceeds `history_total_threshold` (650k chars) after all single-message truncations AND `enable_ai_summarization = True`.

**Goal**: Compress the middle zone of conversation history into a single summary message.

#### Message Zone Identification

```
messages = [
  [0] system_prompt              ← PROTECTED (never touched)
  [1] user: "Hello"             ┐
  [2] assistant: "Hi! How..."   │
  [3] user: "Search for X"      │ MIDDLE ZONE
  [4] assistant: (tool_call)    │ (candidates for summarization)
  [5] tool: (tool_result)       │
  [6] assistant: "Found X..."   │
  [7] user: "Now do Y"          ┘
  [8] user: "Process these"      ← LAST REAL USER MESSAGE (protected)
  [9] assistant: (tool_call)     ← TRAILING TOOL LOOP (protected)
  [10] tool: (tool_result)       ← TRAILING TOOL LOOP (protected)
]

Middle zone = messages[1..7]
Protected tail = messages[8..10]
```

**"Last real user message"** detection:

```python
def _find_last_real_user_index(messages):
    """Find the last user message that is NOT a tool result."""
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "user" and not is_tool_message(msg):
            return i
    return -1  # No user message found
```

**Trailing tool loop** = all messages after the last real user message that are part of the active tool call cycle (assistant with tool_calls + corresponding tool results).

#### Algorithm: History Step 1 (AI-enabled)

```
Input:  messages where total_size > history_total_threshold
Output: messages with middle zone replaced by a single summary

1. IDENTIFY zones:
   - system_idx = 0 if messages[0].role == "system" else None
   - last_user_idx = find_last_real_user_index(messages)
   - middle_zone = messages[system_idx+1 : last_user_idx] (exclusive of both)
   - protected_tail = messages[last_user_idx:]

2. If middle_zone is empty: skip to Step 3

3. FORMAT middle zone as conversation transcript:
   """
   [Turn 1] User: Hello, I need help with...
   [Turn 2] Assistant: Sure! I can help...
   [Turn 3] User: Search for products
   [Turn 4] Assistant: [Called tool: search_products(query="widgets")]
   [Turn 5] Tool Result: {"results": [...]}
   [Turn 6] Assistant: I found 15 products...
   """

4. CALCULATE middle zone size:
   - If total ≤ summarization_chunk_size: single LLM call
   - If total > summarization_chunk_size: rolling map-reduce (same as single-message)

5. CALL LLM with history summarization prompt (see below)

6. REPLACE middle zone:
   - Remove all middle-zone messages
   - Insert single message: {"role": "assistant", "content": "[CONVERSATION SUMMARY]\n{summary}"}

7. RECALCULATE total size → if still over threshold, proceed to Step 3
```

**History summarization prompt**:

```
SYSTEM: You are a summarization assistant. Summarize this conversation history
so a fellow AI assistant can continue the conversation seamlessly.

The conversation's system context:
---
{system_prompt}
---

RULES:
- Your summary MUST be under {target_size} characters
- Preserve: tool call results and key data retrieved
- Preserve: decisions made and user preferences expressed
- Preserve: the overall topic and what was accomplished
- Preserve: any specific names, numbers, IDs, or URLs mentioned
- Format as a concise narrative, not a transcript
- Start with "Previous conversation summary:"
- Do NOT add meta-commentary about summarizing
- This summary will be used as context — make it actionable

USER: Summarize this conversation history:

{conversation_transcript}
```

#### History Truncation: Complete 3-Step Flow

```
┌─────────────────────────────────────────────────────┐
│          Check: total_size > history_total_threshold? │
│          (650k chars)                                 │
└─────────────────────┬───────────────────────────────┘
                      │ YES
                      ▼
┌─────────────────────────────────────────────────────┐
│  STEP 1: Middle Zone Compression                     │
│                                                      │
│  AI ON:  Summarize entire middle zone → 1 message    │
│  AI OFF: Plain-text truncate middle msgs > 100k      │
│          (target: 85k per message)                   │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
             total_size > threshold?
              ┌───┘       └───┐
              │ NO            │ YES
              ▼               ▼
           ✅ DONE    ┌──────────────────────────────┐
                      │  STEP 2: Middle Zone Wipe     │
                      │  (AI OFF only)                │
                      │                               │
                      │  Remove ALL middle-zone msgs  │
                      │                               │
                      │  (AI ON: skip — Step 1        │
                      │   already replaced middle     │
                      │   with 1 summary message)     │
                      └──────────┬────────────────────┘
                                 │
                                 ▼
                        total_size > threshold?
                         ┌───┘       └───┐
                         │ NO            │ YES
                         ▼               ▼
                      ✅ DONE    ┌──────────────────────────────┐
                                 │  STEP 3: Protected Zone Trunc │
                                 │                               │
                                 │  For EVERY user + tool msg    │
                                 │  in remaining messages:       │
                                 │  if msg_size > 100k:          │
                                 │    plain-text truncate → 85k  │
                                 │  (always plain text, even     │
                                 │   with AI ON)                 │
                                 └──────────┬────────────────────┘
                                            │
                                            ▼
                                   total_size > threshold?
                                    ┌───┘       └───┐
                                    │ NO            │ YES
                                    ▼               ▼
                                 ✅ DONE    ┌──────────────────────┐
                                            │  RECURSIVE HALVING    │
                                            │                       │
                                            │  Halve all thresholds │
                                            │  and targets:         │
                                            │  650k → 325k          │
                                            │  500k → 250k          │
                                            │  100k → 50k           │
                                            │  85k → 42.5k          │
                                            │                       │
                                            │  Re-run from Step 1   │
                                            │  (max 3 recursions)   │
                                            └───────────────────────┘
```

#### Conversation Transcript Formatting

For the summarizer to understand the conversation structure, middle-zone messages are formatted as a readable transcript:

```python
def _format_conversation_transcript(self, messages: List[Dict]) -> str:
    """Format messages as a readable conversation transcript for summarization."""
    lines = []
    for i, msg in enumerate(messages, 1):
        role = msg.get("role", "unknown").capitalize()
        content = msg.get("content", "")

        # Handle tool call messages
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            tool_names = [tc.get("name", "unknown") for tc in msg["tool_calls"]]
            lines.append(f"[Turn {i}] Assistant: [Called tools: {', '.join(tool_names)}]")
            if content:
                lines.append(f"  Text: {content[:500]}")  # Brief text preview

        # Handle tool results
        elif is_tool_message(msg):
            content_preview = str(content)[:1000]  # Cap preview
            lines.append(f"[Turn {i}] Tool Result: {content_preview}")

        # Regular messages
        else:
            lines.append(f"[Turn {i}] {role}: {content}")

    return "\n".join(lines)
```

---

## 🛡️ Error Handling & Fallbacks

Every AI summarization call is wrapped with fallback to plain text truncation:

```python
async def _ai_summarize_or_fallback(
    self, content, target_size, system_prompt, llm_client, context_label
) -> str:
    """Attempt AI summarization; fall back to plain text on any error."""
    if not self.enable_ai_summarization or llm_client is None:
        return self._truncate_plain_text(content, context_label, target_size, len(content))

    try:
        summary = await self._ai_summarize_message(
            content, target_size, system_prompt, llm_client
        )
        if len(summary) <= target_size:
            return summary
        # Summary too long — fallback
        logger.warning(
            f"AI summary exceeded target ({len(summary):,} > {target_size:,}), "
            f"applying plain text truncation"
        )
        return self._truncate_plain_text(summary, context_label, target_size, len(content))
    except Exception as e:
        logger.error(f"AI summarization failed: {e}, falling back to plain text")
        return self._truncate_plain_text(content, context_label, target_size, len(content))
```

### Error scenarios and recovery

| Scenario                                        | Recovery                                                              |
| ----------------------------------------------- | --------------------------------------------------------------------- |
| LLM call timeout                                | Fall back to plain text truncation                                    |
| LLM returns empty response                      | Fall back to plain text truncation                                    |
| LLM summary exceeds target                      | Apply plain text truncation to the summary itself                     |
| LLM rate limited (throttled)                    | Wait + retry once, then fall back to plain text                       |
| `llm_client` is None                            | Use plain text truncation (AI summarization silently skipped)         |
| `enable_ai_summarization = False`               | Use plain text truncation (AI summarization skipped)                  |
| ContextWindowExceededError during summarization | Reduce chunk_size by half, retry; if still fails, plain text          |
| Summarization produces hallucinated content     | Not detectable — mitigated by low temperature (0.1) and strict prompt |

---

## ✅ Implementation Tasks

### Task S.1: Config Settings for AI Summarization

**Status**: ⬜ Not Started
**Priority**: P0 (Blocker)
**Estimated Effort**: 0.5 day
**Parent Task**: Chat Manager Tracker — Task 1.1

**Subtasks**:

- [ ] Add `enable_ai_summarization` field to `ChatConfig` (default: `False`)
- [ ] Add `summarization_chunk_size` field (default: `200000`)
- [ ] Add `message_truncation_threshold` field (default: `500000`)
- [ ] Add `message_truncation_target` field (default: `425000`)
- [ ] Add `history_total_threshold` field (default: `500000`)
- [ ] Add `history_msg_threshold` field (default: `100000`)
- [ ] Add `history_msg_target` field (default: `85000`)
- [ ] Add `max_truncation_recursion` field (default: `3`)
- [ ] Add validator: `message_truncation_target < message_truncation_threshold`
- [ ] Add validator: `history_msg_target < history_msg_threshold`
- [ ] Add validator: `summarization_chunk_size > 0`

---

### Task S.2: `preprocess_messages()` Entry Point

**Status**: ⬜ Not Started
**Priority**: P0 (Blocker)
**Estimated Effort**: 0.5 day
**Depends on**: S.1, Chat Manager Tracker — Task 3.1 (rename)

**Subtasks**:

- [ ] Add `preprocess_messages()` method to `MessagePreprocessor`
- [ ] Accept `llm_client` parameter (optional, needed for AI path)
- [ ] Accept `system_prompt` parameter (optional, context for summarizer)
- [ ] Accept `bedrock_params` parameter (optional, model config for summarizer calls)
- [ ] Sequence: single-message truncation → history-total truncation
- [ ] Log total size before and after preprocessing
- [ ] Return processed messages list

---

### Task S.3: Single-Message Plain Text Truncation (Generalized)

**Status**: ⬜ Not Started
**Priority**: P0 (Blocker)
**Estimated Effort**: 0.5 day
**Depends on**: S.2

**Description**: Generalize existing `_intelligently_truncate_tool_result` to handle ALL message types.

**Subtasks**:

- [ ] Implement `_truncate_oversized_messages()` — iterates all messages
- [ ] For each message, extract content size (handle str, list, dict formats)
- [ ] If message size > `message_truncation_threshold` AND AI summarization OFF:
  - Apply `_truncate_plain_text()` to reduce to `message_truncation_target`
- [ ] Preserve message metadata: role, tool_use_id, tool_call_id, etc.
- [ ] Handle Claude format: list of content blocks (truncate each `tool_result` or `text` block)
- [ ] Handle GPT format: role="tool" with string content
- [ ] Handle Llama format: role="user" with is_tool_result flag
- [ ] Handle plain user messages: role="user" with string content
- [ ] Log: message index, role, before/after sizes

---

### Task S.4: `split_into_chunks()` Utility

**Status**: ⬜ Not Started
**Priority**: P0 (Blocker)
**Estimated Effort**: 0.5 day
**Depends on**: None

**Description**: Content-aware text splitting for the rolling summarization.

**Subtasks**:

- [ ] Implement `split_into_chunks(content: str, chunk_size: int, min_chunks: int = 3) -> List[str]`
- [ ] If `len(content) / chunk_size < min_chunks`: adjust chunk_size down to `ceil(len(content) / min_chunks)`
- [ ] Split on natural boundaries in priority order:
  1. `\n\n` (paragraph break)
  2. `\n` (line break)
  3. `.` or `.\n` (sentence end)
  4. `space` (word boundary)
  5. Hard cut (last resort)
- [ ] Each chunk ≤ chunk_size
- [ ] All chunks together = original content (no gaps, no overlaps)
- [ ] Unit test: various sizes (small, exact boundary, large)
- [ ] Unit test: content with and without natural boundaries

**Note**: Consider reusing or adapting `context_aware_chunk()` from `message_chunker.py`.

---

### Task S.5: `_summarize_with_llm()` — Core Summarization Call

**Status**: ⬜ Not Started
**Priority**: P0 (Blocker for AI path)
**Estimated Effort**: 1 day
**Depends on**: S.2

**Description**: The atomic LLM call that summarizes a piece of content.

**Subtasks**:

- [ ] Implement `_summarize_with_llm(content, target_size, system_prompt, llm_client, iteration_context, bedrock_params)`
- [ ] Build summarization prompt (system + user messages)
- [ ] Include system prompt from main conversation as context
- [ ] Include iteration context (e.g., "chunk 2 of 5", "conversation history")
- [ ] Set LLM parameters: temperature=0.1, max_tokens=target_size/4
- [ ] Use `llm_client.format_messages_for_bedrock()` + `llm_client.chat_completion()`
- [ ] Extract text content from response
- [ ] Handle empty/None responses → raise to trigger fallback
- [ ] Handle errors → raise to trigger fallback
- [ ] Log: input size, output size, iteration context

**Prompt template** (see Algorithm Details section A above for full template)

---

### Task S.6: `_ai_summarize_message()` — Rolling Map-Reduce

**Status**: ⬜ Not Started
**Priority**: P0 (Blocker for AI path)
**Estimated Effort**: 1 day
**Depends on**: S.4, S.5

**Description**: Orchestrates the rolling summarization loop for a single oversized message.

**Subtasks**:

- [ ] Implement `_ai_summarize_message(content, target_size, system_prompt, llm_client, bedrock_params)`
- [ ] Call `split_into_chunks()` with `summarization_chunk_size` and `min_chunks=3`
- [ ] Iteration 1: `summary = _summarize_with_llm(chunk_1, target=chunk_size, context="chunk 1 of N")`
- [ ] Iterations 2..N: `summary = _summarize_with_llm(summary + chunk_i, target=chunk_size, context="chunk i of N")`
- [ ] After final iteration: if `len(summary) > message_truncation_target`, fall back to plain text
- [ ] Log: number of chunks, total LLM calls, final summary size
- [ ] Handle partial failures: if one iteration fails, fall back to plain text for entire message

---

### Task S.7: Single-Message AI Summarization Integration

**Status**: ⬜ Not Started
**Priority**: P0 (Blocker for AI path)
**Estimated Effort**: 0.5 day
**Depends on**: S.3, S.6

**Description**: Wire AI summarization into the single-message truncation path.

**Subtasks**:

- [ ] In `_truncate_oversized_messages()`, if AI ON and `llm_client` available:
  - Call `_ai_summarize_message()` instead of `_truncate_plain_text()`
- [ ] Wrap in `_ai_summarize_or_fallback()` for robust error handling
- [ ] Prepend `[AI SUMMARY - Original: {size} chars]` marker to summary
- [ ] Reconstruct message with summarized content + original metadata
- [ ] Log: "AI summarization" vs "plain text truncation" decision per message

---

### Task S.8: History Zone Detection

**Status**: ⬜ Not Started
**Priority**: P0 (Blocker)
**Estimated Effort**: 0.5 day
**Depends on**: S.2

**Description**: Identify protected vs. middle zones in message history.

**Subtasks**:

- [ ] Implement `_identify_message_zones(messages)` returning:
  - `system_idx`: index of system message (or None)
  - `last_user_idx`: index of last real (non-tool) user message
  - `middle_start`: first message after system in middle zone
  - `middle_end`: last message before last real user (exclusive)
  - `trailing_start`: first message in trailing tool loop (or last_user_idx if no trailing)
- [ ] Handle edge cases:
  - No system message
  - No middle zone (system + user only)
  - No trailing tool loop
  - All messages are tool messages
  - Single message (just system)
- [ ] Implement `_find_last_real_user_index(messages)`:
  - Walk backwards from end
  - Return index of last `role=user` that is NOT a tool message
  - Use existing `is_tool_message()` utility
- [ ] Unit test all edge cases

---

### Task S.9: History Truncation — 3-Step Progressive (Plain Text Path)

**Status**: ⬜ Not Started
**Priority**: P0 (Blocker)
**Estimated Effort**: 1 day
**Depends on**: S.3, S.8

**Description**: Implement the 3-step history truncation for when AI summarization is OFF.

**Subtasks**:

- [ ] Implement `_truncate_history_total(messages, llm_client, system_prompt, …)`
- [ ] Calculate total size: `sum(get_content_size(msg) for msg in messages)`
- [ ] If total ≤ `history_total_threshold`: return as-is
- [ ] Call `_identify_message_zones()` to find zones
- [ ] **Step 1 (AI OFF)**: For each middle-zone msg exceeding `history_msg_threshold`:
  - Apply `_truncate_plain_text()` with target `history_msg_target`
- [ ] Recalculate total. If ≤ threshold: return.
- [ ] **Step 2 (AI OFF only)**: Remove ALL middle-zone messages
- [ ] Recalculate total. If ≤ threshold: return.
- [ ] **Step 3**: For each remaining user+tool msg exceeding `history_msg_threshold`:
  - Apply `_truncate_plain_text()` with target `history_msg_target`
- [ ] Recalculate total. If ≤ threshold: return.
- [ ] **Recursive halving**: Call self with halved thresholds (up to `max_truncation_recursion`)
- [ ] Log each step: step number, total before/after, messages affected

---

### Task S.10: History Truncation — AI Summarization Path

**Status**: ✅ Complete
**Priority**: P1 (Important)
**Estimated Effort**: 1 day
**Depends on**: S.5, S.8, S.9

**Description**: Implement Step 1 of history truncation for the AI-enabled path.

**Subtasks**:

- [x] In `_truncate_history_total()`, branch on `enable_ai_summarization` for Step 1
- [x] Collect all middle-zone messages
- [x] Call `_format_conversation_transcript()` to create readable transcript
- [x] If transcript ≤ `summarization_chunk_size`: single `_summarize_with_llm()` call
- [x] If transcript > `summarization_chunk_size`: use rolling map-reduce via `_ai_summarize_message()`
- [x] Replace entire middle zone with: `{"role": "assistant", "content": "[CONVERSATION SUMMARY]\n{summary}"}`
- [x] **Skip Step 2** (middle-zone wipe) when AI path was used (Step 1 already condensed)
- [x] Continue to Step 3 if still over threshold
- [x] Fallback: if AI summarization fails for history, fall back to plain text Step 1
- [x] Log: messages summarized count, transcript size, summary size
- [x] Made `_truncate_history_total()` and `_run_history_truncation()` async
- [x] Updated `preprocess_messages()` to `await`

---

### Task S.11: `_format_conversation_transcript()` Utility

**Status**: ✅ Complete
**Priority**: P1 (Important)
**Estimated Effort**: 0.5 day
**Depends on**: S.8

**Subtasks**:

- [x] Implement `_format_conversation_transcript(messages, indices) -> str`
- [x] Format each message as `[Turn N] Role: content`
- [x] For assistant messages with tool_calls: show tool names called
- [x] For tool result messages: show content preview (cap at 1000 chars per result)
- [x] For user messages: show full content
- [x] For assistant text messages: show full content (these provide context)
- [x] Number turns sequentially
- [x] Handle all message formats (Claude list, GPT, Llama)
- [x] 11 unit tests in `test_ai_history_summarization.py`

---

### Task S.12: Unit Tests — Plain Text Truncation Paths

**Status**: ⬜ Not Started
**Priority**: P0 (Blocker)
**Estimated Effort**: 1 day
**Depends on**: S.3, S.9

**Subtasks**:

- [ ] Test `_truncate_oversized_messages()` with AI OFF:
  - [ ] Message under threshold → unchanged
  - [ ] String content over threshold → truncated
  - [ ] Claude list content with oversized tool_result → truncated
  - [ ] GPT tool message over threshold → truncated
  - [ ] Multiple messages, only some oversized
  - [ ] Metadata preserved after truncation
- [ ] Test `_truncate_history_total()` with AI OFF:
  - [ ] Total under threshold → unchanged
  - [ ] Step 1: middle-zone messages truncated
  - [ ] Step 2: middle zone wiped after Step 1 insufficient
  - [ ] Step 3: protected zone truncated
  - [ ] Recursive halving triggered
  - [ ] Max recursion respected (doesn't infinite loop)
- [ ] Test `_identify_message_zones()`:
  - [ ] Normal conversation with system + history + user
  - [ ] No system message
  - [ ] No middle zone
  - [ ] Trailing tool loop detection
  - [ ] All tool messages
- [ ] Test `split_into_chunks()`:
  - [ ] Paragraph-boundary splits
  - [ ] Line-boundary splits
  - [ ] Minimum chunks enforced
  - [ ] Hard cut fallback

---

### Task S.13: Unit Tests — AI Summarization Paths

**Status**: ⬜ Not Started
**Priority**: P1 (Important)
**Estimated Effort**: 1 day
**Depends on**: S.6, S.7, S.10

**Subtasks**:

- [ ] Test `_summarize_with_llm()`:
  - [ ] Successful summarization (mock LLM returns short summary)
  - [ ] LLM returns empty response → raises
  - [ ] LLM call times out → raises
  - [ ] Prompt includes system_prompt context
  - [ ] Temperature is 0.1
- [ ] Test `_ai_summarize_message()`:
  - [ ] Single chunk (message just over threshold) → 1 LLM call
  - [ ] Multiple chunks → N LLM calls
  - [ ] Min 3 chunks enforced
  - [ ] Rolling accumulation works correctly
  - [ ] Final summary under target → returned
  - [ ] Final summary over target → plain text fallback
  - [ ] One iteration fails → entire message falls back to plain text
- [ ] Test `_ai_summarize_or_fallback()`:
  - [ ] AI ON + success → returns AI summary
  - [ ] AI ON + failure → returns plain text
  - [ ] AI OFF → returns plain text (skips LLM call)
  - [ ] llm_client is None → returns plain text
- [ ] Test `_truncate_oversized_messages()` with AI ON:
  - [ ] Oversized message → AI summarization called
  - [ ] Normal message → no LLM call
- [ ] Test `_truncate_history_total()` with AI ON:
  - [ ] Step 1: middle zone summarized into 1 message
  - [ ] Step 2 skipped
  - [ ] Step 3 still uses plain text (even with AI ON)
  - [ ] AI failure → falls back to plain text Step 1
- [ ] Test `_format_conversation_transcript()`:
  - [ ] Mixed message types formatted correctly
  - [ ] Tool calls show names
  - [ ] Tool results capped at 1000 chars

---

### Task S.14: Integration Test — End-to-End Summarization

**Status**: ⬜ Not Started
**Priority**: P1 (Important)
**Estimated Effort**: 0.5 day
**Depends on**: S.12, S.13

**Subtasks**:

- [ ] Test full `preprocess_messages()` pipeline:
  - [ ] Messages with one oversized message + total under history threshold
  - [ ] Messages with total over history threshold but no oversized individual messages
  - [ ] Messages with both oversized message AND history over threshold
  - [ ] AI ON: verify LLM called with correct prompts (mock)
  - [ ] AI OFF: verify no LLM calls made
- [ ] Test integration with `ChatManager.chat_completion()`:
  - [ ] `preprocess_messages()` called before each LLM call in tool loop
  - [ ] Summarized messages flow correctly through `format_messages_for_bedrock()`

---

## 📁 Files Affected

| File                                 | Changes                                                                                                                                                                                                                                                                |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `config.py`                          | Add 8 config fields + validators                                                                                                                                                                                                                                       |
| `message_preprocessor.py`            | New `preprocess_messages()`, `_truncate_oversized_messages()`, `_truncate_history_total()`, `_identify_message_zones()`, `_ai_summarize_message()`, `_summarize_with_llm()`, `_format_conversation_transcript()`, `split_into_chunks()`, `_ai_summarize_or_fallback()` |
| `chat_manager.py`                    | Calls `preprocessor.preprocess_messages()` before each LLM call                                                                                                                                                                                                        |
| `tests/test_message_preprocessor.py` | New and migrated tests                                                                                                                                                                                                                                                 |

---

## 📊 Metrics to Track

| Metric                                  | How to Measure                           | Target                   |
| --------------------------------------- | ---------------------------------------- | ------------------------ |
| Summarization quality                   | Manual review of 10+ summarized messages | Key facts preserved      |
| Plain text vs AI decision count         | Logging counters                         | Track ratio              |
| Avg summarization LLM calls per message | Logging                                  | < 5 for typical messages |
| Avg summarization latency               | Timing in logs                           | < 10s for single message |
| Fallback rate (AI → plain text)         | Logging counters                         | < 5%                     |
| Total tokens used for summarization     | LLM response metadata                    | Monitor for budget       |

---

## 📝 Open Questions

1. **Should summarization use the same model as the main conversation or a cheaper/faster model?**
   Current design: same model. Could optimize costs by using a smaller model (e.g., Haiku for summarization while main conversation uses Sonnet). This would require a second `model_id` config or auto-selection.

2. **Should we cache summaries?**
   If the same message appears in history across multiple turns, we'd re-summarize it each time. Could cache `hash(content) → summary` in memory. Low priority for MVP.

3. **Should the summary marker `[AI SUMMARY]` be visible to the user or only in internal history?**
   Current design: internal only (in session messages). The marker helps debugging and lets the AI know this is a summary. The WebSocket response to the user shows only the final AI response, not intermediate history.

---

## 📅 Progress Log

| Date       | Task   | Update                                 |
| ---------- | ------ | -------------------------------------- |
| 2026-02-27 | Design | Feature implementation tracker created |
|            |        |                                        |
