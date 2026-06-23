"""Phase 2 — OpenAPI → tool generation tests.

Verifies that ``ToolsGenerator.generate_tools_desc()`` produces the correct
OpenAI-style schema dict at construction time, and that
``ToolManager.generate_langchain_tools()`` wraps the cached metadata into valid
LangChain ``StructuredTool`` objects for use in agent loops.

No real HTTP calls are made; the HTTP execution path is also smoke-tested
via a mock httpx client.
"""

import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.graph.tools.generator import ToolsGenerator
from autolangchat.graph.tools.manager import ToolManager

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
    # generate_tools_desc() is called eagerly inside __init__
    return ToolsGenerator(openapi_spec=spec, config=config)


def _make_manager(spec: Dict) -> ToolManager:
    from autolangchat.config import ChatConfig

    config = ChatConfig(model_id="test-model", excluded_paths=[])
    generator = ToolsGenerator(openapi_spec=spec, config=config)
    return ToolManager(
        generated_tools=generator._generated_tools,
        config=config,
        base_url="http://test-api",
    )


# ---------------------------------------------------------------------------
# ToolsGenerator.generate_tools_desc — schema dict tests
# ---------------------------------------------------------------------------


class TestGenerateToolsDesc:
    def test_returns_function_schema_dict(self):
        gen = _make_generator(_GET_SPEC)
        desc = gen.tools_desc
        assert isinstance(desc, dict)
        assert desc["type"] == "function"
        assert isinstance(desc["functions"], list)
        assert len(desc["functions"]) == 2

    def test_function_names_match_operation_ids(self):
        gen = _make_generator(_GET_SPEC)
        names = {f["name"] for f in gen.tools_desc["functions"]}
        assert "list_jobs" in names
        assert "get_job" in names

    def test_function_descriptions_from_summary(self):
        gen = _make_generator(_GET_SPEC)
        fn_map = {f["name"]: f for f in gen.tools_desc["functions"]}
        assert "List all jobs" in fn_map["list_jobs"]["description"]
        assert "Get a single job" in fn_map["get_job"]["description"]

    def test_optional_param_in_schema(self):
        gen = _make_generator(_GET_SPEC)
        fn_map = {f["name"]: f for f in gen.tools_desc["functions"]}
        props = fn_map["list_jobs"]["parameters"]["properties"]
        required = fn_map["list_jobs"]["parameters"].get("required", [])
        assert "status" in props
        assert "status" not in required

    def test_required_param_in_schema(self):
        gen = _make_generator(_GET_SPEC)
        fn_map = {f["name"]: f for f in gen.tools_desc["functions"]}
        props = fn_map["get_job"]["parameters"]["properties"]
        required = fn_map["get_job"]["parameters"].get("required", [])
        assert "job_id" in props
        assert "job_id" in required

    def test_post_body_params_in_schema(self):
        gen = _make_generator(_POST_SPEC)
        assert len(gen.tools_desc["functions"]) == 1
        fn = gen.tools_desc["functions"][0]
        props = fn["parameters"]["properties"]
        required = fn["parameters"].get("required", [])
        assert "name" in props
        assert "priority" in props
        assert "name" in required
        assert "priority" not in required

    def test_schema_cached_after_init(self):
        gen = _make_generator(_GET_SPEC)
        first = gen.tools_desc
        second = gen.tools_desc
        # Computed from same _generated_tools cache — same content
        assert first == second
        assert len(first["functions"]) > 0

    def test_invalidate_cache_rebuilds_schema(self):
        gen = _make_generator(_GET_SPEC)
        original = gen.tools_desc
        gen.invalidate_cache()
        rebuilt = gen.tools_desc
        # Different object, same content
        assert rebuilt is not original
        assert len(rebuilt["functions"]) == len(original["functions"])


# ---------------------------------------------------------------------------
# ToolManager.generate_langchain_tools
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

        # Patch the HTTP client request method on the manager
        manager._http_client.request = AsyncMock(return_value=fake_response)

        result = await tool_map["list_jobs"].ainvoke({"status": "running"})
        data = json.loads(result)
        assert data == {"jobs": [{"id": 1}]}
