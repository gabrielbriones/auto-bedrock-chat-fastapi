"""Slash commands over the WebSocket: direct ``load_skill``, refusals, history shape and usage."""

import asyncio
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Drop stub ``autolangchat`` packages left by sibling modules (see test_websocket_response_metadata).
for _name in [n for n in list(sys.modules) if n == "autolangchat" or n.startswith("autolangchat.")]:
    if getattr(sys.modules.get(_name), "__spec__", None) is None:
        del sys.modules[_name]

from autolangchat import websocket_handler  # noqa: E402
from autolangchat.graph.graph import build_chat_graph  # noqa: E402
from autolangchat.graph.tools.manager import ToolManager  # noqa: E402
from autolangchat.graph.tools.skill_tools import build_skill_tools  # noqa: E402
from autolangchat.history import format_history_messages  # noqa: E402
from autolangchat.skills.models import Skill, SkillCatalogEntry, SkillParameter  # noqa: E402
from autolangchat.skills.service import SkillService  # noqa: E402
from autolangchat.skills.slash import load_slash_command, parse_slash_command  # noqa: E402
from autolangchat.usage import UsageLedger  # noqa: E402
from autolangchat.websocket_handler import WebSocketChatHandler, _LiveTurn  # noqa: E402

_SKILL = Skill(
    name="triage",
    display_name="Triage",
    description="Triage a failed job.",
    instructions="List the failing step first.",
    parameters=[SkillParameter(name="job_id", required=True)],
    revision=2,
)


class _Store:
    def __init__(self, skill):
        self.skill = skill

    async def get_skill(self, name):
        return self.skill if name == self.skill.name else None

    async def list_enabled_catalog(self):
        if not self.skill.enabled:
            return []
        return [SkillCatalogEntry(name=self.skill.name, display_name=self.skill.display_name)]


def _assistant_answer():
    return {
        "role": "assistant",
        "content": "Step 3 failed.",
        "tool_calls": [],
        "metadata": {"message_id": "answer-1", "timestamp": "2026-01-01T10:00:05"},
    }


def _make_handler(skill_service, graph_state=None):
    config = MagicMock()
    config.model_copy.return_value = config
    config.timeout = 30.0
    config.model_id = "us.anthropic.claude-sonnet-5"
    config.require_tool_auth = False
    config.include_auth_info_in_prompts = False
    config.feedback_allow_anonymous = False
    config.enable_dynamic_overrides = False

    session = SimpleNamespace(session_id="session-1", user_id=None, credentials=None, auth_handler=None, metadata={})
    session_manager = MagicMock()
    session_manager.get_session = AsyncMock(return_value=session)

    chat_graph = MagicMock()
    chat_graph.ainvoke = AsyncMock(
        return_value=graph_state or {"messages": [_assistant_answer()], "metadata": {}, "kb_results": []}
    )
    return WebSocketChatHandler(
        session_manager=session_manager, config=config, chat_graph=chat_graph, skill_service=skill_service
    )


def _send(handler, message: str) -> list[dict]:
    websocket = MagicMock()
    websocket.send_json = AsyncMock()
    asyncio.run(handler._handle_chat_message(websocket, {"message": message}))
    return [call.args[0] for call in websocket.send_json.call_args_list]


def _ainvoke_call(handler):
    handler.chat_graph.ainvoke.assert_awaited_once()
    (graph_input,) = handler.chat_graph.ainvoke.call_args.args
    return graph_input, handler.chat_graph.ainvoke.call_args.kwargs["config"]["configurable"]


