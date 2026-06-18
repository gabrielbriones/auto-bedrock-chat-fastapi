"""Phase 2 — OpenAPI → LangChain tool generation tests.

Verifies that ``ToolsGenerator.generate_langchain_tools()`` and
``ToolManager.generate_langchain_tools()`` produce valid LangChain
``StructuredTool`` objects from an inline OpenAPI spec.

No real HTTP calls are made; the HTTP execution path is also smoke-tested
via a mock httpx client.
"""

import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.graph.tools.generator import make_graph_tools
from autolangchat.tool_manager import ToolManager, ToolsGenerator

# ---------------------------------------------------------------------------
# Minimal inline OpenAPI spec for testing
# ---------------------------------------------------------------------------

# Deduplicated version (paths dict cannot have duplicate keys; POST /jobs
# overwrites GET /jobs in a real dict — use separate spec for POST test)
_GET_SPEC: Dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/jobs": {
            "get": {
                "operationId": "list_jobs",
                "summary": "List all jobs",
                "parameters": [
                    {
                        "name": "status",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string", "description": "Filter by job status"},
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/jobs/{job_id}": {
            "get": {
                "operationId": "get_job",
                "summary": "Get a single job by ID",
                "parameters": [
                    {
                        "name": "job_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "description": "The job identifier"},
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
    },
}

_POST_SPEC: Dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/jobs": {
            "post": {
                "operationId": "create_job",
                "summary": "Create a new job",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "Job name"},
                                    "priority": {"type": "integer", "description": "Priority"},
                                },
                                "required": ["name"],
                            }
                        }
                    },
                },
                "responses": {"201": {"description": "Created"}},
            }
        },
    },
}


def _make_generator(spec: Dict) -> ToolsGenerator:
    from autolangchat.config import ChatConfig

    config = ChatConfig(model_id="test-model", excluded_paths=[])
    gen = ToolsGenerator(openapi_spec=spec, config=config)
    gen.generate_tools_desc()  # populate _generated_tools
    return gen


def _make_manager(spec: Dict) -> ToolManager:
    from autolangchat.config import ChatConfig

    config = ChatConfig(model_id="test-model", excluded_paths=[])
    return ToolManager(openapi_spec=spec, config=config, base_url="http://test-api")


# ---------------------------------------------------------------------------
# ToolsGenerator.generate_langchain_tools tests
# ---------------------------------------------------------------------------


class TestGenerateLangchainTools:
    def test_returns_list_of_tools(self):
        gen = _make_generator(_GET_SPEC)
        tools = gen.generate_langchain_tools()
        assert isinstance(tools, list)
        assert len(tools) == 2

    def test_tool_names_match_operation_ids(self):
        gen = _make_generator(_GET_SPEC)
        tools = gen.generate_langchain_tools()
        names = {t.name for t in tools}
        assert "list_jobs" in names
        assert "get_job" in names

    def test_tool_descriptions_from_summary(self):
        gen = _make_generator(_GET_SPEC)
        tools = gen.generate_langchain_tools()
        tool_map = {t.name: t for t in tools}
        assert "List all jobs" in tool_map["list_jobs"].description
        assert "Get a single job" in tool_map["get_job"].description

    def test_optional_param_in_schema(self):
        gen = _make_generator(_GET_SPEC)
        tools = gen.generate_langchain_tools()
        tool_map = {t.name: t for t in tools}
        # list_jobs has optional 'status' param
        schema = tool_map["list_jobs"].args_schema.model_json_schema()
        assert "status" in schema.get("properties", {})
        # 'status' should not be required
        required = schema.get("required") or []
        assert "status" not in required

    def test_required_param_in_schema(self):
        gen = _make_generator(_GET_SPEC)
        tools = gen.generate_langchain_tools()
        tool_map = {t.name: t for t in tools}
        # get_job has required 'job_id' param
        schema = tool_map["get_job"].args_schema.model_json_schema()
        assert "job_id" in schema.get("properties", {})
        required = schema.get("required") or []
        assert "job_id" in required

    def test_post_body_params_in_schema(self):
        gen = _make_generator(_POST_SPEC)
        tools = gen.generate_langchain_tools()
        assert len(tools) == 1
        schema = tools[0].args_schema.model_json_schema()
        props = schema.get("properties", {})
        assert "name" in props
        assert "priority" in props
        required = schema.get("required") or []
        assert "name" in required
        assert "priority" not in required

    def test_no_tool_manager_returns_error_on_invoke(self):
        """Without tool_manager, invoking a tool returns an error (not raises)."""
        gen = _make_generator(_GET_SPEC)
        tools = gen.generate_langchain_tools(tool_manager=None)
        import asyncio

        result = asyncio.run(tools[0].ainvoke({"status": "running"}))
        data = json.loads(result)
        assert "error" in data


# ---------------------------------------------------------------------------
# ToolManager.generate_langchain_tools (convenience wrapper)
# ---------------------------------------------------------------------------


class TestToolManagerGenerateLangchainTools:
    def test_convenience_wrapper_returns_tools(self):
        manager = _make_manager(_GET_SPEC)
        tools = manager.generate_langchain_tools()
        assert len(tools) == 2

    def test_names_match(self):
        manager = _make_manager(_GET_SPEC)
        tools = manager.generate_langchain_tools()
        names = {t.name for t in tools}
        assert names == {"list_jobs", "get_job"}

    @pytest.mark.asyncio
    async def test_invocation_calls_http(self):
        """Invoking a tool fires an HTTP request via ToolManager's client."""
        manager = _make_manager(_GET_SPEC)
        tools = manager.generate_langchain_tools()
        tool_map = {t.name: t for t in tools}

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {"jobs": [{"id": 1}]}
        fake_response.text = json.dumps({"jobs": [{"id": 1}]})

        # Patch the HTTP client on the manager
        manager._http_client.get = AsyncMock(return_value=fake_response)

        result = await tool_map["list_jobs"].ainvoke({"status": "running"})
        data = json.loads(result)
        assert data == {"jobs": [{"id": 1}]}


# ---------------------------------------------------------------------------
# make_graph_tools utility (graph/tools/generator.py)
# ---------------------------------------------------------------------------


class TestMakeGraphTools:
    def test_returns_same_as_manager_method(self):
        manager = _make_manager(_GET_SPEC)
        from_helper = make_graph_tools(manager)
        from_manager = manager.generate_langchain_tools()
        assert len(from_helper) == len(from_manager)
        helper_names = {t.name for t in from_helper}
        manager_names = {t.name for t in from_manager}
        assert helper_names == manager_names
