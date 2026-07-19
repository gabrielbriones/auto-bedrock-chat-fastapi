"""Phase 2 — OpenAPI → tool generation tests.

Verifies that ``ToolsGenerator.generate_tools_desc()`` produces the correct
OpenAI-style schema dict at construction time, and that
``ToolManager.generate_langchain_tools()`` wraps the cached metadata into valid
LangChain ``StructuredTool`` objects for use in agent loops.

No real HTTP calls are made; the HTTP execution path is also smoke-tested
via a mock httpx client.
"""

import asyncio
import json
import time
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


# ---------------------------------------------------------------------------
# ToolManager.execute_tool_calls — cap / skip / stub behaviour
# ---------------------------------------------------------------------------


def _make_manager_with_limit(limit):
    """Return a ToolManager whose max_tool_calls is set to *limit* (None = unlimited)."""
    from autolangchat.config import ChatConfig

    base_config = ChatConfig(model_id="test-model", excluded_paths=[])
    config = base_config.model_copy(update={"max_tool_calls": limit})
    generator = ToolsGenerator(openapi_spec=_GET_SPEC, config=config)
    return ToolManager(
        generated_tools=generator._generated_tools,
        config=config,
        base_url="http://test-api",
    )


def _make_calls(n, prefix="call"):
    """Build *n* normalised tool-call dicts for ``list_jobs``."""
    return [{"id": f"{prefix}_{i}", "name": "list_jobs", "arguments": {}} for i in range(n)]


class TestToolManagerExecuteCap:
    """Tests for the per-turn tool-call cap, skipped-stub, and unlimited-default paths."""

    @pytest.mark.asyncio
    async def test_unlimited_executes_all_calls(self):
        """max_tool_calls=None must execute every call — no skipping."""
        manager = _make_manager_with_limit(None)

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {}
        fake_response.text = "{}"
        manager._http_client.request = AsyncMock(return_value=fake_response)

        calls = _make_calls(5)
        results = await manager.execute_tool_calls(calls)

        assert len(results) == 5
        assert manager._http_client.request.await_count == 5

    @pytest.mark.asyncio
    async def test_cap_produces_one_result_per_call(self):
        """With a cap of 2 and 5 calls the result list must still have 5 entries."""
        manager = _make_manager_with_limit(2)

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {}
        fake_response.text = "{}"
        manager._http_client.request = AsyncMock(return_value=fake_response)

        calls = _make_calls(5)
        results = await manager.execute_tool_calls(calls)

        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_cap_only_executes_capped_calls_via_http(self):
        """HTTP must be called only for the first *max_tool_calls* entries."""
        manager = _make_manager_with_limit(3)

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {}
        fake_response.text = "{}"
        manager._http_client.request = AsyncMock(return_value=fake_response)

        calls = _make_calls(6)
        await manager.execute_tool_calls(calls)

        assert manager._http_client.request.await_count == 3

    @pytest.mark.asyncio
    async def test_skipped_calls_return_stub_errors(self):
        """Calls beyond the cap must come back as error results (not successes)."""
        manager = _make_manager_with_limit(2)

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {}
        fake_response.text = "{}"
        manager._http_client.request = AsyncMock(return_value=fake_response)

        calls = _make_calls(4)
        results = await manager.execute_tool_calls(calls)

        skipped = results[2:]
        for r in skipped:
            assert "error" in r
            assert "skipped" in r["error"].lower()

    @pytest.mark.asyncio
    async def test_result_tool_call_ids_match_inputs(self):
        """Every result tool_call_id must match the id of the corresponding input call."""
        manager = _make_manager_with_limit(2)

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {}
        fake_response.text = "{}"
        manager._http_client.request = AsyncMock(return_value=fake_response)

        calls = _make_calls(5)
        results = await manager.execute_tool_calls(calls)

        result_ids = {r["tool_call_id"] for r in results}
        call_ids = {c["id"] for c in calls}
        assert result_ids == call_ids


