"""Tests for ToolManager — tool generation caching and HTTP-based execution.

Covers:
- AuthInfo dataclass (is_authenticated)
- tools_desc caching and refresh
- execute_tool_calls: validation, auth, progress, errors, HTTP dispatch
- get_statistics
- _make_http_request: all HTTP methods, error responses, timeouts
"""

from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest

from auto_bedrock_chat_fastapi.auth_handler import AuthType, Credentials
from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.tool_manager import AuthInfo, ToolManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return ChatConfig()


@pytest.fixture
def mock_tools_generator():
    gen = MagicMock()
    gen.generate_tools_desc.return_value = {"type": "function", "functions": [{"name": "get_users"}]}
    gen.get_tool_metadata.return_value = {"method": "GET", "path": "/api/users"}
    gen.validate_tool_call.return_value = True
    gen.get_tool_statistics.return_value = {"total_tools": 1}
    return gen


@pytest.fixture
def mock_http_client():
    client = AsyncMock(spec=httpx.AsyncClient)
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"data": "test"}
    response.text = '{"data": "test"}'
    client.get.return_value = response
    client.post.return_value = response
    client.put.return_value = response
    client.patch.return_value = response
    client.delete.return_value = response
    return client


@pytest.fixture
def tool_manager(mock_tools_generator, mock_http_client, config):
    tm = ToolManager(
        tools_generator=mock_tools_generator,
        base_url="http://localhost:8000",
        config=config,
    )
    tm._http_client = mock_http_client
    return tm


# ---------------------------------------------------------------------------
# AuthInfo
# ---------------------------------------------------------------------------


class TestAuthInfo:
    """Test AuthInfo dataclass."""

    def test_defaults_not_authenticated(self):
        info = AuthInfo()
        assert info.credentials is None
        assert info.auth_handler is None
        assert info.is_authenticated is False

    def test_none_credentials_not_authenticated(self):
        info = AuthInfo(credentials=None, auth_handler=Mock())
        assert info.is_authenticated is False

    def test_none_auth_type_not_authenticated(self):
        creds = Credentials(auth_type=AuthType.NONE)
        info = AuthInfo(credentials=creds, auth_handler=Mock())
        assert info.is_authenticated is False

    def test_bearer_token_authenticated(self):
        creds = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="tok")
        info = AuthInfo(credentials=creds, auth_handler=Mock())
        assert info.is_authenticated is True

    def test_basic_auth_authenticated(self):
        creds = Credentials(auth_type=AuthType.BASIC_AUTH, username="u", password="p")
        info = AuthInfo(credentials=creds, auth_handler=Mock())
        assert info.is_authenticated is True

    def test_api_key_authenticated(self):
        creds = Credentials(auth_type=AuthType.API_KEY, api_key="key")
        info = AuthInfo(credentials=creds, auth_handler=Mock())
        assert info.is_authenticated is True

    def test_oauth2_authenticated(self):
        creds = Credentials(auth_type=AuthType.OAUTH2_CLIENT_CREDENTIALS)
        info = AuthInfo(credentials=creds, auth_handler=Mock())
        assert info.is_authenticated is True

    def test_custom_authenticated(self):
        creds = Credentials(auth_type=AuthType.CUSTOM, custom_headers={"X-Foo": "bar"})
        info = AuthInfo(credentials=creds, auth_handler=Mock())
        assert info.is_authenticated is True


# ---------------------------------------------------------------------------
# ToolManager — init, caching, refresh
# ---------------------------------------------------------------------------


