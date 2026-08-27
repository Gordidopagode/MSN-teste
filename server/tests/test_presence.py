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

    # sender receives the requested transition too; the earlier online
    # notice from the second user's login is intentionally ignored.
    a_notes = [e for e in server.pending_for(a_cid)
               if e["type"] == "USER_STATUS_CHANGED" and e["payload"].get("username") == "mia"]
    assert a_notes and a_notes[-1]["payload"]["status"] == "away"
    assert a_notes[-1]["payload"]["status_message"] == "Almoçando"

    # other online users receive the same transition, identified by username.
    b_notes = [e for e in server.pending_for(b_cid)
               if e["type"] == "USER_STATUS_CHANGED" and e["payload"].get("username") == "mia"]
    assert b_notes and b_notes[-1]["payload"]["status"] == "away"


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
    other_cid, _ = await register_and_login(server, "ruth")
    user_id = server.core.store.get_user_by_username("quinn")["user_id"]
    server.core.client_disconnected(cid)
    assert server.core.presence.get_presence(user_id)["status"].value == "offline"
    notices = [event for event in server.pending_for(other_cid) if event["type"] == "USER_STATUS_CHANGED" and event["payload"].get("user_id") == user_id]
    assert notices and notices[-1]["payload"]["status"] == "offline"


@pytest.mark.asyncio
async def test_presence_is_consistent_through_login_disconnect_and_reconnect(server: FakeServer):
    alice_cid, _ = await register_and_login(server, "presence_alice")
    bob_cid, bob_session = await register_and_login(server, "presence_bob")
    bob_id = server.core.store.get_user_by_username("presence_bob")["user_id"]

    online_notices = [event for event in server.pending_for(alice_cid) if event["type"] == "USER_STATUS_CHANGED" and event["payload"].get("user_id") == bob_id]
    assert online_notices and online_notices[-1]["payload"]["status"] == "online"
    server.flush(alice_cid)

    server.core.client_disconnected(bob_cid)
    offline_notices = [event for event in server.pending_for(alice_cid) if event["type"] == "USER_STATUS_CHANGED" and event["payload"].get("user_id") == bob_id]
    assert offline_notices and offline_notices[-1]["payload"]["status"] == "offline"
    server.flush(alice_cid)
    server.flush(bob_cid)

    reconnect_cid = server.new_connection()
    await server.core.reconnect(reconnect_cid, bob_session)
    reconnect_online = [event for event in server.pending_for(alice_cid) if event["type"] == "USER_STATUS_CHANGED" and event["payload"].get("user_id") == bob_id]
    assert reconnect_online and reconnect_online[-1]["payload"]["status"] == "online"
    assert server.find(reconnect_cid, "RECONNECT_OK")
