"""Chat Manager — Orchestration layer for LLM conversations.

This module is the central orchestrator that coordinates all components involved
in a complete chat turn:

- **MessagePreprocessor** — tool result truncation, single-message truncation,
  history-total truncation, and orphaned tool-result cleanup
- **LLM client** (LLM transport) — format → send → parse

The ChatManager owns the full lifecycle of a chat_completion call:
  1. Message preprocessing (tool truncation, oversized-message truncation,
     history-total truncation, orphaned tool-result cleanup)
  2. Message formatting for the LLM provider
  3. LLM API call with context-window error recovery
  4. Tool call loop (if tool_manager is set and response has tool_calls)
  5. Return ``ChatCompletionResult``

Error recovery includes context-window overflow handling, fallback model
support, and graceful degradation (user-friendly error messages).
"""

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .exceptions import ContextWindowExceededError, LLMClientError
from .message_preprocessor import MessagePreprocessor, get_content_size
from .models import ChatCompletionResult
from .tool_manager import AuthInfo, ToolManager

logger = logging.getLogger(__name__)


class ChatManager:
    """Orchestrates LLM conversations: preprocessing, LLM calls, and error recovery.

    The ChatManager sits between the transport layer (websocket_handler) and
    the LLM transport layer (llm_client).  It applies all message preparation
    steps before calling the LLM, and handles context-window overflow recovery.

    Args:
        llm_client: An LLM transport client (e.g. ``BedrockClient``).
            Must expose ``chat_completion()``, ``format_messages()``.
        config: Application configuration (``ChatConfig`` instance).
        tool_manager: Optional ``ToolManager`` that owns tool generation
            (cached) and tool execution (HTTP-based).  When ``None``, tool
            calls are disabled (no tools_desc sent to LLM, no tool loop).

    The ``MessagePreprocessor`` is created internally from *config* —
    it is not needed outside the ChatManager.

    Example::

        chat_manager = ChatManager(
            llm_client=my_llm_client,
            config=config,
        )
        result = await chat_manager.chat_completion(messages=msgs)
    """

    def __init__(
        self,
        llm_client: Any,
        config: Any,
        tool_manager: Optional[ToolManager] = None,
    ):
        self.llm_client = llm_client
        self.config = config
        self.message_preprocessor = MessagePreprocessor(config=config, llm_client=self.llm_client)
        self.tool_manager = tool_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        auth_info: Optional[AuthInfo] = None,
        on_progress: Optional[Callable] = None,
        **llm_params: Any,
    ) -> ChatCompletionResult:
        """Orchestrate a complete chat turn.

        This is the main entry point that websocket_handler (or any other
        caller) uses instead of calling ``llm_client.chat_completion``
        directly.

        Pipeline (all handled inside ``_call_llm_with_recovery``)
        ----------------------------------------------------------
        1. **Message preprocessing** — tool truncation, single-message
           truncation, history-total truncation, orphaned tool-result cleanup
           (all via ``MessagePreprocessor.preprocess_messages``)
        2. **Format for LLM** — ``llm_client.format_messages``
        3. **LLM call** — ``llm_client.chat_completion`` (with context-window recovery)

        Args:
            messages: Raw conversation messages (user/assistant/tool/system dicts).
            auth_info: Optional ``AuthInfo`` containing credentials and auth
                handler for tool call execution.  ``None`` = unauthenticated.
            on_progress: ``async callable(message_dict) -> None``.
                Optional callback for sending progress updates to the client
                during tool call rounds (e.g. typing indicators).
            **llm_params: Additional params forwarded to the LLM client
                (``model_id``, ``temperature``, ``max_tokens``, etc.).

        Returns:
            ``ChatCompletionResult`` containing the full updated message
            history, the final AI response, any tool results, and metadata.

        Raises:
            ContextWindowExceededError: If the LLM rejects the input even
                after recovery attempts.
            LLMClientError: For other LLM transport errors.
        """
        metadata: Dict[str, Any] = {
            "preprocessing_applied": False,
            "context_window_retries": 0,
            "original_message_count": len(messages),
        }

        # ----------------------------------------------------------
        # Steps 1-5: Preprocess and call LLM with recovery
        # ----------------------------------------------------------
        response, messages = await self._call_llm_with_recovery(messages, llm_params, metadata, on_progress=on_progress)

        # ----------------------------------------------------------
        # Step 6: Tool call loop (if tools are active)
        # ----------------------------------------------------------
        all_tool_results: List[Dict[str, Any]] = []

        if response.get("tool_calls") and self.tool_manager is not None:
            response, messages, all_tool_results = await self._run_tool_call_loop(
                response=response,
                messages=messages,
                auth_info=auth_info,
                on_progress=on_progress,
                llm_params=llm_params,
                metadata=metadata,
            )

        # ----------------------------------------------------------
        # Step 7: Build result
        # ----------------------------------------------------------
        metadata["final_message_count"] = len(messages)

        return ChatCompletionResult(
            messages=messages,
            response=response,
            tool_results=all_tool_results,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Pipeline steps (private)
    # ------------------------------------------------------------------

    async def _preprocess_messages(
        self,
        messages: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        on_progress: Optional[Callable] = None,
        *,
        threshold_factor: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """Run the full message preprocessing pipeline.

        Delegates to ``MessagePreprocessor.preprocess_messages`` which
        applies all preprocessing steps in order:

        1. Tool-result truncation (history + new response thresholds)
        2. Single-message truncation (plain text or AI summarization)
        3. History-total truncation (3-step zone-based + recursive halving)
        4. Orphaned tool-result cleanup

        The *on_progress* callback is forwarded to the preprocessor so
        it can emit granular notifications during AI summarization
        (e.g. ``"Summarizing result 1/5..."``).

        Args:
            messages: Raw conversation messages.
            metadata: Mutable metadata dict — updated with preprocessing stats.
            on_progress: Optional async callback for progress updates.
            threshold_factor: Multiplier for all truncation thresholds
                (default ``1.0``).  Pass ``0.5`` after a context-window
                error to halve thresholds and force further truncation.

        Returns:
            Preprocessed messages ready for LLM formatting.
        """
        before_count = len(messages)
        before_size = sum(get_content_size(m) for m in messages)

        messages = await self.message_preprocessor.preprocess_messages(
            messages,
            on_progress=on_progress,
            threshold_factor=threshold_factor,
        )

        after_count = len(messages)
        after_size = sum(get_content_size(m) for m in messages)
        if after_count != before_count or after_size != before_size:
            metadata["preprocessing_applied"] = True
            logger.info(
                f"Preprocessing: {before_count} → {after_count} messages, "
                f"{before_size:,} → {after_size:,} chars "
                f"(truncation applied)"
            )

        return messages

    async def _run_tool_call_loop(
        self,
        response: Dict[str, Any],
        messages: List[Dict[str, Any]],
        auth_info: Optional[AuthInfo],
        on_progress: Optional[Callable],
        llm_params: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Execute tool calls recursively until the LLM gives a final response.

        Each round:
        1. Notify the client via ``on_progress`` (if provided).
        2. Append the assistant message (with tool_calls) to ``messages``.
        3. Execute tools via ``self.tool_manager.execute_tool_calls()``.
        4. Append the tool-result message to ``messages``.
        5. Call ``_call_llm_with_recovery`` (which re-runs the full
           preprocessing pipeline and calls the LLM).
        6. Check for Llama placeholder responses or max-rounds.

        Args:
            response: The LLM response that contains ``tool_calls``.
            messages: The current local message list (mutated in-place).
            auth_info: Optional ``AuthInfo`` for authenticated tool execution.
            on_progress: Optional ``async callable(msg_dict) -> None``
                for sending progress updates to the client.
            llm_params: Extra params forwarded to the LLM client.
            metadata: Mutable metadata dict — updated with tool-call stats.

        Returns:
            Tuple of ``(final_response, updated_messages, all_tool_results)``.
        """
        current_response = response
        all_tool_results: List[Dict[str, Any]] = []
        round_count = 0
        max_rounds = self.config.max_tool_call_rounds

        # Build tool-progress wrapper once (outside the loop)
        async def _wrap_tool_progress(progress_message: str) -> None:
            await on_progress(
                {
                    "type": "tool_progress",
                    "message": progress_message,
                    "timestamp": datetime.now().isoformat(),
                }
            )

        tool_on_progress = _wrap_tool_progress if on_progress is not None else None

        while current_response.get("tool_calls") and round_count < max_rounds:
            round_count += 1
            tool_calls = current_response["tool_calls"]

            logger.debug(f"Tool call round {round_count}, processing " f"{len(tool_calls)} tool calls")

            # 1. Notify client of progress
            if on_progress is not None:
                progress_content = current_response.get("content") or "Working on your request..."
                await on_progress(
                    {
                        "type": "typing",
                        "message": progress_content,
                        "timestamp": datetime.now().isoformat(),
                    }
                )

            # 2. Append assistant message (preserves reasoning + tool_calls)
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": current_response.get("content", ""),
                "tool_calls": tool_calls,
            }
            messages.append(assistant_msg)

            # 3. Execute tools
            tool_results = await self.tool_manager.execute_tool_calls(
                tool_calls, auth_info=auth_info, on_progress=tool_on_progress
            )
            all_tool_results.extend(tool_results)

            # 4. Append tool-result message
            tool_msg: Dict[str, Any] = {
                "role": "tool",
                "content": f"Tool results (round {round_count})",
                "tool_calls": tool_calls,
                "tool_results": tool_results,
            }
            messages.append(tool_msg)

            # 5. Preprocess and call LLM with recovery
            current_response, messages = await self._call_llm_with_recovery(
                messages, llm_params, metadata, on_progress=on_progress
            )

            # 6a. Check for Llama placeholder response
            response_content = current_response.get("content", "").strip()
            if response_content.startswith("Tool results (round") and not current_response.get("tool_calls"):
                logger.warning(
                    f"Received placeholder response '{response_content}' with no "
                    f"tool calls. Likely Llama confusion — ending loop."
                )
                current_response["content"] = ""
                break

            # 6b. Logging
            if current_response.get("tool_calls"):
                logger.debug(
                    f"AI requested {len(current_response['tool_calls'])} more " f"tool calls in round {round_count + 1}"
                )
            else:
                logger.debug(f"AI provided final response after {round_count} tool call " f"round(s)")

        # Handle max-rounds exceeded
        if round_count >= max_rounds and current_response.get("tool_calls"):
            logger.warning(f"Reached maximum tool call rounds ({max_rounds}), stopping loop")
            content = current_response.get("content", "")
            content += f"\n\n[Note: Reached maximum tool call limit of " f"{max_rounds} rounds]"
            current_response["content"] = content
            current_response["tool_calls"] = []

        # Record stats
        metadata["tool_call_rounds"] = round_count
        metadata["total_tool_calls"] = len(all_tool_results)

        return current_response, messages, all_tool_results

    async def _call_llm_with_recovery(
        self,
        messages: List[Dict[str, Any]],
        llm_params: Dict[str, Any],
        metadata: Dict[str, Any],
        on_progress: Optional[Callable] = None,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Preprocess messages, format for LLM, and call with multi-layer recovery.

        This is the single place where the full preprocessing-to-LLM pipeline
        runs.  Every LLM call goes through here, guaranteeing that messages
        are always trimmed, cleaned, preprocessed, and formatted before being
        sent to the model.

        Pipeline:
            1. Message preprocessing (tool truncation, single-message
               truncation, history-total truncation, orphaned cleanup)
            2. Format for LLM provider
            3. LLM call

        Recovery sequence (each layer tried only if the previous fails):

        1. **Normal call** — preprocess → format → send.
        2. **Context-window recovery** — aggressive message reduction → re-format → retry.
        3. **Fallback model** — re-format messages for the fallback model's parser
           (Claude/Llama/GPT message shapes differ), switch to
           ``config.fallback_model`` (if configured), and retry.
        4. **Graceful degradation** — if ``config.graceful_degradation`` is enabled,
           return a synthetic apology response instead of raising.

        Args:
            messages: Raw or partially-processed messages (preprocessing is
                applied inside this method).
            llm_params: Forwarded to ``llm_client.chat_completion``.
            metadata: Mutable metadata dict — updated with recovery stats.

        Returns:
            Tuple of ``(response_dict, processed_messages)``.
            The processed messages reflect any trimming/truncation applied
            and should be used for subsequent rounds.

        Raises:
            ContextWindowExceededError: If all recovery layers fail and
                graceful degradation is disabled.
            LLMClientError: For non-context-window errors (after
                fallback / degradation attempts).
        """
        # ---- Preprocessing pipeline --------------------------------------
        messages = await self._preprocess_messages(messages, metadata, on_progress=on_progress)

        # ---- Format for LLM ---------------------------------------------
        # Use the caller-supplied model_id (if any) so the correct
        # model-specific parser is selected (Claude / Llama / GPT).
        effective_model_id = llm_params.get("model_id")
        tools_desc = self.tool_manager.tools_desc if self.tool_manager else None
        formatted = self.llm_client.format_messages(messages, model_id=effective_model_id)

        # ---- Layer 1: Normal call ----------------------------------------
        last_error: Optional[Exception] = None
        try:
            result = await self.llm_client.chat_completion(
                messages=formatted,
                tools_desc=tools_desc,
                **llm_params,
            )
            return result, messages
        except ContextWindowExceededError:
            logger.warning(
                "Context window exceeded — applying aggressive message reduction " f"({len(messages)} messages)"
            )
            metadata["context_window_retries"] += 1

            messages = self._aggressive_message_reduction(messages)

            # Re-preprocess the reduced set with halved thresholds.
            # Normal preprocessing already ran but the remaining messages
            # (especially multi-round tool results) may still exceed the
            # context window.  JSON-heavy tool results tokenize at
            # ~3.0 chars/token, worse than the ~3.3 assumed by the
            # default character thresholds.
            messages = await self._preprocess_messages(
                messages, metadata, on_progress=on_progress, threshold_factor=0.8
            )

            formatted = self.llm_client.format_messages(messages, model_id=effective_model_id)

            logger.info(
                f"Retrying LLM call with reduced messages: " f"{len(messages)} messages after aggressive reduction"
            )
        except LLMClientError as exc:
            # Non-context-window error (throttling, network, etc.)
            # Skip the reduction-based Layer 2 retry (won't help) and
            # fall through to fallback model / graceful degradation.
            logger.warning(f"LLM call failed: {exc.__class__.__name__}: {exc}")
            last_error = exc

        # ---- Layer 2: Retry with reduced messages ------------------------
        # Only attempted after context-window recovery (aggressive reduction).
        # Skipped when Layer 1 failed with a non-context error.
        if last_error is None:
            try:
                result = await self.llm_client.chat_completion(
                    messages=formatted,
                    tools_desc=tools_desc,
                    **llm_params,
                )
                return result, messages
            except (ContextWindowExceededError, LLMClientError) as exc:
                logger.warning(f"Retry with reduced messages failed: {exc.__class__.__name__}: {exc}")
                last_error = exc

        # ---- Layer 3: Fallback model (if configured) ---------------------
        fallback_model = getattr(self.config, "fallback_model", None)
        if fallback_model:
            logger.info(
                f"Attempting fallback model: {fallback_model} " f"(primary failed with {last_error.__class__.__name__})"
            )
            metadata["fallback_model_used"] = True
            fallback_params = {**llm_params, "model_id": fallback_model}

            # Re-format messages for the fallback model — different model
            # families (Claude / Llama / GPT) use different message shapes,
            # so the ``formatted`` variable from the primary model cannot be
            # reused safely.
            formatted = self.llm_client.format_messages(messages, model_id=fallback_model)

            try:
                result = await self.llm_client.chat_completion(
                    messages=formatted,
                    tools_desc=tools_desc,
                    **fallback_params,
                )
                return result, messages
            except (ContextWindowExceededError, LLMClientError) as exc:
                logger.warning(f"Fallback model {fallback_model} also failed: " f"{exc.__class__.__name__}: {exc}")
                last_error = exc

        # ---- Layer 4: Graceful degradation (if enabled) ------------------
        graceful = getattr(self.config, "graceful_degradation", False)
        if graceful:
            logger.warning(
                "All recovery attempts exhausted — returning graceful "
                f"degradation response (last error: {last_error})"
            )
            metadata["graceful_degradation_used"] = True
            return self._graceful_degradation_response(last_error), messages

        # ---- Nothing left — propagate -----------------------------------
        raise last_error

    # ------------------------------------------------------------------
    # Recovery helpers
    # ------------------------------------------------------------------

    def _aggressive_message_reduction(
        self,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Emergency message reduction when context window is exceeded.

        Keeps:
        - The system message (if present)
        - The last ``_EMERGENCY_KEEP_MESSAGES`` user/assistant messages

        This is a last-resort fallback.  The normal preprocessing pipeline
        should prevent most context-window errors; this handles edge cases
        (e.g. a single enormous tool result that slipped through).

        Args:
            messages: The full message list that was too large.

        Returns:
            A drastically reduced message list.
        """
        keep_count = self._EMERGENCY_KEEP_MESSAGES

        system_messages = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        recent = non_system[-keep_count:] if len(non_system) > keep_count else non_system

        reduced = system_messages + recent

        logger.warning(
            f"Aggressive reduction: {len(messages)} → {len(reduced)} messages " f"(kept system + last {len(recent)})"
        )

        return reduced

    @staticmethod
    def _graceful_degradation_response(error: Exception) -> Dict[str, Any]:
        """Build a synthetic apology response when all recovery attempts fail.

        This is the last-resort handler when ``config.graceful_degradation`` is
        enabled.  Instead of raising an exception (which would crash the
        WebSocket handler), it returns a polite, user-facing message so the
        conversation can continue.

        Args:
            error: The last exception encountered.

        Returns:
            A response dict shaped like a normal LLM response.
        """
        return {
            "content": (
                "I'm sorry, I'm having trouble processing your request right now. "
                "The conversation may be too long or complex. "
                "Please try again with a shorter message or start a new conversation."
            ),
            "role": "assistant",
            "tool_calls": [],
            "metadata": {"error": str(error), "degraded": True},
        }

    # ------------------------------------------------------------------
    # Class constants
    # ------------------------------------------------------------------

    _EMERGENCY_KEEP_MESSAGES: int = 4
    """Number of recent non-system messages to keep during emergency reduction."""