# ------------------------------------------------------
# ToolManager.execute_tool_calls — concurrent execution
# ------------------------------------------------------


class TestToolManagerExecuteConcurrency:
    """Tests asserting tool calls run concurrently via asyncio.gather()."""

    @pytest.mark.asyncio
    async def test_calls_run_concurrently_not_sequentially(self):
        """Total wall time for N slow calls should be ~1 delay, not N delays."""
        manager = _make_manager_with_limit(None)
        delay = 0.2
        n = 5

        async def _slow_execute(tool_metadata, arguments, auth_info=None):
            await asyncio.sleep(delay)
            return {"ok": True}

        manager._execute_single_tool_call = _slow_execute

        calls = _make_calls(n)
        start = time.monotonic()
        results = await manager.execute_tool_calls(calls)
        elapsed = time.monotonic() - start

        assert len(results) == n
        # Sequential execution would take roughly n * delay; concurrent
        # execution should complete well under that, leaving generous
        # margin for scheduling overhead in CI.
        assert elapsed < delay * n

    @pytest.mark.asyncio
    async def test_results_preserve_original_order(self):
        """Results must be returned in the original tool_calls order even
        when later calls finish before earlier ones."""
        manager = _make_manager_with_limit(None)

        # Reverse-ordered delays: the first call is the slowest, the last is
        # the fastest, so completion order is the reverse of input order.
        async def _variable_delay_execute(tool_metadata, arguments, auth_info=None):
            index = int(arguments.get("index", 0))
            await asyncio.sleep((5 - index) * 0.02)
            return {"index": index}

        manager._execute_single_tool_call = _variable_delay_execute

        calls = [{"id": f"call_{i}", "name": "list_jobs", "arguments": {"index": i}} for i in range(5)]
        results = await manager.execute_tool_calls(calls)

        assert [r["tool_call_id"] for r in results] == [c["id"] for c in calls]

    @pytest.mark.asyncio
    async def test_one_failure_does_not_cancel_others(self):
        """A single failing tool call must not prevent the others from succeeding."""
        manager = _make_manager_with_limit(None)

        async def _maybe_fail_execute(tool_metadata, arguments, auth_info=None):
            if arguments.get("index") == 1:
                raise RuntimeError("boom")
            await asyncio.sleep(0.01)
            return {"ok": True}

        manager._execute_single_tool_call = _maybe_fail_execute

        calls = [{"id": f"call_{i}", "name": "list_jobs", "arguments": {"index": i}} for i in range(4)]
        results = await manager.execute_tool_calls(calls)

        assert len(results) == 4
        failing = [r for r in results if r["tool_call_id"] == "call_1"]
        assert failing and "error" in failing[0] and "boom" in failing[0]["error"]
        succeeding = [r for r in results if r["tool_call_id"] != "call_1"]
        assert all("result" in r for r in succeeding)

    @pytest.mark.asyncio
    async def test_progress_callback_invoked_in_original_order(self):
        """on_progress must fire once per valid call, in original call order."""
        manager = _make_manager_with_limit(None)

        async def _fast_execute(tool_metadata, arguments, auth_info=None):
            return {"ok": True}

        manager._execute_single_tool_call = _fast_execute

        messages = []

        async def on_progress(msg):
            messages.append(msg)

        calls = _make_calls(3)
        await manager.execute_tool_calls(calls, on_progress=on_progress)

        assert messages == [
            "Calling list_jobs... (1/3)",
            "Calling list_jobs... (2/3)",
            "Calling list_jobs... (3/3)",
        ]

    @pytest.mark.asyncio
    async def test_total_tool_calls_executed_counter_correct(self):
        """The counter must increment once per attempted call, matching prior behavior."""
        manager = _make_manager_with_limit(None)

        async def _fast_execute(tool_metadata, arguments, auth_info=None):
            return {"ok": True}

        manager._execute_single_tool_call = _fast_execute

        calls = _make_calls(4)
        await manager.execute_tool_calls(calls)

        assert manager._total_tool_calls_executed == 4
