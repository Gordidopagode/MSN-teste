"""Tests: connection lifecycle, disconnect, reconnect, session separation."""

import pytest

from server.tests.helpers import FakeServer


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.cleanup()


async def register_and_login(server: FakeServer, username: str,
                                 password: str = "secret123"):
    cid = server.new_connection()
    await server.core.register(cid, username, username.capitalize(), password, f"{username}@example.com")
    assert server.find(cid, "REGISTER_OK")
    server.flush(cid)
    await server.core.authenticate(cid, username, password)
    auth_ok = server.find(cid, "AUTH_OK")
    assert auth_ok, f"{username} failed to authenticate"
    session_id = auth_ok[0]["payload"]["session_id"]
    server.flush(cid)
    return cid, session_id


@pytest.mark.asyncio
async def test_connection_requires_auth(server: FakeServer):
    cid = server.new_connection()
    await server.core.request_sync(cid)
    assert server.find(cid, "ERROR")
    await server.core.send_message(cid, "conv", "text", {"content": "x"})
    assert len(server.find(cid, "ERROR")) >= 1


@pytest.mark.asyncio
async def test_disconnect_keeps_session(server: FakeServer):
    cid, session_id = await register_and_login(server, "eva")
    assert len(server.core.online_users()) == 1
    # simulate unexpected network drop
    server.core.client_disconnected(cid)
    assert len(server.core.online_users()) == 0
    # session must still exist for reconnection
    assert server.core.sessions.get_session(session_id) is not None
    # status preserved as offline
    assert server.core.presence.get_presence(
        server.core.sessions.get_session_entry(session_id)["user"]["user_id"]
    )["status"].value == "offline"


@pytest.mark.asyncio
async def test_reconnect_restores_session(server: FakeServer):
    cid, session_id = await register_and_login(server, "frank")
    server.core.client_disconnected(cid)
    server.flush(cid)

    cid2 = server.new_connection()
    await server.core.reconnect(cid2, session_id)
    assert server.find(cid2, "RECONNECT_OK")
    assert len(server.core.online_users()) == 1
    assert server.core.sessions.connection_session_id(cid2) == session_id


@pytest.mark.asyncio
async def test_invalid_session_rejected(server: FakeServer):
    cid = server.new_connection()
    await server.core.reconnect(cid, "sessao_inexistente")
    assert any(e["payload"]["code"] == "RECONNECT_INVALID"
               for e in server.find(cid, "ERROR"))


@pytest.mark.asyncio
async def test_logout_destroys_session(server: FakeServer):
    cid, session_id = await register_and_login(server, "grace")
    await server.core.logout(cid)
    assert server.find(cid, "LOGOUT_OK")
    # session invalidated in the store AND removed from memory
    assert await server.core.auth.restore_session(session_id) is None
    assert server.core.sessions.get_session(session_id) is None
    assert len(server.core.online_users()) == 0
    # reconnect with the destroyed session must fail
    cid2 = server.new_connection()
    await server.core.reconnect(cid2, session_id)
    assert any(e["payload"]["code"] == "RECONNECT_INVALID"
               for e in server.find(cid2, "ERROR"))


@pytest.mark.asyncio
async def test_multiple_simultaneous_connections(server: FakeServer):
    users = ["harry", "iris", "jack", "kate", "leo"]
    sessions = {}
    for name in users:
        cid, sid = await register_and_login(server, name)
        sessions[name] = (cid, sid)

    # all five online at once
    assert len(server.core.online_users()) == 5
    assert {u["username"] for u in server.core.online_users()} == set(users)

    # disconnect the middle one; others unaffected
    mid_cid, _ = sessions["jack"]
    server.core.client_disconnected(mid_cid)
    server.flush(mid_cid)
    assert len(server.core.online_users()) == 4
    assert {u["username"] for u in server.core.online_users()} == \
        {"harry", "iris", "kate", "leo"}
