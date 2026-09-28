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

# ── Feedback/Raw-Content Synthesis Input Budget ─────────────────────────
# Same FRACTION * max_input_tokens pattern as the truncation thresholds
# above, applied to FeedbackSynthesizer._invoke_llm's combined system+user
# message size. Lets a doomed-to-overflow LLM call be skipped (raising
# immediately, which the existing per-document/per-tag-group except blocks
# already treat as a synthesis failure) instead of paying the latency of a
# round trip that Bedrock would reject anyway with "Input is too long".
# Higher than the chat-flow thresholds above (which reserve room for a
# system prompt, tool schemas, and other history messages sharing the same
# budget): a synthesis call only ever sends the ~900-char fixed system
# prompt + one user message (the whole document), so nearly the whole
# budget is available. 0.85 (not 1.0) keeps a cushion against chars/token
# estimation error for unusually dense content (PDF-extracted tables,
# non-English text) -- most exposed on the smallest-context models in
# _PROFILES (16,384 max_input_tokens), where that 15% is still ~2,450 chars.
RAW_CONTENT_SYNTHESIS_INPUT_FRACTION = 0.85

# max_input_tokens is a token count, not a character count -- unlike the
# truncation FRACTIONs above (which compare chars directly against
# max_input_tokens as an already-tuned, empirically-conservative budget),
# RAW_CONTENT_SYNTHESIS_INPUT_FRACTION's check needs an explicit chars/token
# conversion or it implicitly assumes ~1 char/token, rejecting ordinary
# documents ~4x too aggressively on small-context models. 4.0 is the
# commonly-cited average for English text; combined with the 0.85 fraction
# above this still leaves a safety margin for denser content.
CHARS_PER_TOKEN_ESTIMATE = 4.0

# ── Plain-Text Truncation Ratios ────────────────────────────────────────
TRUNCATION_HEAD_RATIO = 0.8
TRUNCATION_TAIL_RATIO = 0.2

# ── Multi-Tool Budget Distribution ──────────────────────────────────────
MIN_PROPORTIONAL_BUDGET = 100

# ── Network / Session ───────────────────────────────────────────────────
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_SESSIONS = 1_000
DEFAULT_SESSION_TIMEOUT = 24 * 3_600
