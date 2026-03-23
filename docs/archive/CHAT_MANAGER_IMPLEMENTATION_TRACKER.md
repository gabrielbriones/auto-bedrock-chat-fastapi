# Chat Manager Refactoring — Implementation Tracker

**Project**: Auto Bedrock Chat FastAPI — Chat Manager + AI Summarization
**Start Date**: TBD
**Last Updated**: March 3, 2026
**Architecture Reference**: [CHAT_MANAGER_ARCHITECTURE.md](CHAT_MANAGER_ARCHITECTURE.md)

---

## 📋 Project Summary

Refactor the codebase to cleanly separate LLM transport, conversation orchestration, and message preprocessing. Add optional AI-based summarization for oversized messages and conversation histories.

**Key deliverables:**

- `chat_manager.py` — New orchestration layer
- `message_preprocessor.py` — Renamed + enhanced from `tool_message_processor.py`
- `bedrock_client.py` — Slimmed to pure LLM transport
- AI-based summarization feature (opt-in) — see [AI_SUMMARIZATION_IMPLEMENTATION_TRACKER.md](AI_SUMMARIZATION_IMPLEMENTATION_TRACKER.md)

---

## 🎯 Success Criteria

- [ ] `bedrock_client.py` has zero references to `ConversationManager`, `ToolMessageProcessor`, or `MessageChunker`
- [ ] No file outside `chat_manager.py` imports `bedrock_client` (except `plugin.py` for instantiation and `__init__.py` for re-export)
- [ ] All existing tests pass after refactoring (green CI)
- [ ] Tool call recursion works identically to current behavior
- [ ] AI summarization correctly reduces oversized messages when enabled
- [ ] History truncation 3-step process works with and without AI summarization
- [ ] New config settings are documented and validated
- [ ] WebSocket handler behavior is externally identical (no client-visible changes)

---

## 📊 Implementation Phases

| Phase   | Focus                                       | Estimated Effort | Status                          |
| ------- | ------------------------------------------- | ---------------- | ------------------------------- |
| Phase 1 | Foundation — New files, config, exceptions  | 2-3 days         | ✅ Complete                     |
| Phase 2 | Slim bedrock_client + Build chat_manager    | 3-4 days         | ✅ Complete                     |
| Phase 3 | Message preprocessor + AI summarization     | 3-4 days         | ✅ Complete (3.1-3.6 all done)  |
| Phase 4 | Integration — Wire everything together      | 2-3 days         | ✅ Complete                     |
| Phase 5 | Testing + Migration validation              | 2-3 days         | 🔄 In Progress (5.1 ✅, 5.2 ✅) |
| Phase 6 | ToolManager extraction                      | 2-3 days         | ✅ Complete                     |
| Phase 7 | Remove ConversationManager & MessageChunker | 1 day            | ✅ Complete                     |

**Total estimated effort**: 14-20 days

---

## ✅ Detailed Task Breakdown

### Phase 1: Foundation

#### Task 1.1: Add New Config Settings

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 0.5 day

**Description**: Add all new configuration fields to `ChatConfig` in `config.py`.

**Subtasks**:

- [x] Add `llm_client_type` field (default: `"bedrock"`)
- [x] Add `enable_ai_summarization` field (default: `False`)
- [x] Add `summarization_chunk_size` field (default: `200000`)
- [x] Add `message_truncation_threshold` field (default: `500000`)
- [x] Add `message_truncation_target` field (default: `425000`)
- [x] Add `history_total_threshold` field (default: `500000`)
- [x] Add `history_msg_threshold` field (default: `100000`)
- [x] Add `history_msg_target` field (default: `85000`)
- [x] Add `max_truncation_recursion` field (default: `3`)
- [x] Add validator for `llm_client_type` (must be in `{"bedrock"}` for now)
- [x] Add validator ensuring `message_truncation_target < message_truncation_threshold`
- [x] Add validator ensuring `history_msg_target < history_msg_threshold`
- [x] Update `.env.example` and `.env.test` with new env vars

**Config table**:

| Setting                        | Env Var                            | Default     | Type   |
| ------------------------------ | ---------------------------------- | ----------- | ------ |
| `llm_client_type`              | `BEDROCK_LLM_CLIENT_TYPE`          | `"bedrock"` | `str`  |
| `enable_ai_summarization`      | `BEDROCK_ENABLE_AI_SUMMARIZATION`  | `False`     | `bool` |
| `summarization_chunk_size`     | `BEDROCK_SUMMARIZATION_CHUNK_SIZE` | `200000`    | `int`  |
| `message_truncation_threshold` | `BEDROCK_MSG_TRUNCATION_THRESHOLD` | `500000`    | `int`  |
| `message_truncation_target`    | `BEDROCK_MSG_TRUNCATION_TARGET`    | `425000`    | `int`  |
| `history_total_threshold`      | `BEDROCK_HISTORY_TOTAL_THRESHOLD`  | `500000`    | `int`  |
| `history_msg_threshold`        | `BEDROCK_HISTORY_MSG_THRESHOLD`    | `100000`    | `int`  |
| `history_msg_target`           | `BEDROCK_HISTORY_MSG_TARGET`       | `85000`     | `int`  |
| `max_truncation_recursion`     | `BEDROCK_MAX_TRUNCATION_RECURSION` | `3`         | `int`  |

---

#### Task 1.2: Add New Exception

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 0.5 hour

**Subtasks**:

- [x] Add `ContextWindowExceededError(BedrockClientError)` to `exceptions.py`
- [x] Update `__init__.py` exports

---

#### Task 1.3: Create `ChatCompletionResult` Dataclass

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 0.5 hour

**Subtasks**:

- [x] Define the dataclass (created in shared `models.py`)
- [x] Fields: `messages`, `response`, `tool_results`, `metadata`

---

### Phase 2: Slim `bedrock_client.py` + Build `chat_manager.py`

#### Task 2.1: Slim Down `bedrock_client.py`

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 1-2 days
**Depends on**: Task 1.2

**Description**: Remove all orchestration logic from `bedrock_client.py`. The `chat_completion` method should only: format → send → parse.

**Subtasks**:

- [x] Remove `ConversationManager` import and instance
- [x] Remove `ToolMessageProcessor` import and instance
- [x] Remove `MessageChunker` import and instance
- [x] Remove conversation history management from `chat_completion`
- [x] Remove `truncate_tool_messages_in_history` call from `chat_completion`
- [x] Remove `remove_orphaned_tool_results` call from `chat_completion`
- [x] Remove `check_and_chunk_messages` call from `chat_completion`
- [x] Simplify `chat_completion` to: rate limit → prepare request → send with retries → parse
- [x] Refactor `_try_request_with_fallback` to raise `ContextWindowExceededError` on context overflow instead of attempting recovery internally
- [x] Remove fallback model logic from `chat_completion` (moved to chat_manager)
- [x] Keep `RetryHandler` for transport-level retries only
- [x] Verify `format_messages_for_bedrock` still works standalone
- [x] Verify `generate_embedding()` and `generate_embeddings_batch()` still work
- [x] Keep logging utilities (module-level functions)
- [x] Migrate `test_multi_tool_calls.py` — 19/19 tests pass (direct ToolMessageProcessor)
- [x] Migrate `test_conversation_management.py` — 25/25 tests pass (direct ConversationManager, MessageChunker, ToolMessageProcessor)
- [x] Full test suite: 234 passed, 1 skipped, 0 failed

