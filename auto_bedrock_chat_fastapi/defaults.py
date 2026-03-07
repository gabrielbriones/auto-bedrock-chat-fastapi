"""
Centralized default values for auto-bedrock-chat-fastapi.

Every numeric threshold, target, and strategy string used across config.py,
message_preprocessor.py, and other modules is defined here once.  Import
from this module instead of hardcoding values.
"""

# ── Tool Call Limits ─────────────────────────────────────────────────────
DEFAULT_MAX_TOOL_CALLS = 10
DEFAULT_MAX_TOOL_CALL_ROUNDS = 10

# ── Conversation History ─────────────────────────────────────────────────
DEFAULT_MAX_CONVERSATION_MESSAGES = 20
DEFAULT_PRESERVE_SYSTEM_MESSAGE = True

# ── LLM Client ──────────────────────────────────────────────────────────
LLM_CLIENT_TYPE_BEDROCK = "bedrock"
VALID_LLM_CLIENT_TYPES = {LLM_CLIENT_TYPE_BEDROCK}
DEFAULT_LLM_CLIENT_TYPE = LLM_CLIENT_TYPE_BEDROCK

# ── AI Summarization ────────────────────────────────────────────────────
DEFAULT_ENABLE_AI_SUMMARIZATION = False
DEFAULT_SUMMARIZATION_MIN_CHUNKS = 3
DEFAULT_SUMMARIZATION_TEMPERATURE = 0.7
DEFAULT_SUMMARIZATION_MIN_MAX_TOKENS = 1_024

# ── Single-Message Truncation (Character-Based) ─────────────────────────
DEFAULT_SINGLE_MSG_LENGTH_THRESHOLD = 500_000
DEFAULT_SINGLE_MSG_TRUNCATION_TARGET = 425_000

# ── History Truncation (Character-Based) ─────────────────────────────────
DEFAULT_HISTORY_TOTAL_LENGTH_THRESHOLD = 650_000
DEFAULT_HISTORY_MSG_LENGTH_THRESHOLD = 100_000
DEFAULT_HISTORY_MSG_TRUNCATION_TARGET = 85_000
DEFAULT_MAX_TRUNCATION_RECURSION = 3

# ── Plain-Text Truncation Ratios ────────────────────────────────────────
# Applied to *content budget* (max_size minus marker overhead), not raw max_size.
# HEAD + TAIL = 1.0 → uses all available content space.
TRUNCATION_HEAD_RATIO = 0.8
TRUNCATION_TAIL_RATIO = 0.2

# ── Multi-Tool Budget Distribution ──────────────────────────────────────
MIN_PROPORTIONAL_BUDGET = 100  # Minimum chars per content-list item

# ── Network / Session ───────────────────────────────────────────────────
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_SESSIONS = 1_000
DEFAULT_SESSION_TIMEOUT = 3_600

# ── Error Handling ──────────────────────────────────────────────────────
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0
DEFAULT_EXPONENTIAL_BACKOFF = True
DEFAULT_GRACEFUL_DEGRADATION = True
