# Chat Manager Architecture & LLM Client Abstraction

**Project**: Auto Bedrock Chat FastAPI — Chat Manager Refactoring
**Created**: February 27, 2026
**Last Updated**: February 27, 2026
**Status**: Design Phase

---

## 📋 Overview

This document describes the architectural design for three interrelated changes:

1. **Slim down `bedrock_client.py`** — make it a pure LLM transport layer
2. **New `chat_manager.py`** — orchestration layer for LLM conversations
3. **Rename `tool_message_processor.py` → `message_preprocessor.py`** — broaden scope to all messages, add AI-based summarization

The goal is a clean separation of concerns that makes it trivial to swap LLM providers (Bedrock → OpenAI → Anthropic) without touching orchestration or transport code.

---

## 🏗️ Architecture Overview

```
┌───────────────────────────────────────────────────────────────────────┐
│                       websocket_handler.py                           │
│                        (Transport Layer)                             │
│                                                                      │
│  • Accept WebSocket connections / route message types                │
│  • Inject tool_executor + on_tool_progress into ChatManager          │
│  • Map ChatCompletionResult → session updates + WS response          │
│  • RAG context retrieval + system prompt enhancement                 │
│  • Auth message handling, ping/pong, history requests                │
└─────────────────────────────┬────────────────────────────────────────┘
                              │ calls chat_completion()
                              ▼
┌───────────────────────────────────────────────────────────────────────┐
│                         chat_manager.py                              │
│                       (Orchestration Layer)                          │
│                                                                      │
│  • chat_completion() — main orchestrator                             │
│  • Uses LLMClient (bedrock/openai/etc.) for LLM calls               │
│  • Uses MessagePreprocessor before EACH LLM call                     │
│  • Uses ConversationManager for count-based message trimming         │
│  • Handles recursive tool call loops                                 │
│  • Handles context-window error recovery                             │
│  • Returns ChatCompletionResult with full updated history            │
└────────────┬───────────────────────────────┬─────────────────────────┘
             │                               │
             ▼                               ▼
┌────────────────────────┐    ┌──────────────────────────────────────┐
│    bedrock_client.py   │    │      message_preprocessor.py         │
│   (LLM Transport)     │    │     (Message Processing Layer)       │
│                        │    │                                      │
│ • chat_completion()    │    │ • preprocess_messages() entry point  │
│   send msgs + tools    │    │ • Single-message truncation          │
│   → parse response     │    │   (plain text OR AI summarization)   │
│ • format_messages()    │    │ • History-total truncation            │
│ • generate_embedding() │    │   (3-step progressive)               │
│ • Transport retries    │    │ • Detection utils                    │
│ • Rate limiting        │    │   (is_tool_message, get_content_size)│
└────────────────────────┘    └──────────────────────────────────────┘
                              ┌──────────────────────────────────────┐
                              │    conversation_manager.py           │
                              │     (UNCHANGED — utilities)          │
                              │                                      │
                              │ • Count-based trimming strategies    │
                              │   (truncate, sliding_window,         │
                              │    smart_prune)                      │
                              │ • Tool pair integrity                │
                              │ • Orphaned tool result cleanup       │
                              └──────────────────────────────────────┘
```

---

## 🔧 Component Details

### 1. `bedrock_client.py` — LLM Transport (Slimmed Down)

**Keeps:**
| Method | Purpose |
|--------|---------|
| `_initialize_client()` | boto3 setup |
| `chat_completion()` | Send messages + tools → API → parse response |
| `_prepare_request_body()` | Model-specific request formatting via parsers |
| `format_messages_for_bedrock()` | Convert raw messages → model-specific format |
| `_make_request_with_retries()` | Transport-level retries (throttle, transient) |
| `_parse_response()` | Model-specific response parsing |
| `_handle_rate_limiting()` | Simple rate limiter |
| `_get_parser()` | Model → Parser routing |
| `generate_embedding()` | Embedding API |
| `generate_embeddings_batch()` | Batch embedding API |

**Removes (moves to `chat_manager.py`):**
| Method/Logic | Destination |
|--------------|-------------|
| `ConversationManager` usage | `chat_manager.py` |
| `ToolMessageProcessor` usage | `chat_manager.py` (via `MessagePreprocessor`) |
| `MessageChunker` usage | `chat_manager.py` (via `MessagePreprocessor`) |
| `_try_request_with_fallback()` context-window recovery | `chat_manager.py` |
| Fallback model logic | `chat_manager.py` |

