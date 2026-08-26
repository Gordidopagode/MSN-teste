"""Tests: synchronization, persistence across restarts, and a real
WebSocket end-to-end test with multiple simultaneous clients."""

import asyncio
import json
import shutil
import tempfile
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest
import websockets
from websockets.asyncio.client import connect as ws_connect

from server.core import ServerCore
from server.tests.helpers import FakeServer, make_settings
from server.tests.test_sessions import register_and_login


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.cleanup()


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_payload_contents(server: FakeServer):
    a_cid, a_sid = await register_and_login(server, "lila")
    b_cid, b_sid = await register_and_login(server, "max")

    # exchange some messages so history is non-empty
    a_id = server.core.online_users()[0]["user_id"]
    b_row = server.core.store.get_user_by_username("max")
    conv = server.core.conversations.get_or_create_individual(a_id, b_row["user_id"])
    for i in range(3):
        await server.core.send_message(
            a_cid, conv.conversation_id, "text", {"content": f"hi {i}"})
        server.flush(b_cid)

    await server.core.request_sync(b_cid)
    data = server.find(b_cid, "SYNC_DATA")
    assert len(data) == 1
    d = data[0]["payload"]["data"]
    assert d["identity"]["username"] == "max"
    assert d["session"]["session_id"] == b_sid
    assert any(u["username"] == "lila" for u in server.core.sessions.all_users)
    assert len(d["conversations"]) >= 1
    conv_ids = list(d["history"].keys())
    assert conv.conversation_id in conv_ids
    assert len(d["history"][conv.conversation_id]) == 3
    assert data[0]["payload"]["version"]  # protocol version present


@pytest.mark.asyncio
async def test_sync_before_auth_rejected(server: FakeServer):
    cid = server.new_connection()
    await server.core.request_sync(cid)
    assert any(e["payload"]["code"] == "AUTH_REQUIRED"
               for e in server.find(cid, "ERROR"))


# ---------------------------------------------------------------------------
# Persistence across server "restart"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persistence_across_restart():
    tmpdir = tempfile.mkdtemp(prefix="msn_persist_")
    try:
        settings = make_settings(tmpdir)

        # --- first run: register, login, status, message -------------------
        core1 = ServerCore(settings)
        c1 = "conn_1"
        core1.client_connected(c1)
        await core1.register(c1, "paula", "Paula", "senha123", "paula@example.com")
        assert any(e["type"] == "REGISTER_OK" for e in core1.pending.get(c1, []))
        core1.pending.pop(c1, None)
        await core1.authenticate(c1, "paula", "senha123")
        session_id = next(e["payload"]["session_id"] for e in core1.pending.get(c1, []) if e["type"] == "AUTH_OK")
        core1.pending.pop(c1, None)
        await core1.change_status(c1, "busy", "Trabalhando")
        core1.pending.pop(c1, None)

        # second user to hold an individual conversation
        c0 = "conn_0"
        core1.client_connected(c0)
        await core1.register(c0, "rafa", "Rafa", "senha123", "rafa@example.com")
        core1.pending.pop(c0, None)
        await core1.authenticate(c0, "rafa", "senha123")
        core1.pending.pop(c0, None)

        paula_row = core1.store.get_user_by_username("paula")
        rafa_row = core1.store.get_user_by_username("rafa")
        conv = core1.conversations.get_or_create_individual(
            paula_row["user_id"], rafa_row["user_id"])
        await core1.send_message(c1, conv.conversation_id, "text",
                                 {"content": "persiste!"})
        core1.pending.pop(c1, None)
        core1.pending.pop(c0, None)
        del core1  # simulate shutdown

        # --- second run (restart): state must be restored -------------------
        core2 = ServerCore(settings)
        user = core2.store.get_user_by_username("paula")
        assert user is not None, "user lost after restart"
        session = await core2.auth.restore_session(session_id)
        assert session is not None, "session lost after restart"

        # status survived: paula was set to busy before shutdown
        assert core2.presence.get_presence(paula_row["user_id"])["status"].value == "busy"
        assert core2.presence.get_presence(paula_row["user_id"])["status_message"] == "Trabalhando"

        # message history survived
        history = core2.messages.get_history(conv.conversation_id,
                                             paula_row["user_id"])
        assert len(history) == 1
        assert history[0]["payload"]["content"] == "persiste!"
        assert history[0]["sender_id"] == paula_row["user_id"]

        # reconnection with the preserved session works
        c2 = "conn_2"
        core2.client_connected(c2)
        await core2.reconnect(c2, session_id)
        assert any(e["type"] == "RECONNECT_OK" for e in core2.pending.get(c2, []))
        assert len(core2.online_users()) == 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Real WebSocket end-to-end with multiple simultaneous clients
