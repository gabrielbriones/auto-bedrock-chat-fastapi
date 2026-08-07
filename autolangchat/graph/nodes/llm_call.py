"""LLM call node — uses ChatBedrockConverse from langchain-aws.

Replaces the old BedrockClient + parser pipeline.
Token usage (input_tokens, output_tokens) is available on the returned
AIMessage.usage_metadata — this is the token tracking unlock from the
migration plan.

Streaming: chunks are forwarded to the on_progress callback so the client
receives typing indicators while the LLM is generating.

Fallback model: if the primary model raises a context-window error,
the node retries once with config.fallback_model (if configured).

Inference-profile retry: some models can only be invoked through a
cross-region inference profile id (e.g. a "us."-prefixed id), not the bare
foundation-model id. Bedrock's own ValidationException says so explicitly
("... retry your request with the ID or ARN of an inference profile that
contains this model"), so rather than hardcoding which model ids are
affected (Bedrock's supported set changes over time), the node detects that
specific error and retries once with a region-prefixed id derived from
config.aws_region.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from langchain_aws import ChatBedrockConverse
except ImportError:  # pragma: no cover
    ChatBedrockConverse = None  # type: ignore[assignment,misc]

from langchain_core.runnables import RunnableConfig

from ...config import MODEL_ID_REGION_PREFIXES, split_model_id
from ...exceptions import ContextWindowExceededError, ModelInvocationError
from ...message_preprocessor import MessagePreprocessor
from ...model_capabilities import DEFAULT_READ_TIMEOUT, build_bedrock_kwargs, supports_tool_calling
from ..state import ChatState

logger = logging.getLogger(__name__)

# Bedrock error codes that indicate a context-window overflow
_CONTEXT_WINDOW_ERROR_CODES = {
    "ValidationException",
    "ServiceUnavailableException",
}
# Multiplier applied to all truncation thresholds when retrying after a
# context-window error (see MessagePreprocessor.preprocess_messages'
# threshold_factor). 0.5 halves every threshold/target for one aggressive
# re-truncation pass before falling back to a different model.
_EMERGENCY_TRUNCATION_THRESHOLD_FACTOR = 0.5

_CONTEXT_WINDOW_PHRASES = (
    "too many tokens",
    "input is too long",
    "context length exceeded",
    "maximum context",
)

# Substring seen in Bedrock's ValidationException message when a model can
# only be invoked through a cross-region inference profile ID (e.g. a
# "us."-prefixed id) rather than the bare foundation-model id -- e.g.
# "Invocation of model ID meta.llama3-3-70b-instruct-v1:0 with on-demand
# throughput isn't supported. Retry your request with the ID or ARN of an
# inference profile that contains this model." This isn't specific to any
# one model/provider -- which ids are affected changes as Bedrock rolls out
# new models, and _PROFILES has no flag for it -- so rather than maintaining
# a denylist, `llm_call_node` retries once with a region-prefixed id whenever
# Bedrock itself reports this (see `_retry_model_id_with_inference_profile`).
_INFERENCE_PROFILE_REQUIRED_PHRASE = "on-demand throughput isn't supported"

# AWS region prefix (e.g. "us-east-1") -> Bedrock cross-region
# inference-profile prefix. Falls back to "us" (the broadest-coverage,
# most commonly available profile) for regions not covered here -- see
# `_infer_profile_prefix_for_region`.
_REGION_TO_PROFILE_PREFIX = {
    "us": "us",
    "eu": "eu",
    "ap-northeast": "jp",
    "ap-southeast-2": "au",
}

# Bedrock's real error text uses a typographic right single quotation mark
# (U+2019, "isn\u2019t") rather than a plain ASCII apostrophe (U+0027,
# "isn't") -- unlike our hardcoded constant above, which was written with a
# plain apostrophe. Without normalizing, the substring check below silently
# never matches against a real Bedrock exception, so the inference-profile
# retry never fires (it only "worked" against test fixtures that happened to
# use the same plain apostrophe). Normalize both sides to ASCII before
# comparing so either quote style matches.
_APOSTROPHE_VARIANTS = ("\u2019", "\u2018", "`")


def _normalize_apostrophes(text: str) -> str:
    """Replace typographic apostrophe variants with a plain ASCII ``'``."""
    for variant in _APOSTROPHE_VARIANTS:
        text = text.replace(variant, "'")
    return text


