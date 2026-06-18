"""LLM call node — uses ChatBedrockConverse from langchain-aws.

Replaces the old BedrockClient + parser pipeline.
Token usage (input_tokens, output_tokens) is available on the returned
AIMessage.usage_metadata — this is the token tracking unlock from the
migration plan.

Streaming: chunks are forwarded to the on_progress callback so the client
receives typing indicators while the LLM is generating.

Fallback model: if the primary model raises a context-window error,
the node retries once with config.fallback_model (if configured).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from langchain_aws import ChatBedrockConverse
except ImportError:  # pragma: no cover
    ChatBedrockConverse = None  # type: ignore[assignment,misc]

from langchain_core.runnables import RunnableConfig

from ...exceptions import ContextWindowExceededError
from ..state import ChatState

logger = logging.getLogger(__name__)

# Bedrock error codes that indicate a context-window overflow
_CONTEXT_WINDOW_ERROR_CODES = {
    "ValidationException",
    "ServiceUnavailableException",
}
_CONTEXT_WINDOW_PHRASES = (
    "too many tokens",
    "input is too long",
    "context length exceeded",
    "maximum context",
)


def _is_context_window_error(exc: Exception) -> bool:
    """Return True when the exception looks like a context-window overflow."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _CONTEXT_WINDOW_PHRASES)


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
            "model_id": getattr(ai_msg, "response_metadata", {}).get("model_id"),
            "usage": usage,
            "timestamp": datetime.now().isoformat(),
        },
    }


def _build_llm(model_id: str, chat_config: Any, tool_manager: Optional[Any] = None):
    """Construct a ChatBedrockConverse instance for the given model_id.

    Claude via Bedrock Converse API rejects requests that specify both
    ``temperature`` and ``top_p`` simultaneously.  We only pass ``top_p``
    when ``temperature`` is not explicitly configured (i.e., is None).

    When ``tool_manager`` is supplied, the LLM is bound with the tools
    generated from the OpenAPI spec so the model can request tool calls.
    """
    if ChatBedrockConverse is None:
        raise ImportError("langchain-aws is required. Install with: pip install langchain-aws")
    temperature = getattr(chat_config, "temperature", None)
    top_p = getattr(chat_config, "top_p", None)

    kwargs: Dict[str, Any] = {
        "model": model_id,
        "region_name": chat_config.aws_region,
        "max_tokens": chat_config.max_tokens,
    }
    # Explicit credentials override the boto3 credential chain when set
    aws_access_key_id = getattr(chat_config, "aws_access_key_id", None)
    aws_secret_access_key = getattr(chat_config, "aws_secret_access_key", None)
    if aws_access_key_id and aws_secret_access_key:
        kwargs["aws_access_key_id"] = aws_access_key_id
        kwargs["aws_secret_access_key"] = aws_secret_access_key
    # Pass only one of temperature / top_p to avoid ValidationException
    if temperature is not None:
        kwargs["temperature"] = temperature
    elif top_p is not None:
        kwargs["top_p"] = top_p

    llm = ChatBedrockConverse(**kwargs)

    # Bind tools if a ToolManager is available (enables tool-call requests)
    if tool_manager is not None:
        try:
            lc_tools = tool_manager.generate_langchain_tools()
            if lc_tools:
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


async def llm_call_node(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """Call the LLM and append the assistant response to state messages.

    Uses ``ChatBedrockConverse`` from langchain-aws.  Model ID, temperature,
    max_tokens, and top_p come from the ``ChatConfig`` stored in
    ``config["configurable"]["chat_config"]``.

    Streaming:
        Chunks are forwarded to ``state["on_progress"]`` while the model
        generates, so the client sees incremental typing indicators.

    Fallback model:
        If the primary call raises a context-window error and
        ``chat_config.fallback_model`` is set, the node retries once with
        the fallback model and records ``"fallback_model_used": True`` in
        metadata.

    Token usage:
        Surfaced from ``AIMessage.usage_metadata`` into
        ``metadata["usage"]``.
    """
    messages: List[Dict] = state.get("messages", [])
    metadata: Dict = dict(state.get("metadata") or {})
    on_progress = (config.get("configurable") or {}).get("on_progress")
    chat_config = config.get("configurable", {}).get("chat_config")
    tool_manager = (config.get("configurable") or {}).get("tool_manager")

    if chat_config is None:
        raise RuntimeError("llm_call_node: chat_config not found in configurable")

    lc_messages = _to_langchain_messages(messages)
    primary_model = chat_config.model_id
    fallback_model = getattr(chat_config, "fallback_model", None)

    # --- Primary call ---
    try:
        llm = _build_llm(primary_model, chat_config, tool_manager=tool_manager)
        ai_msg = await _invoke_with_streaming(llm, lc_messages, on_progress)
        metadata["fallback_model_used"] = False
    except Exception as exc:
        if fallback_model and _is_context_window_error(exc):
            logger.warning(
                "Context-window error on %s; retrying with fallback model %s",
                primary_model,
                fallback_model,
            )
            try:
                llm_fb = _build_llm(fallback_model, chat_config, tool_manager=tool_manager)
                ai_msg = await _invoke_with_streaming(llm_fb, lc_messages, on_progress)
                metadata["fallback_model_used"] = True
                metadata["fallback_model"] = fallback_model
            except Exception as fb_exc:
                raise ContextWindowExceededError(
                    f"Both primary ({primary_model}) and fallback ({fallback_model}) models failed"
                ) from fb_exc
        else:
            raise

    response_dict = _from_langchain_message(ai_msg)

    # Bubble up token usage into top-level metadata for easy access
    usage = response_dict.get("metadata", {}).get("usage", {})
    if usage:
        metadata["input_tokens"] = usage.get("input_tokens")
        metadata["output_tokens"] = usage.get("output_tokens")

    return {"messages": list(messages) + [response_dict], "metadata": metadata}
