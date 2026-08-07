"""XMGPLAT-11193 Part 1 — persisted per-user settings, WebSocket paths.

Covers the settings-hydration path in ``WebSocketChatHandler``:

  (a) first authenticated connect creates an empty row (only parameters the
      user actually changes are ever stored, so a later change to the global
      defaults still reaches existing users) and pushes a ``config_updated``
      to the client;
  (b) a subsequent connect restores the user's stored values instead of the
      global ``override_defaults``;
  (c) settings invalid for the current config (unknown model, over-cap
      ``max_tokens``) are reconciled on load — dropped, reported to the user,
      and written back so they don't resurface every connect;
  (d) anonymous sessions write nothing and get no ``config_updated``;
  (e) the feature gates (``user_settings_persistence_enabled``,
      ``enable_dynamic_overrides``, no store wired) all fall back to today's
      in-memory behaviour;
  (f) ``config_update`` persists session-scoped overrides but not
      per-message ones;
  (g) ``config_reset`` empties the stored row rather than deleting it;
  (h) store failures never break chat — they are logged and the session
      continues with in-memory overrides.
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
from autolangchat.db import SQLiteUserSettingsStore  # noqa: E402
from autolangchat.session_manager import ChatSession  # noqa: E402
from autolangchat.websocket_handler import WebSocketChatHandler  # noqa: E402


async def _open_store():
    store = SQLiteUserSettingsStore(db_path=":memory:")
    await store.open()
    return store


def _make_handler(
    store,
    user_id="alice",
    persistence_enabled=True,
    enable_dynamic_overrides=True,
):
    # ChatConfig fields all declare a pydantic alias and there's no
    # ``populate_by_name``, so construction by field name silently no-ops —
    # use ``model_copy(update=...)`` (same approach as
    # tests/test_websocket_dynamic_overrides.py).
    config = ChatConfig().model_copy(
        update={
            "enable_dynamic_overrides": enable_dynamic_overrides,
            "user_settings_persistence_enabled": persistence_enabled,
        }
    )

    session = ChatSession(session_id="session-123", websocket=MagicMock(), user_id=user_id)

    session_manager = MagicMock()
    session_manager.get_session = AsyncMock(return_value=session)

    handler = WebSocketChatHandler(
        session_manager=session_manager,
        config=config,
        chat_graph=MagicMock(),
        user_settings_store=store,
    )
    return handler, session


def _websocket():
    websocket = MagicMock()
    websocket.send_json = AsyncMock()
    return websocket


def _sent(websocket, message_type):
    return [c.args[0] for c in websocket.send_json.call_args_list if c.args[0].get("type") == message_type]


# ---------------------------------------------------------------------------
# Hydration on connect
# ---------------------------------------------------------------------------


def test_first_connect_creates_an_empty_row():
    """Only parameters the user actually changes are stored -- persisting a
    snapshot of the global defaults would pin every user to whatever was
    configured the day they first logged in."""

    async def scenario():
        store = await _open_store()
        handler, session = _make_handler(store)
        websocket = _websocket()

        await handler._hydrate_user_settings(websocket)

        row = await store.get_settings("alice")
        await store.close()
        return row, session, websocket

    row, session, websocket = asyncio.run(scenario())

    assert row is not None
    assert row["settings"] == {}
    assert "config_overrides" not in session.metadata
    assert _sent(websocket, "config_updated")[0]["active_overrides"] == {}


def test_reconnect_restores_persisted_values():
    async def scenario():
        store = await _open_store()
        await store.set_settings("alice", {"temperature": 0.2, "max_tokens": 512})
        handler, session = _make_handler(store)
        websocket = _websocket()

        await handler._hydrate_user_settings(websocket)

        await store.close()
        return session, websocket

    session, websocket = asyncio.run(scenario())

    assert session.metadata["config_overrides"] == {"temperature": 0.2, "max_tokens": 512}
    assert _sent(websocket, "config_updated")[0]["active_overrides"] == {"temperature": 0.2, "max_tokens": 512}


def test_invalid_persisted_settings_are_reconciled_on_load():
    """A stored model_id that is no longer a known Bedrock profile must be
    dropped (and reported), not raise — and must not resurface next connect."""

    async def scenario():
        store = await _open_store()
        await store.set_settings("alice", {"model_id": "retired.model-v1:0", "temperature": 0.3})
        handler, session = _make_handler(store)
        websocket = _websocket()

        await handler._hydrate_user_settings(websocket)

        row = await store.get_settings("alice")
        await store.close()
        return row, session, websocket

    row, session, websocket = asyncio.run(scenario())

    assert session.metadata["config_overrides"] == {"temperature": 0.3}
    # Reconciled document written back, so the stale key is gone for good.
    assert row["settings"] == {"temperature": 0.3}

    message = _sent(websocket, "config_updated")[0]
    assert message["rejected_overrides"]
    assert "retired.model-v1:0" in message["rejected_overrides"][0]


def test_anonymous_session_persists_nothing():
    async def scenario():
        store = await _open_store()
        handler, session = _make_handler(store, user_id=None)
        websocket = _websocket()

        await handler._hydrate_user_settings(websocket)
        await handler._handle_config_update(websocket, {"config_overrides": {"temperature": 0.4}})

        rows = store._conn.execute("SELECT count(*) FROM user_settings").fetchone()[0]
        await store.close()
        return rows, session, websocket

    rows, session, websocket = asyncio.run(scenario())

    assert rows == 0
    # Session-only behaviour is preserved for the anonymous user.
    assert session.metadata["config_overrides"] == {"temperature": 0.4}
    # Only the config_update confirmation — no hydration message.
    assert len(_sent(websocket, "config_updated")) == 1


def test_hydration_is_a_noop_when_persistence_disabled():
    async def scenario():
        store = await _open_store()
        handler, session = _make_handler(store, persistence_enabled=False)
        websocket = _websocket()

        await handler._hydrate_user_settings(websocket)

        row = await store.get_settings("alice")
        await store.close()
        return row, session, websocket

    row, session, websocket = asyncio.run(scenario())

    assert row is None
    assert "config_overrides" not in session.metadata
    assert _sent(websocket, "config_updated") == []


def test_hydration_is_a_noop_when_dynamic_overrides_disabled():
    async def scenario():
        store = await _open_store()
        handler, _ = _make_handler(store, enable_dynamic_overrides=False)
        websocket = _websocket()

        await handler._hydrate_user_settings(websocket)

        row = await store.get_settings("alice")
        await store.close()
        return row

    assert asyncio.run(scenario()) is None


def test_hydration_is_a_noop_without_a_store():
    """Covers the degraded path where the store failed to open at startup."""

    async def scenario():
        handler, session = _make_handler(store=None)
        websocket = _websocket()

        await handler._hydrate_user_settings(websocket)
        await handler._handle_config_update(websocket, {"config_overrides": {"temperature": 0.4}})
        return session

    session = asyncio.run(scenario())
    assert session.metadata["config_overrides"] == {"temperature": 0.4}


# ---------------------------------------------------------------------------
# config_update / config_reset persistence
# ---------------------------------------------------------------------------


def test_config_update_persists_session_overrides():
    async def scenario():
        store = await _open_store()
        handler, _ = _make_handler(store)
        websocket = _websocket()

        await handler._handle_config_update(websocket, {"config_overrides": {"temperature": 0.4}})
        await handler._handle_config_update(websocket, {"config_overrides": {"max_tokens": 512}})

        row = await store.get_settings("alice")
        await store.close()
        return row

    row = asyncio.run(scenario())
    # The full active set is stored, not just the last delta.
    assert row["settings"] == {"temperature": 0.4, "max_tokens": 512}


def test_config_update_in_message_mode_is_not_persisted():
    async def scenario():
        store = await _open_store()
        handler, _ = _make_handler(store)
        websocket = _websocket()

        await handler._handle_config_update(
            websocket,
            {"config_overrides": {"temperature": 0.4}, "override_mode": "message"},
        )

        row = await store.get_settings("alice")
        await store.close()
        return row

    assert asyncio.run(scenario()) is None


def test_config_reset_clears_and_persists_the_empty_document():
    async def scenario():
        store = await _open_store()
        handler, session = _make_handler(store)
        websocket = _websocket()

        await handler._handle_config_update(websocket, {"config_overrides": {"temperature": 0.4}})
        await handler._handle_config_reset(websocket, {})

        row = await store.get_settings("alice")
        await store.close()
        return row, session

    row, session = asyncio.run(scenario())

    # Row is emptied, not deleted.
    assert row is not None
    assert row["settings"] == {}
    assert "config_overrides" not in session.metadata


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


def test_store_read_failure_does_not_break_the_connection():
    async def scenario():
        store = MagicMock()
        store.get_or_create_settings = AsyncMock(side_effect=RuntimeError("db down"))
        handler, session = _make_handler(store)
        websocket = _websocket()

        await handler._hydrate_user_settings(websocket)
        return session, websocket

    session, websocket = asyncio.run(scenario())

    assert "config_overrides" not in session.metadata
    assert _sent(websocket, "config_updated") == []


def test_store_write_failure_does_not_break_config_update():
    async def scenario():
        store = MagicMock()
        store.set_settings = AsyncMock(side_effect=RuntimeError("db down"))
        handler, session = _make_handler(store)
        websocket = _websocket()

        await handler._handle_config_update(websocket, {"config_overrides": {"temperature": 0.4}})
        return session, websocket

    session, websocket = asyncio.run(scenario())

    # The override still applies for this session, and the client is still
    # told about it.
    assert session.metadata["config_overrides"] == {"temperature": 0.4}
    assert _sent(websocket, "config_updated")[0]["active_overrides"] == {"temperature": 0.4}