**Critical**: The `retry_handler.py` stays. Only the _context-window_ recovery + _conversation reduction_ logic moves to chat_manager. The `is_context_window_error` detection should still live in either retry_handler or bedrock_client so it can raise the right exception.

---

#### Task 2.2: Create `chat_manager.py` — Core Structure

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 1 day
**Depends on**: Task 1.1, 1.2, 1.3

**Subtasks**:

- [x] Create file with `ChatManager` class
- [x] Constructor accepts: `llm_client`, `config`, `conversation_manager`, `tool_message_processor`, `message_chunker`
- [x] Implement `chat_completion()` method signature with all parameters (`messages`, `tools_desc`, `tool_executor`, `on_tool_progress`, `**bedrock_params`)
- [x] Implement count-based trimming step (delegate to `ConversationManager`)
- [x] Implement orphaned tool result cleanup step (delegate to `ConversationManager`)
- [x] Implement message preprocessing step (delegate to `ToolMessageProcessor` + `MessageChunker`)
- [x] Implement message formatting step (delegate to `llm_client.format_messages_for_bedrock`)
- [x] Implement LLM call step (delegate to `llm_client.chat_completion`)
- [x] Implement context-window error recovery (`_call_llm_with_recovery` + `_aggressive_message_reduction`)
- [x] Return `ChatCompletionResult`
- [x] Export `ChatManager` from `__init__.py`
- [x] Write tests: 24/24 pass (97% coverage on chat_manager.py)
- [x] Full test suite: 258 passed, 1 skipped, 0 failed

---

#### Task 2.3: Create `chat_manager.py` — Tool Call Loop

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 1 day
**Depends on**: Task 2.2

**Description**: Move `_handle_tool_calls_recursively` logic from `websocket_handler.py` into `ChatManager`.

**Subtasks**:

- [x] Implement tool call detection in response
- [x] Implement tool execution via injected `tool_executor` callable
- [x] Implement progress notification via injected `on_tool_progress` callable
- [x] Implement recursive loop with `max_tool_call_rounds` limit
- [x] Add assistant message + tool results to local message history per round
- [x] Re-run preprocessing before each subsequent LLM call
- [x] Handle placeholder response detection (Llama edge case)
- [x] Handle max rounds exceeded gracefully

**Implementation Notes**:

- Added `_run_tool_call_loop()` private method (~100 lines)
- Wired into `chat_completion()` as Step 6 (after initial LLM call)
- Each round: notify progress → append assistant msg → execute tools → append tool results → re-run full pipeline (trim → cleanup → preprocess → format → LLM)
- Metadata populated with `tool_call_rounds` and `total_tool_calls`
- 15 new tests in `test_chat_manager.py` (39 total, 98% coverage on chat_manager.py)
- Full suite: 273 passed, 1 skipped, 0 failed

---

#### Task 2.4: Create `chat_manager.py` — Error Recovery

**Status**: ✅ Complete
**Priority**: P1 (Important)
**Estimated Effort**: 0.5-1 day
**Depends on**: Task 2.2

**Subtasks**:

- [x] Catch `ContextWindowExceededError` from LLM client
- [x] Implement aggressive message reduction (keep system + last few messages)
- [x] Retry LLM call with reduced messages
- [x] Implement fallback model logic (if `config.fallback_model` set)
- [x] Implement graceful degradation (if `config.graceful_degradation` enabled)
- [x] Log all recovery attempts

**Implementation Notes**:

- Refactored `_call_llm_with_recovery()` into 4-layer recovery sequence:
  - Layer 1: Normal LLM call
  - Layer 2: Aggressive message reduction → retry
  - Layer 3: Fallback model (if `config.fallback_model` is set)
  - Layer 4: Graceful degradation → synthetic apology response (if `config.graceful_degradation=True`)
- Added `_graceful_degradation_response()` static method
- Metadata keys: `context_window_retries`, `fallback_model_used`, `graceful_degradation_used`
- 9 new tests: 4 ContextWindowRecovery (updated), 4 FallbackModel, 4 GracefulDegradation (+1 unit test)
- Full suite: 282 passed, 1 skipped, 0 failed
- chat_manager.py: 98% coverage (129 statements, 2 missed)

---

### Phase 3: Message Preprocessor + AI Summarization

#### Task 3.1: Rename and Scaffold `message_preprocessor.py`

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 0.5 day
**Depends on**: Task 1.1
**Completed**: Phase 3 scaffold

**Subtasks**:

- [x] Copy `tool_message_processor.py` → `message_preprocessor.py`
- [x] Rename class `ToolMessageProcessor` → `MessagePreprocessor`
- [x] Keep all existing methods and module-level functions
- [x] Add `preprocess_messages()` as the main entry point (delegates to `truncate_tool_messages_in_history`)
- [x] Add `llm_client` and `system_prompt` parameters for AI summarization
- [x] Update all internal imports across the codebase (`chat_manager.py`, `message_chunker.py`)
- [x] Keep backward-compatible import in `tool_message_processor.py` (thin re-export shim)
- [x] Update test imports to use `message_preprocessor` module (aliased as `ToolMessageProcessor`)
- [x] Export `MessagePreprocessor` from `__init__.py`
- [x] Verify all existing tests pass: 282 passed, 1 skipped, 0 failed

**Implementation Notes**:

- `tool_message_processor.py` replaced with backward-compat shim (~40 lines) that re-exports everything from `message_preprocessor`
- `ToolMessageProcessor = MessagePreprocessor` alias ensures full backward compatibility
- `preprocess_messages(messages, llm_client=None, system_prompt=None)` — currently delegates to existing `truncate_tool_messages_in_history()`
- Placeholder comments added for Tasks 3.2 (single-message truncation) and 3.3 (history-total truncation)

---

#### Task 3.2: Single-Message Truncation (Plain Text Path)

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 0.5 day
**Depends on**: Task 3.1

**Description**: Generalize existing truncation to work on ALL message types (not just tool results). Apply to any message exceeding `message_truncation_threshold`.

**Subtasks**:

- [x] Implement `_truncate_oversized_messages()` that iterates all messages
- [x] For each message exceeding `message_truncation_threshold`:
  - [x] If AI summarization disabled → plain text truncation to `message_truncation_target`
  - [x] If AI summarization enabled → defer to AI summarization method (Task 3.4)