class TestHandler:
    def test_valid_command_loads_the_skill_before_the_turn(self):
        handler = _make_handler(SkillService(lambda: _Store(_SKILL)))

        frames = _send(handler, "/triage job_id=j-1")

        types = [frame["type"] for frame in frames]
        assert types.index("skill_loaded") < types.index("typing")
        loaded = frames[types.index("skill_loaded")]
        assert (loaded["name"], loaded["revision"]) == ("triage", 2)
        graph_input, configurable = _ainvoke_call(handler)
        # The checkpointed user message is the command as typed.
        assert graph_input == {"user_message": "/triage job_id=j-1"}
        call_message, tool_message = configurable["direct_messages"]
        assert call_message["metadata"]["message_id"] == loaded["message_id"]
        assert tool_message["tool_results"][0]["result"].startswith("Skill: triage (revision 2)")
        assert frames[-1]["type"] == "ai_response"

    @pytest.mark.parametrize(
        "message, code",
        [
            ("/nope", "unknown_skill"),
            ("/triage", "invalid_arguments"),
            ("/triage job_id=1 extra words", "invalid_arguments"),
            ("/triage a=1 a=2", "invalid_command"),
            ('/triage job_id="open', "invalid_command"),
        ],
    )
    def test_invalid_command_is_refused_without_a_turn(self, message, code):
        handler = _make_handler(SkillService(lambda: _Store(_SKILL)))

        frames = _send(handler, message)

        assert [frame["type"] for frame in frames] == ["skill_error"]
        assert frames[0]["code"] == code and frames[0]["message"]
        handler.chat_graph.ainvoke.assert_not_awaited()

    def test_model_without_tool_calling_is_refused(self, monkeypatch):
        handler = _make_handler(SkillService(lambda: _Store(_SKILL)))
        monkeypatch.setattr(websocket_handler, "supports_tool_calling", lambda model_id: False)

        frames = _send(handler, "/triage job_id=1")

        assert [(f["type"], f["code"]) for f in frames] == [("skill_error", "tools_unsupported")]
        handler.chat_graph.ainvoke.assert_not_awaited()

    def test_busy_conversation_refuses_before_announcing_the_skill(self):
        handler = _make_handler(SkillService(lambda: _Store(_SKILL)))
        websocket = MagicMock()
        websocket.send_json = AsyncMock()
        handler._conversation_persistence_active_for_session = lambda session: True
        session = asyncio.run(handler.session_manager.get_session(websocket))
        session.metadata["conversation_id"] = "c-1"
        handler._live_turns["c-1"] = _LiveTurn(origin=websocket, user_id=None, user_message="hi", started_at="t")

        asyncio.run(handler._handle_chat_message(websocket, {"message": "/triage job_id=1"}))

        frames = [call.args[0] for call in websocket.send_json.call_args_list]
        assert [frame["type"] for frame in frames] == ["error"]
        handler.chat_graph.ainvoke.assert_not_awaited()

    def test_disabled_skill_is_refused(self):
        handler = _make_handler(SkillService(lambda: _Store(_SKILL.model_copy(update={"enabled": False}))))

        frames = _send(handler, "/triage job_id=1")

        assert [(f["type"], f["code"]) for f in frames] == [("skill_error", "skill_disabled")]
        handler.chat_graph.ainvoke.assert_not_awaited()

    def test_unavailable_store_is_refused(self):
        handler = _make_handler(SkillService(lambda: None))

        frames = _send(handler, "/triage job_id=1")

        assert [(f["type"], f["code"]) for f in frames] == [("skill_error", "skills_unavailable")]

    def test_failing_store_read_is_refused(self):
        store = _Store(_SKILL)
        store.get_skill = AsyncMock(side_effect=RuntimeError("database is locked"))
        handler = _make_handler(SkillService(lambda: store))

        frames = _send(handler, "/triage job_id=1")

        assert [(f["type"], f["code"]) for f in frames] == [("skill_error", "skills_unavailable")]
        handler.chat_graph.ainvoke.assert_not_awaited()

    def _conversation_handler(self, follower=None):
        handler = _make_handler(SkillService(lambda: _Store(_SKILL)))
        handler._conversation_persistence_active_for_session = lambda session: True
        handler._turn_viewers = lambda live, conversation_id: [follower] if follower is not None else []
        websocket = MagicMock()
        websocket.send_json = AsyncMock()
        session = asyncio.run(handler.session_manager.get_session(websocket))
        session.metadata["conversation_id"] = "c-1"
        return handler, websocket

    def test_followers_and_replay_get_skill_loaded_before_typing(self):
        follower = MagicMock()
        follower.send_json = AsyncMock()
        handler, websocket = self._conversation_handler(follower)
        replayed: list[str] = []

        async def ainvoke(*args, **kwargs):
            replayed.extend(frame["type"] for frame in handler._live_turns["c-1"].frames)
            return {"messages": [_assistant_answer()], "metadata": {}, "kb_results": []}

        handler.chat_graph.ainvoke.side_effect = ainvoke
        asyncio.run(handler._handle_chat_message(websocket, {"message": "/triage job_id=1"}))

        followed = [call.args[0]["type"] for call in follower.send_json.call_args_list]
        assert followed[:3] == ["turn_started", "skill_loaded", "typing"]
        assert replayed == ["skill_loaded", "typing"]
        sent = [call.args[0]["type"] for call in websocket.send_json.call_args_list]
        assert sent[:2] == ["skill_loaded", "typing"]
        assert sent.count("skill_loaded") == 1 and followed.count("skill_loaded") == 1

    def test_cancelled_announcement_releases_the_conversation(self):
        handler, websocket = self._conversation_handler()

        async def send_json(frame):
            if frame["type"] == "skill_loaded":
                raise asyncio.CancelledError

        websocket.send_json.side_effect = send_json

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(handler._handle_chat_message(websocket, {"message": "/triage job_id=1"}))

        assert not handler.in_progress("c-1")
        assert "c-1" not in handler._live_turns
        handler.chat_graph.ainvoke.assert_not_awaited()

    def test_double_slash_is_plain_text_without_the_escape(self):
        handler = _make_handler(SkillService(lambda: _Store(_SKILL)))

        frames = _send(handler, "//triage job_id=1")

        assert not {"skill_loaded", "skill_error"} & {frame["type"] for frame in frames}
        graph_input, configurable = _ainvoke_call(handler)
        assert graph_input == {"user_message": "/triage job_id=1"}
        assert "direct_messages" not in configurable

    def test_plain_prose_with_a_slash_is_untouched(self):
        handler = _make_handler(SkillService(lambda: _Store(_SKILL)))

        _send(handler, "/usr/bin is missing on and/or off")

        graph_input, configurable = _ainvoke_call(handler)
        assert graph_input == {"user_message": "/usr/bin is missing on and/or off"}
        assert "direct_messages" not in configurable

    def test_without_skills_slash_commands_are_plain_text(self):
        handler = _make_handler(None)

        frames = _send(handler, "//triage job_id=1")

        assert frames[-1]["type"] == "ai_response"
        graph_input, configurable = _ainvoke_call(handler)
        assert graph_input == {"user_message": "//triage job_id=1"}
        assert "direct_messages" not in configurable