**New behavior for `chat_completion()`:**

```python
async def chat_completion(
    self,
    messages: List[Dict[str, Any]],
    model_id: Optional[str] = None,
    tools_desc: Optional[Dict] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Pure LLM transport: format → send → parse.
    No conversation management, no truncation, no fallback.
    Raises ContextWindowExceededError for context overflow.
    """
    model_id = model_id or self.config.model_id
    temperature = temperature if temperature is not None else self.config.temperature
    max_tokens = max_tokens or self.config.max_tokens

    await self._handle_rate_limiting()

    request_body = self._prepare_request_body(
        messages, model_id, tools_desc, temperature, max_tokens, **kwargs
    )
    response = await self._make_request_with_retries(model_id, request_body)
    return self._parse_response(response, model_id)
```

**New exception:** `ContextWindowExceededError(BedrockClientError)` — raised when the API returns a context/token overflow error. The `chat_manager` catches this and applies recovery strategies.

---

### 2. `chat_manager.py` — Orchestration Layer (NEW)

```python
@dataclass
class ChatCompletionResult:
    """Result from ChatManager.chat_completion()"""
    messages: List[Dict[str, Any]]       # Full updated conversation history
    response: Dict[str, Any]             # Final AI response (the last assistant message)
    tool_results: List[Dict[str, Any]]   # All tool results across all rounds
    metadata: Dict[str, Any]             # Stats: rounds count, total tool calls, etc.


class ChatManager:
    """
    Orchestrates LLM conversations: preprocessing, LLM calls,
    tool call loops, and error recovery.
    """

    def __init__(
        self,
        llm_client: BedrockClient,             # Future: LLMClient protocol
        config: ChatConfig,
        message_preprocessor: MessagePreprocessor,
        conversation_manager: ConversationManager,
    ):
        self.llm_client = llm_client
        self.config = config
        self.preprocessor = message_preprocessor
        self.conversation_manager = conversation_manager

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools_desc: Optional[Dict] = None,
        tool_executor: Optional[Callable] = None,
        on_tool_progress: Optional[Callable] = None,
        bedrock_params: Optional[Dict] = None,
    ) -> ChatCompletionResult:
        """
        Main orchestrator for a complete chat turn.

        Args:
            messages: Raw conversation messages (user/assistant/tool/system dicts)
            tools_desc: Tool descriptions for the LLM
            tool_executor: async callable(tool_calls) -> tool_results
                           Injected by websocket_handler; None disables tool use
            on_tool_progress: async callable(message_dict) -> None
                              Optional callback for sending WS progress updates
            bedrock_params: model_id, temperature, max_tokens, etc.

        Returns:
            ChatCompletionResult with full history, final response, all tool results
        """
```

**Key orchestration flow:**

```
chat_completion() called with raw messages
  │
  ├── 1. Count-based trimming (ConversationManager)
  ├── 2. Orphaned tool result cleanup (ConversationManager)
  ├── 3. Message preprocessing (MessagePreprocessor)
  │     ├── Single-message truncation / AI summarization
  │     └── History-total truncation (3-step progressive)
  ├── 4. Format messages for LLM (llm_client.format_messages_for_bedrock)
  ├── 5. Call LLM (llm_client.chat_completion)
  │     ├── On ContextWindowExceededError → aggressive fallback + retry
  │     └── On other error → fallback model (if configured) or raise
  ├── 6. If tool_calls in response AND tool_executor provided:
  │     ├── Add assistant message to history
  │     ├── Execute tools via tool_executor
  │     ├── Notify via on_tool_progress (if provided)
  │     ├── Add tool results to history
  │     └── GOTO step 1 (up to max_tool_call_rounds)
  └── 7. Return ChatCompletionResult
```

**Tool executor injection pattern:**