class TestToolManagerInit:
    """Test ToolManager construction and caching."""

    def test_tools_desc_cached_at_init(self, mock_tools_generator, mock_http_client, config):
        tm = ToolManager(
            tools_generator=mock_tools_generator,
            base_url="http://localhost:8000",
            config=config,
        )
        tm._http_client = mock_http_client
        mock_tools_generator.generate_tools_desc.assert_called_once()
        assert tm.tools_desc == {"type": "function", "functions": [{"name": "get_users"}]}

    def test_tools_desc_not_regenerated_on_property_access(self, tool_manager, mock_tools_generator):
        _ = tool_manager.tools_desc
        _ = tool_manager.tools_desc
        # Only the one call during __init__
        mock_tools_generator.generate_tools_desc.assert_called_once()

    def test_refresh_tools_regenerates(self, tool_manager, mock_tools_generator):
        mock_tools_generator.generate_tools_desc.return_value = {
            "type": "function",
            "functions": [{"name": "new_tool"}],
        }
        tool_manager.refresh_tools()
        assert mock_tools_generator.generate_tools_desc.call_count == 2
        assert tool_manager.tools_desc == {"type": "function", "functions": [{"name": "new_tool"}]}

    def test_base_url_trailing_slash_stripped(self, mock_tools_generator, mock_http_client, config):
        tm = ToolManager(
            tools_generator=mock_tools_generator,
            base_url="http://localhost:8000/",
            config=config,
        )
        tm._http_client = mock_http_client
        assert tm._base_url == "http://localhost:8000"


# ---------------------------------------------------------------------------
# execute_tool_calls — validation
# ---------------------------------------------------------------------------


class TestExecuteToolCallsValidation:
    """Test tool call validation in execute_tool_calls."""

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, tool_manager, mock_tools_generator):
        mock_tools_generator.get_tool_metadata.return_value = None

        results = await tool_manager.execute_tool_calls([{"id": "tc1", "name": "nonexistent_tool", "arguments": {}}])

        assert len(results) == 1
        assert results[0]["error"] == "Unknown tool: nonexistent_tool"
        assert results[0]["tool_call_id"] == "tc1"
        assert results[0]["name"] == "nonexistent_tool"

    @pytest.mark.asyncio
    async def test_invalid_arguments_returns_error(self, tool_manager, mock_tools_generator):
        mock_tools_generator.validate_tool_call.return_value = False

        results = await tool_manager.execute_tool_calls(
            [{"id": "tc2", "name": "get_users", "arguments": {"bad": "arg"}}]
        )

        assert len(results) == 1
        assert results[0]["error"] == "Invalid arguments"

    @pytest.mark.asyncio
    async def test_valid_tool_call_returns_result(self, tool_manager, mock_http_client):
        results = await tool_manager.execute_tool_calls([{"id": "tc3", "name": "get_users", "arguments": {}}])

        assert len(results) == 1
        assert results[0]["result"] == {"data": "test"}
        assert results[0]["tool_call_id"] == "tc3"
        mock_http_client.get.assert_called_once()


# ---------------------------------------------------------------------------
# execute_tool_calls — auth
# ---------------------------------------------------------------------------


class TestExecuteToolCallsAuth:
    """Test authentication application during tool execution."""

    @pytest.mark.asyncio
    async def test_bearer_token_auth_applied(self, tool_manager, mock_http_client, mock_tools_generator):
        mock_tools_generator.get_tool_metadata.return_value = {
            "method": "GET",
            "path": "/api/users",
            "_metadata": {"authentication": {"type": "bearer_token"}},
        }

        mock_auth_handler = AsyncMock()
        mock_auth_handler.apply_auth_to_headers.return_value = {"Authorization": "Bearer test-token"}

        auth_info = AuthInfo(
            credentials=Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="test-token"),
            auth_handler=mock_auth_handler,
        )

        results = await tool_manager.execute_tool_calls(
            [{"id": "tc4", "name": "get_users", "arguments": {}}],
            auth_info=auth_info,
        )

        assert len(results) == 1
        assert results[0]["result"] == {"data": "test"}

        # Verify auth was applied
        mock_auth_handler.apply_auth_to_headers.assert_called_once()
        call_args = mock_auth_handler.apply_auth_to_headers.call_args
        assert call_args[0][1] == {"type": "bearer_token"}  # tool_auth_config

        # Verify HTTP call used auth headers
        call_kwargs = mock_http_client.get.call_args[1]
        assert call_kwargs["headers"] == {"Authorization": "Bearer test-token"}

    @pytest.mark.asyncio
    async def test_no_auth_info_sends_default_headers(self, tool_manager, mock_http_client):
        results = await tool_manager.execute_tool_calls(
            [{"id": "tc5", "name": "get_users", "arguments": {}}],
        )

        assert len(results) == 1
        call_kwargs = mock_http_client.get.call_args[1]
        assert call_kwargs["headers"]["Content-Type"] == "application/json"
        assert "Authorization" not in call_kwargs["headers"]

    @pytest.mark.asyncio
    async def test_unauthenticated_auth_info_skips_auth(self, tool_manager, mock_http_client):
        """AuthInfo with NONE credentials should not apply authentication."""
        auth_info = AuthInfo(
            credentials=Credentials(auth_type=AuthType.NONE),
            auth_handler=AsyncMock(),
        )

        await tool_manager.execute_tool_calls(
            [{"id": "tc6", "name": "get_users", "arguments": {}}],
            auth_info=auth_info,
        )

        # auth_handler should NOT have been called
        auth_info.auth_handler.apply_auth_to_headers.assert_not_called()

    @pytest.mark.asyncio
    async def test_auth_failure_returns_error(self, tool_manager, mock_tools_generator):
        mock_tools_generator.get_tool_metadata.return_value = {"method": "GET", "path": "/api/secure"}

        mock_auth_handler = AsyncMock()
        mock_auth_handler.apply_auth_to_headers.side_effect = Exception("Auth error")

        auth_info = AuthInfo(
            credentials=Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="bad"),
            auth_handler=mock_auth_handler,
        )

        results = await tool_manager.execute_tool_calls(
            [{"id": "tc7", "name": "get_users", "arguments": {}}],
            auth_info=auth_info,
        )

        assert len(results) == 1
        assert results[0]["result"]["error"] == "Authentication failed: Auth error"


