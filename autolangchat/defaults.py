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

DEFAULT_MAX_TRUNCATION_RECURSION = 3

# ── Single-Message / History Truncation (fraction of model's max_input_tokens) ──
# Truncation thresholds have no static/fallback default -- they are computed
# directly from the selected model's max_input_tokens (see
# langchain_aws.data._profiles): threshold_chars = FRACTION * max_input_tokens.
# A model with a smaller context window gets a proportionally smaller
# absolute char budget, so a Bedrock "Input is too long" overflow can't
# happen from an over-generous static threshold (XMGPLAT-11175). See
# ChatConfig._scaled_truncation_threshold().
SINGLE_MSG_LENGTH_THRESHOLD_FRACTION = 0.5
SINGLE_MSG_TRUNCATION_TARGET_FRACTION = 0.425
HISTORY_TOTAL_LENGTH_THRESHOLD_FRACTION = 0.65
HISTORY_MSG_LENGTH_THRESHOLD_FRACTION = 0.1
HISTORY_MSG_TRUNCATION_TARGET_FRACTION = 0.085

# ── Plain-Text Truncation Ratios ────────────────────────────────────────
TRUNCATION_HEAD_RATIO = 0.8
TRUNCATION_TAIL_RATIO = 0.2

# ── Multi-Tool Budget Distribution ──────────────────────────────────────
MIN_PROPORTIONAL_BUDGET = 100

# ── Network / Session ───────────────────────────────────────────────────
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_SESSIONS = 1_000
DEFAULT_SESSION_TIMEOUT = 3_600