```python
# In websocket_handler.py:
async def _handle_chat_message(self, websocket, data):
    ...
    # Wrap execute_tool_calls + send_message as callables
    async def tool_executor(tool_calls):
        return await self._execute_tool_calls(tool_calls, session)

    async def on_tool_progress(msg_dict):
        await self._send_message(websocket, msg_dict)

    result = await self.chat_manager.chat_completion(
        messages=message_dicts,
        tools_desc=tools_desc,
        tool_executor=tool_executor,
        on_tool_progress=on_tool_progress,
        bedrock_params=self.config.get_bedrock_params(),
    )

    # Update session with full history
    await self.session_manager.replace_messages(session.session_id, result.messages)

    # Send final response to client
    await self._send_message(websocket, {
        "type": "ai_response",
        "message": result.response.get("content", ""),
        ...
    })
```

**Context-window error recovery:**

```python
async def _call_llm_with_recovery(self, messages, tools_desc, bedrock_params):
    """Call LLM with context-window overflow recovery."""
    try:
        formatted = self.llm_client.format_messages_for_bedrock(messages)
        return await self.llm_client.chat_completion(
            messages=formatted, tools_desc=tools_desc, **bedrock_params
        )
    except ContextWindowExceededError:
        # Aggressive fallback: keep system + last few messages
        reduced = self._aggressive_message_reduction(messages)
        formatted = self.llm_client.format_messages_for_bedrock(reduced)
        return await self.llm_client.chat_completion(
            messages=formatted, tools_desc=tools_desc, **bedrock_params
        )
```

---

### 3. `message_preprocessor.py` — Message Processing (Renamed + Enhanced)

Renamed from `tool_message_processor.py`. All existing functionality preserved, plus:

#### 3.1 Entry Point

```python
class MessagePreprocessor:
    """
    Preprocesses conversation messages before LLM calls.
    Handles truncation, AI summarization, and history management.
    """

    async def preprocess_messages(
        self,
        messages: List[Dict[str, Any]],
        llm_client: Optional[Any] = None,   # Needed for AI summarization
        system_prompt: Optional[str] = None, # Context for summarizer
    ) -> List[Dict[str, Any]]:
        """
        Main entry point — run all preprocessing steps.

        Steps:
        1. Single-message truncation for oversized individual messages
        2. History-total truncation if combined size exceeds threshold
        """
        # Step 1: Single-message truncation
        messages = await self._truncate_oversized_messages(messages, llm_client, system_prompt)

        # Step 2: History-total truncation (if needed)
        messages = await self._truncate_history_total(messages, llm_client, system_prompt)

        return messages
```

#### 3.2 Single-Message Truncation

**Threshold**: `message_truncation_threshold` (default: 500,000 chars)
**Target**: `message_truncation_target` (default: 425,000 chars)

For any individual message that exceeds the threshold:

- **AI summarization disabled**: Plain text truncation (keep head + tail, same as current `_truncate_plain_text`)
- **AI summarization enabled**: Rolling map-reduce summarization:

```
Original message (e.g., 800k chars)
  ├── Split into chunks of ≤ summarization_chunk_size (200k)
  │   → [chunk_1 (200k), chunk_2 (200k), chunk_3 (200k), chunk_4 (200k)]
  │
  ├── Iteration 1: summarize(chunk_1) → summary_1 (≤200k)
  ├── Iteration 2: summarize(summary_1 + chunk_2) → summary_2 (≤200k)
  ├── Iteration 3: summarize(summary_2 + chunk_3) → summary_3 (≤200k)
  └── Iteration 4: summarize(summary_3 + chunk_4) → summary_4 (≤200k)

  Final: summary_4 replaces original message

  If summary_4 > message_truncation_target:
    apply plain text truncation as fallback
```

**Summarization prompt template:**

```
You are a summarization assistant. Your job is to condense information
while preserving all key facts, data points, and actionable details.

The main conversation has this system prompt for context:
---
{system_prompt}
---

Summarize the following content. Keep all important data, names, numbers,
and conclusions. Your summary MUST be under {target_size} characters.

Content to summarize:
{content}
```

#### 3.3 History-Total Truncation

**Total threshold**: `history_total_threshold` (default: 500,000 chars)
**Per-message threshold**: `history_msg_threshold` (default: 100,000 chars)
**Per-message target**: `history_msg_target` (default: 85,000 chars)

Triggered when `sum(len(msg.content) for msg in messages)` exceeds `history_total_threshold` after all single-message truncations.

**Message zones:**