def _is_context_window_error(exc: Exception) -> bool:
    """Return True when the exception looks like a context-window overflow."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _CONTEXT_WINDOW_PHRASES)


def _requires_inference_profile(exc: Exception) -> bool:
    """Return True when Bedrock rejected the request because this model can
    only be invoked through a cross-region inference profile id."""
    return _INFERENCE_PROFILE_REQUIRED_PHRASE in _normalize_apostrophes(str(exc).lower())


def _infer_profile_prefix_for_region(aws_region: Optional[str]) -> str:
    """Best-guess Bedrock inference-profile prefix ("us", "eu", "jp", "au")
    for a given AWS region string, e.g. "us-east-1" -> "us". Defaults to
    "us" -- the broadest-coverage profile -- when the region is unset or
    isn't one of the handful of prefixes we know about."""
    region = (aws_region or "").lower()
    for region_prefix, profile_prefix in _REGION_TO_PROFILE_PREFIX.items():
        if region.startswith(region_prefix):
            return profile_prefix
    return "us"


def _retry_model_id_with_inference_profile(model_id: str, chat_config: Any) -> Optional[str]:
    """Return a region-prefixed retry id for ``model_id`` (e.g.
    "meta.llama3-3-70b-instruct-v1:0" -> "us.meta.llama3-3-70b-instruct-v1:0"),
    or ``None`` if ``model_id`` already carries a recognized region prefix
    (nothing left to retry with)."""
    region_prefix, _provider_key = split_model_id(model_id)
    if region_prefix in MODEL_ID_REGION_PREFIXES:
        return None
    prefix = _infer_profile_prefix_for_region(getattr(chat_config, "aws_region", None))
    return f"{prefix}.{model_id}"


def _extract_json_object(text: str) -> Any:
    """Best-effort ``json.loads`` of ``text``, tolerating leading/trailing
    prose around a single JSON object/array (some models wrap a tool-call
    attempt in a sentence or code fence instead of returning bare JSON).

    Returns ``None`` when no JSON object/array could be parsed.
    """
    stripped = text.strip().strip("`").strip()
    for candidate in (stripped, text):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            pass
    start = min((i for i in (stripped.find("{"), stripped.find("[")) if i != -1), default=-1)
    end = max(stripped.rfind("}"), stripped.rfind("]"))
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(stripped[start : end + 1])
    except (ValueError, TypeError):
        return None


