"""Tests for reactive auth-expiration handling in tools_execution_node.

Covers XMGPLAT-11046 Phase 2: ToolManager stays a pure HTTP-dispatch
component (no changes tested here beyond the status_code field it now
returns); the catch/refresh/retry logic lives in tool_node.py instead.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.auth_handler import AuthenticationHandler, AuthType, Credentials
from autolangchat.config import ChatConfig
from autolangchat.graph.tools.generator import ToolsGenerator
from autolangchat.graph.tools.manager import AuthInfo, ToolManager
from autolangchat.graph.tools.tool_node import tools_execution_node

_GET_JOBS_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {"/jobs": {"get": {"operationId": "get_jobs", "responses": {"200": {"description": "OK"}}}}},
}


def _make_real_tool_manager() -> ToolManager:
    """A real ToolManager (not a MagicMock) so execute_tool_calls()'s actual
    result-shape logic is exercised end-to-end, not bypassed by mocking."""
    config = ChatConfig(model_id="test-model", excluded_paths=[])
    generator = ToolsGenerator(openapi_spec=_GET_JOBS_SPEC, config=config)
    return ToolManager(generated_tools=generator._generated_tools, config=config, base_url="http://test-api")


def _make_sso_auth_info(session_token="sess-jwt", sso_session_id="sso-sess-1"):
    credentials = Credentials(
        auth_type=AuthType.SSO,
        bearer_token="old_access_token",
        session_token=session_token,
        metadata={"sso_session_id": sso_session_id},
    )
    auth_handler = AuthenticationHandler(credentials=credentials)
    return AuthInfo(credentials=credentials, auth_handler=auth_handler)


def _make_bearer_auth_info():
    credentials = Credentials(auth_type=AuthType.BEARER_TOKEN, bearer_token="manual-token")
    auth_handler = AuthenticationHandler(credentials=credentials)
    return AuthInfo(credentials=credentials, auth_handler=auth_handler)


def _make_state_with_tool_call(name="get_jobs", call_id="call_1"):
    return {
        "messages": [
            {"role": "user", "content": "list jobs"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": name, "args": {"q": "all"}, "id": call_id, "type": "tool_call"}],
            },
        ],
        "metadata": {},
    }


class TestNoneModeUnaffected:
    @pytest.mark.asyncio
    async def test_401_passed_through_unchanged_and_no_retry(self):
        tm = MagicMock()
        tm.execute_tool_calls = AsyncMock(
            return_value=[{"tool_call_id": "call_1", "name": "get_jobs", "error": "HTTP 401", "status_code": 401}]
        )
        config = {
            "configurable": {
                "tool_manager": tm,
                "auth_info": _make_sso_auth_info(),
                "chat_config": SimpleNamespace(auth_expiration_behaviour="none"),
            }
        }

        result = await tools_execution_node(_make_state_with_tool_call(), config)

        tm.execute_tool_calls.assert_awaited_once()
        assert "auth_expired" not in result["metadata"]
        assert result["messages"][-1]["tool_results"][0]["status_code"] == 401


class TestReactiveModeRefreshSucceeds:
    @pytest.mark.asyncio
    async def test_refresh_and_retry_recovers_the_call(self):
        tm = MagicMock()
        tm.execute_tool_calls = AsyncMock(
            side_effect=[
                [{"tool_call_id": "call_1", "name": "get_jobs", "error": "HTTP 401", "status_code": 401}],
                [{"tool_call_id": "call_1", "name": "get_jobs", "result": {"jobs": []}}],
            ]
        )
        auth_info = _make_sso_auth_info()

        sso_session_store = MagicMock()
        sso_session_store.get_session.return_value = {"refresh_token": "rt", "access_token": "old_access_token"}
        sso_provider = MagicMock()

        refreshed_session = {"access_token": "new_access_token", "refresh_token": "rt"}
        with patch(
            "autolangchat.sso.sso_session_store.refresh_sso_session_if_needed",
            new=AsyncMock(return_value=refreshed_session),
        ) as mock_refresh:
            config = {
                "configurable": {
                    "tool_manager": tm,
                    "auth_info": auth_info,
                    "chat_config": SimpleNamespace(auth_expiration_behaviour="reactive"),
                    "sso_session_store": sso_session_store,
                    "sso_provider": sso_provider,
                }
            }
            result = await tools_execution_node(_make_state_with_tool_call(), config)

        mock_refresh.assert_awaited_once_with(sso_session_store, sso_provider, "sso-sess-1")
        assert tm.execute_tool_calls.await_count == 2
        # Retry call only re-sends the failed tool call
        retry_call_args = tm.execute_tool_calls.await_args_list[1].args[0]
        assert [c["id"] for c in retry_call_args] == ["call_1"]

        assert auth_info.credentials.bearer_token == "new_access_token"
        assert "auth_expired" not in result["metadata"]
        tool_results = result["messages"][-1]["tool_results"]
        assert tool_results[0]["result"] == {"jobs": []}


class TestReactiveModeRefreshFails:
    @pytest.mark.asyncio
    async def test_refresh_failure_sets_auth_expired_without_retry(self):
        tm = MagicMock()
        tm.execute_tool_calls = AsyncMock(
            return_value=[{"tool_call_id": "call_1", "name": "get_jobs", "error": "HTTP 401", "status_code": 401}]
        )
        sso_session_store = MagicMock()
        sso_session_store.get_session.return_value = {"refresh_token": "rt"}
        sso_provider = MagicMock()

        with patch(
            "autolangchat.sso.sso_session_store.refresh_sso_session_if_needed",
            new=AsyncMock(return_value=None),
        ):
            config = {
                "configurable": {
                    "tool_manager": tm,
                    "auth_info": _make_sso_auth_info(),
                    "chat_config": SimpleNamespace(auth_expiration_behaviour="reactive"),
                    "sso_session_store": sso_session_store,
                    "sso_provider": sso_provider,
                }
            }
            result = await tools_execution_node(_make_state_with_tool_call(), config)

        tm.execute_tool_calls.assert_awaited_once()  # no retry attempted
        assert result["metadata"]["auth_expired"] is True

    @pytest.mark.asyncio
    async def test_manual_bearer_token_cannot_refresh_and_skips_straight_to_auth_expired(self):
        tm = MagicMock()
        tm.execute_tool_calls = AsyncMock(
            return_value=[{"tool_call_id": "call_1", "name": "get_jobs", "error": "HTTP 401", "status_code": 401}]
        )
        config = {
            "configurable": {
                "tool_manager": tm,
                "auth_info": _make_bearer_auth_info(),
                "chat_config": SimpleNamespace(auth_expiration_behaviour="reactive"),
                # No sso_session_store/sso_provider -- bearer_token has no refresh path anyway.
            }
        }

        result = await tools_execution_node(_make_state_with_tool_call(), config)

        tm.execute_tool_calls.assert_awaited_once()
        assert result["metadata"]["auth_expired"] is True


class TestBothModeBehavesLikeReactiveAtThisLayer:
    @pytest.mark.asyncio
    async def test_both_mode_also_refreshes_and_retries(self):
        tm = MagicMock()
        tm.execute_tool_calls = AsyncMock(
            side_effect=[
                [{"tool_call_id": "call_1", "name": "get_jobs", "error": "HTTP 401", "status_code": 401}],
                [{"tool_call_id": "call_1", "name": "get_jobs", "result": {"ok": True}}],
            ]
        )
        sso_session_store = MagicMock()
        sso_session_store.get_session.return_value = {"refresh_token": "rt", "access_token": "old_access_token"}
        sso_provider = MagicMock()

        with patch(
            "autolangchat.sso.sso_session_store.refresh_sso_session_if_needed",
            new=AsyncMock(return_value={"access_token": "new_access_token", "refresh_token": "rt"}),
        ):
            config = {
                "configurable": {
                    "tool_manager": tm,
                    "auth_info": _make_sso_auth_info(),
                    "chat_config": SimpleNamespace(auth_expiration_behaviour="both"),
                    "sso_session_store": sso_session_store,
                    "sso_provider": sso_provider,
                }
            }
            result = await tools_execution_node(_make_state_with_tool_call(), config)

        assert tm.execute_tool_calls.await_count == 2
        assert "auth_expired" not in result["metadata"]


class TestMixedResultsOrderingPreserved:
    @pytest.mark.asyncio
    async def test_only_401_entries_are_retried_others_untouched(self):
        state = {
            "messages": [
                {"role": "user", "content": "do two things"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"name": "ok_call", "args": {}, "id": "call_ok", "type": "tool_call"},
                        {"name": "get_jobs", "args": {}, "id": "call_401", "type": "tool_call"},
                    ],
                },
            ],
            "metadata": {},
        }
        tm = MagicMock()
        tm.execute_tool_calls = AsyncMock(
            side_effect=[
                [
                    {"tool_call_id": "call_ok", "name": "ok_call", "result": {"fine": True}},
                    {"tool_call_id": "call_401", "name": "get_jobs", "error": "HTTP 401", "status_code": 401},
                ],
                [{"tool_call_id": "call_401", "name": "get_jobs", "result": {"jobs": []}}],
            ]
        )
        sso_session_store = MagicMock()
        sso_session_store.get_session.return_value = {"refresh_token": "rt", "access_token": "old_access_token"}
        sso_provider = MagicMock()

        with patch(
            "autolangchat.sso.sso_session_store.refresh_sso_session_if_needed",
            new=AsyncMock(return_value={"access_token": "new_access_token", "refresh_token": "rt"}),
        ):
            config = {
                "configurable": {
                    "tool_manager": tm,
                    "auth_info": _make_sso_auth_info(),
                    "chat_config": SimpleNamespace(auth_expiration_behaviour="reactive"),
                    "sso_session_store": sso_session_store,
                    "sso_provider": sso_provider,
                }
            }
            result = await tools_execution_node(state, config)

        retry_call_args = tm.execute_tool_calls.await_args_list[1].args[0]
        assert [c["id"] for c in retry_call_args] == ["call_401"]

        tool_results = result["messages"][-1]["tool_results"]
        assert tool_results[0]["tool_call_id"] == "call_ok"
        assert tool_results[0]["result"] == {"fine": True}
        assert tool_results[1]["tool_call_id"] == "call_401"
        assert tool_results[1]["result"] == {"jobs": []}


class TestEndToEndWithRealToolManager:
    """Uses a real ToolManager (mocking only the httpx layer) so the full
    execute_tool_calls() -> tools_execution_node() chain is exercised,
    instead of mocking tool_manager.execute_tool_calls() directly (which
    would mask the top-level-vs-nested result-shape bug fixed in
    manager.py -- see PR #150 review)."""

    @pytest.mark.asyncio
    async def test_real_401_response_triggers_refresh_and_retry(self):
        tm = _make_real_tool_manager()
        ok_response = MagicMock(status_code=200)
        ok_response.json.return_value = {"jobs": []}
        unauthorized_response = MagicMock(status_code=401, text="Unauthorized")
        tm._http_client.request = AsyncMock(side_effect=[unauthorized_response, ok_response])

        sso_session_store = MagicMock()
        sso_session_store.get_session.return_value = {"refresh_token": "rt", "access_token": "old_access_token"}
        sso_provider = MagicMock()

        with patch(
            "autolangchat.sso.sso_session_store.refresh_sso_session_if_needed",
            new=AsyncMock(return_value={"access_token": "new_access_token", "refresh_token": "rt"}),
        ):
            config = {
                "configurable": {
                    "tool_manager": tm,
                    "auth_info": _make_sso_auth_info(),
                    "chat_config": SimpleNamespace(auth_expiration_behaviour="reactive"),
                    "sso_session_store": sso_session_store,
                    "sso_provider": sso_provider,
                }
            }
            result = await tools_execution_node(_make_state_with_tool_call(name="get_jobs"), config)

        assert tm._http_client.request.await_count == 2
        assert "auth_expired" not in result["metadata"]
        tool_results = result["messages"][-1]["tool_results"]
        assert tool_results[0]["result"] == {"jobs": []}
