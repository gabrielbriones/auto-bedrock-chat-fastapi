"""Unit tests for AutoLangChatPlugin.startup()/shutdown() idempotency (XMGPLAT-10682).

These tests build a minimal AutoLangChatPlugin instance without running
__init__ (which requires a full FastAPI app, validated ChatConfig, and
real AWS/DB dependencies). Only the attributes touched by startup()/
shutdown() are set, and the checkpointer open/close/purge functions are
patched so no real connection pool is opened.
"""

import asyncio
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Other test modules in this suite use a stub-package import helper
# (``tests._autolangchat_imports.load_module``) that temporarily replaces
# ``autolangchat``/``autolangchat.db`` (among others) with bare stub packages
# in ``sys.modules`` while loading a single submodule in isolation. On some
# collection orders those stubs are left behind instead of being restored to
# the real package (see XMGPLAT-10766), so a later plain
# ``from autolangchat.plugin import ...`` here can pick up the stub and fail
# with ImportError. Only these two specific entries are ever left stubbed;
# drop them (if they are indeed bare stubs, i.e. have no ``__spec__``) so
# Python re-imports the real packages. Already-real submodules (e.g.
# ``autolangchat.graph.graph``) are untouched and get reused as-is, so this
# doesn't create duplicate class objects for anything else.
for _name in ("autolangchat", "autolangchat.db"):
    _mod = sys.modules.get(_name)
    if _mod is not None and getattr(_mod, "__spec__", None) is None:
        del sys.modules[_name]

import autolangchat.graph.checkpointer as checkpointer_module  # noqa: E402
from autolangchat.plugin import AutoLangChatPlugin  # noqa: E402


def _make_plugin(**overrides) -> AutoLangChatPlugin:
    """Build a bare AutoLangChatPlugin with just the lifecycle-relevant attributes set."""
    plugin = object.__new__(AutoLangChatPlugin)
    plugin._started = False
    plugin._startup_lock = asyncio.Lock()
    plugin._credibility_decay_task = None
    plugin._kb_store = None
    plugin._kb_needs_population = False
    plugin._feedback_store = None
    plugin._token_usage_store = None
    plugin._conversation_store = None
    plugin.config = SimpleNamespace(
        checkpoint_ttl_seconds=3600,
        kb_credibility_decay_enabled=False,
        kb_allow_empty=True,
    )
    plugin.chat_graph = SimpleNamespace(checkpointer=MagicMock(name="checkpointer"))
    plugin.websocket_handler = SimpleNamespace(shutdown=AsyncMock(), feedback_store=None)
    plugin.tool_manager = SimpleNamespace(shutdown=AsyncMock())
    for key, value in overrides.items():
        setattr(plugin, key, value)
    return plugin


@pytest.fixture(autouse=True)
def patch_checkpointer(monkeypatch):
    """Patch checkpointer open/close/purge so no real DB pool is touched."""
    mocks = SimpleNamespace(
        open=AsyncMock(),
        close=AsyncMock(),
        purge=AsyncMock(return_value=0),
    )
    monkeypatch.setattr(checkpointer_module, "open_checkpointer", mocks.open)
    monkeypatch.setattr(checkpointer_module, "close_checkpointer", mocks.close)
    monkeypatch.setattr(checkpointer_module, "purge_expired_checkpoints", mocks.purge)
    return mocks


async def _cancel_new_tasks(before):
    """Cancel any asyncio tasks spawned during a test (e.g. the expiry-sweep loop)."""
    for task in asyncio.all_tasks() - before:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_startup_is_idempotent(patch_checkpointer):
    plugin = _make_plugin()
    before = asyncio.all_tasks()

    await plugin.startup()
    await plugin.startup()
    await plugin.startup()

    assert plugin._started is True
    assert patch_checkpointer.open.await_count == 1

    await _cancel_new_tasks(before)


async def test_startup_does_not_double_schedule_background_tasks(patch_checkpointer):
    plugin = _make_plugin()
    before = asyncio.all_tasks()

    await plugin.startup()
    tasks_after_first = asyncio.all_tasks() - before
    await plugin.startup()
    tasks_after_second = asyncio.all_tasks() - before

    assert tasks_after_first == tasks_after_second

    await _cancel_new_tasks(before)


async def test_concurrent_startup_only_runs_once(patch_checkpointer):
    plugin = _make_plugin()
    before = asyncio.all_tasks()

    await asyncio.gather(plugin.startup(), plugin.startup(), plugin.startup())

    assert plugin._started is True
    assert patch_checkpointer.open.await_count == 1

    await _cancel_new_tasks(before)


