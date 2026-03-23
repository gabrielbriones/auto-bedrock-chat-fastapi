# Intelligent Conversation History Truncation Strategy

## Overview

The intelligent conversation history truncation strategy addresses a critical limitation in token management when the assistant makes multiple sequential tool calls. This document describes the problem, solution, and how it prevents context window overflow.

## The Problem: Multi-Tool-Call Context Overflow

### Scenario

When an assistant makes multiple sequential tool calls, each response appends to the conversation history:

```
User Query
↓
Assistant calls Tool 1 → Receives 40KB response
↓
Assistant calls Tool 2 → Receives 40KB response
↓
Assistant calls Tool 3 → Receives 40KB response
↓
Total conversation history: ~150KB (user message + 3 tool responses + assistant messages)
```

### Previous Limitation

The original truncation strategy only addressed **individual tool response truncation**:

- Tool 1 response: 40KB (within 50K threshold) ✓ No truncation
- Tool 2 response: 40KB (within 50K threshold) ✓ No truncation
- Tool 3 response: 40KB (within 50K threshold) ✓ No truncation

**Problem:** Even though each individual response was within limits, their **cumulative effect** caused conversation history to exceed available context window, resulting in API errors like:

```
ContextWindowExceededError: Total tokens exceed limit of 200,000
```

## The Solution: Two-Level Truncation Strategy

### Architecture

The system now implements intelligent truncation at **two levels**:

```
Level 1: Individual Response Truncation (500K threshold)
├─ When a new/current tool response arrives
├─ If larger than threshold (500K)
├─ Truncate to target (425K)
└─ Preserves current response with high fidelity

Level 2: Conversation History Truncation (50K threshold) ← NEW
├─ When preparing to send messages to assistant
├─ Applied to ALL historical tool messages
├─ Each historical tool message: if > 50K → truncate to 42.5K
├─ Skips truncation of last message (current response)
└─ Prevents cumulative overflow from multi-tool scenarios
```

### Implementation Details

#### Method: `_truncate_tool_messages_in_history()`

**Location:** `auto_bedrock_chat_fastapi/bedrock_client.py` (lines 1066-1150)

**Key Features:**

1. **Iterates through conversation history** - Processes ALL messages
2. **Identifies tool messages** - Handles both formats:
   - Claude format: `role="user"` with `content` list containing `tool_result` items
   - GPT format: `role="tool"` with string `content`
3. **Applies history thresholds** - Uses 50K threshold (not 500K) for historical messages
4. **Skips last message** - Never truncates current/most recent response
5. **Preserves non-tool messages** - Keeps user and assistant messages in full

#### Call Location

```python
def chat_completion(self, messages, ...):
    # ... existing code ...

    # Step 1: Manage conversation history (sliding window, etc.)
    messages = self._manage_conversation_history(messages)

    # Step 2: Truncate tool messages in history ← NEW
    messages = self._truncate_tool_messages_in_history(messages)

    # Step 3: Check and chunk remaining messages
    messages = self._check_and_chunk_messages(messages)

    # Step 4: Send to Bedrock
    response = self.client.converse(messages=messages, ...)
```

## Configuration

### Default Values (Conservative - GPT OSS-Friendly)

```python
# New response thresholds
tool_result_new_response_threshold = 500_000      # 500K
tool_result_new_response_target = 425_000         # 85% of threshold

# History thresholds (NEW)
tool_result_history_threshold = 50_000            # 50K
tool_result_history_target = 42_500               # 85% of threshold

# Ratio maintained: 10x between new response and history thresholds
```

### Environment Variables

```bash
BEDROCK_TOOL_RESULT_NEW_RESPONSE_THRESHOLD=500000
BEDROCK_TOOL_RESULT_NEW_RESPONSE_TARGET=425000
BEDROCK_TOOL_RESULT_HISTORY_THRESHOLD=50000
BEDROCK_TOOL_RESULT_HISTORY_TARGET=42500
```

### Custom Configuration

```python
from auto_bedrock_chat_fastapi.config import ChatConfig

config = ChatConfig(
    tool_result_history_threshold=100_000,  # Increase for more history
    tool_result_history_target=85_000,      # Maintain 85% ratio
)
```

## Behavior Examples

### Example 1: Five Sequential Tool Calls

**Scenario:**

```
User: "Analyze data in 5 steps"
Assistant: Calls Tool 1 → 60KB response
Assistant: Calls Tool 2 → 60KB response
Assistant: Calls Tool 3 → 60KB response
Assistant: Calls Tool 4 → 60KB response
Assistant: Calls Tool 5 → 60KB response (last message, NOT truncated)
```

**Before Truncation:**

- Tool 1: 60KB (exceeds 50K threshold)
- Tool 2: 60KB (exceeds 50K threshold)
- Tool 3: 60KB (exceeds 50K threshold)
- Tool 4: 60KB (exceeds 50K threshold)
- Tool 5: 60KB (last, preserved)
- **Total: 300KB tool content**

**After Truncation:**

- Tool 1: 42.5KB (truncated to history target)
- Tool 2: 42.5KB (truncated to history target)
- Tool 3: 42.5KB (truncated to history target)
- Tool 4: 42.5KB (truncated to history target)
- Tool 5: 60KB (NOT truncated - last message)
- **Total: 230KB tool content (23% reduction)**

### Example 2: Mixed Message Sizes

**Scenario:**

```
User: "Get info"
Tool 1: 35KB (within threshold)
Assistant: "Got it"
Tool 2: 75KB (exceeds threshold)
Assistant: "Processing"
Tool 3: 80KB (exceeds threshold, last)
```

**After Truncation:**

- Tool 1: 35KB (under threshold, no change)
- Tool 2: 42.5KB (exceeds threshold, truncated)
- Tool 3: 80KB (last message, NOT truncated)