- [x] Handle all message formats: string content, list content (Claude format), dict content
- [x] Preserve message metadata (role, tool_use_id, etc.)
- [x] Log truncation actions with before/after sizes

**Implementation Notes**:

- `_truncate_oversized_messages()` iterates all messages, checking `get_content_size()` vs `config.message_truncation_threshold`
- Dispatches to `_truncate_message_content()` which handles str, list, and dict content formats
- List content uses proportional truncation via `_truncate_list_content_items()` (budget distributed by item size)
- `_truncate_plain_text()` gained a `label` kwarg for custom markers (default `"TOOL RESULT"`, general uses `"MESSAGE CONTENT"`)
- When `config` is `None`, the method is a no-op (backward compat)
- AI summarization enabled flag is checked but falls back to plain text (Task 3.4 placeholder)
- Wired into `preprocess_messages()` as Step 2 (after tool-result truncation) and into `chat_manager._preprocess_messages()` as step 3b
- 37 new tests: `test_single_message_truncation.py` — covers all methods, content formats, logging, pipeline integration, and ChatManager wiring
- Full suite: 383 passed, 1 skipped, 0 failed

---

#### Task 3.3: History-Total Truncation (Plain Text Path)

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 1 day
**Depends on**: Task 3.2

**Description**: Implement the 3-step progressive history truncation + recursive halving, for the non-AI path (plain text truncation only).

**Subtasks**:

- [x] Implement `_truncate_history_total()` main method
- [x] Implement total size calculation across all messages
- [x] Implement "zone detection":
  - System prompt (index 0, if role=system) → PROTECTED
  - Last real user message (role=user, not tool result) → PROTECTED
  - Trailing tool loop (assistant+tool messages after last user) → PROTECTED
  - Everything in between → MIDDLE ZONE
- [x] **Step 1 (AI OFF)**: Plain text truncate middle-zone messages exceeding `history_msg_threshold` to `history_msg_target`
- [x] **Step 2 (AI OFF only)**: If still over `history_total_threshold`, wipe ALL middle-zone messages
- [x] **Step 3**: Truncate protected-zone user+tool messages exceeding `history_msg_threshold` to `history_msg_target`
- [x] **Recursive halving**: If still over threshold, re-run with halved values (up to `max_truncation_recursion`)
- [x] Log each step with sizes before/after

**Implementation Notes**:

- `_truncate_history_total()` reads config and delegates to `_run_history_truncation()` which implements the 3-step + recursive loop
- `_detect_zones()` classifies each message index as `protected` or `middle` based on role and position
- `_history_step_truncate_zone()` truncates messages at given indices that exceed `msg_threshold` (reuses `_truncate_message_content` from Task 3.2)
- `_wipe_middle_zone()` removes all middle-zone messages when step 1 is insufficient (AI OFF path only)
- When AI is enabled, step 2 (wipe) is skipped — reserved for AI summarization in Task 3.4
- Recursive halving: thresholds halved each iteration, up to `max_truncation_recursion` (default 3)
- Wired into `preprocess_messages()` as Step 3 (after tool-result truncation and single-message truncation)
- 31 new tests: `test_history_total_truncation.py` — covers zone detection, helpers, all 3 steps, recursive halving, pipeline integration, edge cases
- Full suite: 414 passed, 1 skipped, 0 failed

---

#### Task 3.4: AI-Based Single-Message Summarization

**Status**: ✅ Complete
**Priority**: P1 (Important)
**Estimated Effort**: 1-1.5 days
**Depends on**: Task 3.2

**Description**: Implement rolling map-reduce summarization for individual oversized messages.

**Subtasks**:

- [x] Implement `_ai_summarize_message()` method
- [x] Split message content into chunks of ≤ `summarization_chunk_size` (200,000 chars)
- [x] Ensure minimum 3 chunks if message is over threshold (adjust chunk size down if needed)
- [x] Implement rolling summarization loop:
  - Iteration 1: `summarize(chunk_1)` → `summary_1`
  - Iteration N: `summarize(summary_{N-1} + chunk_N)` → `summary_N`
- [x] Build summarization prompt:
  - Include system prompt context from main conversation
  - Include target size limit
  - Instruct to preserve key facts, numbers, names, conclusions
- [x] Call `llm_client.chat_completion()` for each summarization step
- [x] If final summary exceeds `message_truncation_target`, fall back to plain text truncation
- [x] Handle LLM errors gracefully (fall back to plain text truncation)
- [x] Log each summarization iteration with input/output sizes

**Implementation notes**:

- Added `split_into_chunks()` module-level utility (content-aware splitting with natural boundaries)
- Added `_extract_text_content()` — extracts text from any message format (str/list/dict)
- Added `_summarize_with_llm()` — atomic LLM call (temperature=0.1, max_tokens=target/4)
- Added `_ai_summarize_message()` — rolling map-reduce orchestrator
- Added `_ai_truncate_or_fallback()` — try AI, catch errors, fall back to plain text
- Made `_truncate_oversized_messages()` async (needed for `await` on LLM calls)
- Made `chat_manager._preprocess_messages()` async (both callers are already in async context)
- Prepends `[AI SUMMARY - Original: X chars, reduced to: Y chars]` marker
- 46 new tests in `tests/test_ai_single_message_summarization.py`
- Full suite: 460 passed, 1 skipped

**Summarization prompt template**:

```
You are a summarization assistant. Your task is to condense the provided content
while preserving ALL key facts, data points, names, numbers, and actionable details.

The main conversation uses this system context:
---
{system_prompt}
---

RULES:
- Your summary MUST be under {target_size} characters
- Preserve all specific data: names, numbers, URLs, error messages, IDs
- Preserve the structure (lists, key-value pairs) where possible
- If the content is a tool/API response, keep the important result data
- Omit redundant context, boilerplate, and verbose formatting

Content to summarize:
{content}
```

**Token budget analysis for `summarization_chunk_size = 200,000`**:

| Component                  | Chars        | Approx Tokens |
| -------------------------- | ------------ | ------------- |
| Previous summary (input)   | ≤200,000     | ≤50,000       |
| Next chunk (input)         | ≤200,000     | ≤50,000       |
| System prompt overhead     | ~500         | ~125          |
| **Total input**            | **~400,500** | **~100,125**  |
| Output (summary)           | ≤200,000     | ≤50,000       |
| **Total (input + output)** | **~600,500** | **~150,125**  |

This fits within Claude's 200k token window. For models with ≤128k token context (Llama 3, GPT), users should reduce `summarization_chunk_size` to `100,000` via config.

---

#### Task 3.5: AI-Based History Summarization

**Status**: ✅ Complete
**Priority**: P1 (Important)
**Estimated Effort**: 1 day
**Depends on**: Task 3.3, 3.4

**Description**: Implement Step 1 of history truncation for the AI-enabled path — summarize all middle-zone messages into one.

**Subtasks**:

- [x] In `_truncate_history_total()`, if AI summarization enabled, take different Step 1 path
- [x] Collect all middle-zone messages
- [x] Format them as a conversation transcript for the summarizer (`_format_conversation_transcript`)
- [x] If total middle-zone content ≤ `summarization_chunk_size`, summarize in one call
- [x] If total middle-zone content > `summarization_chunk_size`, use rolling map-reduce (same as single-message)
- [x] Replace entire middle zone with single assistant message: `{"role": "assistant", "content": "[CONVERSATION SUMMARY]\n{summary}"}`
- [x] Skip Step 2 (middle-zone wipe) when AI summarization is enabled since Step 1 already condensed
- [x] Log: number of messages summarized, before/after sizes
- [x] Made `_truncate_history_total()` and `_run_history_truncation()` async
- [x] Updated `preprocess_messages()` to `await _truncate_history_total()`
- [x] Fall back to plain-text Step 1 on AI failure (`_ai_summarize_history_or_fallback`)
- [x] Updated 31 existing history-total tests for async
- [x] 34 new tests in `tests/test_ai_history_summarization.py`
- [x] Full suite: **494 passed, 1 skipped**

**Summarization prompt for conversation history**:

```
You are a summarization assistant. Summarize this conversation history
while preserving all key outcomes, decisions, tool results, and context
that would be needed to continue the conversation.

The conversation's system context:
---
{system_prompt}
---

RULES:
- Your summary MUST be under {target_size} characters
- Preserve: tool call results, key data retrieved, decisions made, user preferences
- Preserve: the flow of what was discussed and what was accomplished
- Format as a concise narrative, not a full transcript

Conversation to summarize:
{conversation_transcript}
```

---

#### Task 3.6: Deprecate Old Tool Result Truncation Settings

**Status**: ✅ Complete
**Priority**: P1 (Important)
**Estimated Effort**: 0.5 day
**Depends on**: Task 3.2, 3.3

**Description**: Remove the 4 legacy `tool_result_*` config settings from `config.py`. These are superseded by the new generalized `message_truncation_*` and `history_*` settings which apply to ALL message types (not just tool results).

**Settings removed from `config.py`**:

- [x] `tool_result_new_response_threshold` (env: `BEDROCK_TOOL_RESULT_NEW_RESPONSE_THRESHOLD`)
- [x] `tool_result_new_response_target` (env: `BEDROCK_TOOL_RESULT_NEW_RESPONSE_TARGET`)
- [x] `tool_result_history_threshold` (env: `BEDROCK_TOOL_RESULT_HISTORY_THRESHOLD`)
- [x] `tool_result_history_target` (env: `BEDROCK_TOOL_RESULT_HISTORY_TARGET`)

**Files updated**:

- [x] `config.py` — Removed the 4 fields + their env var aliases; added migration comment block
- [x] `.env.example` — Replaced "Tool Result Truncation" section with "Message Truncation (Unified System)"
- [x] `message_preprocessor.py` — `__init__` derives thresholds from config; legacy params accepted only when config=None
- [x] `plugin.py` — Simplified constructors: `MessagePreprocessor(config=)`, `MessageChunker` uses config fields
- [x] `parsers/base.py` — `config.tool_result_new_response_threshold` → `config.message_truncation_threshold`
- [x] `workload_analyzer/config.py` — Renamed 4 fields to `bedrock_message_truncation_*` / `bedrock_history_msg_*`
- [x] `workload_analyzer/main.py` — Updated `load_config()` to use new setting names
- [x] `tests/test_multi_tool_calls.py` — All config fixtures use alias names; all assertions use new fields
- [x] `tests/test_truncation_configuration.py` — Updated defaults & env var assertions
- [x] `tests/test_conversation_management.py` — Updated field references
- [x] `tests/test_basic.py` — Updated plugin config assertions
- [x] `tests/test_single_message_truncation.py` — Updated pipeline test to use config thresholds
- [x] `docs/TRUNCATION_QUICK_REF.md` — Fully rewritten with new settings and migration table

**Mapping (old → new)**:
| Old Setting | New Setting | Notes |
|-------------|-------------|-------|
| `tool_result_new_response_threshold` | `message_truncation_threshold` | Now applies to ALL messages, not just tool results |
| `tool_result_new_response_target` | `message_truncation_target` | Same |
| `tool_result_history_threshold` | `history_msg_threshold` | Per-message threshold during history truncation |
| `tool_result_history_target` | `history_msg_target` | Same |

---

### Phase 4: Integration — Wire Everything Together

#### Task 4.1: Update `plugin.py`

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 0.5 day
**Depends on**: Phase 2, Task 3.1
**Completed**: Plugin wiring + 5 new tests

**Subtasks**:

- [x] Import `ChatManager`, `MessagePreprocessor`, `ConversationManager`, `MessageChunker`
- [x] Instantiate `ConversationManager` with `max_conversation_messages`, `conversation_strategy` from config
- [x] Instantiate `MessagePreprocessor` with config + tool result thresholds
- [x] Instantiate `MessageChunker` with config values
- [x] Instantiate `ChatManager` with `llm_client=bedrock_client`, `config`, `message_preprocessor`, `conversation_manager`, `message_chunker`
- [x] Pass `ChatManager` to `WebSocketChatHandler` via new optional `chat_manager` parameter
- [x] Keep `BedrockClient` accessible for embedding API and health checks
- [x] Update `WebSocketChatHandler.__init__` to accept optional `chat_manager: Any = None`
- [x] Add 5 tests verifying component instantiation, wiring, and config propagation
- [x] Full suite: 287 passed, 1 skipped, 0 failed

**Implementation Notes**:

- `WebSocketChatHandler` now accepts `chat_manager` as optional kwarg (backward compatible)
- All orchestration components are public attributes on `BedrockChatPlugin` for advanced usage
- `bedrock_client` still passed to `WebSocketChatHandler` — direct usage migrated in Task 4.2

---

#### Task 4.2: Refactor `websocket_handler.py`

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 1-2 days
**Depends on**: Task 4.1
**Completed**: Chat delegation + 11 new tests

**Description**: Replace direct bedrock_client usage with chat_manager delegation. The websocket handler becomes a pure transport/session orchestrator.

**Subtasks**:

- [x] Add `chat_manager: ChatManager` parameter to `WebSocketChatHandler.__init__` (done in 4.1)
- [x] Remove direct `bedrock_client` dependency (only 1 reference remains: `generate_embedding` for RAG)
- [x] Refactor `_handle_chat_message()`:
  - [x] Build raw message dicts from session context (unchanged)
  - [x] Define `_tool_executor` closure wrapping `_execute_tool_calls`
  - [x] Define `_on_tool_progress` closure wrapping `_send_message`
  - [x] Call `chat_manager.chat_completion()` with these callables + bedrock_params
  - [x] Sync intermediate tool-loop messages to session (scan after last user msg in result.messages)
  - [x] Send `result.response` to WebSocket client