```
messages = [
  system_prompt,          ← PROTECTED (never truncated)
  msg_1,                  ┐
  msg_2,                  │ MIDDLE ZONE (truncation candidates)
  ...                     │
  msg_N-K,                ┘
  last_user_message,      ← PROTECTED (+ any trailing assistant+tool messages)
  assistant_tool_call,    ← PROTECTED (trailing tool loop)
  tool_result,            ← PROTECTED (trailing tool loop)
]
```

**"Last user message"** = the last message with `role=user` that is NOT a tool result.

**3-step progressive truncation:**

**Step 1 — Middle zone compression:**

- **AI summarization ON**: Send all middle-zone messages to the summarizer. Replace them with a single assistant message containing the summary.
- **AI summarization OFF**: For each middle-zone message exceeding `history_msg_threshold`, apply single-message plain text truncation with target `history_msg_target`.

**Step 2 — Middle zone wipe (only if AI summarization OFF):**

- If total still exceeds `history_total_threshold` after Step 1, remove ALL middle-zone messages entirely.

**Step 3 — Protected zone truncation:**

- If total still exceeds `history_total_threshold`, apply single-message truncation (plain text) with `history_msg_threshold` / `history_msg_target` to every user and tool message that exceeds `history_msg_threshold`.

**Recursive halving (safety net):**

- If total STILL exceeds `history_total_threshold` after all 3 steps, re-run the entire history truncation process with halved values:
  - `history_total_threshold` → `history_total_threshold / 2`
  - `message_truncation_target` → `message_truncation_target / 2`
  - `history_msg_threshold` → `history_msg_threshold / 2`
  - `history_msg_target` → `history_msg_target / 2`
- **Max recursion depth**: 3 (configurable via `max_truncation_recursion`)

---

### 4. `conversation_manager.py` — UNCHANGED

Stays as-is. Its responsibilities:

- Count-based trimming (`truncate`, `sliding_window`, `smart_prune`)
- `remove_orphaned_tool_results()`
- Tool pair integrity (`_ensure_tool_pairs_stay_together`, etc.)

Called by `ChatManager` before `MessagePreprocessor`.

---

## ⚙️ Configuration (New Settings)

All new settings in `config.py` (`ChatConfig`):

| Setting                        | Env Var                            | Default     | Description                                                        |
| ------------------------------ | ---------------------------------- | ----------- | ------------------------------------------------------------------ |
| `llm_client_type`              | `BEDROCK_LLM_CLIENT_TYPE`          | `"bedrock"` | LLM client to use (`"bedrock"`, future: `"openai"`, `"anthropic"`) |
| `enable_ai_summarization`      | `BEDROCK_ENABLE_AI_SUMMARIZATION`  | `False`     | Enable AI-based summarization (uses extra LLM calls)               |
| `summarization_chunk_size`     | `BEDROCK_SUMMARIZATION_CHUNK_SIZE` | `200000`    | Max chars per chunk for AI summarization rolling window            |
| `message_truncation_threshold` | `BEDROCK_MSG_TRUNCATION_THRESHOLD` | `500000`    | Single-message truncation threshold (chars)                        |
| `message_truncation_target`    | `BEDROCK_MSG_TRUNCATION_TARGET`    | `425000`    | Single-message target after truncation (chars)                     |
| `history_total_threshold`      | `BEDROCK_HISTORY_TOTAL_THRESHOLD`  | `500000`    | Total history threshold (chars)                                    |
| `history_msg_threshold`        | `BEDROCK_HISTORY_MSG_THRESHOLD`    | `100000`    | Per-message threshold during history truncation (chars)            |
| `history_msg_target`           | `BEDROCK_HISTORY_MSG_TARGET`       | `85000`     | Per-message target during history truncation (chars)               |
| `max_truncation_recursion`     | `BEDROCK_MAX_TRUNCATION_RECURSION` | `3`         | Max recursion depth for history truncation halving                 |

**Note on `summarization_chunk_size = 200,000`:**
At 200k chars ≈ 50k tokens per chunk, the summarizer input is ~100k tokens (previous summary + next chunk) with ~50k tokens output budget. This fits comfortably in Claude's 200k token context window and minimizes the number of LLM calls. For models with smaller context windows (Llama 3, GPT at 128k tokens), reduce this value to 100,000 or lower to avoid overflow.

---

## 📊 Impact on Existing Files

### Files Modified