def _catalog(handler) -> dict:
    websocket = MagicMock()
    websocket.send_json = AsyncMock()
    asyncio.run(handler._dispatch(websocket, "skill_catalog", {"type": "skill_catalog"}))
    (frame,) = [call.args[0] for call in websocket.send_json.call_args_list]
    return frame


class TestCatalogOverTheSocket:
    def test_lists_the_enabled_skills_without_instructions(self):
        frame = _catalog(_make_handler(SkillService(lambda: _Store(_SKILL))))

        assert frame["type"] == "skill_catalog" and "error" not in frame
        assert [item["name"] for item in frame["items"]] == ["triage"]
        assert "instructions" not in frame["items"][0]

    def test_requires_an_authenticated_socket_when_tool_auth_is_required(self):
        handler = _make_handler(SkillService(lambda: _Store(_SKILL)))
        handler.config.require_tool_auth = True

        frame = _catalog(handler)

        assert frame["items"] == [] and frame["error"]["code"] == "authentication_required"

    def test_accepts_any_credential_the_socket_authenticated_with(self):
        handler = _make_handler(SkillService(lambda: _Store(_SKILL)))
        handler.config.require_tool_auth = True
        session = handler.session_manager.get_session.return_value
        session.credentials = SimpleNamespace(get_auth_type_string=lambda: "bearer_token")

        assert [item["name"] for item in _catalog(handler)["items"]] == ["triage"]

    @pytest.mark.parametrize("skill_service", [None, SkillService(lambda: None)])
    def test_reports_unavailable_skills(self, skill_service):
        frame = _catalog(_make_handler(skill_service))

        assert frame["items"] == [] and frame["error"]["code"] == "skills_unavailable"

    def test_reports_a_store_failure_as_unavailable(self):
        store = _Store(_SKILL)
        store.list_enabled_catalog = AsyncMock(side_effect=RuntimeError("db down"))

        frame = _catalog(_make_handler(SkillService(lambda: store)))

        assert frame["error"]["code"] == "skills_unavailable"


# ---------------------------------------------------------------------------
# Through the real graph (mocked model)
# ---------------------------------------------------------------------------


