# Bedrock Client Refactoring Plan

> **Document Created:** December 9, 2025  
> **Original File Size:** 2,151 lines  
> **Current File Size:** 1,139 lines (after Proposals 1 & 2)
> **Target File Size:** ~500 lines (after full refactoring)

## Overview

The `bedrock_client.py` file has grown organically and now violates the Single Responsibility Principle. This document tracks the refactoring plan to make it more maintainable.

---

## Current State Analysis

### Responsibility Breakdown

| Responsibility | Lines (~approx) | Complexity |
|----------------|-----------------|------------|
| AWS Client Initialization | ~60 | Low |
| Rate Limiting | ~20 | Low |
| Chat Completion (main flow) | ~150 | High |
| Retry Logic with Fallback | ~200 | High |
| **Conversation Management** | ~400 | Very High |
| **Tool Message Truncation** | ~350 | Very High |
| **Message Chunking** | ~250 | Medium |
| Response Parsing (delegation) | ~100 | Low |
| Logging/Debug Helpers | ~100 | Low |
| Error Handling | ~100 | Medium |

### Key Problems Identified

1. **Single Responsibility Violation** - The class handles AWS client, conversation management, truncation, chunking, AND retry logic
2. **Duplicated Logic** - `is_tool_msg()` helper is defined **inside** `_truncate_tool_messages_in_history` but needed elsewhere
3. **Similar Code in 3 Strategies** - `_truncate_messages`, `_sliding_window_messages`, `_smart_prune_messages` have ~80% identical code for orphan detection
4. **Hard to Test** - Large methods with many branches make unit testing difficult
5. **Debug Logging Noise** - Same logging pattern repeated 3+ times in `chat_completion`

---

## Refactoring Proposals

### Proposal 1: Extract `ConversationManager` Class

**Priority:** HIGH  
**Status:** [x] COMPLETED (2025-12-10)  
**Effort:** Medium  
**Impact:** High

**Description:**  
Move all conversation history management to a new file.

**New File:** `auto_bedrock_chat_fastapi/conversation_manager.py` (677 lines)

**Methods Extracted:**
- [x] `_manage_conversation_history()` → `ConversationManager.manage_conversation_history()`
- [x] `_truncate_messages()` → `ConversationManager.truncate_messages()`
- [x] `_sliding_window_messages()` → `ConversationManager.sliding_window_messages()`
- [x] `_smart_prune_messages()` → `ConversationManager.smart_prune_messages()`
- [x] `_remove_orphaned_tool_results()` → `ConversationManager.remove_orphaned_tool_results()`
- [x] `_build_tool_use_location_map()` → `build_tool_use_location_map()` (module-level function)
- [x] `_ensure_tool_pairs_stay_together()` → `ConversationManager._ensure_tool_pairs_stay_together()`
- [x] `_remove_orphaned_tool_results_from_selection()` → `ConversationManager._remove_orphaned_tool_results_from_selection()`
- [x] `_finalize_message_selection()` → `ConversationManager._finalize_message_selection()`

**Changes Made:**
1. Created `conversation_manager.py` with:
   - `build_tool_use_location_map()` - module-level helper function
   - `get_selected_tool_use_ids()` - module-level helper function
   - `is_tool_result_message()` - module-level helper function
   - `ConversationManager` class with all conversation trimming strategies
2. Updated BedrockClient:
   - Added `self._conversation_manager` initialization in `__init__`
   - Replaced 9 methods with thin delegation wrappers (maintaining backward compatibility)
   - bedrock_client.py reduced from 1,586 to 1,139 lines (-447 lines)

**Benefits:**
- Single responsibility per class
- Easier to test conversation strategies independently
- Can add new strategies without touching BedrockClient

---

### Proposal 2: Extract `ToolMessageProcessor` Class

**Priority:** HIGH  
**Status:** [x] COMPLETED (2025-12-09)  
**Effort:** Medium  
**Impact:** High

**Description:**  
Move all tool message processing to a new file.

**New File:** `auto_bedrock_chat_fastapi/tool_message_processor.py` (686 lines)

**Methods Extracted:**
- [x] `_truncate_tool_messages_in_history()` → `ToolMessageProcessor.truncate_tool_messages_in_history()`
- [x] `_process_tool_result_message()` → `ToolMessageProcessor.process_tool_result_message()`
- [x] `_intelligently_truncate_tool_result()` → `ToolMessageProcessor._intelligently_truncate_tool_result()`
- [x] `_truncate_plain_text()` → `ToolMessageProcessor._truncate_plain_text()`
- [x] `is_tool_msg()` → `is_tool_message()` (module-level function, reusable)
- [x] `get_content_size()` (module-level function, reusable)