| File                   | Change                                                                                                                                        |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `bedrock_client.py`    | Remove conversation management, truncation, chunking, fallback logic. Add `ContextWindowExceededError`.                                       |
| `websocket_handler.py` | Replace direct `bedrock_client` usage with `chat_manager`. Remove `_handle_tool_calls_recursively`. Inject tool executor + progress callback. |
| `config.py`            | Add new settings (see table above).                                                                                                           |
| `plugin.py`            | Instantiate `ChatManager` and `MessagePreprocessor`. Pass `ChatManager` to `WebSocketChatHandler` instead of `BedrockClient`.                 |
| `__init__.py`          | Update exports (add `ChatManager`, `MessagePreprocessor`; `BedrockClient` remains for advanced/embedding usage).                              |
| `exceptions.py`        | Add `ContextWindowExceededError`.                                                                                                             |

### Files Created

| File                      | Purpose                                        |
| ------------------------- | ---------------------------------------------- |
| `chat_manager.py`         | New orchestration layer                        |
| `message_preprocessor.py` | Renamed + enhanced `tool_message_processor.py` |

### Files Deleted / Deprecated

| File                        | Action                                                                                |
| --------------------------- | ------------------------------------------------------------------------------------- |
| `tool_message_processor.py` | Rename to `message_preprocessor.py` (keep import alias for backward compat if needed) |

### Files Unchanged

| File                      | Reason                                                     |
| ------------------------- | ---------------------------------------------------------- |
| `conversation_manager.py` | Utility role stays the same                                |
| `session_manager.py`      | Session storage role stays the same                        |
| `retry_handler.py`        | Transport-level retries stay in bedrock_client scope       |
| `message_chunker.py`      | May be merged into message_preprocessor or kept as utility |
| `tools_generator.py`      | Unaffected                                                 |
| `auth_handler.py`         | Unaffected                                                 |
| `parsers/`                | Unaffected (still used by bedrock_client)                  |

---

## 🔀 Data Flow: Complete Chat Turn

```
User sends WebSocket message
  │
  ▼
websocket_handler._handle_chat_message()
  ├── Add user message to session history
  ├── RAG: retrieve KB context, enhance system prompt
  ├── Get context messages from session
  ├── Convert ChatMessage objects → raw dicts
  │
  ▼
chat_manager.chat_completion(messages, tools_desc, tool_executor, on_tool_progress)
  │
  ├── conversation_manager.manage_conversation_history(messages)
  │   └── Count-based trimming (sliding_window / truncate / smart_prune)
  │
  ├── conversation_manager.remove_orphaned_tool_results(messages)
  │
  ├── message_preprocessor.preprocess_messages(messages, llm_client, system_prompt)
  │   ├── Single-message truncation (plain text or AI summarization)
  │   └── History-total truncation (3-step progressive)
  │
  ├── llm_client.format_messages_for_bedrock(messages)
  ├── llm_client.chat_completion(formatted_messages, tools_desc, **params)
  │   └── _prepare_request_body → _make_request_with_retries → _parse_response
  │
  ├── If tool_calls AND tool_executor:
  │   ├── on_tool_progress({"type": "typing", "message": "Working..."})
  │   ├── Add assistant msg to local history
  │   ├── tool_results = await tool_executor(tool_calls)
  │   ├── Add tool results to local history
  │   └── LOOP BACK to conversation_manager step
  │
  └── Return ChatCompletionResult(messages, response, tool_results, metadata)
  │
  ▼
websocket_handler
  ├── Update session history with result.messages
  ├── Send result.response to WebSocket client
  └── Include metadata (kb_sources, tool_results, etc.)
```

---

## 🧪 Testing Strategy

### Unit Tests

- `test_chat_manager.py` — Orchestration logic, tool loops, error recovery
- `test_message_preprocessor.py` — All truncation/summarization paths
- `test_bedrock_client_slim.py` — Slimmed-down client (no orchestration)

### Integration Tests

- Chat manager + bedrock client end-to-end (mock AWS)
- AI summarization with real LLM calls (optional, expensive)
- Tool call loops with mock tool executor

### Migration Tests

- Verify existing tool_message_processor tests pass with message_preprocessor
- Verify existing bedrock_client tests still pass after slimming
- Verify websocket_handler behavior is identical after refactoring