def _chat_config():
    from autolangchat.config import ChatConfig

    return ChatConfig(AUTOCHAT_MODEL_ID="us.anthropic.claude-sonnet-5", excluded_paths=[])


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if isinstance(block, dict))


def _ai_message(content="", tool_calls=None):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    msg.response_metadata = {"model_id": "us.anthropic.claude-sonnet-5"}
    msg.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
    return msg


async def _run_graph(model_replies, user_message, **configurable):
    service = SkillService(lambda: _Store(_SKILL))
    await service.refresh()
    chat_config = _chat_config()
    tool_manager = ToolManager(
        generated_tools={}, config=chat_config, internal_tools=build_skill_tools(service), skill_service=service
    )
    model_inputs: list[Any] = []

    def reply(lc_messages, *args, **kwargs):
        model_inputs.append(lc_messages)
        return model_replies[len(model_inputs) - 1]

    def llm_factory(*args, **kwargs):
        llm = MagicMock()
        llm.bind_tools.return_value = llm
        llm.ainvoke = AsyncMock(side_effect=reply)
        return llm

    ledger = UsageLedger()
    if "direct" in configurable:
        command = parse_slash_command(user_message)
        configurable["direct_messages"] = (await load_slash_command(service, command)).messages
        del configurable["direct"]
    with patch("autolangchat.graph.nodes.llm_call.ChatBedrockConverse", side_effect=llm_factory):
        graph = build_chat_graph(chat_config, tool_manager=tool_manager)
        result = await graph.ainvoke(
            {"user_message": user_message},
            config={"configurable": {"thread_id": f"t-{id(ledger)}", "usage_ledger": ledger, **configurable}},
        )
    return result, model_inputs, ledger


def _shape(history):
    """Roles, keys and tool-call/result keys of a history, without the values or the ``direct`` flags."""
    shape = []
    for message in history:
        metadata_keys = sorted(set(message["metadata"]) - {"direct", "model_id", "usage", "stop_reason"})
        shape.append(
            (
                message["role"],
                sorted(message),
                metadata_keys,
                [sorted(call) for call in message["tool_calls"]],
                [
                    (sorted(entry), sorted(set(entry.get("metadata", {})) - {"direct"}))
                    for entry in message["tool_results"]
                ],
            )
        )
    return shape


class TestThroughTheGraph:
    async def test_first_model_call_already_has_the_instructions(self):
        result, model_inputs, ledger = await _run_graph(
            [_ai_message("Step 3 failed.")], "/triage job_id=j-1", direct=True
        )

        assert len(model_inputs) == 1
        human, assistant, tool = model_inputs[0][-3:]
        assert _text(human.content) == "/triage job_id=j-1"
        assert assistant.tool_calls[0]["name"] == "load_skill"
        assert "List the failing step first." in _text(tool.content)
        assert tool.tool_call_id == assistant.tool_calls[0]["id"]
        assert result["messages"][-1]["content"] == "Step 3 failed."

    async def test_usage_counts_only_the_turns_inference(self):
        _, _, ledger = await _run_graph([_ai_message("ok")], "/triage job_id=j-1", direct=True)

        assert [event.purpose for event in ledger.events] == ["chat"]
        assert ledger.summary()["input_tokens"] == 10

    async def test_direct_and_model_initiated_loads_have_the_same_history_shape(self):
        load_call = {"name": "load_skill", "args": {"name": "triage", "params": {"job_id": "j-1"}}}
        model, _, _ = await _run_graph(
            [_ai_message(tool_calls=[{**load_call, "id": "s1", "type": "tool_call"}]), _ai_message("Step 3 failed.")],
            "why did j-1 fail?",
        )
        direct, _, _ = await _run_graph([_ai_message("Step 3 failed.")], "/triage job_id=j-1", direct=True)

        model_history = format_history_messages(model["messages"])
        direct_history = format_history_messages(direct["messages"])
        assert _shape(direct_history) == _shape(model_history)
        model_entry = model_history[2]["tool_results"][0]
        direct_entry = direct_history[2]["tool_results"][0]
        assert direct_entry["result"] == model_entry["result"]
        assert direct_entry["metadata"] == {**model_entry["metadata"], "direct": True}
        assert direct_history[1]["tool_calls"][0]["args"] == model_history[1]["tool_calls"][0]["args"]