**Changes Made:**
1. Created `tool_message_processor.py` with:
   - `is_tool_message()` - unified tool detection for all formats (Claude, GPT, Llama, dict)
   - `is_user_message()` - helper function
   - `get_content_size()` - content size calculation
   - `is_assistant_with_tool_use()` - helper function
   - `is_tool_result_message()` - helper function
   - `ToolMessageProcessor` class with all truncation methods
2. Updated BedrockClient to use `ToolMessageProcessor`:
   - Added `self._tool_processor` initialization in `__init__`
   - Replaced `self._truncate_tool_messages_in_history()` call with `self._tool_processor.truncate_tool_messages_in_history()`
   - Replaced `self._process_tool_result_message()` calls with `self._tool_processor.process_tool_result_message()`
   - Removed duplicate `is_tool_msg()` nested functions
3. Deleted from BedrockClient:
   - `_truncate_tool_messages_in_history()` (147 lines)
   - `_process_tool_result_message()` (234 lines)
   - `_intelligently_truncate_tool_result()` (22 lines)
   - `_truncate_plain_text()` (25 lines)
4. Updated test files to use `client._tool_processor.truncate_tool_messages_in_history()`

**Benefits:**
- Centralized tool truncation logic in one place
- `is_tool_message()` now reusable across codebase
- Supports all formats: Claude (list), GPT (role=tool), Llama (is_tool_result flag), dict format
- Group truncation logic for trailing tools preserved
- bedrock_client.py reduced from 2,052 to 1,577 lines (-475 lines)

---

### Proposal 3: Extract `MessageChunker` Class

**Priority:** MEDIUM  
**Status:** [x] COMPLETED (2025-12-10)  
**Effort:** Low  
**Impact:** Medium

**Description:**  
Move chunking logic to a new file.

**New File:** `auto_bedrock_chat_fastapi/message_chunker.py` (372 lines)

**Methods Extracted:**
- [x] `_check_and_chunk_messages()` → `MessageChunker.check_and_chunk_messages()`
- [x] `_chunk_large_message()` → `MessageChunker.chunk_large_message()`
- [x] `_simple_chunk()` → `simple_chunk()` (module-level) + `MessageChunker.simple_chunk()`
- [x] `_context_aware_chunk()` → `context_aware_chunk()` (module-level) + `MessageChunker.context_aware_chunk()`
- [x] `_semantic_chunk()` → `semantic_chunk()` (module-level) + `MessageChunker.semantic_chunk()`

**Changes Made:**
1. Created `message_chunker.py` with:
   - Module-level functions: `simple_chunk()`, `context_aware_chunk()`, `semantic_chunk()`
   - `MessageChunker` class with all chunking methods
   - `_get_content_size()` helper method
2. Updated BedrockClient:
   - Added `self._message_chunker` initialization in `__init__`
   - Replaced 5 methods with thin delegation wrappers
   - bedrock_client.py reduced from 1,139 to 939 lines (-200 lines)

**Benefits:**
- Chunking strategies become pluggable
- Easy to add more sophisticated chunking (NLP-based, etc.)

---

### Proposal 4: Extract `RetryHandler` Class

**Priority:** MEDIUM  
**Status:** [ ] Not Started  
**Effort:** Low  
**Impact:** Medium

**Description:**  
Move retry/fallback logic to a utility.

**New File:** `auto_bedrock_chat_fastapi/retry_handler.py` (~150 lines)

**Methods to Extract:**
- [ ] `_make_request_with_retries()`
- [ ] `_try_request_with_fallback()`
- [ ] `_aggressive_conversation_fallback()`
- [ ] `_calculate_retry_delay()`
- [ ] `_create_error_response()`

**Benefits:**
- Retry logic is reusable for other AWS services
- Easier to adjust backoff strategies

---

### Proposal 5: Consolidate Duplicate Orphan Detection

**Priority:** HIGH  
**Status:** [x] COMPLETED (2025-12-09)  
**Effort:** Low  
**Impact:** High

**Description:**  
The orphan detection logic was repeated with minor variations in three places:
- `_truncate_messages()` (lines 1156-1260)
- `_sliding_window_messages()` (lines 1268-1390)
- `_smart_prune_messages()` (lines 1392-1532)