# ---------------------------------------------------------------------------
# execute_tool_calls — progress, capping, statistics
# ---------------------------------------------------------------------------


class TestExecuteToolCallsBehavior:
    """Test progress callbacks, max_tool_calls cap, and statistics."""

    @pytest.mark.asyncio
    async def test_on_progress_called(self, tool_manager):
        progress_cb = AsyncMock()

        await tool_manager.execute_tool_calls(
            [{"id": "tc8", "name": "get_users", "arguments": {}}],
            on_progress=progress_cb,
        )

        progress_cb.assert_called_once_with("Calling get_users... (1/1)")

    @pytest.mark.asyncio
    async def test_on_progress_multiple_tools(self, tool_manager, mock_tools_generator):
        """Progress callback invoked once per tool call."""
        mock_tools_generator.get_tool_metadata.return_value = {"method": "GET", "path": "/api/x"}

        progress_cb = AsyncMock()
        tool_calls = [
            {"id": "tc_a", "name": "tool_a", "arguments": {}},
            {"id": "tc_b", "name": "tool_b", "arguments": {}},
        ]

        await tool_manager.execute_tool_calls(tool_calls, on_progress=progress_cb)

        assert progress_cb.call_count == 2
        progress_cb.assert_any_call("Calling tool_a... (1/2)")
        progress_cb.assert_any_call("Calling tool_b... (2/2)")

    @pytest.mark.asyncio
    async def test_max_tool_calls_caps_execution(self, mock_tools_generator, mock_http_client):
        config = ChatConfig()
        config.max_tool_calls = 2
        tm = ToolManager(
            tools_generator=mock_tools_generator,
            base_url="http://localhost:8000",
            config=config,
        )
        tm._http_client = mock_http_client

        tool_calls = [
            {"id": "tc1", "name": "t1", "arguments": {}},
            {"id": "tc2", "name": "t2", "arguments": {}},
            {"id": "tc3", "name": "t3", "arguments": {}},
        ]
        results = await tm.execute_tool_calls(tool_calls)

        assert len(results) == 2  # capped at 2

    @pytest.mark.asyncio
    async def test_statistics_updated(self, tool_manager, mock_tools_generator):
        stats_before = tool_manager.get_statistics()
        assert stats_before["total_tool_calls_executed"] == 0

        await tool_manager.execute_tool_calls([{"id": "tc9", "name": "get_users", "arguments": {}}])

        stats_after = tool_manager.get_statistics()
        assert stats_after["total_tool_calls_executed"] == 1
        # Also includes tools_generator stats
        assert stats_after["total_tools"] == 1

    @pytest.mark.asyncio
    async def test_exception_during_execution_returns_error(self, tool_manager, mock_http_client):
        mock_http_client.get.side_effect = Exception("Kaboom")

        results = await tool_manager.execute_tool_calls([{"id": "tc10", "name": "get_users", "arguments": {}}])

        # The exception is caught at the outer level in execute_tool_calls
        assert len(results) == 1
        # Could be caught at _make_http_request or outer try/except
        assert "error" in results[0] or "error" in results[0].get("result", {})

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_independent(self, tool_manager, mock_tools_generator, mock_http_client):
        """Failure in one tool call doesn't prevent execution of the next."""

        def side_effect(name):
            if name == "failing_tool":
                return None  # unknown tool
            return {"method": "GET", "path": "/api/ok"}

        mock_tools_generator.get_tool_metadata.side_effect = side_effect

        results = await tool_manager.execute_tool_calls(
            [
                {"id": "tc_fail", "name": "failing_tool", "arguments": {}},
                {"id": "tc_ok", "name": "ok_tool", "arguments": {}},
            ]
        )

        assert len(results) == 2
        assert "error" in results[0]  # failing_tool
        assert "result" in results[1]  # ok_tool succeeded