# ---------------------------------------------------------------------------

async def ws_send(ws, **fields) -> dict:
    import json
    msg = {"command": fields.pop("command")}
    msg.update(fields)
    await ws.send(json.dumps(msg))
    return json.loads(await ws.recv())


async def ws_wait_type(ws, expected):
    while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        if message["type"] == expected:
            return message


async def ws_register_login(url, username, password="secret123"):
    ws = await ws_connect(url)
    reg = await ws_send(ws, command="REGISTER", username=username,
                        display_name=username.capitalize(), email=f"{username}@example.com", password=password)
    assert reg["type"] == "REGISTER_OK", reg
    auth = await ws_send(ws, command="LOGIN", username=username,
                         password=password)
    assert auth["type"] == "AUTH_OK", auth
    return ws, auth["payload"]["session_id"], auth["payload"]["user_id"]


@pytest.mark.asyncio
async def test_end_to_end_websocket_multiple_clients():
    tmpdir = tempfile.mkdtemp(prefix="msn_e2e_")
    try:
        settings = make_settings(tmpdir, port=0)
        core = ServerCore(settings)

        from server.network.handler import WebSocketHandler
        handler = WebSocketHandler(core)

        from websockets.asyncio.server import serve as ws_serve
        async with ws_serve(handler.handle, "127.0.0.1", 0) as srv:
            host, port = srv.sockets[0].getsockname()
            url = f"ws://{host}:{port}"

            ws_a, sid_a, uid_a = await ws_register_login(url, "e2e_alice")
            ws_b, sid_b, uid_b = await ws_register_login(url, "e2e_bruno")
            ws_c, sid_c, uid_c = await ws_register_login(url, "e2e_carla")
            assert len(core.online_users()) == 3

            import asyncio

            # sync (ws_send already returns the parsed reply envelope)
            sync = await ws_send(ws_b, command="REQUEST_SYNC")
            assert sync["type"] == "SYNC_DATA"
            assert len(sync["payload"]["data"]["presence"]) == 3

            # create group: a gets CONVERSATION_CREATED; b and c are notified
            grp = await ws_send(ws_a, command="CREATE_GROUP", name="Time",
                                participants=["e2e_bruno", "e2e_carla"])
            assert grp["type"] == "CONVERSATION_CREATED"
            group_id = grp["payload"]["conversation"]["conversation_id"]
            conv_b = await asyncio.wait_for(ws_b.recv(), timeout=5)
            conv_c = await asyncio.wait_for(ws_c.recv(), timeout=5)
            assert json.loads(conv_b)["type"] == "CONVERSATION_CREATED"
            assert json.loads(conv_c)["type"] == "CONVERSATION_CREATED"

            # message in group -> a gets ACK; b and c receive the message
            ack = await ws_send(ws_a, command="SEND_MESSAGE",
                                conversation_id=group_id, type="text",
                                payload={"content": "olá time"})
            assert ack["type"] == "MESSAGE_ACK"
            msg_b = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=5))
            msg_c = json.loads(await asyncio.wait_for(ws_c.recv(), timeout=5))
            assert msg_b["payload"]["message"]["payload"]["content"] == "olá time"
            assert msg_c["payload"]["message"]["payload"]["content"] == "olá time"

            # status change broadcast (one USER_STATUS_CHANGED per client)
            await ws_a.send(json.dumps({"command": "CHANGE_STATUS",
                                        "status": "away",
                                        "status_message": "AFK"}))
            seen = await asyncio.gather(
                asyncio.wait_for(ws_a.recv(), timeout=5),
                asyncio.wait_for(ws_b.recv(), timeout=5),
                asyncio.wait_for(ws_c.recv(), timeout=5),
            )
            parsed = [json.loads(m) for m in seen]
            assert all(s["type"] == "USER_STATUS_CHANGED" for s in parsed)
            assert parsed[0]["payload"]["status"] == "away"

            # history
            hist = await ws_send(ws_c, command="GET_HISTORY",
                                 conversation_id=group_id, limit=10)
            assert hist["type"] == "HISTORY"
            assert len(hist["payload"]["messages"]) == 1

            # unexpected disconnect (close socket b) -> session preserved
            await ws_b.close()
            await asyncio.sleep(0.2)
            assert len(core.online_users()) == 2
            assert await core.auth.restore_session(sid_b) is not None

            # reconnect b via new websocket
            ws_b2 = await ws_connect(url)
            resp = await ws_send(ws_b2, command="RECONNECT", session_id=sid_b)
            assert resp["type"] == "RECONNECT_OK", resp
            assert len(core.online_users()) == 3

            # logout a -> session destroyed
            logout_resp = await ws_send(ws_a, command="LOGOUT")
            assert logout_resp["type"] == "LOGOUT_OK"
            assert await core.auth.restore_session(sid_a) is None
            assert len(core.online_users()) == 2
            offline_notice = json.loads(await asyncio.wait_for(ws_b2.recv(), timeout=5))
            assert offline_notice["type"] == "USER_STATUS_CHANGED"
            assert offline_notice["payload"]["user_id"] == uid_a
            assert offline_notice["payload"]["status"] == "offline"

            # invalid raw frame
            await ws_b2.send("não é json válido {{{")
            err = json.loads(await asyncio.wait_for(ws_b2.recv(), timeout=5))
            assert err["type"] == "ERROR"
            assert err["payload"]["code"] == "INVALID_MESSAGE"

            await ws_a.close()
            await ws_b2.close()
            await ws_c.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_end_to_end_modern_features_over_websocket():
    tmpdir = tempfile.mkdtemp(prefix="msn_modern_e2e_")
    sockets = []
    try:
        core = ServerCore(make_settings(tmpdir, port=0, attachment_http_port=0))
        from server.attachments.http import AttachmentHTTPServer
        from server.network.handler import WebSocketHandler
        from websockets.asyncio.server import serve as ws_serve

        attachment_http = AttachmentHTTPServer(core)
        await attachment_http.start("127.0.0.1", 0)
        attachment_port = attachment_http.sockets[0].getsockname()[1]
        handler = WebSocketHandler(core)
        async with ws_serve(handler.handle, "127.0.0.1", 0) as srv:
            host, port = srv.sockets[0].getsockname()
            core.settings.public_base_url = f"http://{host}:{attachment_port}"
            url = f"ws://{host}:{port}"
            ws_a, _, uid_a = await ws_register_login(url, "modern_alice")
            ws_b, _, uid_b = await ws_register_login(url, "modern_bruno")
            sockets.extend([ws_a, ws_b])

            created = await ws_send(ws_a, command="CREATE_GROUP", name="Anexos", participants=["modern_bruno"])
            group_id = created["payload"]["conversation"]["conversation_id"]
            assert (await ws_wait_type(ws_b, "CONVERSATION_CREATED"))["payload"]["conversation"]["conversation_id"] == group_id

            ready = await ws_send(ws_a, command="BEGIN_ATTACHMENT_UPLOAD", conversation_id=group_id, filename="e2e.txt", mime="text/plain", size=8)
            assert ready["type"] == "ATTACHMENT_UPLOAD_READY"
            await ws_a.send(b"e2e data")
            await ws_a.send(json.dumps({"command": "FINISH_ATTACHMENT_UPLOAD", "upload_id": ready["payload"]["upload_id"]}))
            complete = await ws_wait_type(ws_a, "ATTACHMENT_UPLOAD_COMPLETE")
            assert complete["payload"]["attachment"]["download_url"]
            delivered = await ws_wait_type(ws_b, "MESSAGE")
            received_attachment = delivered["payload"]["message"]["payload"]["attachment"]
            assert received_attachment["original_name"] == "e2e.txt"
            assert parse_qs(urlsplit(received_attachment["download_url"]).query)["user"] == [uid_b]
            downloaded = await asyncio.to_thread(lambda: urllib.request.urlopen(received_attachment["download_url"], timeout=5).read())
            assert downloaded == b"e2e data"
            outsider_url = core._attachment_public_url(received_attachment["attachment_id"], "not-a-participant")
            with pytest.raises(urllib.error.HTTPError) as denied:
                await asyncio.to_thread(lambda: urllib.request.urlopen(outsider_url, timeout=5).read())
            assert denied.value.code == 404

            search = await ws_send(ws_b, command="SEARCH_MESSAGES", conversation_id=group_id, query="e2e.txt")
            assert search["type"] == "MESSAGE_SEARCH_RESULT"
            message_id = search["payload"]["messages"][0]["message_id"]
            pinned = await ws_send(ws_b, command="PIN_MESSAGE", conversation_id=group_id, message_id=message_id)
            assert pinned["type"] == "MESSAGE_PINNED"
            assert pinned["payload"]["is_pinned"] is True
            event_a = await ws_wait_type(ws_a, "MESSAGE_PINNED")
            assert event_a["payload"]["message"]["message_id"] == message_id
            listed = await ws_send(ws_a, command="LIST_PINNED_MESSAGES", conversation_id=group_id)
            assert len(listed["payload"]["messages"]) == 1

            await ws_a.close()
            await ws_b.close()
    finally:
        for ws in sockets:
            try:
                await ws.close()
            except Exception:
                pass
        if "attachment_http" in locals():
            await attachment_http.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
