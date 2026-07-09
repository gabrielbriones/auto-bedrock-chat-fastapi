"""
Centralized default values for autolangchat.

Every numeric threshold, target, and strategy string used across config.py,
message_preprocessor.py, and other modules is defined here once.  Import
from this module instead of hardcoding values.
"""

# ── Tool Call Limits ─────────────────────────────────────────────────────
DEFAULT_MAX_TOOL_CALLS = None  # None = unlimited

# ── Conversation History ─────────────────────────────────────────────────
DEFAULT_MAX_CONVERSATION_MESSAGES = 20
DEFAULT_PRESERVE_SYSTEM_MESSAGE = True

# ── AI Summarization ────────────────────────────────────────────────────
DEFAULT_ENABLE_AI_SUMMARIZATION = False
DEFAULT_SUMMARIZATION_MIN_CHUNKS = 3
DEFAULT_SUMMARIZATION_TEMPERATURE = 0.7

# ── Single-Message Truncation (Character-Based) ─────────────────────────
DEFAULT_SINGLE_MSG_LENGTH_THRESHOLD = 500_000
DEFAULT_SINGLE_MSG_TRUNCATION_TARGET = 425_000

# ── History Truncation (Character-Based) ─────────────────────────────────
DEFAULT_HISTORY_TOTAL_LENGTH_THRESHOLD = 650_000
DEFAULT_HISTORY_MSG_LENGTH_THRESHOLD = 100_000
DEFAULT_HISTORY_MSG_TRUNCATION_TARGET = 85_000
DEFAULT_MAX_TRUNCATION_RECURSION = 3

# ── Plain-Text Truncation Ratios ────────────────────────────────────────
TRUNCATION_HEAD_RATIO = 0.8
TRUNCATION_TAIL_RATIO = 0.2

# ── Multi-Tool Budget Distribution ──────────────────────────────────────
MIN_PROPORTIONAL_BUDGET = 100

# ── Network / Session ───────────────────────────────────────────────────
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_SESSIONS = 1_000
DEFAULT_SESSION_TIMEOUT = 3_600
