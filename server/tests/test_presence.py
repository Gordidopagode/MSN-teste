"""Tests: presence states and broadcasts."""

import pytest

from server.tests.helpers import FakeServer
from server.tests.test_sessions import register_and_login


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.cleanup()


@pytest.mark.asyncio
async def test_status_change_broadcast(server: FakeServer):
    a_cid, _ = await register_and_login(server, "mia")
    b_cid, _ = await register_and_login(server, "noah")

    await server.core.change_status(a_cid, "away", "Almoçando")

    # sender receives the notification too
    a_notes = [e for e in server.pending_for(a_cid)
               if e["type"] == "USER_STATUS_CHANGED"]
    assert len(a_notes) == 1
    assert a_notes[0]["payload"]["status"] == "away"
    assert a_notes[0]["payload"]["status_message"] == "Almoçando"

    # other online users receive it as well
    b_notes = [e for e in server.pending_for(b_cid)
               if e["type"] == "USER_STATUS_CHANGED"]
    assert len(b_notes) == 1
    assert b_notes[0]["payload"]["username"] == "mia"


@pytest.mark.asyncio
async def test_invalid_status_rejected(server: FakeServer):
    cid, _ = await register_and_login(server, "omar")
    await server.core.change_status(cid, "invisivel")
    assert any(e["payload"]["code"] == "INVALID_STATUS"
               for e in server.find(cid, "ERROR"))
    # presence untouched
    me = server.core.online_users()[0]
    assert me["status"] == "online"


@pytest.mark.asyncio
async def test_logout_sets_offline(server: FakeServer):
    cid, _ = await register_and_login(server, "paul")
    user_id = server.core.online_users()[0]["user_id"]
    await server.core.logout(cid)
    assert server.core.presence.get_presence(user_id)["status"].value == "offline"


@pytest.mark.asyncio
async def test_unexpected_disconnect_sets_offline(server: FakeServer):
    cid, _ = await register_and_login(server, "quinn")
    user_id = server.core.online_users()[0]["user_id"]
    server.core.client_disconnected(cid)
    assert server.core.presence.get_presence(user_id)["status"].value == "offline"
