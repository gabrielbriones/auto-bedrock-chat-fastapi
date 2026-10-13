"""Unit tests for ChatSessionManager reconnect-collision handling.

Covers XMGPLAT-11842: a client-supplied ``preferred_session_id`` that is
already bound to a live connection must not silently let the new connection
overwrite the manager's bookkeeping while the old connection's own
``_websocket_to_session`` entry survives (the "late disconnect kills the
replacement session" bug).

  (a) collision, same user (or both anonymous) — stale connection evicted,
      its socket closed, new connection registered cleanly;
  (b) collision, different user — takeover rejected with ``SessionError``,
      the original session is left completely untouched;
  (c) ``remove_session`` is ownership-aware: a disconnect callback for a
      socket that is no longer the one registered for its session_id is a
      no-op for the (already-replaced) live session;
  (d) idle-expired and capacity-evicted sessions have their sockets closed;
  (e) ``_user_sessions``/``get_statistics()`` never accumulate duplicate
      session ids for the same user across a reconnect collision.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from autolangchat import session_manager as session_manager_module
from autolangchat.exceptions import SessionError
from autolangchat.session_manager import ChatSession, ChatSessionManager


@pytest.fixture
def session_manager():
    cfg = MagicMock()
    cfg.max_sessions = 100
    cfg.session_timeout = 3600
    return ChatSessionManager(cfg)


def _websocket():
    ws = MagicMock()
    ws.close = AsyncMock()
    return ws


async def test_collision_same_user_evicts_stale_connection_and_closes_socket(session_manager):
    preferred = str(uuid.uuid4())
    old_ws = _websocket()
    new_ws = _websocket()

    sid_old = await session_manager.create_session(websocket=old_ws, user_id="alice", preferred_session_id=preferred)
    sid_new = await session_manager.create_session(websocket=new_ws, user_id="alice", preferred_session_id=preferred)

    assert sid_old == sid_new == preferred

    session = await session_manager.get_session_by_id(preferred)
    assert session.websocket is new_ws

    old_ws.close.assert_awaited_once()
    # The stale socket's mapping must not resolve to anything anymore.
    assert await session_manager.peek_session(old_ws) is None


async def test_collision_same_user_both_anonymous_replaces_cleanly(session_manager):
    preferred = str(uuid.uuid4())
    old_ws = _websocket()
    new_ws = _websocket()

    await session_manager.create_session(websocket=old_ws, preferred_session_id=preferred)
    await session_manager.create_session(websocket=new_ws, preferred_session_id=preferred)

    session = await session_manager.get_session_by_id(preferred)
    assert session.websocket is new_ws
    old_ws.close.assert_awaited_once()


async def test_collision_different_user_takeover_rejected(session_manager):
    preferred = str(uuid.uuid4())
    old_ws = _websocket()
    new_ws = _websocket()

    await session_manager.create_session(websocket=old_ws, user_id="alice", preferred_session_id=preferred)

    with pytest.raises(SessionError):
        await session_manager.create_session(websocket=new_ws, user_id="bob", preferred_session_id=preferred)

    # Original session is completely untouched.
    session = await session_manager.get_session_by_id(preferred)
    assert session.websocket is old_ws
    assert session.user_id == "alice"
    old_ws.close.assert_not_called()
    new_ws.close.assert_not_called()
    assert await session_manager.peek_session(new_ws) is None


async def test_remove_session_is_noop_for_superseded_websocket(session_manager):
    """Direct regression test for the reported bug: even if a stale
    ``_websocket_to_session`` entry for an old socket somehow survives a
    takeover, a late disconnect of that socket must not remove the live
    replacement session."""
    old_ws = _websocket()
    new_ws = _websocket()

    sid = await session_manager.create_session(websocket=old_ws, user_id="alice")

    # Simulate the race the fix guards against: _sessions[sid] already
    # points at a different connection while old_ws's mapping entry is
    # still present.
    session_manager._sessions[sid] = ChatSession(session_id=sid, websocket=new_ws, user_id="alice")
    session_manager._websocket_to_session[new_ws] = sid

    result = await session_manager.remove_session(old_ws)

    assert result is None
    replacement = await session_manager.get_session_by_id(sid)
    assert replacement is not None
    assert replacement.websocket is new_ws
    assert session_manager._user_sessions["alice"] == [sid]


async def test_idle_expired_session_socket_closed(session_manager, monkeypatch):
    ws = _websocket()
    sid = await session_manager.create_session(websocket=ws)
    session = await session_manager.get_session_by_id(sid)
    session.last_activity = datetime.now() - timedelta(seconds=session_manager.config.session_timeout + 1)

    call_count = 0

    async def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(session_manager_module.asyncio, "sleep", fake_sleep)

    await session_manager._cleanup_expired_sessions()

    assert await session_manager.get_session_by_id(sid) is None
    ws.close.assert_awaited_once()


async def test_capacity_eviction_closes_oldest_sockets(session_manager):
    old_ws = _websocket()
    new_ws = _websocket()

    sid_old = await session_manager.create_session(websocket=old_ws)
    (await session_manager.get_session_by_id(sid_old)).created_at = datetime.now() - timedelta(minutes=5)
    sid_new = await session_manager.create_session(websocket=new_ws)

    await session_manager._cleanup_oldest_sessions(1)

    assert await session_manager.get_session_by_id(sid_old) is None
    assert await session_manager.get_session_by_id(sid_new) is not None
    old_ws.close.assert_awaited_once()
    new_ws.close.assert_not_called()


async def test_statistics_consistent_no_duplicate_ids_across_collision(session_manager):
    preferred = str(uuid.uuid4())
    old_ws = _websocket()
    new_ws = _websocket()

    await session_manager.create_session(websocket=old_ws, user_id="alice", preferred_session_id=preferred)
    await session_manager.create_session(websocket=new_ws, user_id="alice", preferred_session_id=preferred)

    stats = await session_manager.get_statistics()
    assert stats["unique_users"] == 1
    assert stats["active_sessions"] == 1
    assert session_manager._user_sessions["alice"] == [preferred]