- [x] Remove `_handle_tool_calls_recursively()` method (~120 lines, moved to ChatManager.\_run_tool_call_loop)
- [x] Keep: `_execute_tool_calls`, `_execute_single_tool_call` (tool HTTP execution)
- [x] Keep: `_execute_tool_calls_with_progress` (retained for potential direct use)
- [x] Keep: All auth, ping, history, clear, logout handlers
- [x] Keep: RAG retrieval methods (`_retrieve_kb_context`, `_format_kb_context`)
- [x] Keep: Statistics, error handling, WebSocket send utilities
- [x] Decision: RAG context injection stays in websocket_handler (before calling chat_manager) — keeps vector_db concerns out of ChatManager
- [x] 11 new tests in `test_websocket_chat_delegation.py`: delegation, session sync, tool rounds, error handling
- [x] Full suite: 298 passed, 1 skipped, 0 failed

**Implementation Notes**:

- `websocket_handler.py` reduced from 1105 → 1014 lines (~91 lines removed)
- `bedrock_client` only used for `generate_embedding()` in RAG path
- Session sync strategy: after chat_completion, find last user msg in result.messages; everything after it = new tool-loop messages → add to session
- `_handle_tool_calls_recursively` fully replaced by `ChatManager._run_tool_call_loop`

---

#### Task 4.3: Update `__init__.py` and Exports

**Status**: ✅ Complete
**Priority**: P1 (Important)
**Estimated Effort**: 0.5 hour
**Depends on**: Task 4.1
**Completed**: Already done during Tasks 2.2 and 3.1

**Subtasks**:

- [x] Add `ChatManager` to exports (done in Task 2.2)
- [x] Add `MessagePreprocessor` to exports (done in Task 3.1)
- [x] Add `ChatCompletionResult` to exports (done in Task 1.3)
- [x] Keep `BedrockClient` in exports (for embedding/advanced usage)
- [x] Add `ContextWindowExceededError` to exception exports (done in Task 1.2)

---

#### Task 4.4: Update Remaining Import References

**Status**: ✅ Complete
**Priority**: P1 (Important)
**Estimated Effort**: 0.5 day
**Depends on**: Task 4.2

**Subtasks**:

- [x] Search all files for `tool_message_processor` imports → update to `message_preprocessor`
- [x] Search all files for `ToolMessageProcessor` class references → update to `MessagePreprocessor`
- [x] Search for direct `bedrock_client` imports outside allowed files
- [x] Update `message_chunker.py` imports if it references `tool_message_processor`
- [x] Update `examples/` if they reference changed modules (none found — already clean)
- [x] Verify no circular imports

**Changes Made**:

- Renamed `ToolMessageProcessor` → `MessagePreprocessor` in 3 test files (22 occurrences): `test_chat_manager.py`, `test_multi_tool_calls.py`, `test_conversation_management.py`
- Updated `message_chunker.py` docstring param reference
- `commands/kb.py` imports `BedrockClient` for embedding pipeline — legitimate, not chat orchestration
- No circular imports confirmed
- 298 tests pass, 1 skipped

---

### Phase 5: Testing + Migration Validation

#### Task 5.1: Migrate Existing Tests

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 1-2 days
**Depends on**: Phase 4

**Subtasks**:

- [x] Update `test_tool_message_processor.py` → `test_message_preprocessor.py` (N/A — file never existed separately; tests already in `test_multi_tool_calls.py` and `test_chat_manager.py`)
- [x] Update bedrock_client tests to reflect slimmed-down API (already correct — `TestBedrockClient` tests init + health only)
- [x] Update websocket_handler tests to use chat_manager (fixed 2 stale tests in `test_websocket_authentication.py` — replaced `_handle_tool_calls_recursively` + direct `bedrock_client.chat_completion` mocking with `chat_manager.chat_completion` returning `ChatCompletionResult`)
- [x] Update plugin tests to verify new wiring (already done in Task 4.1 — 5 tests in `test_basic.py`)
- [x] Run full test suite — 298 passed, 1 skipped
- [x] Fix any import/path breakages (renamed misleading `bedrock_client` variable → `conv_manager` in `test_conversation_management.py`)

**Changes Made**:

- `test_websocket_authentication.py`: Updated `test_chat_allowed_when_require_auth_and_authenticated` and `test_chat_allowed_when_require_auth_false` to mock `chat_manager.chat_completion` instead of removed `_handle_tool_calls_recursively`
- `test_conversation_management.py`: Renamed `bedrock_client` fixture/variable → `conv_manager` for clarity (was actually `ConversationManager`)
- Zero remaining references to `_handle_tool_calls_recursively` or `ToolMessageProcessor` in tests

---

#### Task 5.2: New Unit Tests — `chat_manager.py`

**Status**: ✅ Complete
**Priority**: P0 (Blocker)
**Estimated Effort**: 1 day
**Depends on**: Task 5.1

**Subtasks**:

- [x] Test basic chat completion (no tools) — `TestChatCompletionHappyPath` (7 tests)
- [x] Test tool call loop (1 round, 2 rounds, max rounds) — `TestToolCallLoop` + `TestToolCallLoopEdgeCases`
- [x] Test tool executor is called correctly — `test_single_round_tool_call`, `test_multiple_tools_in_single_round`
- [x] Test on_tool_progress callback is invoked — `test_on_tool_progress_called`, `_default_message_when_content_none`, `_none_is_fine`
- [x] Test ContextWindowExceededError recovery — `TestContextWindowRecovery` + `test_context_window_error_during_tool_loop`
- [x] Test fallback model logic — `TestFallbackModel` (4 tests)
- [x] Test graceful degradation — `TestGracefulDegradation` (4 tests) + `test_bedrock_error_with_degradation`
- [x] Test chat_completion without tool_executor (tools disabled) — `test_no_tool_executor_returns_immediately`
- [x] Test chat_completion without on_tool_progress (no WebSocket) — `test_on_tool_progress_none_is_fine`
- [x] Test ChatCompletionResult structure — `TestChatCompletionResultStructure` (2 tests)

**New tests added (14)**:

- `TestPreprocessingMetadata`: chunking sets `preprocessing_applied`, stays false when no chunking
- `TestToolExecutorErrors`: tool executor exception propagates, context-window error during tool loop triggers graceful degradation
- `TestNoToolCallMetadata`: `tool_call_rounds` absent without tools, `final_message_count` always set, `tool_results` empty list
- `TestBedrockClientErrorPropagation`: BedrockClientError without fallback raises; with degradation returns apology
- `TestChatCompletionResultStructure`: result shape with tool calls, messages include tool history
- `TestToolCallLoopEdgeCases`: empty tool results, `tools_desc=None` still runs loop, `max_rounds=1` enforced

**Coverage**: `chat_manager.py` at **100%** (129/129 statements). Total suite: **312 passed, 1 skipped**.

---

#### Task 5.3: New Unit Tests — AI Summarization

**Status**: ✅ Complete
**Priority**: P1 (Important)
**Estimated Effort**: 1 day
**Depends on**: Task 5.1

