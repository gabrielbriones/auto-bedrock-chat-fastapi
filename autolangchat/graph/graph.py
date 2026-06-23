"""Assemble and compile the autolangchat StateGraph.

Usage
-----
    from autolangchat.graph.graph import build_chat_graph

    graph = build_chat_graph(config, tool_manager)
    state = await graph.ainvoke(
        {"messages": message_dicts, "metadata": {}},
        config={"configurable": {
            "thread_id": session_id,
            "on_progress": cb,
            "auth_info": auth_info,
        }},
    )

Graph topology (Phase 2)
------------------------

    START → preprocess → llm → [should_continue] → END
                                        ↓
                                  tools_execution
                                        ↓
                                (loops back to llm)

Phase 3 swaps MemorySaver for AsyncPostgresSaver.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .checkpointer import build_checkpointer
from .nodes.init_turn import init_turn_node
from .nodes.llm_call import llm_call_node
from .nodes.preprocess import preprocess_node
from .nodes.rag import rag_node
from .routing import should_continue
from .state import ChatState, InputState, OutputState
from .tools.tool_node import tools_execution_node

if TYPE_CHECKING:
    from ..config import ChatConfig
    from .tools.manager import ToolManager

logger = logging.getLogger(__name__)


def _inject_node_config(chat_config: Any, tool_manager: Any, node_fn):
    """Wrap a node function so chat_config and tool_manager are always injected.

    LangGraph's with_config() doesn't deep-merge configurable when the caller
    supplies their own config dict, so we inject at node call time via a closure.
    """

    async def _wrapped(state, config: RunnableConfig):
        configurable = dict(config.get("configurable") or {})
        if "chat_config" not in configurable:
            configurable["chat_config"] = chat_config
        if "tool_manager" not in configurable and tool_manager is not None:
            configurable["tool_manager"] = tool_manager
        config = {**config, "configurable": configurable}
        return await node_fn(state, config)

    _wrapped.__name__ = node_fn.__name__
    _wrapped.__qualname__ = node_fn.__qualname__
    return _wrapped


def build_chat_graph(
    config: "ChatConfig",
    tool_manager: Optional["ToolManager"] = None,
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

    Returns
    -------
    CompiledGraph
        A compiled LangGraph graph ready for ``ainvoke`` / ``astream``.
    """
    builder = StateGraph(ChatState, input=InputState, output=OutputState)

    # Nodes — wrapped so chat_config and tool_manager are always injected
    builder.add_node("init_turn", _inject_node_config(config, tool_manager, init_turn_node))
    builder.add_node("rag", _inject_node_config(config, tool_manager, rag_node))
    builder.add_node("preprocess", _inject_node_config(config, tool_manager, preprocess_node))
    builder.add_node("llm", _inject_node_config(config, tool_manager, llm_call_node))

    # Edges
    builder.add_edge(START, "init_turn")
    builder.add_edge("init_turn", "rag")
    builder.add_edge("rag", "preprocess")
    builder.add_edge("preprocess", "llm")

    if tool_manager is not None:
        builder.add_node("tools", _inject_node_config(config, tool_manager, tools_execution_node))
        builder.add_conditional_edges(
            "llm",
            should_continue,
            {"tools": "tools", "__end__": END},
        )
        # tools node loops back to llm for multi-round tool calling
        builder.add_edge("tools", "llm")
    else:
        builder.add_edge("llm", END)

    # Checkpointer: MemorySaver for Phase 1/2; Postgres (AsyncPostgresSaver) in Phase 3.
    # The pool is created closed here and opened in the FastAPI startup event via
    # open_checkpointer().
    postgres_url = getattr(config, "checkpoint_postgres_url", None)
    pool_size = getattr(config, "checkpoint_pool_size", 5)
    checkpointer = build_checkpointer(postgres_url=postgres_url, pool_size=pool_size)

    graph = builder.compile(checkpointer=checkpointer)

    topology = (
        "init_turn → rag → preprocess → llm → [tools] → llm → END"
        if tool_manager is not None
        else "init_turn → rag → preprocess → llm → END"
    )
    logger.info(
        "LangGraph chat graph compiled (nodes: %s, checkpointer: %s)",
        topology,
        type(checkpointer).__name__,
    )
    return graph
