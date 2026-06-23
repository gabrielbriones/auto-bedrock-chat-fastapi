"""Tool execution node.

``tools_execution_node`` executes tool calls from the last assistant message
by delegating to ``ToolManager.execute_tool_calls()``.  Auth info and the
tool manager itself are read from ``config["configurable"]``.
"""

from __future__ import annotations

from .tool_node import tools_execution_node  # noqa: F401 — re-exported for graph.py