def _coerce_tool_args(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the argument mapping out of a text-emitted tool call, tolerating
    both the ``parameters`` (Llama/Bedrock) and ``arguments`` (OpenAI) keys
    and an arguments value that is itself a JSON *string* rather than an
    object. Returns ``{}`` when nothing usable is present -- a tool call with
    no arguments is still a valid call.
    """
    args = candidate.get("parameters")
    if args is None:
        args = candidate.get("arguments")
    if isinstance(args, str):
        decoded = _extract_json_object(args)
        args = decoded if isinstance(decoded, dict) else {}
    return args if isinstance(args, dict) else {}


def _recover_tool_calls_from_text(content: Any, tool_names: set) -> List[Dict[str, Any]]:
    """Recover tool calls that the model emitted as plain response *text*
    instead of Bedrock returning them as structured ``toolUse`` blocks.

    Meta's Llama models are fine-tuned to emit tool calls as bare JSON --
    ``{"type": "function", "name": "download_file", "parameters": {...}}`` --
    and Bedrock's Converse implementation does not reliably translate that
    into a ``toolUse`` content block. When it doesn't, ``AIMessage.tool_calls``
    stays empty, ``routing.should_continue()`` ends the turn, and the raw JSON
    is shown to the user as if it were the final answer (XMGPLAT-11193).

    This is the same recovery the pre-LangGraph ``LlamaParser`` performed with
    a regex over the raw completion; it is reinstated here as a provider-
    agnostic fallback on top of the Converse API. Only called when
    ``tool_calls`` is already empty and tools were bound for this turn.

    Matching is deliberately narrow -- the parsed object (or an item of a
    parsed list) must be a dict whose ``name`` matches one of the tools
    actually bound for this call, and it must carry a
    ``parameters``/``arguments`` key or an explicit ``"type": "function"``
    marker -- so a model legitimately discussing or returning JSON that merely
    mentions a tool by name isn't misread as a call.

    Returns LangChain-style tool-call dicts (``name``/``args``/``id``/``type``),
    or an empty list when nothing recoverable is found.
    """
    if not isinstance(content, str) or not tool_names:
        return []
    parsed = _extract_json_object(content)
    candidates = parsed if isinstance(parsed, list) else [parsed]
    recovered: List[Dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        name = candidate.get("name")
        if name not in tool_names:
            continue
        if not (candidate.get("type") == "function" or "parameters" in candidate or "arguments" in candidate):
            continue
        recovered.append(
            {
                "name": name,
                "args": _coerce_tool_args(candidate),
                "id": f"recovered-{uuid.uuid4()}",
                "type": "tool_call",
            }
        )
    return recovered


def _wrap_model_invocation_error(model_id: str, exc: Exception, chat_config: Any) -> ModelInvocationError:
    """Wrap a raw Bedrock/langchain-aws exception into a ``ModelInvocationError``
    with a clear, specific, user-facing message -- instead of letting a bare
    ``ValidationException`` bubble up to be flattened into the generic
    "I'm having trouble with the AI model" bucket in
    ``AutoLangChatWebSocketHandler._create_error_response()``.
    """
    display_name = (
        chat_config.get_model_display_name(model_id) if hasattr(chat_config, "get_model_display_name") else model_id
    )
    reason = str(exc)
    hint = ""
    if _INFERENCE_PROFILE_REQUIRED_PHRASE in _normalize_apostrophes(reason.lower()):
        hint = (
            " This model can only be invoked through a cross-region inference "
            "profile ID (e.g. a 'us.' prefix), not the bare model ID -- it "
            "should be removed from AUTOCHAT_AVAILABLE_MODELS / the settings "
            "sidebar catalog until a working profile ID is available."
        )
    message = f"Model '{display_name}' ({model_id}) rejected the request: {reason}.{hint}"
    return ModelInvocationError(message, model_id=model_id, reason=reason)


def _to_langchain_messages(message_dicts: List[Dict]) -> List[Any]:
    """Convert internal dict messages to LangChain BaseMessage objects."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    lc_messages = []

    for msg in message_dicts:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            lc_messages.append(AIMessage(content=content, tool_calls=tool_calls))
        elif role == "tool":
            tool_results = msg.get("tool_results") or []
            for tr in tool_results:
                lc_messages.append(
                    ToolMessage(
                        content=str(tr.get("result") or tr.get("error", "")),
                        tool_call_id=tr.get("tool_call_id", ""),
                        name=tr.get("name", ""),
                    )
                )
    return lc_messages


def _from_langchain_message(ai_msg: Any) -> Dict:
    """Convert an AIMessage back to internal dict format.

    Claude Bedrock Converse can return structured content (a list of
    content blocks like ``[{"type": "text", "text": "...", "index": 0}]``).
    We normalise that to a plain string.
    """
    raw_content = ai_msg.content
    if isinstance(raw_content, str):
        content = raw_content
    elif isinstance(raw_content, list):
        # Extract text blocks; concatenate in order
        parts = []
        for block in raw_content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        content = "".join(parts)
    else:
        content = str(raw_content)
    tool_calls = getattr(ai_msg, "tool_calls", []) or []

    usage = {}
    if hasattr(ai_msg, "usage_metadata") and ai_msg.usage_metadata:
        usage = {
            "input_tokens": ai_msg.usage_metadata.get("input_tokens"),
            "output_tokens": ai_msg.usage_metadata.get("output_tokens"),
        }

    return {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
        "metadata": {
            "message_id": str(uuid.uuid4()),
            "model_id": getattr(ai_msg, "response_metadata", {}).get("modelId")
            or getattr(ai_msg, "response_metadata", {}).get("model_id"),
            "usage": usage,
            "timestamp": datetime.now().isoformat(),
        },
    }


def _build_llm(model_id: str, chat_config: Any):
    """Construct a ChatBedrockConverse instance for the given model_id.

    Parameter selection is delegated to
    :func:`~autolangchat.model_capabilities.build_bedrock_kwargs`, which drops
    parameters the model doesn't support (e.g. ``temperature``/``top_p`` for
    reasoning models) and clamps ``max_tokens`` to the model's output cap, so
    every model in the catalog is usable and not just Claude Sonnet 5.

    When ``chat_config.langchain_tools`` is populated and the model supports
    tool calling, the LLM is bound with those tools so the model can request
    tool calls.
    """
    if ChatBedrockConverse is None:
        raise ImportError("langchain-aws is required. Install with: pip install langchain-aws")

    # Set a generous read timeout on the underlying boto3 client so that
    # large-output requests (e.g. max_tokens=8192) don't hit the default 60s
    # botocore limit.  chat_config.timeout (default 30s) is intentionally
    # only a floor input here because it governs tool-call HTTP timeouts, not
    # Bedrock generation time.
    kwargs = build_bedrock_kwargs(
        model_id,
        chat_config,
        read_timeout=getattr(chat_config, "timeout", None) or DEFAULT_READ_TIMEOUT,
    )

    logger.debug("Building ChatBedrockConverse: args=%s", kwargs)

    llm = ChatBedrockConverse(**kwargs)

    # Bind tools from config if available (enables tool-call requests)
    lc_tools = getattr(chat_config, "langchain_tools", None)
    if lc_tools and not supports_tool_calling(model_id):
        logger.warning(
            "Model '%s' does not support tool calling; skipping bind_tools for %d tool(s)",
            model_id,
            len(lc_tools),
        )
    elif lc_tools:
        try:
            llm = llm.bind_tools(lc_tools)
            logger.debug("LLM bound with %d tool(s)", len(lc_tools))
        except Exception as exc:
            logger.warning("Could not bind tools to LLM: %s", exc)

    return llm


async def _invoke_with_streaming(
    llm: Any,
    lc_messages: List[Any],
    on_progress: Optional[Any],
) -> Any:
    """Invoke the LLM, streaming chunks to on_progress if provided.

    Accumulates chunks and returns the final AIMessage so the rest of
    the node can treat streaming and non-streaming identically.
    """
    if on_progress is None:
        return await llm.ainvoke(lc_messages)

    # Stream and forward chunks as typing indicators
    chunks = []
    async for chunk in llm.astream(lc_messages):
        chunks.append(chunk)
        content_so_far = "".join(c.content for c in chunks if isinstance(c.content, str))
        if content_so_far:
            try:
                await on_progress(
                    {
                        "type": "typing",
                        "message": content_so_far,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
            except Exception:
                pass  # never let progress errors abort the LLM call

    if not chunks:
        return await llm.ainvoke(lc_messages)

    # Merge chunks into a single AIMessage
    result = chunks[0]
    for chunk in chunks[1:]:
        result = result + chunk
    return result


def _generate_message_preview(content: Any, max_preview_len: int = 100) -> tuple:
    """Return (content_length, preview_string) for debug logging."""
    if isinstance(content, str):
        content_len = len(content)
        preview = content[:max_preview_len].replace("\n", " ")
        if len(content) > max_preview_len:
            preview += "..."
    elif isinstance(content, list):
        content_len = sum(len(str(item)) for item in content)
        text_parts = [
            (
                item.get("text", "")
                if isinstance(item, dict) and item.get("type") == "text"
                else str(item)[:max_preview_len]
            )
            for item in content[:2]
        ]
        preview = " | ".join(text_parts)[:max_preview_len]
        if len(content) > 2 or content_len > max_preview_len:
            preview += "..."
    else:
        content_len = len(str(content))
        preview = str(content)[:max_preview_len] + "..."
    return content_len, preview


def _log_conversation(messages: List[Dict], label: str) -> None:
    """Log message states at DEBUG level — role, sizes, tool call/result counts."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    logger.debug("%s: %d messages", label, len(messages))
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls") or []
        tool_results = msg.get("tool_results") or []
        content_len, preview = _generate_message_preview(content)
        role_label = f"{role} [system prompt]" if role == "system" else role
        logger.debug(
            "  [%d] %s (%s chars, tool_calls=%d, tool_results=%d): %s",
            i,
            role_label,
            f"{content_len:,}",
            len(tool_calls),
            len(tool_results),
            preview,
        )
        for j, tr in enumerate(tool_results):
            if not isinstance(tr, dict):
                continue
            logger.debug(
                "      - tool_result[%d] name=%s tool_call_id=%s",
                j,
                tr.get("name"),
                tr.get("tool_call_id") or tr.get("tool_use_id"),
            )
        for j, tc in enumerate(tool_calls):
            logger.debug("      - tool_call[%d] id=%s name=%s", j, tc.get("id"), tc.get("name"))


async def llm_call_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Call the LLM and append the assistant response to state messages.

    Uses ``ChatBedrockConverse`` from langchain-aws.  Model ID, temperature,
    max_tokens, and top_p come from the ``ChatConfig`` stored in
    ``config["configurable"]["chat_config"]``.

    Streaming:
        Chunks are forwarded to ``state["on_progress"]`` while the model
        generates, so the client sees incremental typing indicators.

    Context-window error recovery (two safety nets, in order):
        1. Emergency re-truncation: the conversation is re-preprocessed
           with ``threshold_factor=0.5`` (halving every truncation
           threshold/target) and retried once against the *same* model.
           Sets ``metadata["emergency_retruncation_applied"] = True`` as
           soon as re-truncation runs, regardless of whether the retry
           call itself then succeeds (if it doesn't, safety net 2 still
           runs and this flag stays ``True``).
        2. Fallback model: if re-truncation still fails and
           ``chat_config.fallback_model`` is set, the node retries once
           more with the fallback model and records
           ``metadata["fallback_model_used"] = True``.

    Token usage:
        Surfaced from ``AIMessage.usage_metadata`` into
        ``metadata["usage"]``.
    """
    messages: List[Dict] = state.get("messages", [])
    metadata: Dict = dict(state.get("metadata") or {})
    on_progress = (config.get("configurable") or {}).get("on_progress")
    chat_config = config.get("configurable", {}).get("chat_config")

    if chat_config is None:
        raise RuntimeError("llm_call_node: chat_config not found in configurable")

    lc_messages = _to_langchain_messages(messages)
    primary_model = chat_config.model_id
    fallback_model = getattr(chat_config, "fallback_model", None)

    _log_conversation(messages, "LLM call — conversation state")

    # --- Primary call ---
    try:
        llm = _build_llm(primary_model, chat_config)
        ai_msg = await _invoke_with_streaming(llm, lc_messages, on_progress)
        metadata["fallback_model_used"] = False
    except Exception as exc:
        if _is_context_window_error(exc):
            # --- Safety net 1: emergency re-truncation, same model ---
            # Re-run preprocessing with all thresholds halved (threshold_factor)
            # and retry the primary model once before switching models.
            logger.warning(
                "Context-window error on %s; retrying with emergency re-truncation "
                "(threshold_factor=%s) before considering a fallback model",
                primary_model,
                _EMERGENCY_TRUNCATION_THRESHOLD_FACTOR,
            )
            try:
                retruncated = await MessagePreprocessor(config=chat_config).preprocess_messages(
                    messages=list(messages),
                    on_progress=on_progress,
                    threshold_factor=_EMERGENCY_TRUNCATION_THRESHOLD_FACTOR,
                )
                # Mark the safety net as applied as soon as re-truncation runs --
                # regardless of whether the subsequent retry call itself succeeds
                # (a failed retry still falls back to safety net 2 having gone
                # through this path, which callers need to be able to observe).
                metadata["emergency_retruncation_applied"] = True
                llm = _build_llm(primary_model, chat_config)
                ai_msg = await _invoke_with_streaming(llm, _to_langchain_messages(retruncated), on_progress)
                metadata["fallback_model_used"] = False
            except Exception as retry_exc:
                if not (fallback_model and _is_context_window_error(retry_exc)):
                    raise ContextWindowExceededError(
                        f"Primary model ({primary_model}) failed even after emergency re-truncation"
                    ) from retry_exc

                # --- Safety net 2: fallback model ---
                logger.warning(
                    "Emergency re-truncation insufficient on %s; retrying with fallback model %s",
                    primary_model,
                    fallback_model,
                )
                try:
                    llm_fb = _build_llm(fallback_model, chat_config)
                    ai_msg = await _invoke_with_streaming(llm_fb, lc_messages, on_progress)
                    metadata["fallback_model_used"] = True
                    metadata["fallback_model"] = fallback_model
                except Exception as fb_exc:
                    raise ContextWindowExceededError(
                        f"Primary ({primary_model}), emergency re-truncation, and fallback "
                        f"({fallback_model}) models all failed"
                    ) from fb_exc
        elif _requires_inference_profile(exc) and (
            profile_model_id := _retry_model_id_with_inference_profile(primary_model, chat_config)
        ):
            # Bedrock rejected the bare model id and told us to use a
            # cross-region inference profile id instead -- retry once with
            # one derived from chat_config.aws_region, rather than failing
            # outright or requiring every such model to be manually
            # allow/deny-listed ahead of time.
            logger.warning(
                "%s requires a cross-region inference profile; retrying as %s",
                primary_model,
                profile_model_id,
            )
            try:
                llm_profile = _build_llm(profile_model_id, chat_config)
                ai_msg = await _invoke_with_streaming(llm_profile, lc_messages, on_progress)
                metadata["fallback_model_used"] = False
                metadata["inference_profile_model_id"] = profile_model_id
            except Exception as profile_exc:
                logger.error(
                    "Inference-profile retry also failed for %s (tried %s): %s",
                    primary_model,
                    profile_model_id,
                    profile_exc,
                )
                raise _wrap_model_invocation_error(primary_model, profile_exc, chat_config) from profile_exc
        else:
            logger.error("LLM call failed for model %s: %s", primary_model, exc)
            raise _wrap_model_invocation_error(primary_model, exc, chat_config) from exc

    response_dict = _from_langchain_message(ai_msg)

    # Some models (notably Meta Llama, XMGPLAT-11193) emit a tool call as
    # plain response text -- `{"type": "function", "name": "download_file",
    # "parameters": {...}}` -- instead of Bedrock returning it as a structured
    # toolUse block. `tool_calls` then stays empty, routing.should_continue()
    # ends the turn, and the raw JSON is shown to the user as if it were the
    # answer. Recover the call here so the graph routes to the tools node,
    # which is what the pre-LangGraph LlamaParser did via regex.
    if not response_dict.get("tool_calls"):
        bound_tool_names = {getattr(t, "name", None) for t in (getattr(chat_config, "langchain_tools", None) or [])}
        bound_tool_names.discard(None)
        recovered = _recover_tool_calls_from_text(response_dict.get("content"), bound_tool_names)
        if recovered:
            effective_model_id = metadata.get("inference_profile_model_id") or (
                metadata.get("fallback_model") if metadata.get("fallback_model_used") else primary_model
            )
            logger.warning(
                "Model %s emitted %d tool call(s) as plain text instead of structured toolUse "
                "block(s); recovering: %s",
                effective_model_id,
                len(recovered),
                [tc["name"] for tc in recovered],
            )
            response_dict["tool_calls"] = recovered
            # Drop the raw JSON from the visible answer -- it is the call, not
            # prose, and replaying it as assistant text would confuse the next
            # turn now that a real tool_call carries the same information.
            response_dict["content"] = ""
            metadata["recovered_text_tool_calls"] = metadata.get("recovered_text_tool_calls", 0) + len(recovered)

    if logger.isEnabledFor(logging.DEBUG):
        content_len, preview = _generate_message_preview(response_dict.get("content", ""))
        tool_calls = response_dict.get("tool_calls") or []
        logger.debug(
            "LLM response — assistant (%s chars, tool_calls=%d): %s",
            f"{content_len:,}",
            len(tool_calls),
            preview,
        )
        for j, tc in enumerate(tool_calls):
            logger.debug("  - tool_call[%d] id=%s name=%s", j, tc.get("id"), tc.get("name"))

    # Fill in model_id from config if Bedrock didn't return it in response_metadata
    if not response_dict.get("metadata", {}).get("model_id"):
        response_dict["metadata"]["model_id"] = (
            metadata.get("fallback_model") if metadata.get("fallback_model_used") else primary_model
        )

    # Accumulate token usage into top-level metadata across tool-call rounds
    usage = response_dict.get("metadata", {}).get("usage", {})
    if usage:
        metadata["input_tokens"] = (metadata.get("input_tokens") or 0) + (usage.get("input_tokens") or 0)
        metadata["output_tokens"] = (metadata.get("output_tokens") or 0) + (usage.get("output_tokens") or 0)

    # Update model_id with the actual model used (may differ if fallback was triggered)
    metadata["model_id"] = response_dict.get("metadata", {}).get("model_id") or primary_model

    return {"messages": list(messages) + [response_dict], "metadata": metadata}