**Subtasks**:

- [x] Test single-message AI summarization (mock LLM)
- [x] Test rolling map-reduce with multiple chunks
- [x] Test fallback to plain text when AI fails
- [x] Test history summarization (middle-zone compression)
- [x] Test conversation transcript formatting
- [x] Test recursive halving safety net
- [x] Test with AI summarization disabled (plain text paths)
- [x] Test `summarization_chunk_size` splitting logic
- [x] Test that system prompt is passed to summarizer

**Implementation**: `tests/test_ai_summarization_comprehensive.py` — 85 new tests covering:

- `split_into_chunks` boundary priority (paragraph > line > sentence > word), edge cases
- `_summarize_with_llm` max_tokens floor (1024), exact prompt format, None response
- `_ai_summarize_message` first-chunk failure, chunk target_size, many chunks, iteration context
- `_ai_truncate_or_fallback` list/dict content, whitespace-only summary, marker format
- `_format_conversation_transcript` 500-char cap, empty tool_calls, missing role
- `_detect_zones` direct tests (no system prompt, no user msg, tool messages)
- `_wipe_middle_zone`, `_total_messages_size`, `_history_step_truncate_zone` direct tests
- `_truncate_message_content` all 4 dispatch branches (str, list, dict, unknown)
- `_truncate_list_content_items` proportional budget allocation
- `_item_content_size` direct tests
- `_truncate_plain_text` output format (head/tail/recommendation)
- Recursive halving depth tracking, max-recursion logging, chunk_size not halved
- History single-call vs map-reduce boundary, target_size calculation, prompt rules
- AI Step 1 → Step 3 flow, boundary conditions (exact threshold)
- System prompt end-to-end through `preprocess_messages`
- Total: **579 passed, 1 skipped** (85 new + 494 existing)

---

#### Task 5.4: Integration Testing

**Status**: ⬜ Not Started
**Priority**: P1 (Important)
**Estimated Effort**: 0.5-1 day
**Depends on**: Task 5.2, 5.3

**Subtasks**:

- [ ] End-to-end test: WebSocket message → chat_manager → mock LLM → response
- [ ] End-to-end test with tool calls
- [ ] Test with RAG context injection
- [ ] Test session history update flow
- [ ] Verify backward compatibility with existing client behavior

---

## 📁 File Inventory

### New Files

| File                                                | Phase   | Description                          |
| --------------------------------------------------- | ------- | ------------------------------------ |
| `auto_bedrock_chat_fastapi/chat_manager.py`         | Phase 2 | Orchestration layer                  |
| `auto_bedrock_chat_fastapi/message_preprocessor.py` | Phase 3 | Renamed + enhanced message processor |
| `tests/test_chat_manager.py`                        | Phase 5 | Chat manager tests                   |
| `tests/test_message_preprocessor.py`                | Phase 5 | Preprocessor tests (migrated + new)  |

### Modified Files

| File                           | Phase      | Changes                                                                   |
| ------------------------------ | ---------- | ------------------------------------------------------------------------- |
| `config.py`                    | Phase 1, 3 | New settings (9 fields + validators); remove 4 old `tool_result_*` fields |
| `exceptions.py`                | Phase 1    | Add `ContextWindowExceededError`                                          |
| `bedrock_client.py`            | Phase 2    | Remove orchestration, slim to pure transport                              |
| `plugin.py`                    | Phase 4    | Instantiate ChatManager, update wiring                                    |
| `websocket_handler.py`         | Phase 4    | Use ChatManager, remove tool recursion                                    |
| `__init__.py`                  | Phase 4    | Update exports                                                            |
| `tool_manager.py`              | Phase 6    | New — ToolManager + AuthInfo (tool gen caching + execution)               |
| `message_chunker.py`           | Phase 3-4  | Remove old tool_result params, use new config settings                    |
| `parsers/base.py`              | Phase 3    | Update threshold reference to new config field                            |
| `workload_analyzer/config.py`  | Phase 3    | Remove/update old `bedrock_tool_result_*` fields                          |
| `workload_analyzer/main.py`    | Phase 3    | Update config field references                                            |
| `.env.example`                 | Phase 3    | Remove old Tool Result Truncation env vars                                |
| `docs/TRUNCATION_QUICK_REF.md` | Phase 3    | Update documentation for new settings                                     |

### Deprecated Files

| File                        | Action                             | Notes                                        |
| --------------------------- | ---------------------------------- | -------------------------------------------- |
| `tool_message_processor.py` | Rename → `message_preprocessor.py` | Keep backward-compat import alias (optional) |

---

## 🔄 Migration Strategy

### Approach: Incremental Refactoring

Each phase should be independently testable. The migration order ensures that:

1. **Phase 1** (Foundation) creates the scaffolding without breaking anything
2. **Phase 2** (bedrock_client + chat_manager) can be tested with a temporary shim: `websocket_handler` calls `chat_manager`, which calls the slimmed `bedrock_client`. Both old and new paths coexist briefly.
3. **Phase 3** (message_preprocessor) is additive — new class replaces old class with same interface
4. **Phase 4** (Integration) flips the switch — `websocket_handler` now uses `chat_manager` exclusively
5. **Phase 5** (Testing) validates everything end-to-end

### Rollback Plan

- Each phase is a separate git branch / set of commits
- Phase 4 is the "point of no return" — all prior phases can be reverted independently
- If Phase 4 has issues, revert to pre-Phase-4 and debug

---

## 📝 Notes & Decisions

### Decision: `summarization_chunk_size = 200,000`

**Rationale**: At 200k chars (~50k tokens), the summarizer input is ~100k tokens (previous summary + chunk) with ~50k tokens output budget. This fits Claude's 200k token window and minimizes LLM calls. For a 1M char message: 5 chunks → 5 LLM calls with 200k chunks vs. 10 calls with 100k chunks — 50% fewer calls.
**Tradeoff**: Models with ≤128k token context (Llama 3, GPT) may overflow. Users should reduce via config.
**Mitigation**: The setting is configurable. Document the model-specific recommendation.

### Decision: New `chat_manager.py` vs. reuse `conversation_manager.py`

**Rationale**: `ConversationManager` is a stateless utility class for count-based message trimming. `ChatManager` is an orchestrator that calls LLM clients, handles tool loops, coordinates preprocessing. Mixing these responsibilities would violate SRP and make the class unwieldy.
**Result**: Keep `conversation_manager.py` as utilities, create new `chat_manager.py`.

### Decision: Tool executor injection (callable) vs. direct WebSocket access

**Rationale**: Passing `websocket` to `ChatManager` would couple it to WebSocket transport. Using an injected `tool_executor` callable keeps ChatManager transport-agnostic — it could work with REST, CLI, or batch processing.
**Result**: `tool_executor: Callable` and `on_tool_progress: Callable` parameters.

### Decision: RAG context injection stays in `websocket_handler.py`