**Solution Implemented:**  
Created four new helper methods:

1. `_build_tool_use_location_map(messages)` - Builds a mapping of tool_use_id to assistant message index
2. `_ensure_tool_pairs_stay_together(messages, selected_indices, tool_use_locations, strategy_name)` - Iteratively ensures tool_use/tool_result pairs stay together
3. `_remove_orphaned_tool_results_from_selection(messages, selected_indices, strategy_name)` - Removes orphaned tool_results
4. `_finalize_message_selection(messages, selected_indices, tool_use_locations, strategy_name)` - Convenience method combining the above

**Impact:** Reduced ~250 lines of duplicated code across the three strategy methods

**Note:** The helper methods created in this proposal were subsequently moved to `conversation_manager.py` as part of Proposal 1.

---

### Proposal 6: Simplify Debug Logging

**Priority:** LOW  
**Status:** [ ] Not Started  
**Effort:** Low  
**Impact:** Low

**Description:**  
The same logging pattern appears 3 times in `chat_completion()` (lines 127-180).

**Solution:**  
Create a `_log_messages_debug(messages, label)` helper method.

---

### Proposal 7: Create Type Hints and Protocols

**Priority:** LOW  
**Status:** [ ] Not Started  
**Effort:** Low  
**Impact:** Low

**Description:**  
Add typed protocols for message formats to improve IDE support.

```python
from typing import TypedDict, Protocol

class ToolResult(TypedDict):
    tool_call_id: str
    content: str
    
class ToolCall(TypedDict):
    id: str
    name: str
    input: dict
```

---

## Additional Cleanup Tasks

### Task A: Remove `_execute_tool_calls` Placeholder

**Status:** [x] COMPLETED (2025-12-09)

**Location:** Lines 2012-2048 (removed)

**Changes Made:**
1. Removed the `_execute_tool_calls` placeholder method (~40 lines)
2. Removed the call to it in `chat_completion()` method (~4 lines)
3. Added comment explaining that tool execution is handled by WebSocketChatHandler

**Reason:** This method was never actually used. Tool execution is handled in `WebSocketChatHandler._execute_tool_calls()`.

---

### Task B: Remove or Implement `_semantic_chunk`

**Status:** [ ] Not Started

**Location:** Lines 1771-1779

**Reason:** Currently just calls `_context_aware_chunk`, providing no additional value. Either remove or implement actual NLP-based chunking.

---

### Task C: Deduplicate `truncate_tool_results`

**Status:** [x] COMPLETED (2025-12-09)

**Analysis:**  
The `BedrockClient.truncate_tool_results()` method was a thin wrapper that delegated to `parser.truncate_tool_results()`. However, it was **never called** from anywhere in the codebase - it was completely dead code.

**Changes Made:**
- Removed the unused `truncate_tool_results()` method from BedrockClient (~17 lines)
- The canonical implementation in `parsers/base.py` remains unchanged

**Reason:** The method was dead code. Actual tool result truncation happens in `_truncate_tool_messages_in_history()` which uses `_process_tool_result_message()`.

---

### Task D: Extract `_generate_message_preview` to Utility

**Status:** [x] COMPLETED (2025-12-09)

**Analysis:**  
The methods `_generate_message_preview()` and `_format_conversation_summary()` were pure utility functions used only for debug logging. They didn't require any instance state (`self`).

