"""End-to-end MCP auth-path tests (XMGPLAT-11065, Phase 7).

Unlike ``tests/test_mcp_auth.py`` (unit tests for header -> ``Credentials``
mapping in isolation) and ``tests/test_mcp_server.py`` (adapter wiring with a
fake ``ToolManager``), these tests exercise the **full real pipeline** for
each of the 6 supported auth types: simulated MCP request headers ->
``build_auth_info_from_headers`` -> a real ``ToolManager.call_tool`` -> a real
``AuthenticationHandler.apply_auth_to_headers`` -> the actual outbound HTTP
request headers sent to the tool's target API. Only the underlying
``httpx.AsyncClient`` transport is mocked.
"""

import base64
import time
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

mcp = pytest.importorskip("mcp", reason="mcp SDK not installed (optional [mcp] extra)")
from mcp import types  # noqa: E402
from mcp.server.lowlevel.server import request_ctx  # noqa: E402
from mcp.shared.context import RequestContext  # noqa: E402

from autolangchat.config import ChatConfig  # noqa: E402
from autolangchat.graph.tools.generator import ToolsGenerator  # noqa: E402
from autolangchat.graph.tools.manager import ToolManager  # noqa: E402
from autolangchat.mcp.server import build_mcp_server  # noqa: E402

_SPEC: Dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/jobs": {
            "get": {
                "operationId": "list_jobs",
                "summary": "List all jobs",
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}


def _make_generator_and_manager():
    config = ChatConfig(model_id="test-model", excluded_paths=[])
    generator = ToolsGenerator(openapi_spec=_SPEC, config=config)
    manager = ToolManager(
        generated_tools=generator._generated_tools,
        config=config,
        base_url="http://test-api",
    )
    return generator, manager


class _FakeHttpRequest:
    def __init__(self, headers):
        self.headers = headers


class _FakeSSOSessionStore:
    _SECRET = "test-sso-session-secret"

    def __init__(self):
        self.sessions = {}

    def create_session(self, session_id, access_token, user_info=None):
        self.sessions[session_id] = {
            "access_token": access_token,
            "user_info": user_info or {},
            "expires_at": time.time() + 3600,
        }

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def delete_session(self, session_id):
        self.sessions.pop(session_id, None)

    def generate_session_token(self, session_id):
        import jwt

        return jwt.encode({"session_id": session_id}, self._SECRET, algorithm="HS256")

    def validate_session_token(self, token, secret):
        import jwt
        from jwt.exceptions import PyJWTError

        try:
            return jwt.decode(token, secret, algorithms=["HS256"]).get("session_id")
        except PyJWTError:
            return None


def _fake_response(json_body=None, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else {}
    response.text = "{}"
    return response


async def _call_list_jobs_with_headers(server, headers, mock_request):
    """Invoke the real tools/call handler with a simulated request context."""
    handler = server.request_handlers[types.CallToolRequest]
    call_request = types.CallToolRequest(params=types.CallToolRequestParams(name="list_jobs", arguments={}))

    token = request_ctx.set(
        RequestContext(
            request_id="1",
            meta=None,
            session=None,
            lifespan_context=None,
            request=_FakeHttpRequest(headers),
        )
    )
    try:
        result = await handler(call_request)
    finally:
        request_ctx.reset(token)

    assert result.root.isError is False, result.root.content
    return mock_request.call_args


class TestEndToEndAuthPaths:
    """One test per auth type -- verifies the actual outbound HTTP headers."""

    @pytest.mark.asyncio
    async def test_bearer_token(self):
        generator, tool_manager = _make_generator_and_manager()
        tool_manager._http_client.request = AsyncMock(return_value=_fake_response())
        server = build_mcp_server(generator, tool_manager)

        call = await _call_list_jobs_with_headers(
            server, {"Authorization": "Bearer tok123"}, tool_manager._http_client.request
        )

        assert call.kwargs["headers"]["Authorization"] == "Bearer tok123"

    @pytest.mark.asyncio
    async def test_basic_auth(self):
        generator, tool_manager = _make_generator_and_manager()
        tool_manager._http_client.request = AsyncMock(return_value=_fake_response())
        server = build_mcp_server(generator, tool_manager)
        encoded = base64.b64encode(b"alice:s3cret").decode()

        call = await _call_list_jobs_with_headers(
            server, {"Authorization": f"Basic {encoded}"}, tool_manager._http_client.request
        )

        expected = base64.b64encode(b"alice:s3cret").decode()
        assert call.kwargs["headers"]["Authorization"] == f"Basic {expected}"

    @pytest.mark.asyncio
    async def test_api_key(self):
        generator, tool_manager = _make_generator_and_manager()
        tool_manager._http_client.request = AsyncMock(return_value=_fake_response())
        server = build_mcp_server(generator, tool_manager)

        call = await _call_list_jobs_with_headers(server, {"X-API-Key": "key-abc"}, tool_manager._http_client.request)

        assert call.kwargs["headers"]["X-API-Key"] == "key-abc"

    @pytest.mark.asyncio
    async def test_custom_headers(self):
        generator, tool_manager = _make_generator_and_manager()
        tool_manager._http_client.request = AsyncMock(return_value=_fake_response())
        server = build_mcp_server(generator, tool_manager)

        call = await _call_list_jobs_with_headers(
            server,
            {"X-Forward-Tenant-Id": "acme", "X-Forward-Region": "us-east"},
            tool_manager._http_client.request,
        )

        assert call.kwargs["headers"]["Tenant-Id"] == "acme"
        assert call.kwargs["headers"]["Region"] == "us-east"

    @pytest.mark.asyncio
    async def test_oauth2_client_credentials(self):
        generator, tool_manager = _make_generator_and_manager()
        tool_manager._http_client.request = AsyncMock(return_value=_fake_response())
        tool_manager._http_client.post = AsyncMock(
            return_value=_fake_response({"access_token": "minted-tok", "expires_in": 3600})
        )
        server = build_mcp_server(generator, tool_manager)

        call = await _call_list_jobs_with_headers(
            server,
            {
                "X-OAuth2-Client-Id": "client-1",
                "X-OAuth2-Client-Secret": "secret-1",
                "X-OAuth2-Token-Url": "https://idp.example.com/token",
            },
            tool_manager._http_client.request,
        )

        tool_manager._http_client.post.assert_awaited_once()
        token_url_called = tool_manager._http_client.post.call_args.args[0]
        assert token_url_called == "https://idp.example.com/token"
        assert call.kwargs["headers"]["Authorization"] == "Bearer minted-tok"

    @pytest.mark.asyncio
    async def test_sso_session_token(self):
        generator, tool_manager = _make_generator_and_manager()
        tool_manager._http_client.request = AsyncMock(return_value=_fake_response())
        sso_session_store = _FakeSSOSessionStore()
        sso_session_store.create_session("sess-1", access_token="idp-access-tok")
        session_token = sso_session_store.generate_session_token("sess-1")

        server = build_mcp_server(
            generator,
            tool_manager,
            sso_session_store=sso_session_store,
            sso_session_secret=sso_session_store._SECRET,
        )

        call = await _call_list_jobs_with_headers(
            server, {"Authorization": f"Bearer {session_token}"}, tool_manager._http_client.request
        )

        # The IdP access token is forwarded for leg 2, not the opaque session_token.
        assert call.kwargs["headers"]["Authorization"] == "Bearer idp-access-tok"