**Rationale**: RAG depends on `VectorDB`, `generate_embedding()`, and domain-specific formatting. Moving it to ChatManager would introduce dependencies on embedding models and vector databases into the orchestration layer. Better to inject the enhanced system prompt from the handler.
**Result**: `websocket_handler` injects KB context into messages before calling `chat_manager.chat_completion()`.

### Note: `message_chunker.py` disposition

The `MessageChunker` currently handles splitting oversized messages into multiple smaller messages. This is conceptually related to `MessagePreprocessor`. Two options:

1. **Merge** `MessageChunker` into `MessagePreprocessor` (less files, unified preprocessing)
2. **Keep separate** and call from `MessagePreprocessor.preprocess_messages()` (more modular)

**Recommendation**: Keep separate for now, call from `preprocess_messages()`. Can merge later if desired.

---

### Phase 6: ToolManager Extraction

**Goal**: Extract tool orchestration (generation + execution) from `websocket_handler.py` into a dedicated `ToolManager` class. Makes tool handling reusable, testable, and decoupled from WebSocket transport.

#### Architecture

```
Before:
  websocket_handler
    ├── tools_generator.generate_tools_desc()   ← called every message (wasteful)
    ├── _execute_tool_calls(tool_calls, session) ← closure, coupled to session
    └── chat_manager.chat_completion(tools_desc=..., tool_executor=..., on_tool_progress=...)

After:
  ToolManager (owns ToolsGenerator + tool execution)
    ├── __init__(tools_generator, http_client, base_url, config)
    ├── tools_desc (property, cached at init)
    └── execute_tool_calls(tool_calls, auth_info=None) → list[dict]

  ChatManager.__init__(..., tool_manager: Optional[ToolManager])
  ChatManager.chat_completion(messages, auth_info=None, on_progress=None, **llm_params)
    └── uses self.tool_manager internally (no more tools_desc/tool_executor params)

  websocket_handler
    └── chat_manager.chat_completion(messages, auth_info=session_auth, on_progress=ws_callback, **llm_params)
```

#### Task 6.1 — Create `tool_manager.py` ✅

- [x] Create `ToolManager` class in `auto_bedrock_chat_fastapi/tool_manager.py`
- [x] Constructor: `__init__(self, tools_generator, http_client, base_url, config)`
- [x] `tools_desc` property: call `tools_generator.generate_tools_desc()` once at init, cache result
- [x] Move `_execute_tool_calls()` from `websocket_handler.py` into `ToolManager.execute_tool_calls()`
- [x] Move `_execute_single_tool_call()` from `websocket_handler.py` into `ToolManager._execute_single_tool_call()`
- [x] Replace `session` parameter with `auth_info: Optional[Dict]` containing only auth values (auth_type, headers, credentials)
- [x] Move `_execute_tool_calls_with_progress()` logic — progress callbacks injected via callable param, not WebSocket reference
- [x] Create `AuthInfo` dataclass or TypedDict for clean auth passing

#### Task 6.2 — Update `ChatManager` to own `ToolManager` ✅

- [x] Add `tool_manager: Optional[ToolManager] = None` to `ChatManager.__init__()`
- [x] Remove `tools_desc`, `tool_executor` params from `chat_completion()` signature
- [x] Add `auth_info: Optional[AuthInfo] = None` and `on_progress: Optional[Callable] = None` params to `chat_completion()`
- [x] `_call_llm_with_recovery()` uses `self.tool_manager.tools_desc` instead of receiving `tools_desc` param
- [x] `_run_tool_call_loop()` calls `self.tool_manager.execute_tool_calls(tool_calls, auth_info=auth_info)` instead of injected `tool_executor`
- [x] `_run_tool_call_loop()` uses `on_progress` callback instead of `on_tool_progress`
- [x] When `tool_manager is None`, tool calls are disabled (no tools_desc sent, no loop)
- [x] Updated `websocket_handler.py` caller to pass `auth_info` + `on_progress` (part of 6.2)
- [x] Updated `plugin.py` to create `ToolManager` and inject into `ChatManager` (part of 6.2)
- [x] Updated `test_chat_manager.py` — all 62 tests migrated to tool_manager pattern
- [x] Updated `test_websocket_chat_delegation.py` — delegation assertions updated
- [x] 312 passed, 1 skipped. `chat_manager.py` at 100% coverage.

#### Task 6.3 — Update `websocket_handler.py` callers ✅

- [x] Remove per-message `tools_generator.generate_tools_desc()` call (done in 6.2)
- [x] Build `auth_info` dict from `session.credentials` / `session.auth_handler` (done in 6.2)
- [x] Simplify `_handle_chat_message()` call (done in 6.2)
- [x] Remove `_execute_tool_calls()`, `_execute_single_tool_call()`, `_execute_tool_calls_with_progress()` from `WebSocketChatHandler`
- [x] Remove `tools_generator` from `WebSocketChatHandler.__init__()` (it lives in ToolManager now)
- [x] Remove `ToolsGenerator` import from `websocket_handler.py`
- [x] Update `get_statistics()` to use `tool_manager.get_statistics()`
- [x] Remove `_total_tool_calls_executed` counter (now tracked by ToolManager)
- [x] Update `plugin.py` — remove `tools_generator` from `WebSocketChatHandler` constructor call
- [x] Update `test_websocket_chat_delegation.py` — remove `tools_generator=MagicMock()` from handler fixture
- [x] Update `test_websocket_authentication.py` — remove all `tools_generator` references and `ToolsGenerator` patches
- [x] Remove `TestToolCallAuthentication` class (3 tests) — functionality moved to ToolManager (Task 6.5)
- [x] 309 passed, 1 skipped (3 tests moved to ToolManager scope)

#### Task 6.4 — Update `plugin.py` wiring ✅

- [x] Create `ToolManager(tools_generator, http_client, base_url, config)` in plugin setup (done in 6.2)
- [x] Pass `tool_manager` to `ChatManager` constructor (done in 6.2)
- [x] Verify tools are generated once at startup, not per-message (done in 6.2)
- [x] Remove `tools_generator` from `WebSocketChatHandler` constructor (done in 6.3)

#### Task 6.5 — Tests ✅

- [x] Unit tests for `ToolManager` — 38 tests in `test_tool_manager.py` (97% coverage)
  - AuthInfo: 8 tests (is_authenticated for all auth types)
  - Init/caching: 4 tests (cached at init, not regenerated, refresh, base_url)
  - Validation: 3 tests (unknown tool, invalid args, valid call)
  - Auth: 4 tests (bearer applied, no auth, unauthenticated skipped, auth failure)
  - Behavior: 5 tests (progress callback, multiple progress, max cap, statistics, exception, independence)
  - HTTP dispatch: 14 tests (GET/POST/PUT/PATCH/DELETE, path params, error responses, timeout, non-JSON, unsupported method)
