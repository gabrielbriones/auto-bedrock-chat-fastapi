"""Assemble and compile the autolangchat StateGraph.

Usage
-----
    from autolangchat.graph.graph import build_chat_graph

    graph = build_chat_graph(config, tool_manager, token_usage_store)
    state = await graph.ainvoke(
        {"messages": message_dicts, "metadata": {}},
        config={"configurable": {
            "thread_id": session_id,
            "on_progress": cb,
            "auth_info": auth_info,
        }},
    )

Graph topology
------------------------

    START → init_turn → rag → preprocess → llm → citation_boost → token_usage → END
                                                ↓             ↑
                                          tools_execution      |
                                                ↓             |
                                          (loops back to llm) ─┘

MemorySaver can be swapped for AsyncPostgresSaver.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, Union

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .checkpointer import build_checkpointer
from .nodes.citation_boost import citation_boost_node
from .nodes.init_turn import init_turn_node
from .nodes.llm_call import llm_call_node
from .nodes.preprocess import preprocess_node
from .nodes.rag import rag_node
from .nodes.token_usage import token_usage_node
from .routing import should_continue
from .state import ChatState, InputState, OutputState
from .tools.tool_node import tools_execution_node

if TYPE_CHECKING:
    from ..config import ChatConfig
    from ..db import BaseTokenUsageStore
    from .tools.manager import ToolManager

logger = logging.getLogger(__name__)


async def _resolve_token_usage_store(token_usage_store: Any) -> Any:
    """Resolve ``token_usage_store``, which may be a live instance or a
    zero-arg callable returning the current instance (or an awaitable of it).

    A callable lets a host (e.g. ``AutoLangChatPlugin``) hand the graph a
    live lookup (``lambda: self._token_usage_store``) instead of a frozen
    reference -- if the store gets disabled at runtime (e.g. its ``open()``
    fails during startup, after the graph was already compiled), callers
    that don't override ``configurable["token_usage_store"]`` themselves
    still see the up-to-date value instead of a stale, never-opened store.

    Recognized as providers: plain functions/lambdas/bound methods
    (``inspect.isfunction``/``ismethod``) and ``functools.partial``. Not
    ``callable()`` in general -- a real store instance (or a test double
    standing in for one, e.g. ``MagicMock()``) is itself callable but must
    be used as-is, not invoked. If invoking the provider returns an
    awaitable (e.g. an async provider), it is awaited before returning.

    Token-usage recording is best-effort: if the provider itself raises
    (misconfigured, transient failure, etc.), the error is logged and
    ``None`` is returned instead of propagating and failing the whole turn.
    """
    if (
        inspect.isfunction(token_usage_store)
        or inspect.ismethod(token_usage_store)
        or isinstance(token_usage_store, functools.partial)
    ):
        try:
            result = token_usage_store()
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.exception("token_usage_store provider raised; treating as unavailable for this turn")
            return None
        return result
    return token_usage_store


def _inject_node_config(
    chat_config: Any, tool_manager: Any, token_usage_store: Any, node_fn, resolve_token_usage_store: bool = False
):
    """Wrap a node function so chat_config/tool_manager are always injected.

    LangGraph's with_config() doesn't deep-merge configurable when the caller
    supplies their own config dict, so we inject at node call time via a closure.

    ``resolve_token_usage_store`` should only be set for the ``token_usage``
    node -- it's the only node that reads ``token_usage_store`` from
    ``configurable``, so resolving it (potentially awaiting an async
    provider) on every other node call would be wasted work, up to once per
    node per turn.
    """

    async def _wrapped(state, config: RunnableConfig):
        configurable = dict(config.get("configurable") or {})
        if "chat_config" not in configurable:
            configurable["chat_config"] = chat_config
        if "tool_manager" not in configurable and tool_manager is not None:
            configurable["tool_manager"] = tool_manager
        if resolve_token_usage_store and getattr(configurable["chat_config"], "token_usage_enabled", False):
            # A per-call override in configurable takes priority, but still
            # needs the same provider-vs-instance resolution as the
            # graph-level default. Skipped entirely when disabled --
            # token_usage_node no-ops anyway, so resolving (and potentially
            # awaiting/failing) the provider would be wasted work.
            configurable["token_usage_store"] = await _resolve_token_usage_store(
                configurable.get("token_usage_store", token_usage_store)
            )
        config = {**config, "configurable": configurable}
        return await node_fn(state, config)

    _wrapped.__name__ = node_fn.__name__
    _wrapped.__qualname__ = node_fn.__qualname__
    return _wrapped


def build_chat_graph(
    config: "ChatConfig",
    tool_manager: Optional["ToolManager"] = None,
    token_usage_store: Optional[
        Union[
            "BaseTokenUsageStore",
            Callable[[], Optional["BaseTokenUsageStore"]],
            Callable[[], Awaitable[Optional["BaseTokenUsageStore"]]],
        ]
    ] = None,
):
    """Build and compile the chat StateGraph.

    Parameters
    ----------
    config:
        Application ``ChatConfig``.  Stored in the graph's ``configurable``
        namespace so every node can access it without global state.
    tool_manager:
        Optional pre-built ``ToolManager`` instance. If not provided, the
        graph operates without tools.
    token_usage_store:
        Optional pre-built, already-opened ``BaseTokenUsageStore`` instance
        (e.g. ``AutoLangChatPlugin._token_usage_store``), or a zero-arg
        callable (sync or async) returning the current instance (e.g.
        ``lambda: self._token_usage_store``) -- use a callable when the store
        can be disabled at runtime after the graph is built (e.g. a startup
        ``open()`` failure), so callers see the up-to-date value instead of
        a stale reference. Resolved once per turn and injected into the
        ``token_usage`` node's ``configurable`` so ``token_usage_node``
        records usage for *any* ``ainvoke()`` caller -- not just the
        WebSocket handler -- without that caller having to pass it
        explicitly. A caller may still override it per-call via
        ``config["configurable"]["token_usage_store"]``.

    Returns
    -------
    CompiledGraph
        A compiled LangGraph graph ready for ``ainvoke`` / ``astream``.
    """
    builder = StateGraph(ChatState, input=InputState, output=OutputState)

    # Nodes — wrapped so chat_config/tool_manager are always injected;
    # token_usage_store is only resolved for the token_usage node (see
    # _inject_node_config's resolve_token_usage_store param).
    builder.add_node("init_turn", _inject_node_config(config, tool_manager, token_usage_store, init_turn_node))
    builder.add_node("rag", _inject_node_config(config, tool_manager, token_usage_store, rag_node))
    builder.add_node("preprocess", _inject_node_config(config, tool_manager, token_usage_store, preprocess_node))
    builder.add_node("llm", _inject_node_config(config, tool_manager, token_usage_store, llm_call_node))
    builder.add_node(
        "citation_boost", _inject_node_config(config, tool_manager, token_usage_store, citation_boost_node)
    )
    builder.add_node(
        "token_usage",
        _inject_node_config(config, tool_manager, token_usage_store, token_usage_node, resolve_token_usage_store=True),
    )

    # Edges
    builder.add_edge(START, "init_turn")
    builder.add_edge("init_turn", "rag")
    builder.add_edge("rag", "preprocess")
    builder.add_edge("preprocess", "llm")

    if tool_manager is not None:
        builder.add_node("tools", _inject_node_config(config, tool_manager, token_usage_store, tools_execution_node))
        builder.add_conditional_edges(
            "llm",
            should_continue,
            {"tools": "tools", "__end__": "citation_boost"},
        )
        # tools node loops back through preprocess so tool results are truncated
        # before being sent to the LLM (avoids context-window overflow)
        builder.add_edge("tools", "preprocess")
    else:
        builder.add_edge("llm", "citation_boost")

    builder.add_edge("citation_boost", "token_usage")
    builder.add_edge("token_usage", END)

    # Checkpointer: MemorySaver by default; can be swapped for Postgres (AsyncPostgresSaver).
    # The pool is created closed here and opened in the FastAPI startup event via
    # open_checkpointer().
    postgres_url = getattr(config, "checkpoint_postgres_url", None)
    pool_size = getattr(config, "checkpoint_pool_size", 5)
    checkpointer = build_checkpointer(postgres_url=postgres_url, pool_size=pool_size)

    graph = builder.compile(checkpointer=checkpointer)

    topology = (
        "init_turn → rag → preprocess → llm → [tools → preprocess → llm →]* citation_boost → token_usage → END"
        if tool_manager is not None
        else "init_turn → rag → preprocess → llm → citation_boost → token_usage → END"
    )
    logger.info(
        "LangGraph chat graph compiled (nodes: %s, checkpointer: %s)",
        topology,
        type(checkpointer).__name__,
    )
    return graph
