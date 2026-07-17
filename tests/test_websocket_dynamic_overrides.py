"""XMGPLAT-9697 Phase 2 — WebSocket dynamic parameter override tests.

Covers `config_overrides`/`override_mode` on `chat` messages, the
`config_update`/`config_reset` message types, merge priority (per-message >
per-session > global), validation-rejection surfacing, the disabled feature
gate, and session overrides resetting on reconnect.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

# Sibling test modules install lightweight ``autolangchat`` package stubs into
# ``sys.modules`` at import time (see test_websocket_response_metadata.py).
# Drop stub entries so this file always gets the genuine package.
for _name in [n for n in list(sys.modules) if n == "autolangchat" or n.startswith("autolangchat.")]:
    if getattr(sys.modules.get(_name), "__spec__", None) is None:
        del sys.modules[_name]

from autolangchat.config import ChatConfig  # noqa: E402
from autolangchat.session_manager import ChatSession  # noqa: E402
from autolangchat.websocket_handler import WebSocketChatHandler  # noqa: E402


def _assistant_graph_state():
    return {
        "messages": [
            {
                "role": "assistant",
                "content": "hi",
                "tool_calls": [],
                "tool_results": [],
                "metadata": {"message_id": "msg-1"},
            }
        ],
        "metadata": {},
        "kb_results": [],
    }


def _make_handler(enable_dynamic_overrides=True, allowed_dynamic_overrides=None):
    # NOTE: ChatConfig fields all declare a pydantic ``alias`` (e.g.
    # AUTOCHAT_ENABLE_DYNAMIC_OVERRIDES), and pydantic-settings only accepts
    # the alias at construction time (no ``populate_by_name``) -- passing the
    # Python field name directly (``ChatConfig(enable_dynamic_overrides=True)``)
    # silently falls back to the default. Use ``model_copy(update=...)`` instead,
    # which updates by field name and matches how the handler itself builds
    # `effective_config`.
    config = ChatConfig().model_copy(
        update={
            "enable_dynamic_overrides": enable_dynamic_overrides,
            "allowed_dynamic_overrides": allowed_dynamic_overrides,
        }
    )

    session = ChatSession(session_id="session-123", websocket=MagicMock())

    session_manager = MagicMock()
    session_manager.get_session = AsyncMock(return_value=session)

    chat_graph = MagicMock()
    chat_graph.ainvoke = AsyncMock(return_value=_assistant_graph_state())

    handler = WebSocketChatHandler(
        session_manager=session_manager,
        config=config,
        chat_graph=chat_graph,
    )
    return handler, session


def _websocket():
    websocket = MagicMock()
    websocket.send_json = AsyncMock()
    return websocket


def _sent_messages(websocket):
    return [call.args[0] for call in websocket.send_json.call_args_list]


def _effective_config(handler):
    _, kwargs = handler.chat_graph.ainvoke.call_args
    return kwargs["config"]["configurable"]["chat_config"]


class TestChatMessageOverrides:
    def test_per_message_override_reaches_effective_config(self):
        handler, _ = _make_handler()
        websocket = _websocket()

        asyncio.run(
            handler._handle_chat_message(websocket, {"message": "hi", "config_overrides": {"temperature": 0.1}})
        )

        effective_config = _effective_config(handler)
        assert effective_config.temperature == 0.1
        # Global baked-in config is untouched
        assert handler.config.temperature == 0.7

    def test_message_mode_override_not_persisted_to_session(self):
        handler, session = _make_handler()
        websocket = _websocket()

        asyncio.run(
            handler._handle_chat_message(
                websocket,
                {"message": "hi", "config_overrides": {"temperature": 0.1}, "override_mode": "message"},
            )
        )

        assert session.metadata.get("config_overrides", {}) == {}

    def test_message_mode_override_does_not_leak_into_next_turn(self):
        """XMGPLAT-9697 Phase 3 regression test: a one-shot (`override_mode:
        "message"`) override must not affect turn N+1 -- the effective config
        is rebuilt fresh from the untouched global `self.config` every turn."""
        handler, session = _make_handler()
        websocket = _websocket()

        asyncio.run(
            handler._handle_chat_message(websocket, {"message": "hi", "config_overrides": {"temperature": 0.1}})
        )
        assert _effective_config(handler).temperature == 0.1

        asyncio.run(handler._handle_chat_message(websocket, {"message": "again"}))
        assert _effective_config(handler).temperature == 0.7
        assert session.metadata.get("config_overrides", {}) == {}
        # The global baked-in config was never mutated by the first turn.
        assert handler.config.temperature == 0.7

    def test_session_mode_override_persisted_and_applied_next_turn(self):
        handler, session = _make_handler()
        websocket = _websocket()

        asyncio.run(
            handler._handle_chat_message(
                websocket,
                {"message": "hi", "config_overrides": {"temperature": 0.2}, "override_mode": "session"},
            )
        )
        assert session.metadata["config_overrides"] == {"temperature": 0.2}

        # Next turn, with no overrides in the message itself, should still
        # apply the persisted session override.
        asyncio.run(handler._handle_chat_message(websocket, {"message": "again"}))
        assert _effective_config(handler).temperature == 0.2

    def test_merge_priority_message_overrides_session(self):
        handler, session = _make_handler()
        session.metadata["config_overrides"] = {"temperature": 0.2}
        websocket = _websocket()

        asyncio.run(
            handler._handle_chat_message(websocket, {"message": "hi", "config_overrides": {"temperature": 0.9}})
        )

        assert _effective_config(handler).temperature == 0.9

    def test_rejected_overrides_surfaced_in_response_without_failing_turn(self):
        handler, _ = _make_handler()
        websocket = _websocket()

        asyncio.run(
            handler._handle_chat_message(
                websocket,
                {"message": "hi", "config_overrides": {"temperature": 5.0, "enable_rag": True}},
            )
        )

        ai_responses = [m for m in _sent_messages(websocket) if m.get("type") == "ai_response"]
        assert ai_responses
        rejected = ai_responses[-1]["metadata"].get("rejected_overrides", [])
        assert any("temperature" in r for r in rejected)

        effective_config = _effective_config(handler)
        assert effective_config.enable_rag is True
        assert effective_config.temperature == 0.7  # rejected -> falls back to global default

    def test_disabled_feature_gate_ignores_all_overrides(self):
        handler, _ = _make_handler(enable_dynamic_overrides=False)
        websocket = _websocket()

        asyncio.run(
            handler._handle_chat_message(websocket, {"message": "hi", "config_overrides": {"temperature": 0.1}})
        )

        effective_config = _effective_config(handler)
        assert effective_config is handler.config
        assert effective_config.temperature == 0.7

    def test_allowlist_restricts_overridable_params(self):
        handler, _ = _make_handler(allowed_dynamic_overrides=["temperature"])
        websocket = _websocket()

        asyncio.run(
            handler._handle_chat_message(
                websocket,
                {"message": "hi", "config_overrides": {"temperature": 0.3, "max_tokens": 100}},
            )
        )

        effective_config = _effective_config(handler)
        assert effective_config.temperature == 0.3
        assert effective_config.max_tokens == 4096  # rejected (not in allowlist) -> global default


class TestConfigUpdateMessage:
    def test_config_update_persists_session_overrides_by_default(self):
        handler, session = _make_handler()
        websocket = _websocket()

        asyncio.run(handler._handle_config_update(websocket, {"config_overrides": {"temperature": 0.4}}))

        assert session.metadata["config_overrides"] == {"temperature": 0.4}
        sent = _sent_messages(websocket)
        assert sent[-1]["type"] == "config_updated"
        assert sent[-1]["active_overrides"] == {"temperature": 0.4}
        assert sent[-1]["rejected_overrides"] == []

    def test_config_update_message_mode_does_not_persist(self):
        handler, session = _make_handler()
        websocket = _websocket()

        asyncio.run(
            handler._handle_config_update(
                websocket, {"config_overrides": {"temperature": 0.4}, "override_mode": "message"}
            )
        )

        assert session.metadata.get("config_overrides", {}) == {}

    def test_config_update_surfaces_rejections(self):
        handler, _ = _make_handler()
        websocket = _websocket()

        asyncio.run(handler._handle_config_update(websocket, {"config_overrides": {"bogus_param": 1}}))

        sent = _sent_messages(websocket)
        assert sent[-1]["type"] == "config_updated"
        assert sent[-1]["applied_overrides"] == {}
        assert any("bogus_param" in r for r in sent[-1]["rejected_overrides"])

    def test_config_update_disabled_feature_gate(self):
        handler, session = _make_handler(enable_dynamic_overrides=False)
        websocket = _websocket()

        asyncio.run(handler._handle_config_update(websocket, {"config_overrides": {"temperature": 0.4}}))

        assert session.metadata.get("config_overrides", {}) == {}
        sent = _sent_messages(websocket)
        assert sent[-1]["applied_overrides"] == {}
        assert len(sent[-1]["rejected_overrides"]) == 1


class TestConfigResetMessage:
    def test_config_reset_clears_session_overrides(self):
        handler, session = _make_handler()
        session.metadata["config_overrides"] = {"temperature": 0.9}
        websocket = _websocket()

        asyncio.run(handler._handle_config_reset(websocket, {}))

        assert session.metadata.get("config_overrides", {}) == {}
        sent = _sent_messages(websocket)
        assert sent[-1]["type"] == "config_updated"
        assert sent[-1]["active_overrides"] == {}

    def test_config_reset_next_turn_uses_global_defaults(self):
        handler, session = _make_handler()
        session.metadata["config_overrides"] = {"temperature": 0.9}
        websocket = _websocket()

        asyncio.run(handler._handle_config_reset(websocket, {}))
        asyncio.run(handler._handle_chat_message(websocket, {"message": "hi"}))

        assert _effective_config(handler).temperature == 0.7


class TestSessionOverridesResetOnReconnect:
    def test_new_session_has_no_config_overrides(self):
        """A fresh ChatSession (as created on (re)connect) starts with empty
        metadata, so session-level overrides from a prior connection do not
        carry over (XMGPLAT-9697 v1 design: in-memory only, see plan Notes)."""
        session = ChatSession(session_id="new-session", websocket=MagicMock())
        assert session.metadata.get("config_overrides", {}) == {}