# ---------------------------------------------------------------------------
# HTTP dispatch — _execute_single_tool_call / _make_http_request
# ---------------------------------------------------------------------------


class TestHTTPDispatch:
    """Test HTTP request dispatching and response parsing."""

    @pytest.mark.asyncio
    async def test_get_request(self, tool_manager, mock_tools_generator, mock_http_client):
        mock_tools_generator.get_tool_metadata.return_value = {"method": "GET", "path": "/api/items"}

        await tool_manager.execute_tool_calls([{"id": "tc", "name": "get_items", "arguments": {"status": "active"}}])

        mock_http_client.get.assert_called_once()
        call_kwargs = mock_http_client.get.call_args[1]
        assert call_kwargs["url"] == "http://localhost:8000/api/items"
        assert call_kwargs["params"] == {"status": "active"}

    @pytest.mark.asyncio
    async def test_post_request_with_body(self, tool_manager, mock_tools_generator, mock_http_client):
        mock_tools_generator.get_tool_metadata.return_value = {"method": "POST", "path": "/api/items"}

        await tool_manager.execute_tool_calls(
            [{"id": "tc", "name": "create_item", "arguments": {"name": "widget", "price": 10}}]
        )

        mock_http_client.post.assert_called_once()
        call_kwargs = mock_http_client.post.call_args[1]
        assert call_kwargs["json"] == {"name": "widget", "price": 10}

    @pytest.mark.asyncio
    async def test_put_request(self, tool_manager, mock_tools_generator, mock_http_client):
        mock_tools_generator.get_tool_metadata.return_value = {"method": "PUT", "path": "/api/items/{item_id}"}

        await tool_manager.execute_tool_calls(
            [{"id": "tc", "name": "update_item", "arguments": {"item_id": 42, "name": "updated"}}]
        )

        mock_http_client.put.assert_called_once()
        call_kwargs = mock_http_client.put.call_args[1]
        assert call_kwargs["url"] == "http://localhost:8000/api/items/42"
        assert call_kwargs["json"] == {"name": "updated"}

    @pytest.mark.asyncio
    async def test_patch_request(self, tool_manager, mock_tools_generator, mock_http_client):
        mock_tools_generator.get_tool_metadata.return_value = {"method": "PATCH", "path": "/api/items/{item_id}"}

        await tool_manager.execute_tool_calls(
            [{"id": "tc", "name": "patch_item", "arguments": {"item_id": 7, "status": "done"}}]
        )

        mock_http_client.patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_request(self, tool_manager, mock_tools_generator, mock_http_client):
        mock_tools_generator.get_tool_metadata.return_value = {"method": "DELETE", "path": "/api/items/{item_id}"}

        await tool_manager.execute_tool_calls([{"id": "tc", "name": "delete_item", "arguments": {"item_id": 99}}])

        mock_http_client.delete.assert_called_once()
        call_kwargs = mock_http_client.delete.call_args[1]
        assert call_kwargs["url"] == "http://localhost:8000/api/items/99"
        assert call_kwargs["params"] is None  # no extra query params

    @pytest.mark.asyncio
    async def test_path_param_substitution(self, tool_manager, mock_tools_generator, mock_http_client):
        mock_tools_generator.get_tool_metadata.return_value = {
            "method": "GET",
            "path": "/api/users/{user_id}/posts/{post_id}",
        }

        await tool_manager.execute_tool_calls(
            [{"id": "tc", "name": "get_post", "arguments": {"user_id": 5, "post_id": 12, "format": "json"}}]
        )

        call_kwargs = mock_http_client.get.call_args[1]
        assert call_kwargs["url"] == "http://localhost:8000/api/users/5/posts/12"
        assert call_kwargs["params"] == {"format": "json"}

    @pytest.mark.asyncio
    async def test_http_error_response(self, tool_manager, mock_tools_generator, mock_http_client):
        error_response = Mock()
        error_response.status_code = 404
        error_response.text = "Not Found"
        error_response.json.return_value = {"detail": "Item not found"}
        mock_http_client.get.return_value = error_response

        results = await tool_manager.execute_tool_calls([{"id": "tc", "name": "get_users", "arguments": {}}])

        assert results[0]["result"]["error"] == "HTTP 404: Item not found"
        assert results[0]["result"]["status_code"] == 404

    @pytest.mark.asyncio
    async def test_http_error_non_json_body(self, tool_manager, mock_tools_generator, mock_http_client):
        error_response = Mock()
        error_response.status_code = 500
        error_response.text = "Internal Server Error"
        error_response.json.side_effect = Exception("not json")
        mock_http_client.get.return_value = error_response

        results = await tool_manager.execute_tool_calls([{"id": "tc", "name": "get_users", "arguments": {}}])

        assert "HTTP 500" in results[0]["result"]["error"]

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, tool_manager, mock_http_client):
        mock_http_client.get.side_effect = httpx.TimeoutException("timed out")

        results = await tool_manager.execute_tool_calls([{"id": "tc", "name": "get_users", "arguments": {}}])

        assert results[0]["result"]["error"] == "Request timeout"

    @pytest.mark.asyncio
    async def test_request_error_returns_error(self, tool_manager, mock_http_client):
        mock_http_client.get.side_effect = httpx.RequestError("conn refused")

        results = await tool_manager.execute_tool_calls([{"id": "tc", "name": "get_users", "arguments": {}}])

        assert "Request failed" in results[0]["result"]["error"]

    @pytest.mark.asyncio
    async def test_non_json_success_response(self, tool_manager, mock_http_client):
        response = Mock()
        response.status_code = 200
        response.json.side_effect = Exception("not json")
        response.text = "plain text result"
        mock_http_client.get.return_value = response

        results = await tool_manager.execute_tool_calls([{"id": "tc", "name": "get_users", "arguments": {}}])

        assert results[0]["result"]["result"] == "plain text result"
        assert results[0]["result"]["status_code"] == 200

    @pytest.mark.asyncio
    async def test_unsupported_http_method(self, tool_manager, mock_tools_generator):
        mock_tools_generator.get_tool_metadata.return_value = {"method": "OPTIONS", "path": "/api/x"}

        results = await tool_manager.execute_tool_calls([{"id": "tc", "name": "options_x", "arguments": {}}])

        assert "Unsupported HTTP method" in results[0]["result"]["error"]

    @pytest.mark.asyncio
    async def test_delete_with_query_params(self, tool_manager, mock_tools_generator, mock_http_client):
        mock_tools_generator.get_tool_metadata.return_value = {"method": "DELETE", "path": "/api/cache"}

        await tool_manager.execute_tool_calls([{"id": "tc", "name": "clear_cache", "arguments": {"scope": "all"}}])

        call_kwargs = mock_http_client.delete.call_args[1]
        assert call_kwargs["params"] == {"scope": "all"}


# ---------------------------------------------------------------------------
# ToolManager — shutdown
# ---------------------------------------------------------------------------


class TestToolManagerShutdown:
    """Test ToolManager.shutdown() closes the HTTP client."""

    @pytest.mark.asyncio
    async def test_shutdown_closes_http_client(self, tool_manager, mock_http_client):
        await tool_manager.shutdown()
        mock_http_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_tolerates_aclose_error(self, tool_manager, mock_http_client):
        mock_http_client.aclose.side_effect = RuntimeError("already closed")
        # Should not raise
        await tool_manager.shutdown()