- [x] Update `test_chat_manager.py` — remove `tool_executor`/`tools_desc` params, use `tool_manager` mock (done in 6.2)
- [x] Update `test_websocket_chat_delegation.py` — verify delegation through tool_manager (done in 6.2/6.3)
- [x] Update `test_websocket_authentication.py` — remove `tools_generator` from constructor calls (done in 6.3)
- [x] 347 passed, 1 skipped

#### Task 6.6 — Service-agnostic cleanup pass ✅

- [x] Audit remaining `bedrock`/`Bedrock` references in non-Bedrock-specific files
- [x] Renamed `bedrock_client` → `llm_client` in `websocket_handler.py` (param, attribute, type hint)
- [x] Removed `from .bedrock_client import BedrockClient` from `websocket_handler.py`
- [x] Updated `plugin.py` wiring: `llm_client=self.bedrock_client` in WebSocketChatHandler constructor
- [x] Updated `chat_manager.py` docstrings: "LLM client" instead of "BedrockClient" / "bedrock_client"
- [x] Updated `test_websocket_authentication.py`: removed all `BedrockClient` patches and `bedrock_client=` params
- [x] Updated `test_websocket_chat_delegation.py`: renamed `bedrock_client` → `llm_client` in fixture + assertions
- [x] `chat_manager.py`, `tool_manager.py`, `websocket_handler.py` are fully service-agnostic
- [x] 347 passed, 1 skipped

---

### Phase 7 — Remove ConversationManager & MessageChunker ✅

**Goal**: Eliminate `ConversationManager` and `MessageChunker` entirely. All preprocessing is now handled by `MessagePreprocessor.preprocess_messages()`.

**Analysis summary**: Deep analysis confirmed both classes are fully redundant — every capability (sliding-window trimming, orphaned tool cleanup, message chunking) is already covered by the `MessagePreprocessor` pipeline. One gap identified: orphaned tool results after `_wipe_middle_zone` in history truncation needed a new cleanup step.

#### Task 7.1 — Add orphaned tool result cleanup to MessagePreprocessor ✅

- [x] Added `_cleanup_orphaned_tool_results()` static method to `MessagePreprocessor`
- [x] Handles pre-formatted format (`tool_calls[].id` ↔ `tool_results[].tool_call_id`)
- [x] Handles Bedrock/Claude format (`content[type=tool_use].id` ↔ `content[type=tool_result].tool_use_id`)
- [x] Wired as Step 4 in `preprocess_messages()` after `_truncate_history_total()`

#### Task 7.2 — Simplify ChatManager ✅

- [x] Removed `ConversationManager` and `MessageChunker` imports
- [x] Simplified constructor: `__init__(self, llm_client, config, tool_manager=None)` — removed `conversation_manager` and `message_chunker` params
- [x] Rewrote `_preprocess_messages` to delegate to `self.message_preprocessor.preprocess_messages()`
- [x] Deleted `_trim_conversation` and `_cleanup_orphaned_tools` methods
- [x] Updated all docstrings (pipeline: "1. Message preprocessing, 2. Format for LLM, 3. LLM call")

#### Task 7.3 — Update plugin.py wiring ✅

- [x] Removed `ConversationManager` and `MessageChunker` imports and instantiation
- [x] Simplified ChatManager constructor: `ChatManager(llm_client=..., config=..., tool_manager=...)`

#### Task 7.4 — Delete source files ✅

- [x] `git rm -f auto_bedrock_chat_fastapi/conversation_manager.py`
- [x] `git rm -f auto_bedrock_chat_fastapi/message_chunker.py`

#### Task 7.5 — Update all test files ✅

- [x] `test_chat_manager.py` — Removed 24 locations: imports, fixtures, constructor params, entire `TestConversationTrimming` class (4 tests), rewrote pipeline ordering tests, preprocessing metadata test
- [x] `test_basic.py` — Removed imports, assertions, and entire `test_plugin_conversation_manager_uses_config` test
- [x] `test_single_message_truncation.py` — Removed inline `ConversationManager`/`MessageChunker` in `TestChatManagerPreprocessWiring`; fixed `test_pipeline_tool_result_then_general` to include matching `tool_use` assistant message for orphan cleanup
- [x] `test_history_total_truncation.py` — Removed inline `ConversationManager`/`MessageChunker` in `TestChatManagerHistoryWiring`
- [x] `git rm -f tests/test_conversation_management.py` (entire file tested removed classes)
- [x] 539 passed, 1 skipped

#### Task 7.6 — Extract defaults.py ✅

- [x] Created `auto_bedrock_chat_fastapi/defaults.py` — single source of truth for all numeric thresholds, targets, strategies, and magic numbers
- [x] Updated `config.py` — all `Field(default=...)` values and validator strategy sets now import from `defaults.py`
- [x] Updated `message_preprocessor.py` — all `getattr` fallbacks, legacy no-config fallbacks, truncation ratios, summarization params, and magic numbers now import from `defaults.py`
- [x] Fixed legacy inconsistency: no-config fallbacks (50K/42.5K) now aligned to config defaults (100K/85K) via shared constants
- [x] Constants extracted: 35 values covering tool limits, conversation history, chunking, LLM client types, AI summarization, truncation thresholds/ratios, transcript formatting, network/session, and error handling
- [x] 539 passed, 1 skipped

---

## 📈 Risk Register

| Risk                                                 | Impact | Likelihood | Mitigation                                                               |
| ---------------------------------------------------- | ------ | ---------- | ------------------------------------------------------------------------ |
| AI summarization produces poor summaries             | Medium | Medium     | Fallback to plain text; configurable; can disable                        |
| Summarization LLM calls add latency                  | Medium | High       | Opt-in only; chunk size tuned to minimize calls                          |
| Circular imports after restructuring                 | Low    | Medium     | Careful import order; test early                                         |
| Existing tests break due to interface changes        | Medium | High       | Phase 5 dedicated to migration; incremental phases                       |
| Context window overflow for non-Claude summarization | Medium | Low        | Configurable chunk size; documented recommendations                      |
| Tool executor injection pattern too complex          | Low    | Low        | Clean callable interface; well-documented                                |
| ToolManager extraction breaks tool auth flow         | Medium | Medium     | Phase 6 — incremental tasks; auth_info dataclass; thorough test coverage |

---

## 📅 Progress Log

| Date       | Phase    | Update                                                                                                                                                                                                                    |
| ---------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-02-27 | Pre-work | Architecture design and implementation tracker created                                                                                                                                                                    |
| 2026-03-03 | Phase 7  | Removed ConversationManager & MessageChunker; all preprocessing consolidated into MessagePreprocessor; added \_cleanup_orphaned_tool_results; 539 passed                                                                  |
| 2026-03-03 | Task 7.6 | Extracted defaults.py; removed 6 orphaned config fields (conversation_strategy, max_message_size, chunk_size, chunking_strategy, chunk_overlap, enable_message_chunking) + validators + load_config overrides; 539 passed |
