"""Message Preprocessor -- Preprocessing pipeline for conversation messages.

The ``MessagePreprocessor`` class provides a unified
``preprocess_messages()`` entry point for:

- Single-message truncation for oversized messages
- History-total truncation with progressive reduction
- AI-based summarization (with plain-text fallback)

Module-level utility functions (``is_tool_message``, ``is_user_message``,
``get_content_size``, etc.) are available for direct import.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from .config import ChatConfig

from .defaults import (
    DEFAULT_ENABLE_AI_SUMMARIZATION,
    DEFAULT_HISTORY_MSG_LENGTH_THRESHOLD,
    DEFAULT_HISTORY_MSG_TRUNCATION_TARGET,
    DEFAULT_HISTORY_TOTAL_LENGTH_THRESHOLD,
    DEFAULT_MAX_TRUNCATION_RECURSION,
    DEFAULT_SINGLE_MSG_LENGTH_THRESHOLD,
    DEFAULT_SINGLE_MSG_TRUNCATION_TARGET,
    DEFAULT_SUMMARIZATION_MIN_CHUNKS,
    DEFAULT_SUMMARIZATION_MIN_MAX_TOKENS,
    DEFAULT_SUMMARIZATION_TEMPERATURE,
    MIN_PROPORTIONAL_BUDGET,
    TRUNCATION_HEAD_RATIO,
    TRUNCATION_TAIL_RATIO,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Module-level utility functions for tool message detection
# ============================================================================


def is_tool_message(msg: dict) -> bool:
    """
    Check if a message is a tool-related message.

    Supports multiple formats:
    - Claude format: role="user" with content list containing tool_result
    - Dict format: role="user" with tool_result dict content
    - GPT format: role="tool" with string content
    - Llama format: role="user" with is_tool_result flag

    Args:
        msg: Message dictionary to check

    Returns:
        True if the message is a tool result message
    """
    if not isinstance(msg, dict):
        return False

    content = msg.get("content", "")

    # Claude format: role="user" with content list containing tool_result
    if msg.get("role") == "user" and isinstance(content, list):
        return any(isinstance(item, dict) and item.get("type") == "tool_result" for item in content)

    # Dict format: role="user" with tool_result dict
    if msg.get("role") == "user" and isinstance(content, dict):
        return content.get("type") == "tool_result"

    # Llama format: role="user" with is_tool_result flag
    if msg.get("role") == "user" and msg.get("is_tool_result"):
        return True

    # GPT format: role="tool" with string content
    if msg.get("role") == "tool" and isinstance(content, str):
        return True

    return False


def is_user_message(msg: dict) -> bool:
    """Check if a message is a user message (non-tool)."""
    if not isinstance(msg, dict):
        return False
    # User message but not a tool result
    return msg.get("role") == "user" and not is_tool_message(msg)


def _get_tool_result_payload(tr: dict) -> tuple[str, str]:
    """Return ``(key, text)`` for the result payload of a tool_results entry.

    The ChatManager format uses ``"result"`` as the key, while some
    legacy/test formats use ``"content"``.  This helper tries both so
    callers can read and write back using the correct key.

    Returns:
        ``(key_name, payload_string)`` or ``("", "")`` when neither key
        is present.
    """
    if "result" in tr:
        return "result", str(tr["result"]) if tr["result"] else ""
    if "content" in tr:
        return "content", str(tr["content"]) if tr["content"] else ""
    return "", ""


def get_content_size(msg: dict) -> int:
    """
    Get the size of message content for truncation decisions.

    Accounts for *both* the ``content`` field and the ``tool_results``
    payload.  The ChatManager stores tool-call results in
    ``msg["tool_results"]`` (a list of dicts with a ``result`` key),
    while ``content`` is only a human-readable label (e.g.
    "Tool results (round 1)").  If ``tool_results`` is present its
    size dominates the true token cost, so we include it here.

    Args:
        msg: Message dictionary

    Returns:
        Size of the content in characters (including tool_results payload)
    """
    if not isinstance(msg, dict):
        return 0

    content = msg.get("content", "")

    if isinstance(content, str):
        size = len(content)
    elif isinstance(content, list):
        # Claude format: sum all content items
        total = 0
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    total += len(str(item["text"]))
                elif "content" in item:
                    total += len(str(item["content"]))
                else:
                    total += len(str(item))
            else:
                total += len(str(item))
        size = total
    elif isinstance(content, dict):
        size = len(str(content.get("content", content)))
    else:
        size = len(str(content))

    # ChatManager format: actual tool-result data lives in ``tool_results``
    tool_results = msg.get("tool_results")
    if tool_results and isinstance(tool_results, list):
        for tr in tool_results:
            if isinstance(tr, dict):
                _, payload = _get_tool_result_payload(tr)
                size += len(payload)

    return size


def split_into_chunks(
    content: str,
    chunk_size: int,
    min_chunks: int = DEFAULT_SUMMARIZATION_MIN_CHUNKS,
) -> List[str]:
    """Split *content* into chunks of at most *chunk_size* characters.

    If the natural number of chunks (``ceil(len / chunk_size)``) is less
    than *min_chunks*, ``chunk_size`` is reduced so that at least
    *min_chunks* chunks are produced.

    The splitter tries to break on natural boundaries in priority order:

    1. ``\\n\\n`` (paragraph break)
    2. ``\\n`` (line break)
    3. ``". "`` or ``.\\n`` (sentence end)
    4. ``" "`` (word boundary)
    5. Hard cut (last resort)

    All chunks concatenated equal the original content (no gaps / overlaps).

    Args:
        content: The text to split.
        chunk_size: Maximum characters per chunk (before *min_chunks*
            adjustment).
        min_chunks: Minimum number of chunks to produce.

    Returns:
        Non-empty list of text chunks.
    """
    if not content:
        return [content]

    length = len(content)

    # Only enforce min_chunks when the content actually exceeds chunk_size.
    # If it fits in a single chunk there is no value in artificially splitting.
    num_natural = math.ceil(length / chunk_size) if chunk_size > 0 else length
    if num_natural > 1 and num_natural < min_chunks and length > 0:
        chunk_size = math.ceil(length / min_chunks)

    break_patterns = ["\n\n", "\n", ". ", ".\n", " "]

    chunks: List[str] = []
    i = 0
    while i < length:
        ideal_end = min(i + chunk_size, length)

        if ideal_end >= length:
            # Last chunk -- take everything remaining
            chunks.append(content[i:])
            break

        # Look for a good break point before ideal_end
        best_break = ideal_end
        search_start = max(i + chunk_size // 2, ideal_end - chunk_size // 4)
        for pattern in break_patterns:
            last_occ = content.rfind(pattern, search_start, ideal_end)
            if last_occ > i:
                best_break = last_occ + len(pattern)
                break

        chunk = content[i:best_break]
        if chunk:
            chunks.append(chunk)

        i = max(best_break, i + 1)  # Ensure forward progress

    return chunks if chunks else [content]


class MessagePreprocessor:
    """
    Preprocesses conversation messages before LLM calls.

    Handles truncation, AI summarization, and history management
    via a unified ``preprocess_messages()`` pipeline.
    """

    def __init__(
        self,
        config: ChatConfig | None = None,
        history_msg_threshold: int | None = None,
        history_msg_target: int | None = None,
        single_msg_threshold: int | None = None,
        single_msg_target: int | None = None,
        llm_client: Any = None,
    ):
        """Initialize the message preprocessor.

        Args:
            config: Optional ``ChatConfig`` instance.  When provided,
                truncation thresholds are read from it (the generalized
                ``single_msg_length_threshold`` /
                ``history_msg_length_threshold`` settings).
                ``None`` disables AI summarization and uses the explicit
                threshold arguments (backward compat).
            llm_client: Optional LLM transport client for AI
                summarization.  ``None`` disables AI summarization --
                plain-text fallback is used instead.
            history_msg_threshold: **Deprecated** -- use
                ``config.history_msg_length_threshold`` instead.  Kept for
                backward compatibility; ignored when *config* is
                provided.
            history_msg_target: **Deprecated** -- use
                ``config.history_msg_truncation_target``.
            single_msg_threshold: **Deprecated** -- use
                ``config.single_msg_length_threshold``.
            single_msg_target: **Deprecated** -- use
                ``config.single_msg_truncation_target``.
        """
        self.config = config
        self.llm_client = llm_client
        self._on_progress: Optional[Callable] = None
        self._system_prompt: Optional[str] = None

        # Derive truncation thresholds from the unified config settings.
        # Legacy constructor params are honoured only when no config is
        # provided (backward compat for callers that haven't migrated).
        if config is not None:
            self._system_prompt = getattr(config, "system_prompt", None)
            self.history_msg_threshold = getattr(
                config, "history_msg_length_threshold", DEFAULT_HISTORY_MSG_LENGTH_THRESHOLD
            )
            self.history_msg_target = getattr(
                config, "history_msg_truncation_target", DEFAULT_HISTORY_MSG_TRUNCATION_TARGET
            )
            self.single_msg_threshold = getattr(
                config, "single_msg_length_threshold", DEFAULT_SINGLE_MSG_LENGTH_THRESHOLD
            )
            self.single_msg_target = getattr(
                config, "single_msg_truncation_target", DEFAULT_SINGLE_MSG_TRUNCATION_TARGET
            )
            self.history_total_threshold = getattr(
                config, "history_total_length_threshold", DEFAULT_HISTORY_TOTAL_LENGTH_THRESHOLD
            )
            self.max_truncation_recursion = getattr(
                config, "max_truncation_recursion", DEFAULT_MAX_TRUNCATION_RECURSION
            )
            self.enable_ai_summarization = getattr(config, "enable_ai_summarization", DEFAULT_ENABLE_AI_SUMMARIZATION)
        else:
            self.history_msg_threshold = (
                history_msg_threshold if history_msg_threshold is not None else DEFAULT_HISTORY_MSG_LENGTH_THRESHOLD
            )
            self.history_msg_target = (
                history_msg_target if history_msg_target is not None else DEFAULT_HISTORY_MSG_TRUNCATION_TARGET
            )
            self.single_msg_threshold = (
                single_msg_threshold if single_msg_threshold is not None else DEFAULT_SINGLE_MSG_LENGTH_THRESHOLD
            )
            self.single_msg_target = (
                single_msg_target if single_msg_target is not None else DEFAULT_SINGLE_MSG_TRUNCATION_TARGET
            )
            self.history_total_threshold = DEFAULT_HISTORY_TOTAL_LENGTH_THRESHOLD
            self.max_truncation_recursion = DEFAULT_MAX_TRUNCATION_RECURSION
            self.enable_ai_summarization = DEFAULT_ENABLE_AI_SUMMARIZATION

    @property
    def ai_enabled(self) -> bool:
        """Whether AI summarization is available and enabled."""
        return self.llm_client is not None and self.enable_ai_summarization

    # ------------------------------------------------------------------
    # Public API -- main entry point
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Progress notification helper
    # ------------------------------------------------------------------

    async def _notify(self, message: str) -> None:
        """Send a progress notification to the client (if callback set).

        This is a thin wrapper around ``self._on_progress`` that
        silently does nothing when no callback is registered.

        Args:
            message: Human-readable progress text shown in the UI.
        """
        if self._on_progress is not None:
            await self._on_progress(
                {
                    "type": "typing",
                    "message": message,
                }
            )

    # ------------------------------------------------------------------
    # Public API -- main entry point
    # ------------------------------------------------------------------

    async def preprocess_messages(
        self,
        messages: List[Dict[str, Any]],
        on_progress: Optional[Callable] = None,
        *,
        threshold_factor: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """Run the full message preprocessing pipeline.

        This is the primary entry point called by ``ChatManager``.  It
        orchestrates the two-stage preprocessing pipeline:

        **Stage 1 -- Single-message truncation**: Any individual message
        whose content exceeds ``single_msg_length_threshold`` is
        truncated (plain text) or AI-summarized to
        ``single_msg_truncation_target``.

        **Stage 2 -- History-total truncation**: If the combined size of
        all messages still exceeds ``history_total_length_threshold``,
        a progressive multi-step reduction is applied:

        - 2.1: Truncate/summarize each middle-zone message individually
          to ``history_msg_truncation_target`` (AI-aware per message).
        - 2.2 (AI OFF only): Wipe all middle-zone messages.
        - 2.3: Truncate every user/tool message exceeding
          ``history_msg_length_threshold`` (all zones).
        - 2.4: Recurse with halved thresholds.

        The system prompt used for AI summarization context is resolved
        from ``config.system_prompt`` at construction time and stored in
        ``self._system_prompt``.

        Args:
            messages: Raw conversation messages (user/assistant/tool/system
                dicts).
            on_progress: Optional ``async callable(msg_dict) -> None``.
                When provided, the preprocessor emits progress
                notifications (e.g. ``"Summarizing result 1/5..."``) so
                the UI can display live feedback during AI summarization.
            threshold_factor: Multiplier applied to all truncation
                thresholds/targets (default ``1.0``).  After a
                context-window error and aggressive message reduction,
                the caller can pass e.g. ``0.5`` to halve every limit
                so that remaining oversized content is truncated further.

        Returns:
            Preprocessed message list, ready for LLM formatting.
        """
        self._on_progress = on_progress
        try:
            f = threshold_factor

            # Stage 1: Single-message truncation -- any message exceeding
            # single_msg_length_threshold is truncated/summarized.
            messages = await self._truncate_oversized_messages(
                messages,
                threshold=int(self.single_msg_threshold * f),
                target=int(self.single_msg_target * f),
            )

            # Stage 2: History-total truncation -- if combined size exceeds
            # history_total_length_threshold, progressively reduce.
            messages = await self._truncate_history_total(
                messages,
                total_threshold=int(self.history_total_threshold * f),
                msg_threshold=int(self.history_msg_threshold * f),
                msg_target=int(self.history_msg_target * f),
            )

            return messages
        finally:
            self._on_progress = None

    # ------------------------------------------------------------------
    # History-total truncation
    # ------------------------------------------------------------------

    async def _truncate_history_total(
        self,
        messages: List[Dict[str, Any]],
        *,
        total_threshold: Optional[int] = None,
        msg_threshold: Optional[int] = None,
        msg_target: Optional[int] = None,
        max_recursion: Optional[int] = None,
        depth: int = 0,
    ) -> List[Dict[str, Any]]:
        """Stage 2: Progressive history-total truncation.

        When the combined character size of **all** messages exceeds
        ``config.history_total_length_threshold``, this method applies
        an increasingly aggressive multi-step reduction:

        2.1. Truncate/summarize each middle-zone message individually
             to ``history_msg_truncation_target`` (AI-aware per message,
             not whole-zone summarization).
        2.2. (AI OFF only) If still over budget, wipe all middle-zone
             messages entirely.
        2.3. Truncate every user/tool message exceeding
             ``history_msg_length_threshold`` in ALL zones (not just
             middle) down to ``history_msg_truncation_target``.
        2.4. If still over budget, recurse with halved threshold/target
             values, up to ``max_truncation_recursion`` times.

        **Zone layout** (by index):

        - *Protected - system*: index 0 if ``role == "system"``.
        - *Middle zone*: everything between the system prompt and the
          last real user message (exclusive).
        - *Protected - trailing*: the last real user message and all
          subsequent assistant/tool messages.

        Uses ``self.llm_client`` and ``self._system_prompt`` for AI
        summarization when ``self.ai_enabled`` is true.

        Args:
            messages: Conversation messages (already individually
                truncated by Stage 1).
            total_threshold: Override for ``self.history_total_threshold``.
            msg_threshold: Override for ``self.history_msg_threshold``.
            msg_target: Override for ``self.history_msg_target``.
            max_recursion: Override for ``self.max_truncation_recursion``.
            depth: Current recursion depth (0 on the initial call).

        Returns:
            Messages after history-level reduction.
        """
        if total_threshold is None:
            total_threshold = self.history_total_threshold
        if msg_threshold is None:
            msg_threshold = self.history_msg_threshold
        if msg_target is None:
            msg_target = self.history_msg_target
        if max_recursion is None:
            max_recursion = self.max_truncation_recursion

        total_size = self._total_messages_size(messages)
        if total_size <= total_threshold:
            logger.debug(
                "History truncation depth=%d: total size %s chars within threshold %s chars",
                depth,
                f"{total_size:,}",
                f"{total_threshold:,}",
            )
            return messages

        logger.info(
            "History truncation depth=%d: total %s chars > threshold %s",
            depth,
            f"{total_size:,}",
            f"{total_threshold:,}",
        )

        zones = self._detect_zones(messages)
        middle_indices = zones["middle"]

        # ── Stage 2.1: Middle-zone per-message truncation ────────────
        # Both AI-on and AI-off paths use per-message truncation.
        # When AI is enabled, _history_step_truncate_zone dispatches to
        # _truncate_single_message which uses _truncate_text (AI-aware).
        if middle_indices:
            logger.info(
                "History truncation Stage 2.1: truncating %d middle-zone " "messages (per-message)",
                len(middle_indices),
            )
            messages = await self._history_step_truncate_zone(
                messages,
                indices=middle_indices,
                msg_threshold=msg_threshold,
                msg_target=msg_target,
            )
            total_size = self._total_messages_size(messages)
            if total_size <= total_threshold:
                logger.info(
                    "History truncation resolved after Stage 2.1: %s chars",
                    f"{total_size:,}",
                )
                return messages

        # ── Stage 2.2 (AI OFF only): Wipe all middle-zone messages ───
        if not self.ai_enabled:
            if middle_indices:
                logger.info(
                    "History truncation Stage 2.2: wiping %d middle-zone " "messages (AI off)",
                    len(middle_indices),
                )
                messages = self._wipe_middle_zone(messages, middle_indices)
                total_size = self._total_messages_size(messages)
                if total_size <= total_threshold:
                    logger.info(
                        "History truncation resolved after Stage 2.2: %s chars",
                        f"{total_size:,}",
                    )
                    return messages

        # ── Stage 2.3: Truncate ALL user/tool messages ───────────────
        # Unlike previous steps that targeted only specific zones,
        # this step targets every user and tool message in the entire
        # conversation that exceeds msg_threshold.
        all_user_tool_indices = [
            i
            for i in range(len(messages))
            if isinstance(messages[i], dict)
            and (messages[i].get("role") in ("user", "tool") or is_tool_message(messages[i]))
        ]
        logger.info(
            "History truncation Stage 2.3: truncating %d user/tool " "messages (all zones)",
            len(all_user_tool_indices),
        )
        messages = await self._history_step_truncate_zone(
            messages,
            indices=all_user_tool_indices,
            msg_threshold=msg_threshold,
            msg_target=msg_target,
        )
        total_size = self._total_messages_size(messages)
        if total_size <= total_threshold:
            logger.info(
                "History truncation resolved after Stage 2.3: %s chars",
                f"{total_size:,}",
            )
            return messages

        # ── Stage 2.4: Recursive halving ─────────────────────────────
        if depth < max_recursion:
            halved_threshold = total_threshold // 2
            halved_msg_threshold = msg_threshold // 2
            halved_msg_target = msg_target // 2
            logger.warning(
                "History truncation: still over budget after all stages "
                "(%s > %s). Retrying with halved values (depth=%d).",
                f"{total_size:,}",
                f"{total_threshold:,}",
                depth + 1,
            )
            return await self._truncate_history_total(
                messages,
                total_threshold=halved_threshold,
                msg_threshold=halved_msg_threshold,
                msg_target=halved_msg_target,
                max_recursion=max_recursion,
                depth=depth + 1,
            )

        logger.error(
            "History truncation: max recursion (%d) reached. " "Returning best-effort result (%s chars).",
            max_recursion,
            f"{total_size:,}",
        )
        return messages

    # ── Zone detection ───────────────────────────────────────────────

    @staticmethod
    def _detect_zones(
        messages: List[Dict[str, Any]],
    ) -> Dict[str, List[int]]:
        """Classify message indices into *protected* and *middle* zones.

        Protected indices:
        - Index 0 if ``role == "system"``.
        - The last real user message (``is_user_message``).
        - All messages after the last real user message (trailing
          assistant + tool-result loop).

        Middle zone:
        - Everything else (between system prompt and the trailing
          protected block).

        Returns:
            ``{"protected": [...], "middle": [...]}`` with sorted index
            lists.
        """
        if not messages:
            return {"protected": [], "middle": []}

        protected: set = set()
        n = len(messages)

        # System prompt
        if isinstance(messages[0], dict) and messages[0].get("role") == "system":
            protected.add(0)

        # Find last real user message index
        last_user_idx: Optional[int] = None
        for i in range(n - 1, -1, -1):
            if is_user_message(messages[i]):
                last_user_idx = i
                break

        if last_user_idx is not None:
            # Protect last user message + everything after it
            for i in range(last_user_idx, n):
                protected.add(i)

        middle = sorted(i for i in range(n) if i not in protected)
        return {"protected": sorted(protected), "middle": middle}

    # ── Truncation helpers for history steps ──────────────────────────

    async def _history_step_truncate_zone(
        self,
        messages: List[Dict[str, Any]],
        *,
        indices: List[int],
        msg_threshold: int,
        msg_target: int,
    ) -> List[Dict[str, Any]]:
        """Truncate messages at *indices* that exceed *msg_threshold*.

        When ``self.ai_enabled`` is true, tool-result entries are
        AI-summarized; otherwise plain-text truncation is used.

        Returns a **new** list (same length) with affected messages
        replaced by their truncated versions.
        """
        result = list(messages)  # shallow copy
        truncated_count = 0
        for idx in indices:
            msg = result[idx]
            if not isinstance(msg, dict):
                continue
            size = get_content_size(msg)
            if size <= msg_threshold:
                continue
            result[idx] = await self._truncate_single_message(
                msg,
                msg_target,
            )
            truncated_count += 1
            new_size = get_content_size(result[idx])
            logger.info(
                "History truncation: index %d (%s) %s → %s chars",
                idx,
                msg.get("role", "?"),
                f"{size:,}",
                f"{new_size:,}",
            )
        return result

    @staticmethod
    def _wipe_middle_zone(
        messages: List[Dict[str, Any]],
        middle_indices: List[int],
    ) -> List[Dict[str, Any]]:
        """Remove all messages at *middle_indices* from the list."""
        removed = set(middle_indices)
        return [msg for i, msg in enumerate(messages) if i not in removed]

    @staticmethod
    def _total_messages_size(messages: List[Dict[str, Any]]) -> int:
        """Sum of ``get_content_size`` across all messages."""
        return sum(get_content_size(m) for m in messages)

    # ------------------------------------------------------------------
    # AI-based single-message summarization
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text_content(msg: Dict[str, Any]) -> str:
        """Extract a single text string from any message format.

        Handles:
        - ``str`` content → returned as-is.
        - ``list`` content (Claude format) → text blocks concatenated.
        - ``dict`` content → inner ``"content"`` value extracted.

        Args:
            msg: Conversation message dict.

        Returns:
            Concatenated text content.
        """
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    # text blocks, tool_result blocks, etc.
                    inner = item.get("text") or item.get("content") or ""
                    if isinstance(inner, str):
                        parts.append(inner)
                    elif isinstance(inner, (dict, list)):
                        parts.append(str(inner))
            return "\n".join(parts)
        if isinstance(content, dict):
            inner = content.get("content") or content.get("text") or ""
            return str(inner) if not isinstance(inner, str) else inner
        return str(content)

    async def _summarize_with_llm(
        self,
        content: str,
        target_size: int,
        iteration_context: str = "",
    ) -> str:
        """Call the LLM to summarize *content*.

        Builds a summarization-specific prompt and calls
        ``self.llm_client.chat_completion()``.

        Args:
            content: The text to summarize.
            target_size: Maximum character count for the summary.
            iteration_context: Human-readable context such as
                ``"chunk 2 of 5"`` -- embedded in the prompt.

        Returns:
            The summary text.

        Raises:
            RuntimeError: If the LLM returns an empty/missing response.
        """
        sys_context = ""
        if self._system_prompt:
            sys_context = (
                "\nThe main conversation uses this system context:\n" "---\n" f"{self._system_prompt}\n" "---\n"
            )

        system_msg = (
            "You are a summarization assistant. Condense the content below "
            "while preserving ALL key facts, data points, names, numbers, "
            "error messages, IDs, URLs, and actionable details."
            f"{sys_context}\n"
            "RULES:\n"
            f"- Your summary MUST be under {target_size:,} characters\n"
            "- Preserve specific data: names, numbers, URLs, error messages, "
            "IDs, dates\n"
            "- Preserve structure (lists, key-value pairs, tables) where "
            "possible\n"
            "- If content is a tool/API response, keep the result data that "
            "answers the query\n"
            "- If content is a user message, keep the core request and any "
            "provided details\n"
            "- Omit: redundant context, verbose formatting, boilerplate, "
            "repeated headers\n"
            "- Do NOT add commentary -- output ONLY the summarized content\n"
        )

        if iteration_context:
            system_msg += f"- This is {iteration_context}\n"

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": content},
        ]

        max_tokens = max(target_size // 4, DEFAULT_SUMMARIZATION_MIN_MAX_TOKENS)

        response = await self.llm_client.chat_completion(
            messages=messages,
            temperature=DEFAULT_SUMMARIZATION_TEMPERATURE,
            max_tokens=max_tokens,
        )

        summary = (response or {}).get("content", "")
        if not summary:
            raise RuntimeError("LLM returned empty summary response")

        return summary

    async def _ai_summarize_message(
        self,
        content: str,
        target_size: int,
        chunk_size: int,
    ) -> str:
        """Rolling map-reduce summarization for a single oversized message.

        1. Split *content* into chunks of ≤ *chunk_size* (min 3 chunks).
        2. Summarize chunk 1.
        3. For each subsequent chunk, summarize(prev_summary + chunk).
        4. Return the final rolling summary.

        If any iteration fails, the entire call raises so the caller can
        fall back to plain-text truncation.

        Uses ``self.llm_client`` and ``self._system_prompt`` for the LLM
        calls.

        Args:
            content: Full text content of the oversized message.
            target_size: Target character count for the final summary.
            chunk_size: Max characters per chunk (from config).

        Returns:
            The final summarized text.
        """
        if not content:
            return content

        chunks = split_into_chunks(content, chunk_size, min_chunks=DEFAULT_SUMMARIZATION_MIN_CHUNKS)
        n = len(chunks)
        logger.debug(
            "AI summarization: splitting %s chars into %d chunks " "(chunk_size=%s)",
            f"{len(content):,}",
            n,
            f"{chunk_size:,}",
        )

        # Iteration 1: summarize the first chunk
        summary = await self._summarize_with_llm(
            content=chunks[0],
            target_size=target_size if n == 1 else chunk_size,
            iteration_context=f"chunk 1 of {n}",
        )
        logger.debug(
            "AI summarization chunk 1/%d: %s → %s chars. Target was %s chars.",
            n,
            f"{len(chunks[0]):,}",
            f"{len(summary):,}",
            f"{target_size if n == 1 else chunk_size:,}",
        )

        # Iterations 2..N: rolling accumulation
        for i in range(1, n):
            combined = summary + "\n\n---NEXT SECTION---\n\n" + chunks[i]
            # Use target_size on the final iteration so the last summary
            # respects the caller's desired output size.
            is_last = i == n - 1
            summary = await self._summarize_with_llm(
                content=combined,
                target_size=target_size if is_last else chunk_size,
                iteration_context=f"chunk {i + 1} of {n}",
            )
            logger.debug(
                "AI summarization chunk %d/%d: %s → %s chars. Target was %s chars.",
                i + 1,
                n,
                f"{len(combined):,}",
                f"{len(summary):,}",
                f"{target_size if is_last else chunk_size:,}",
            )

        logger.debug(
            "AI summarization complete: %s → %s chars in %d LLM call(s). Target was %s chars.",
            f"{len(content):,}",
            f"{len(summary):,}",
            n,
            f"{target_size:,}",
        )
        return summary

    async def _try_ai_summarize(
        self,
        content: str,
        target_size: int,
    ) -> Optional[str]:
        """Attempt AI summarization of *content*.

        Returns the summary string if successful and within
        *target_size*, or ``None`` if AI fails or the result
        exceeds the budget.

        Called by ``_truncate_text`` to keep the try/check/fallback
        logic in one place.
        """
        chunk_size = self.single_msg_threshold // 2
        try:
            summary = await self._ai_summarize_message(
                content=content,
                target_size=target_size,
                chunk_size=chunk_size,
            )
            if len(summary) <= target_size:
                return summary
            logger.warning(
                "AI summary exceeded target (%s > %s), falling back",
                f"{len(summary):,}",
                f"{target_size:,}",
            )
        except Exception as exc:
            logger.warning("AI summarization failed: %s, falling back", exc)
        return None

    # ------------------------------------------------------------------
    # Single-message truncation
    # ------------------------------------------------------------------

    async def _truncate_oversized_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        threshold: Optional[int] = None,
        target: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Stage 1: Truncate any individual message whose content exceeds the threshold.

        Iterates every message and applies plain-text truncation (or, when
        enabled, AI summarization) to any that exceed *threshold*.

        This is a *general* pass that catches oversized messages of **any**
        role/format.

        Uses ``self.llm_client`` and ``self._system_prompt`` for AI
        summarization when ``self.ai_enabled`` is true.

        Args:
            messages: Raw conversation messages.
            threshold: Override for ``self.single_msg_threshold``.
            target: Override for ``self.single_msg_target``.

        Returns:
            Message list with oversized messages truncated.
        """
        effective_threshold = threshold if threshold is not None else self.single_msg_threshold
        effective_target = target if target is not None else self.single_msg_target

        logger.debug(
            "Looking for oversized messages exceeding %s chars for Stage 1 truncation",
            f"{effective_threshold:,}",
        )
        result: List[Dict[str, Any]] = []

        # Pre-scan to count how many messages need truncation so
        # progress messages can show "1/N" style counters.
        oversized_indices = [
            i for i, msg in enumerate(messages) if isinstance(msg, dict) and get_content_size(msg) > effective_threshold
        ]
        total_to_truncate = len(oversized_indices)
        truncated_count = 0

        if total_to_truncate and self.ai_enabled:
            await self._notify("Summarizing conversation...")

        for msg in messages:
            if not isinstance(msg, dict):
                result.append(msg)
                continue

            size = get_content_size(msg)
            if size <= effective_threshold:
                result.append(msg)
                continue

            role = msg.get("role", "unknown")
            original_size = size
            truncated_count += 1

            if total_to_truncate > 1 and self.ai_enabled:
                await self._notify(f"Summarizing message {truncated_count}/{total_to_truncate}...")

            logger.debug(
                "Truncating message with role=%s size=%s to target %s chars",
                msg.get("role"),
                f"{original_size:,}",
                f"{effective_target:,}",
            )
            truncated_msg = await self._truncate_single_message(msg, effective_target)

            new_size = get_content_size(truncated_msg)
            logger.info(
                "Truncated oversized %s message: %s → %s chars",
                role,
                f"{original_size:,}",
                f"{new_size:,}",
            )
            result.append(truncated_msg)

        if truncated_count:
            logger.info(
                "Oversized message truncation finished: %d message(s) truncated",
                truncated_count,
            )
        else:
            logger.debug("Oversized message truncation finished: no messages exceeded threshold")

        return result

    async def _truncate_single_message(
        self,
        msg: Dict[str, Any],
        target: int,
    ) -> Dict[str, Any]:
        """Truncate a single message to *target* characters.

        Central dispatch that extracts text content, truncates it
        via ``_truncate_text`` (AI-aware with plain-text fallback),
        and reconstructs the message in its original structure.

        Format-specific handling:

        - **ChatManager tool messages** (``role="tool"`` +
          ``tool_results``): proportional per-entry truncation via
          ``_truncate_result_entries``.
        - **List content** (Claude format): proportional per-item
          truncation via ``_truncate_list_content_items``.
        - **Dict content**: truncation of the inner ``content`` value,
          preserving the outer dict structure.
        - **String content**: direct ``_truncate_text`` call.

        This is the preferred entry point for truncating an individual
        message.  Both ``_truncate_oversized_messages`` (Stage 1) and
        ``_history_step_truncate_zone`` (Stage 2) delegate here.

        Args:
            msg: The oversized message dict.
            target: Maximum content size in characters.

        Returns:
            A new message dict with truncated/summarized content.
        """
        # ChatManager tool messages: payload is in tool_results, not content
        tool_results_list = msg.get("tool_results")
        if msg.get("role") == "tool" and tool_results_list and isinstance(tool_results_list, list):
            return await self._truncate_result_entries(
                msg,
                tool_results_list,
                target,
            )

        content = msg.get("content", "")

        # List content (Claude format): proportional per-item truncation
        if isinstance(content, list):
            return await self._truncate_list_content_items(
                msg,
                content,
                target,
            )

        # Dict content: truncate inner "content" value
        if isinstance(content, dict):
            inner = content.get("content", "")
            inner_str = str(inner) if not isinstance(inner, str) else inner
            if len(inner_str) <= target:
                return msg
            truncated = await self._truncate_text(
                inner_str,
                target,
                label="MESSAGE CONTENT",
            )
            return {**msg, "content": {**content, "content": truncated}}

        # String content (or unknown — stringify)
        text = content if isinstance(content, str) else str(content)
        truncated = await self._truncate_text(
            text,
            target,
            label="MESSAGE CONTENT",
        )
        return {**msg, "content": truncated}

    async def _truncate_result_entries(
        self,
        msg: Dict[str, Any],
        tool_results_list: List[Dict[str, Any]],
        target: int,
    ) -> Dict[str, Any]:
        """Proportionally truncate tool_results entries to fit *target* chars.

        Used for ChatManager-format tool messages where the real payload
        lives in ``msg["tool_results"][*]["result"]``.

        Each entry's budget is proportional to its current size, so that
        small results are left alone and large results absorb most of the
        reduction.

        Args:
            msg: The tool message dict.
            tool_results_list: The ``tool_results`` list (already validated
                as a non-empty list of dicts).
            target: Total target size in characters.

        Returns:
            A new message dict with truncated ``tool_results``.
        """
        tool_calls_list = msg.get("tool_calls", [])

        # Measure each entry's payload size
        entries: List[tuple] = []  # (index, payload_key, payload_str, size)
        total_payload_size = 0
        for j, tr in enumerate(tool_results_list):
            if isinstance(tr, dict):
                key, payload = _get_tool_result_payload(tr)
                entries.append((j, key, payload, len(payload)))
                total_payload_size += len(payload)
            else:
                entries.append((j, "", "", 0))

        if total_payload_size == 0:
            return msg

        new_tool_results = list(tool_results_list)  # shallow copy
        any_changed = False

        # Count entries that actually need truncation for progress reporting
        entries_needing_truncation = [
            e for e in entries if e[1] and e[3] > max(int(target * (e[3] / total_payload_size)), 1)
        ]
        total_to_truncate = len(entries_needing_truncation)
        truncation_idx = 0

        for j, payload_key, payload_str, size in entries:
            if not payload_key or size == 0:
                continue
            # Proportional share of the budget
            share = max(int(target * (size / total_payload_size)), 1)
            if size > share:
                truncation_idx += 1
                tool_call_id = tool_calls_list[j].get("id", f"tool_{j}") if j < len(tool_calls_list) else f"tool_{j}"
                if self.ai_enabled:
                    await self._notify(f"Summarizing result {truncation_idx}/{total_to_truncate}...")
                truncated = await self._truncate_text(
                    payload_str,
                    share,
                    context=f"Tool {tool_call_id}",
                )
                new_tr = dict(tool_results_list[j])
                new_tr[payload_key] = truncated
                new_tool_results[j] = new_tr
                any_changed = True

        if any_changed:
            return {**msg, "tool_results": new_tool_results}
        return msg

    async def _truncate_list_content_items(
        self,
        msg: Dict[str, Any],
        content_list: List[Any],
        target: int,
    ) -> Dict[str, Any]:
        """Proportionally truncate items in a list-format content block.

        Each item's share of *target* is proportional to its current size.
        Items that are already within their proportional budget are kept
        as-is.

        When ``self.ai_enabled`` is true, oversized items are
        AI-summarized via ``_truncate_text``; otherwise they receive
        plain-text truncation.

        Handles Claude-format content blocks:
        - ``{"type": "text", "text": "..."}``
        - ``{"type": "tool_result", "content": "..."}``
        - ``{"type": "tool_use", "input": {...}}``

        Args:
            msg: The parent message dict.
            content_list: The list of content blocks.
            target: Total target size for all items combined.

        Returns:
            A new message dict with proportionally truncated content.
        """
        items_with_sizes: List[tuple] = []
        total_size = 0
        for item in content_list:
            size = self._item_content_size(item)
            items_with_sizes.append((item, size))
            total_size += size

        if total_size <= target:
            return msg

        new_content: List[Any] = []
        for item, item_size in items_with_sizes:
            if item_size == 0:
                new_content.append(item)
                continue

            # Proportional share of the target budget
            item_target = max(MIN_PROPORTIONAL_BUDGET, int(target * item_size / total_size))

            if not isinstance(item, dict) or item_size <= item_target:
                new_content.append(item)
                continue

            new_item = dict(item)
            if "text" in item and len(item["text"]) > item_target:
                new_item["text"] = await self._truncate_text(
                    item["text"],
                    item_target,
                    label="MESSAGE CONTENT",
                )
            elif "content" in item:
                inner = item["content"]
                inner_str = str(inner) if not isinstance(inner, str) else inner
                if len(inner_str) > item_target:
                    new_item["content"] = await self._truncate_text(
                        inner_str,
                        item_target,
                        context=f"Tool {item.get('tool_use_id', 'content-block')}",
                    )
            new_content.append(new_item)

        return {**msg, "content": new_content}

    @staticmethod
    def _item_content_size(item: Any) -> int:
        """Return the character size of a single content-list item."""
        if isinstance(item, dict):
            if "text" in item:
                return len(item["text"])
            if "content" in item:
                c = item["content"]
                return len(c) if isinstance(c, str) else len(str(c))
            return len(str(item))
        return len(str(item))

    # ------------------------------------------------------------------
    # Truncation helpers
    # ------------------------------------------------------------------

    async def _truncate_text(
        self,
        text: str,
        target: int,
        *,
        label: str = "TOOL RESULT",
        context: str = "",
    ) -> str:
        """AI-aware text truncation with plain-text fallback.

        When ``self.ai_enabled`` is true, attempts AI summarization
        first via ``_try_ai_summarize``.  If that succeeds and the
        summary fits within *target*, returns it with an
        ``[AI SUMMARY ...]`` marker.  Otherwise falls back to
        plain-text head + tail truncation via ``_truncate_plain_text``.

        Args:
            text: The text to truncate.
            target: Maximum character count for the result.
            label: Label for the plain-text truncation marker
                (e.g. ``"TOOL RESULT"`` or ``"MESSAGE CONTENT"``).
            context: Optional context string for the AI summary marker
                and log messages (e.g. ``"Tool tool_0"`` or
                ``"user message"``).

        Returns:
            Truncated or summarized text fitting within *target*
            characters.
        """
        original_size = len(text)
        if original_size <= target:
            return text

        # ── AI path ──────────────────────────────────────────────
        if self.ai_enabled:
            summary = await self._try_ai_summarize(text, target)
            if summary is not None:
                ctx_part = f" {context}," if context else ""
                marker = (
                    f"[AI SUMMARY -{ctx_part} Original: "
                    f"{original_size:,} chars, reduced to: "
                    f"{len(summary):,} chars]\n\n"
                )
                result = marker + summary
                if len(result) > target:
                    # Marker pushes over budget — use summary alone
                    result = summary
                logger.debug(
                    "AI summarization succeeded%s: %s → %s chars",
                    f" for {context}" if context else "",
                    f"{original_size:,}",
                    f"{len(result):,}",
                )
                return result

        # ── Plain-text fallback ──────────────────────────────────
        logger.debug(
            "Plain-text truncation%s: %s chars → max %s",
            f" for {context}" if context else "",
            f"{original_size:,}",
            f"{target:,}",
        )
        return self._truncate_plain_text(text, target, original_size, label=label)

    def _truncate_plain_text(
        self,
        text: str,
        max_size: int,
        original_size: int,
        *,
        label: str = "TOOL RESULT",
    ) -> str:
        """
        Truncate plain text with beginning + end preview.

        Strategy: Show first and last portions with summary.

        Args:
            text: The text to truncate.
            max_size: Maximum allowed size.
            original_size: Original size (for the marker).
            label: Human-readable label used in the truncation marker
                (e.g. ``"TOOL RESULT"`` or ``"MESSAGE CONTENT"``).
        """
        if len(text) <= max_size:
            return text

        # Count lines for summary
        total_lines = text.count("\n") + 1

        # ── Compute marker overhead dynamically ──────────────────
        # Build the scaffolding *without* head/tail content to
        # measure how many characters the fixed markers consume.
        # Use ``original_size`` as the placeholder for the omitted-
        # chars number -- the actual omitted count is always ≤
        # original_size, so its formatted width is never wider.
        scaffolding = (
            f"[{label} TRUNCATED - Original size: {original_size:,} chars, {total_lines:,} lines]\n\n"
            f"BEGINNING:\n"
            f"\n\n"
            f"... ({original_size:,} chars omitted) ...\n\n"
            f"ENDING:\n"
            f"\n\n"
            f"RECOMMENDATION: Use filtering or pagination to get specific data."
        )
        marker_overhead = len(scaffolding)

        # ── Allocate remaining budget to content ────────────────
        content_budget = max_size - marker_overhead
        if content_budget <= 0:
            # max_size too small for structured output; simple cut
            # with a minimal truncation marker when possible.
            truncation_note = f"\n[{label} TRUNCATED]"
            if max_size > len(truncation_note):
                return text[: max_size - len(truncation_note)] + truncation_note
            # Even shorter fallback for very tiny budgets
            short_note = "\n[TRUNCATED]"
            if max_size > len(short_note):
                return text[: max_size - len(short_note)] + short_note
            return text[:max_size]

        head_size = int(content_budget * TRUNCATION_HEAD_RATIO)
        tail_size = int(content_budget * TRUNCATION_TAIL_RATIO)

        head = text[:head_size]
        tail = text[-tail_size:] if tail_size > 0 else ""

        result = (
            f"[{label} TRUNCATED - Original size: {original_size:,} chars, {total_lines:,} lines]\n\n"
            f"BEGINNING:\n"
            f"{head}\n\n"
            f"... ({original_size - head_size - tail_size:,} chars omitted) ...\n\n"
            f"ENDING:\n"
            f"{tail}\n\n"
            f"RECOMMENDATION: Use filtering or pagination to get specific data."
        )

        return result
