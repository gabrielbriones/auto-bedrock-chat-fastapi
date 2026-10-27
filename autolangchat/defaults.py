"""
Centralized default values for autolangchat.

Every numeric threshold, target, and strategy string used across config.py,
message_preprocessor.py, and other modules is defined here once.  Import
from this module instead of hardcoding values.
"""

# ── Tool Call Limits ─────────────────────────────────────────────────────
DEFAULT_MAX_TOOL_CALLS = None  # None = unlimited
DEFAULT_TOOL_RESPONSE_MAX_BYTES = 64 * 1024 * 1024

# ── Conversation History ─────────────────────────────────────────────────
DEFAULT_MAX_CONVERSATION_MESSAGES = 20
DEFAULT_PRESERVE_SYSTEM_MESSAGE = True

# ── Session Artifacts ────────────────────────────────────────────────────
DEFAULT_ARTIFACT_MAX_AGE_SECONDS = 86400
DEFAULT_ARTIFACT_MAX_BYTES_PER_SESSION = 192 * 1024 * 1024
DEFAULT_ARTIFACT_MAX_ITEMS_PER_SESSION = 128
DEFAULT_ARTIFACT_MAX_BYTES = 512 * 1024 * 1024
ARTIFACT_READ_MAX_BYTES = 65536
ARTIFACT_READ_MAX_LINES = 1000

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

# ── Context Budget ──────────────────────────────────────
# Chars/token ratios used by autolangchat.context_budget to estimate input
# tokens *before* invocation (Bedrock Converse has no count-tokens call in
# this pipeline). Keyed by the raw Bedrock provider key from
# config.split_model_id() (e.g. "anthropic" for "us.anthropic.claude-...")
# because model families use different tokenizers, then by content kind:
# dense JSON tokenizes at roughly 1.7-2.5 chars/token, log/tabular output
# at 1.0-2.2 (digit-heavy CSV is the densest: some tokenizers split every
# digit), prose at about 3.4-5.3.
#
# Calibrated 2026-10-15 (langchain-aws 1.8.1, us-east-1) against real
# Bedrock usage_metadata.input_tokens for 1-4 models per provider; raw
# numbers are in tests/fixtures/context_budget/calibration.json. Each value
# is the lowest measured chars/token across that provider's models and the
# kind's fixtures, / 1.05, rounded down to 0.05, so the densest tokenizer in
# a family is estimated a few percent high and lighter ones (e.g. Claude
# Haiku 4.5 vs Sonnet/Opus, Nova 2 Lite vs Nova Micro/Pro) further over.
CONTEXT_BUDGET_KIND_PROSE = "prose"
CONTEXT_BUDGET_KIND_JSON = "json"
CONTEXT_BUDGET_KIND_LOG_TABULAR = "log_tabular"
CONTEXT_BUDGET_KINDS = (
    CONTEXT_BUDGET_KIND_PROSE,
    CONTEXT_BUDGET_KIND_JSON,
    CONTEXT_BUDGET_KIND_LOG_TABULAR,
)


def _ratios(prose: float, json_: float, log_tabular: float) -> dict:
    return {
        CONTEXT_BUDGET_KIND_PROSE: prose,
        CONTEXT_BUDGET_KIND_JSON: json_,
        CONTEXT_BUDGET_KIND_LOG_TABULAR: log_tabular,
    }


# Columns: prose, json, log_tabular. Comment = number of models measured.
CONTEXT_BUDGET_CHARS_PER_TOKEN = {
    "amazon": _ratios(5.05, 1.85, 1.00),  # 3
    "anthropic": _ratios(3.40, 1.70, 1.55),  # 4
    "deepseek": _ratios(5.00, 2.35, 1.55),  # 2
    "google": _ratios(5.00, 1.80, 1.00),  # 3
    "meta": _ratios(5.05, 2.45, 1.55),  # 3
    "minimax": _ratios(5.05, 2.40, 1.55),  # 3
    "mistral": _ratios(4.75, 1.75, 1.00),  # 4
    "moonshot": _ratios(5.05, 2.50, 1.55),  # 1
    "moonshotai": _ratios(5.05, 2.50, 1.55),  # 2
    "nvidia": _ratios(5.00, 1.90, 1.00),  # 2
    "openai": _ratios(5.05, 2.45, 1.55),  # 3
    "qwen": _ratios(5.05, 1.95, 1.00),  # 3
    "writer": _ratios(5.05, 1.95, 1.00),  # 2
    "xai": _ratios(5.00, 2.30, 1.55),  # 2
    "zai": _ratios(5.05, 2.30, 1.40),  # 3
}

# Providers with no calibrated entry use the most conservative (lowest)
# chars/token ratio seen across all calibrated providers for each kind, so
# an uncalibrated tokenizer is over- rather than under-estimated.
CONTEXT_BUDGET_FALLBACK_CHARS_PER_TOKEN = {
    kind: min(ratios[kind] for ratios in CONTEXT_BUDGET_CHARS_PER_TOKEN.values()) for kind in CONTEXT_BUDGET_KINDS
}

# Input-token cap assumed when a model's max_input_tokens is unknown (model
# not profiled, or langchain_aws _PROFILES failed to import). Deliberately
# at or below the smallest max_input_tokens in the shipped catalog (32,768
# as of the installed langchain-aws) so an unknown model is never
# over-budgeted; a warning is logged whenever this is used.
DEFAULT_MAX_INPUT_TOKENS_UNKNOWN = 16_384

# The content-kind classifier only inspects this many leading characters so
# it stays cheap on multi-MB payloads.
CONTEXT_BUDGET_CLASSIFIER_PREFIX_CHARS = 64 * 1024

# Approximate per-message framing tokens (role markers, content-block
# wrappers) added on top of each message's content estimate.
CONTEXT_BUDGET_PER_MESSAGE_OVERHEAD_TOKENS = 8

# Default fraction of the input cap held back as a safety margin; exposed as
# ChatConfig.context_budget_safety_fraction (AUTOCHAT_CONTEXT_BUDGET_SAFETY_FRACTION).
DEFAULT_CONTEXT_BUDGET_SAFETY_FRACTION = 0.10

# ── Plain-Text Truncation Ratios ────────────────────────────────────────
TRUNCATION_HEAD_RATIO = 0.8
TRUNCATION_TAIL_RATIO = 0.2

# ── Multi-Tool Budget Distribution ──────────────────────────────────────
MIN_PROPORTIONAL_BUDGET = 100

# ── Network / Session ───────────────────────────────────────────────────
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_SESSIONS = 1_000
DEFAULT_SESSION_TIMEOUT = 24 * 3_600