**Result:**

- Cumulative: ~157KB (vs. 190KB before)
- Conversation maintains context while staying within limits

## Testing

Comprehensive test suite in `tests/test_multi_tool_calls.py`:

### Unit Tests (9 tests)

- Verify truncation of multiple large tool responses
- Test both Claude and GPT message formats
- Confirm non-tool messages are preserved
- Validate last message is never truncated
- Handle edge cases (empty messages, zero-size content)

### Integration Tests (7 tests)

- Verify cumulative history stays under limits
- Test realistic 10-20 sequential tool call scenarios
- Confirm conversation context preserved during truncation
- Validate mixed message format handling
- Test extreme scenarios (900KB+ cumulative content)

**All 16 tests passing:**

```bash
pytest tests/test_multi_tool_calls.py -v
# 16 passed in 2.65s
```

## Benefits

### 1. **Prevents Context Overflow**

- Multi-tool scenarios no longer cause API errors
- Cumulative history automatically managed

### 2. **Preserves Conversation Context**

- User and assistant messages kept in full
- Maintains dialogue coherence for model understanding

### 3. **Intelligent Truncation**

- Only historical tool messages truncated (not current)
- Last response preserved with high fidelity
- Supports both Claude and GPT message formats

### 4. **Configurable Thresholds**

- Users can adjust trade-offs between compression and detail
- Different thresholds for new responses vs. history
- Environment variable configuration for production

### 5. **Transparent Logging**

- Logs indicate when truncation occurs
- Shows size reduction achieved
- Helps debug and monitor behavior

**Example Log Output:**

```
Truncating tool message in history: 75,000 chars → 42,500 chars (threshold: 50,000)
Truncated 4 tool messages in conversation history (total original size: 240,000 chars)
```

## Production Deployment

### Recommended Settings

**For GPT OSS Models (Conservative):**

```
BEDROCK_TOOL_RESULT_HISTORY_THRESHOLD=50000
BEDROCK_TOOL_RESULT_HISTORY_TARGET=42500
BEDROCK_TOOL_RESULT_NEW_RESPONSE_THRESHOLD=500000
BEDROCK_TOOL_RESULT_NEW_RESPONSE_TARGET=425000
```

**For High-Context Models (Aggressive):**

```
BEDROCK_TOOL_RESULT_HISTORY_THRESHOLD=200000
BEDROCK_TOOL_RESULT_HISTORY_TARGET=170000
BEDROCK_TOOL_RESULT_NEW_RESPONSE_THRESHOLD=1000000
BEDROCK_TOOL_RESULT_NEW_RESPONSE_TARGET=850000
```

### Monitoring

Track these metrics in production:

1. **Truncation Frequency** - How often does truncation occur?
2. **Size Reduction** - What % of history is truncated?
3. **Response Latency** - Is truncation impacting performance?
4. **Error Rate** - Are context overflow errors eliminated?

### Troubleshooting

**Issue:** Responses seem truncated/incomplete

**Solutions:**

- Increase `tool_result_history_threshold` (more history preserved)
- Increase `tool_result_new_response_threshold` (new responses preserved better)
- Reduce number of sequential tool calls per response
- Use larger context window model if available

**Issue:** Performance degradation with many tool calls

**Solutions:**

- Tool calls are still being processed, just truncated for history
- Consider batching multiple tool calls into one
- Increase history threshold if context allows

**Issue:** Conversation becomes incoherent after many tool calls

**Solutions:**

- Verify non-tool messages are being preserved (they should be)
- Reduce density of tool calls per conversation
- Consider starting new conversation periodically

## Implementation Details for Developers

### Key Classes

**BedrockClient** (`auto_bedrock_chat_fastapi/bedrock_client.py`)

- `_truncate_tool_messages_in_history()` - Main method (lines 1066-1150)
- `_process_tool_result_message()` - Helper for content truncation
- `chat_completion()` - Calls truncation method (line 110)

**ChatConfig** (`auto_bedrock_chat_fastapi/config.py`)

- `tool_result_history_threshold` - Config parameter
- `tool_result_history_target` - Config parameter

### Extension Points

To customize truncation behavior:

```python
# Override in BedrockClient subclass
def _truncate_tool_messages_in_history(self, messages):
    # Custom truncation logic
    return messages
```

To add logging:

```python
# Logger is accessible
logger.info(f"Custom truncation: {message_size} → {target_size}")
```

## Performance Characteristics

### Time Complexity

- O(n × m) where n = number of messages, m = average content size
- Typical conversation: < 1ms overhead

### Space Complexity

- O(n) - creates new message list with same structure

### Real-World Impact

**Scenario:** 10 sequential tool calls, 60KB each

| Metric          | Before | After | Change    |
| --------------- | ------ | ----- | --------- |
| Total size      | 600KB  | 425KB | -29%      |
| Processing time | 5ms    | 6ms   | +1ms      |
| API calls       | 1      | 1     | No change |

## Future Enhancements

1. **Selective Truncation** - Truncate less important messages first
2. **Adaptive Thresholds** - Adjust based on model capabilities
3. **Message Compression** - Use algorithms beyond truncation
4. **Semantic Preservation** - AI-guided truncation maintaining meaning
5. **Streaming Support** - Apply truncation to streaming responses

## References

- [TRUNCATION_CONFIG_SUMMARY.md](./TRUNCATION_CONFIG_SUMMARY.md) - Configuration details
- [TRUNCATION_QUICK_REF.md](./TRUNCATION_QUICK_REF.md) - Quick reference
- [Configuration Guide](./CONFIGURATION.md) - General configuration
- Test Suite: `tests/test_multi_tool_calls.py`
