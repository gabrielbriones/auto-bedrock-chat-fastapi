"""Utility: generate LangChain tools from a ToolManager's OpenAPI spec.

This is the unique value-add of the library — any FastAPI app (or any app with
an OpenAPI spec) automatically gets LangGraph-compatible ``StructuredTool``
objects that can be passed to ``ToolNode`` or used directly in agent chains.

Usage (standalone)::

    from autolangchat.graph.tools.generator import make_graph_tools
    from autolangchat.tool_manager import ToolManager

    manager = ToolManager(app=my_fastapi_app, config=config)
    tools = make_graph_tools(manager)

    # Use with LangGraph ToolNode:
    from langgraph.prebuilt import ToolNode
    tool_node = ToolNode(tools)

Note: The autolangchat graph itself uses ``tools_execution_node`` directly
(calling ``ToolManager.execute_tool_calls``) rather than a ``ToolNode`` from
this module, because the graph state is in plain-dict format rather than
LangChain message format.  ``make_graph_tools`` is provided as a utility for
callers who want LangChain-native tool objects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List

if TYPE_CHECKING:
    from ...tool_manager import ToolManager


def make_graph_tools(tool_manager: "ToolManager") -> List[Any]:
    """Return LangChain ``StructuredTool`` objects for all tools in the spec.

    Thin wrapper around ``ToolManager.generate_langchain_tools()`` that
    provides a stable import path from the ``graph/tools/`` sub-package.

    Args:
        tool_manager: ``ToolManager`` instance to generate tools from.

    Returns:
        List of ``langchain_core.tools.StructuredTool`` instances, one per
        API endpoint exposed by the ``ToolManager``'s OpenAPI spec.
    """
    return tool_manager.generate_langchain_tools()