async def test_startup_does_not_rerun_kb_population(monkeypatch, patch_checkpointer):
    import autolangchat.commands.kb as kb_module

    kb_populate_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(kb_module, "kb_populate", kb_populate_mock)

    plugin = _make_plugin(_kb_needs_population=True)
    plugin.config.kb_sources_config = "sources.yml"
    plugin.config.kb_database_path = "kb.db"
    before = asyncio.all_tasks()

    await plugin.startup()
    await plugin.startup()

    assert kb_populate_mock.await_count == 1

    await _cancel_new_tasks(before)


async def test_shutdown_before_startup_is_a_noop():
    plugin = _make_plugin()

    await plugin.shutdown()

    assert plugin._started is False
    plugin.websocket_handler.shutdown.assert_not_awaited()
    plugin.tool_manager.shutdown.assert_not_awaited()


async def test_shutdown_is_idempotent(patch_checkpointer):
    plugin = _make_plugin()
    before = asyncio.all_tasks()
    await plugin.startup()

    await plugin.shutdown()
    await plugin.shutdown()
    await plugin.shutdown()

    assert plugin._started is False
    assert patch_checkpointer.close.await_count == 1
    plugin.websocket_handler.shutdown.assert_awaited_once()
    plugin.tool_manager.shutdown.assert_awaited_once()

    await _cancel_new_tasks(before)


async def test_startup_after_shutdown_restarts(patch_checkpointer):
    plugin = _make_plugin()
    before = asyncio.all_tasks()

    await plugin.startup()
    await plugin.shutdown()
    await plugin.startup()

    assert plugin._started is True
    assert patch_checkpointer.open.await_count == 2
    assert patch_checkpointer.close.await_count == 1

    await _cancel_new_tasks(before)


# ---------------------------------------------------------------------------
# Conversation persistence: MemorySaver degraded-mode warning (XMGPLAT-10380)
# ---------------------------------------------------------------------------


async def test_startup_warns_when_memory_saver_and_conversation_persistence_enabled(patch_checkpointer, caplog):
    conversation_store = MagicMock()
    conversation_store.open = AsyncMock()
    plugin = _make_plugin(_conversation_store=conversation_store)
    plugin.config.conversation_persistence_enabled = True
    # A plain MagicMock checkpointer is not an AsyncPostgresSaver instance —
    # stands in for the default MemorySaver.
    plugin.chat_graph = SimpleNamespace(checkpointer=MagicMock(name="memory_saver"))
    plugin.websocket_handler.conversation_store = conversation_store
    before = asyncio.all_tasks()

    with caplog.at_level(logging.WARNING, logger="autolangchat.plugin"):
        await plugin.startup()

    assert any("not AsyncPostgresSaver" in r.message for r in caplog.records)
    assert plugin._started is True  # degraded-mode warning must not block startup

    await plugin.shutdown()
    await _cancel_new_tasks(before)


async def test_startup_does_not_warn_when_checkpointer_is_async_postgres_saver(patch_checkpointer, caplog):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    conversation_store = MagicMock()
    conversation_store.open = AsyncMock()
    plugin = _make_plugin(_conversation_store=conversation_store)
    plugin.config.conversation_persistence_enabled = True
    plugin.chat_graph = SimpleNamespace(checkpointer=MagicMock(spec=AsyncPostgresSaver))
    plugin.websocket_handler.conversation_store = conversation_store
    before = asyncio.all_tasks()

    with caplog.at_level(logging.WARNING, logger="autolangchat.plugin"):
        await plugin.startup()

    assert not any("not AsyncPostgresSaver" in r.message for r in caplog.records)
    assert plugin._started is True

    await plugin.shutdown()
    await _cancel_new_tasks(before)


async def test_startup_does_not_warn_when_conversation_store_not_configured(patch_checkpointer, caplog):
    # _conversation_store defaults to None in _make_plugin — even if the
    # config flag were somehow True, there's no store to have degraded.
    plugin = _make_plugin()
    plugin.config.conversation_persistence_enabled = True
    before = asyncio.all_tasks()

    with caplog.at_level(logging.WARNING, logger="autolangchat.plugin"):
        await plugin.startup()

    assert not any("not AsyncPostgresSaver" in r.message for r in caplog.records)

    await plugin.shutdown()
    await _cancel_new_tasks(before)


async def test_startup_does_not_warn_when_conversation_persistence_disabled(patch_checkpointer, caplog):
    conversation_store = MagicMock()
    conversation_store.open = AsyncMock()
    plugin = _make_plugin(_conversation_store=conversation_store)
    plugin.config.conversation_persistence_enabled = False
    plugin.chat_graph = SimpleNamespace(checkpointer=MagicMock(name="memory_saver"))
    plugin.websocket_handler.conversation_store = conversation_store
    before = asyncio.all_tasks()

    with caplog.at_level(logging.WARNING, logger="autolangchat.plugin"):
        await plugin.startup()

    assert not any("not AsyncPostgresSaver" in r.message for r in caplog.records)

    await plugin.shutdown()
    await _cancel_new_tasks(before)
