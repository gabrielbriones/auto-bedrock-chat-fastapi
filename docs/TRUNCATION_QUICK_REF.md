# Tool Result Truncation - Quick Reference

## Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `tool_result_new_response_threshold` | 500,000 | Size threshold for new tool responses (chars) |
| `tool_result_new_response_target` | 425,000 | Target size after truncating new responses (chars) |
| `tool_result_history_threshold` | 50,000 | Size threshold for historical tool responses (chars) |
| `tool_result_history_target` | 42,500 | Target size after truncating history (chars) |

## Environment Variables

```bash
BEDROCK_TOOL_RESULT_NEW_RESPONSE_THRESHOLD=500000
BEDROCK_TOOL_RESULT_NEW_RESPONSE_TARGET=425000
BEDROCK_TOOL_RESULT_HISTORY_THRESHOLD=50000
BEDROCK_TOOL_RESULT_HISTORY_TARGET=42500
```

## Common Presets

### Conservative (Minimize Context) - Current Default
```bash
BEDROCK_TOOL_RESULT_NEW_RESPONSE_THRESHOLD=500000
BEDROCK_TOOL_RESULT_NEW_RESPONSE_TARGET=425000
BEDROCK_TOOL_RESULT_HISTORY_THRESHOLD=50000
BEDROCK_TOOL_RESULT_HISTORY_TARGET=42500
```

### Balanced (Increased Context)
```bash
BEDROCK_TOOL_RESULT_NEW_RESPONSE_THRESHOLD=1000000
BEDROCK_TOOL_RESULT_NEW_RESPONSE_TARGET=850000
BEDROCK_TOOL_RESULT_HISTORY_THRESHOLD=100000
BEDROCK_TOOL_RESULT_HISTORY_TARGET=85000
```

### Generous (Maximize Context)
```bash
BEDROCK_TOOL_RESULT_NEW_RESPONSE_THRESHOLD=2000000
BEDROCK_TOOL_RESULT_NEW_RESPONSE_TARGET=1700000
BEDROCK_TOOL_RESULT_HISTORY_THRESHOLD=200000
BEDROCK_TOOL_RESULT_HISTORY_TARGET=170000
```

## Python Configuration

```python
from auto_bedrock_chat_fastapi import ChatConfig

# Conservative (Current Default)
config = ChatConfig(
    tool_result_new_response_threshold=500_000,
    tool_result_new_response_target=425_000,
    tool_result_history_threshold=50_000,
    tool_result_history_target=42_500,
)

# Balanced
config = ChatConfig(
    tool_result_new_response_threshold=1_000_000,
    tool_result_new_response_target=850_000,
    tool_result_history_threshold=100_000,
    tool_result_history_target=85_000,
)

# Generous
config = ChatConfig(
    tool_result_new_response_threshold=2_000_000,
    tool_result_new_response_target=1_700_000,
    tool_result_history_threshold=200_000,
    tool_result_history_target=170_000,
)
```

## Testing

```bash
# Validate configuration
poetry run python test_truncation_config.py

# Test with real data
poetry run python test_truncation.py
```

## Best Practices

1. **Maintain 85% ratio**: `target = threshold × 0.85`
2. **Keep 10x difference**: `new_threshold = history_threshold × 10`
3. **Monitor logs**: Enable `BEDROCK_LOG_API_CALLS=true`
4. **Start conservative**: Increase gradually if needed
5. **Test with real data**: Use your actual API responses

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Context window exceeded | Reduce both thresholds proportionally |
| Not enough context | Increase new response threshold/target |
| Memory issues | Decrease history threshold/target |
| Too many items shown | Decrease target size |

## Space Savings

With default settings (2.9MB test data):
- **New response**: 2.9MB → 632KB (78% reduction)
- **History**: 2.9MB → 57KB (98% reduction)
- **Transition savings**: 575KB (91% reduction)

## Log Messages

Look for:
```
Tool result toolu_xyz in new tool response is very large (2,937,536 chars),
truncating to ~850,000 chars (threshold: 1,000,000)...
```

Or:
```
Tool result toolu_xyz in conversation history is very large (2,937,536 chars),
truncating to ~85,000 chars (threshold: 100,000)...
```

## Related Settings

Works with:
- `max_conversation_messages`: Controls message count
- `conversation_strategy`: How to trim old messages
- `max_message_size`: General message size limit
- `enable_message_chunking`: Message chunking feature

## Documentation

- Full guide: `docs/TOOL_RESULT_TRUNCATION.md`
- Implementation details: `TRUNCATION_CONFIG_SUMMARY.md`
- Test script: `test_truncation_config.py`