**Changes Made:**
- Converted both methods to module-level functions at the top of `bedrock_client.py`
- Renamed to `generate_message_preview()` and `format_conversation_summary()` (dropped underscore prefix since they're now module-level)
- Updated `_log_conversation_history()` to call the module-level functions
- Added proper docstrings explaining they are logging utilities

**Benefits:**
- Clearer separation of concerns (utilities vs class logic)
- Functions can be imported and reused if needed
- Class interface is cleaner

**Note:** File size increased slightly (+16 lines) due to better documentation, but the class is now smaller and cleaner.

---

## Proposed Final Structure

After completing all refactoring:

```
auto_bedrock_chat_fastapi/
├── bedrock_client.py          # ~500 lines (down from 2151)
│   ├── __init__
│   ├── _initialize_client
│   ├── _get_parser
│   ├── chat_completion        # Orchestration only
│   ├── format_messages_for_bedrock
│   ├── _parse_response
│   ├── _handle_rate_limiting
│   └── health_check
│
├── conversation_manager.py    # 677 lines (NEW - COMPLETED)
│   ├── build_tool_use_location_map (helper)
│   ├── get_selected_tool_use_ids (helper)
│   ├── is_tool_result_message (helper)
│   └── ConversationManager class
│       ├── manage_conversation_history
│       ├── truncate_messages
│       ├── sliding_window_messages
│       ├── smart_prune_messages
│       ├── remove_orphaned_tool_results
│       └── _finalize_message_selection (+ helpers)
│
├── tool_message_processor.py  # 686 lines (NEW - COMPLETED)
│   ├── is_tool_message (helper)
│   ├── is_user_message (helper)
│   ├── get_content_size (helper)
│   ├── is_assistant_with_tool_use (helper)
│   ├── is_tool_result_message (helper)
│   └── ToolMessageProcessor class
│       ├── truncate_tool_messages_in_history
│       ├── process_tool_result_message
│       └── _intelligently_truncate_tool_result
│
├── message_chunker.py         # 372 lines (NEW - COMPLETED)
│   ├── simple_chunk (helper)
│   ├── context_aware_chunk (helper)
│   ├── semantic_chunk (helper)
│   └── MessageChunker class
│       ├── check_and_chunk_messages
│       ├── chunk_large_message
│       └── _get_content_size
│
├── retry_handler.py           # ~150 lines (NEW - PENDING)
│   ├── make_request_with_retries
│   ├── try_with_fallback
│   └── calculate_retry_delay
│
└── parsers/                   # Existing (good structure)
    ├── base.py
    ├── claude.py
    ├── gpt.py
    └── llama.py
```

---

## Recommended Execution Order

| Order | Task | Effort | Impact | Status |
|-------|------|--------|--------|--------|
| 1 | Proposal 5: Consolidate orphan detection | Low | High | ✅ DONE |
| 2 | Task A: Remove placeholder method | Low | Low | ✅ DONE |
| 3 | Task C: Remove dead truncate_tool_results | Low | Low | ✅ DONE |
| 4 | Task D: Extract logging utilities | Low | Low | ✅ DONE |
| 5 | **Proposal 2: Extract `ToolMessageProcessor`** | Medium | High | ✅ DONE |
| 6 | **Proposal 1: Extract `ConversationManager`** | Medium | High | ✅ DONE |
| 7 | **Proposal 3: Extract `MessageChunker`** | Low | Medium | ✅ DONE |
| 8 | Proposal 4: Extract `RetryHandler` | Low | Medium | Pending |
| 9 | Proposal 6: Simplify debug logging | Low | Low | Pending |

---

## Progress Log

| Date | Change | Proposals Affected | Lines |
|------|--------|-------------------|-------|
| 2025-12-09 | Document created | N/A | 2,151 |
| 2025-12-09 | Proposal 5 completed: Consolidated duplicate orphan detection into 4 helper methods | Proposal 5 | 2,093 (-58) |
| 2025-12-09 | Task A completed: Removed _execute_tool_calls placeholder | Task A | 2,053 (-40) |
| 2025-12-09 | Task C completed: Removed dead truncate_tool_results wrapper | Task C | 2,036 (-17) |
| 2025-12-09 | Task D completed: Extracted logging utilities to module-level functions | Task D | 2,052 (+16) |
| 2025-12-09 | **Proposal 2 completed**: Extracted ToolMessageProcessor class to new file | Proposal 2 | 1,577 (-475) |
| 2025-12-10 | **Proposal 1 completed**: Extracted ConversationManager class to new file | Proposal 1 | 1,139 (-438) |
| 2025-12-10 | **Proposal 3 completed**: Extracted MessageChunker class to new file | Proposal 3 | 939 (-200) |

---

## Summary of Changes

### Files Created
- `auto_bedrock_chat_fastapi/tool_message_processor.py` (686 lines)
- `auto_bedrock_chat_fastapi/conversation_manager.py` (677 lines)
- `auto_bedrock_chat_fastapi/message_chunker.py` (372 lines)

### bedrock_client.py Reduction
- **Original:** 2,151 lines
- **After Proposal 2:** 1,577 lines (-574 lines, -27%)
- **After Proposal 1:** 1,139 lines (-1,012 lines, -47%)
- **After Proposal 3:** 939 lines (-1,212 lines, **-56%**)

### Tests
- All 137 tests pass after refactoring
- Backward compatibility maintained via thin delegation wrappers

---

## Notes

- All refactoring should maintain backward compatibility
- Each proposal should include comprehensive unit tests
- Run full test suite after each proposal completion
- Consider feature flags for gradual rollout if needed
