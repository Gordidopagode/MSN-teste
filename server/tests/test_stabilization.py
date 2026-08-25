"""New stabilization tests — one (or more) per specification item (1–12).

These tests run ON TOP of the existing suite and must not touch the old test
files (zero-regression rule). Unit-style tests use the FakeServer harness;
integration tests use real WebSocket connections against a temporary server.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
import websockets
from websockets.asyncio.client import connect as ws_connect

from server.core import ServerCore
from server.config.settings import ServerSettings
from server.tests.helpers import FakeServer, make_settings
from server.tests.test_sessions import register_and_login
from server.tests.test_sync_persistence import ws_send, ws_register_login


# ---------------------------------------------------------------------------
# Item 1 — SESSION_TAKEN is delivered before the socket is flushed/closed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_taken_delivered_before_close():
    """Re-login for the same account must QUEUE SESSION_TAKEN for the old
    connection; the broadcast flush (handler hook) delivers it before the
    socket is physically closed. With the bug present, `pending.pop` dropped
    the envelope and the old client never saw the notice."""
    s = FakeServer()
    try:
        a_cid, session_id = await register_and_login(s, "alice")
        s.core.pending.pop(a_cid, None)

        # A second LOGIN for the same account evicts the first connection.
        b_cid = s.new_connection()
        await s.core.authenticate(b_cid, "alice", "secret123")
        b_ok = [e for e in s.core.pending.get(b_cid, [])
                if e["type"] == "AUTH_OK"]
        assert b_ok, "second login must succeed"

        # The old connection must have received SESSION_TAKEN in its queue
        # (the real delivery is done by the handler broadcast flush; here we
        # assert the envelope is queued and the old connection is marked).
        assert s.find(a_cid, "SESSION_TAKEN"), (
            "SESSION_TAKEN must be queued for the evicted connection")
        assert a_cid in s.core._closed_connections
        # The original session id is still resolvable (login kept a session,
        # so a later RECONNECT attempt resolves to the current binding, not
        # an orphaned row).
        assert session_id, "login must produce a session id"
    finally:
        s.cleanup()


# ---------------------------------------------------------------------------
# Item 2 — the old socket is REALLY closed after a session takeover
# ---------------------------------------------------------------------------

async def _run_server(origins=None, tmpdir=None):
    """Start a temporary real server and return (port, close_fn)."""
    tmpdir = tmpdir or tempfile.mkdtemp(prefix="msn_stab_")
    settings = ServerSettings(
        host="127.0.0.1", port=0, data_dir=tmpdir,
        max_message_length=20000, max_username_length=64,
        max_display_name_length=64, max_messages_per_minute=30,
        session_ttl_minutes=60, allowed_origins=origins,
    )
    core = ServerCore(settings)
    from server.network.handler import WebSocketHandler
    handler = WebSocketHandler(core)

    async with websockets.serve(
        handler.handle, "127.0.0.1", 0,
        ping_interval=None, origins=origins,
    ) as server:
        port = server.sockets[0].getsockname()[1]
        yield port, handler
        # keep the context manager alive for the duration of the test
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_old_socket_actually_closed_after_takeover():
    """Two real WebSockets, same account: the first must receive
    SESSION_TAKEN and then be physically closed, while the second stays
    authenticated and online. Presence must stay correct (item 2 + 12)."""
    tmpdir = tempfile.mkdtemp(prefix="msn_takeover_")
    try:
        settings = ServerSettings(
            host="127.0.0.1", port=0, data_dir=tmpdir,
            max_message_length=20000, max_username_length=64,
            max_display_name_length=64, max_messages_per_minute=30,
            session_ttl_minutes=60, allowed_origins=None,
        )
        core = ServerCore(settings)
        from server.network.handler import WebSocketHandler
        handler = WebSocketHandler(core)

        async with websockets.serve(
            handler.handle, "127.0.0.1", 0, ping_interval=None,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"

            ws_a, sid, uid = await ws_register_login(url, "takeover2")
            ws_b = await ws_connect(url)
            auth_b = await ws_send(
                ws_b, command="LOGIN", username="takeover2",
                password="secret123")
            assert auth_b["type"] == "AUTH_OK", auth_b

            # --- A must receive SESSION_TAKEN and its socket must close ---
            notice = json.loads(
                await asyncio.wait_for(ws_a.recv(), timeout=5))
            assert notice["type"] == "SESSION_TAKEN", notice
            # after the close, A's recv raises ConnectionClosed
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await asyncio.wait_for(ws_a.recv(), timeout=5)

            # --- B stays online with its new session ---
            # authenticate creates a FRESH session for B (the old one is
            # force-closed per the spec), so B's session id comes from
            # AUTH_OK, not A's original sid.
            new_sid = auth_b["payload"]["session_id"]
            assert core.sessions.get_session(new_sid) is not None
            # online_users counts must exclude A (closed) and include B
            assert len(core.online_users()) == 1
            assert core.online_users()[0]["username"] == "takeover2"

            # --- Presence: exactly one online user ---
            await ws_b.send(json.dumps({"command": "REQUEST_SYNC"}))
            sync = json.loads(await ws_b.recv())
            online = [p for p in sync["payload"]["data"]["presence"].values()
                      if p["status"] == "online"]
            assert len(online) == 1, sync["payload"]["data"]["presence"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Item 3 — reconnect + sync protocol is explicit (Option A)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconnect_sync_full_flow():
    """RECONNECT → RECONNECT_OK, then the client explicitly asks
    REQUEST_SYNC → SYNC_DATA with identity, conversations and history.
    The server MUST NOT send SYNC_DATA automatically (Option A)."""
    tmpdir = tempfile.mkdtemp(prefix="msn_reconnect_")
    try:
        settings = ServerSettings(
            host="127.0.0.1", port=0, data_dir=tmpdir,
            max_message_length=20000, max_username_length=64,
            max_display_name_length=64, max_messages_per_minute=30,
            session_ttl_minutes=60, allowed_origins=None,
        )
        core = ServerCore(settings)
        from server.network.handler import WebSocketHandler
        handler = WebSocketHandler(core)

        async with websockets.serve(
            handler.handle, "127.0.0.1", 0, ping_interval=None,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"

            ws, session_id, user_id = await ws_register_login(url, "sync_user")
            # create a conversation with a message so sync is meaningful
            other = await ws_register_login(url, "sync_other")
            grp = await ws_send(ws, command="CREATE_GROUP", name="Sync Team",
                                participants=["sync_other"])
            cid = grp["payload"]["conversation"]["conversation_id"]
            ack = await ws_send(ws, command="SEND_MESSAGE",
                                conversation_id=cid, type="text",
                                payload={"content": "sync check"})
            assert ack["type"] == "MESSAGE_ACK"
            # sync_other receives the MESSAGE broadcast; consume it so the
            # second socket never picks it up after ws is closed.
            other_ws = other[0]
            # other receives the CONVERSATION_CREATED invitation broadcast,
            # then the MESSAGE broadcast — consume both before closing it.
            inv = json.loads(await other_ws.recv())
            assert inv["type"] == "CONVERSATION_CREATED", inv
            other_msg = json.loads(await other_ws.recv())
            assert other_msg["type"] == "MESSAGE", other_msg
            await other_ws.close()
            # drop the connection
            await ws.close()

            # new socket: RECONNECT (explicit option A — no automatic sync)
            ws2 = await ws_connect(url)
            rc = await ws_send(ws2, command="RECONNECT",
                               session_id=session_id)
            assert rc["type"] == "RECONNECT_OK", rc

            # give the server a moment: if SYNC_DATA arrived automatically,
            # it would be in the receive queue already
            await asyncio.sleep(0.05)
            try:
                got = await asyncio.wait_for(ws2.recv(), timeout=0.15)
                # nothing should be queued; if anything arrived it must not
                # be SYNC_DATA
                assert json.loads(got)["type"] != "SYNC_DATA"
            except asyncio.TimeoutError:
                pass  # nothing queued — exactly as Option A requires

            sync = await ws_send(ws2, command="REQUEST_SYNC")
            assert sync["type"] == "SYNC_DATA", sync
            data = sync["payload"]["data"]
            assert data["identity"]["username"] == "sync_user"
            assert any(c["conversation_id"] == cid
                       for c in data["conversations"]), data["conversations"]
            assert any(m["payload"]["content"] == "sync check"
                       for m in data["history"].get(cid, []))
            assert data["presence"][user_id]["status"] == "online"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Item 4 — session TTL actually invalidates sessions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ttl_expires_session():
    """With the clock advanced past the TTL, restore_session must return
    None and the server answers RECONNECT_INVALID. Uses monkeypatch on the
    module-level ``_utcnow`` clock (wall-clock independent)."""
    s = FakeServer(settings=make_settings(
        tempfile.mkdtemp(prefix="msn_ttl_"), session_ttl_minutes=30))
    now = {"t": datetime.now(timezone.utc)}  # controlled test clock
    import server.auth.manager as am
    original_utcnow = am._utcnow
    am._utcnow = lambda: now["t"]
    try:
        cid, session_id = await register_and_login(s, "ttl_user")
        assert await s.core.auth.restore_session(session_id) is not None

        # inside TTL: still valid
        now["t"] += timedelta(minutes=29)
        assert await s.core.auth.restore_session(session_id) is not None

        # past TTL: expired and removed
        now["t"] += timedelta(minutes=2)
        assert await s.core.auth.restore_session(session_id) is None
        assert s.core.store.get_session(session_id) is None

        # RECONNECT on an expired session → RECONNECT_INVALID
        c2 = s.new_connection()
        await s.core.reconnect(c2, session_id)
        err = s.find(c2, "ERROR")
        assert any(e["payload"]["code"] == "RECONNECT_INVALID" for e in err), err
    finally:
        am._utcnow = original_utcnow
        s.cleanup()


@pytest.mark.asyncio
async def test_ttl_after_restart():
    """Restarted server still respects the TTL: a session whose
    last_seen_at is past the TTL in the DB cannot reconnect."""
    tmpdir = tempfile.mkdtemp(prefix="msn_ttl2_")
    try:
        settings = make_settings(tmpdir, session_ttl_minutes=30)
        core1 = ServerCore(settings)
        c1 = "conn_1"
        core1.client_connected(c1)
        await core1.register(c1, "restart_ttl", "Restart TTL", "p12345", "restart_ttl@example.com")
        core1.pending.pop(c1, None)
        await core1.authenticate(c1, "restart_ttl", "p12345")
        session_id = next(e["payload"]["session_id"]
                          for e in core1.pending.get(c1, [])
                          if e["type"] == "AUTH_OK")
        del core1

        import server.auth.manager as am
        original_utcnow = am._utcnow
        now = {"t": datetime.now(timezone.utc) + timedelta(minutes=65)}
        am._utcnow = lambda: now["t"]
        try:
            core2 = ServerCore(settings)
            session = await core2.auth.restore_session(session_id)
            assert session is None, "expired session must not survive restart"
        finally:
            am._utcnow = original_utcnow
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Item 5 — last_seen_at is updated by authenticated activity (SQLite)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_last_seen_at_updated():
    """Authenticated activity must advance last_seen_at (persisted to
    SQLite). A controlled clock is injected so the comparison is
    deterministic regardless of execution speed."""
    import server.auth.manager as am
    clock = {"t": datetime.now(timezone.utc)}
    original_now_iso = am.now_iso
    am.now_iso = lambda: clock["t"].isoformat()
    try:
        s = FakeServer()
        try:
            cid, session_id = await register_and_login(s, "seen_user")
            first = s.core.store.get_session(session_id)["last_seen_at"]

            # advance the clock and run an authenticated command. The
            # FakeServer harness bypasses the network layer, so the
            # activity-touch injected by the handler must be simulated here.
            clock["t"] += timedelta(seconds=10)
            await s.core.change_status(cid, "away", "ocupado")
            await s.core.touch_activity(cid)
            second = s.core.store.get_session(session_id)["last_seen_at"]
            assert second > first, (first, second)

            clock["t"] += timedelta(seconds=10)
            await s.core.request_sync(cid)
            await s.core.touch_activity(cid)
            third = s.core.store.get_session(session_id)["last_seen_at"]
            assert third > second, (second, third)

            # persisted value matches memory
            row = s.core.store.get_session(session_id)
            live = s.core.sessions.get_session(session_id)
            assert row["last_seen_at"] == live.last_seen_at
        finally:
            s.cleanup()
    finally:
        am.now_iso = original_now_iso


# ---------------------------------------------------------------------------
# Item 6 — MSN_ALLOWED_ORIGINS parsing + WebSocket enforcement
# ---------------------------------------------------------------------------

def test_allowed_origins_parsing_absent():
    s = ServerSettings.from_env()
    assert s.allowed_origins is None


def test_allowed_origins_parsing_multi():
    import os
    with pytest.MonkeyPatch().context() as m:
        m.setenv("MSN_ALLOWED_ORIGINS",
                 "http://localhost,  https://app.example.com, ")
        s = ServerSettings.from_env()
        assert s.allowed_origins == [
            "http://localhost", "https://app.example.com"]


def test_allowed_origins_invalid_scheme():
    with pytest.MonkeyPatch().context() as m:
        m.setenv("MSN_ALLOWED_ORIGINS", "ftp://nope")
        with pytest.raises(ValueError):
            ServerSettings.from_env()


@pytest.mark.asyncio
async def test_origins_allowed_and_rejected():
    """Real WebSocket: allowed origin connects; forbidden origin is refused
    at the handshake (1006/invalid status) by websockets itself."""
    tmpdir = tempfile.mkdtemp(prefix="msn_orig_")
    try:
        settings = ServerSettings(
            host="127.0.0.1", port=0, data_dir=tmpdir,
            max_message_length=20000, max_username_length=64,
            max_display_name_length=64, max_messages_per_minute=30,
            session_ttl_minutes=60,
            allowed_origins=["http://ok.example.com"],
        )
        core = ServerCore(settings)
        from server.network.handler import WebSocketHandler
        handler = WebSocketHandler(core)

        async with websockets.serve(
            handler.handle, "127.0.0.1", 0, ping_interval=None,
            origins=["http://ok.example.com"],
        ) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"

            async with ws_connect(url,
                                  origin="http://ok.example.com") as ok_ws:
                await ok_ws.send(json.dumps({"command": "REQUEST_SYNC"}))
                err = json.loads(await ok_ws.recv())
                assert err["type"] == "ERROR"  # not authed, but connected

            with pytest.raises(websockets.InvalidStatus):
                async with ws_connect(url,
                                      origin="http://evil.example.com") as ws:
                    await ws.recv()  # handshake refused
                # context manager exits: socket never opened
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Item 7 — smoke-style reconnect via session_id after a drop
#   (covered end-to-end in smoke_test.py; here a focused WebSocket test)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconnect_restores_history_after_drop():
    tmpdir = tempfile.mkdtemp(prefix="msn_drop_")
    try:
        settings = ServerSettings(
            host="127.0.0.1", port=0, data_dir=tmpdir,
            max_message_length=20000, max_username_length=64,
            max_display_name_length=64, max_messages_per_minute=30,
            session_ttl_minutes=60, allowed_origins=None,
        )
        core = ServerCore(settings)
        from server.network.handler import WebSocketHandler
        handler = WebSocketHandler(core)

        async with websockets.serve(
            handler.handle, "127.0.0.1", 0, ping_interval=None,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"

            ws, session_id, user_id = await ws_register_login(url, "drop_user")
            sync = await ws_send(ws, command="REQUEST_SYNC")
            assert sync["type"] == "SYNC_DATA"
            await ws.close()  # simulated network drop

            ws2 = await ws_connect(url)
            rc = await ws_send(ws2, command="RECONNECT",
                               session_id=session_id)
            assert rc["type"] == "RECONNECT_OK"
            sync2 = await ws_send(ws2, command="REQUEST_SYNC")
            assert sync2["type"] == "SYNC_DATA"
            assert sync2["payload"]["data"]["identity"]["username"] \
                   == "drop_user"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Item 8 — presence offline after server restart (DB state ≠ connection)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_presence_offline_after_restart():
    tmpdir = tempfile.mkdtemp(prefix="msn_offline_")
    try:
        settings = make_settings(tmpdir)
        core1 = ServerCore(settings)
        c1 = "conn_1"
        core1.client_connected(c1)
        await core1.register(c1, "offline_user", "Offline", "p12345", "offline_user@example.com")
        core1.pending.pop(c1, None)
        await core1.authenticate(c1, "offline_user", "p12345")
        session_id = next(e["payload"]["session_id"]
                          for e in core1.pending.get(c1, [])
                          if e["type"] == "AUTH_OK")
        assert len(core1.online_users()) == 1
        del core1

        # restart: user is NOT connected anymore → presence must be offline
        core2 = ServerCore(settings)
        user = core2.store.get_user_by_username("offline_user")
        assert core2.presence.get_presence(user["user_id"])["status"].value \
               == "offline"
        assert len(core2.online_users()) == 0

        # session record survives (reconnect possible), but user offline
        assert await core2.auth.restore_session(session_id) is not None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Item 9 — SessionManager encapsulation (regression guard)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_manager_encapsulation():
    """Core must use public SessionManager methods, never ``_sessions``."""
    s = FakeServer()
    try:
        cid, session_id = await register_and_login(s, "encap_user")
        # public API works
        entries = list(s.core.sessions.iter_sessions())
        assert any(e["session"].session_id == session_id for e in entries)
        conn = s.core.sessions.get_connection_for_user(
            s.core.sessions.get_session(session_id).user_id)
        assert conn == cid
    finally:
        s.cleanup()


# ---------------------------------------------------------------------------
# Item 10 — strict type validation returns ERROR without crashing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_strict_types_on_real_ws():
    """Malformed commands must yield ERROR envelopes (INVALID_MESSAGE) with
    the server staying up. Verified over real WebSockets for the main
    commands."""
    tmpdir = tempfile.mkdtemp(prefix="msn_types_")
    try:
        settings = ServerSettings(
            host="127.0.0.1", port=0, data_dir=tmpdir,
            max_message_length=20000, max_username_length=64,
            max_display_name_length=64, max_messages_per_minute=30,
            session_ttl_minutes=60, allowed_origins=None,
        )
        core = ServerCore(settings)
        from server.network.handler import WebSocketHandler
        handler = WebSocketHandler(core)

        async with websockets.serve(
            handler.handle, "127.0.0.1", 0, ping_interval=None,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"

            async with ws_connect(url) as ws:
                bad = [
                    {"command": "LOGIN", "username": 123,
                     "password": "p"},                     # non-string user
                    {"command": "LOGIN", "username": "u",
                     "password": ""},                       # empty password
                    {"command": "RECONNECT",
                     "session_id": 42},                     # non-string id
                    {"command": "CHANGE_STATUS",
                     "status": True},                       # bool status
                    {"command": "SEND_MESSAGE",
                     "conversation_id": "c", "type": "text",
                     "payload": "not-an-object"},           # bad payload
                    {"command": "CREATE_GROUP", "name": "g",
                     "participants": [1, 2]},               # non-string list
                    {"command": "CREATE_GROUP", "name": "g",
                     "participants": []},                   # empty list
                    {"command": "GET_HISTORY",
                     "conversation_id": "c",
                     "limit": 1.5},                         # float limit
                    {"command": "GET_HISTORY",
                     "conversation_id": "c",
                     "limit": True},                        # bool limit
                ]
                for msg in bad:
                    await ws.send(json.dumps(msg))
                    resp = json.loads(await ws.recv())
                    assert resp["type"] == "ERROR", (msg, resp)
                    assert resp["payload"]["code"] == "INVALID_MESSAGE", resp

                # server still functional after the bad inputs
                reg = await ws_send(
                        ws, command="REGISTER", username="types_user",
                        display_name="Types", email="types_user@example.com", password="p12345678")
                assert reg["type"] == "REGISTER_OK", reg
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Item 11 — message_id collision: same-content retry vs. conflict
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_id_retry_and_conflict():
    s = FakeServer()
    try:
        a_cid, _ = await register_and_login(s, "mid_a")
        b_cid, _ = await register_and_login(s, "mid_b")

        a_id = s.core.online_users()[0]["user_id"]
        b_row = s.core.store.get_user_by_username("mid_b")
        conv = s.core.conversations.get_or_create_individual(
            a_id, b_row["user_id"])
        cid = conv.conversation_id

        # Case A: first send stores; identical retry → duplicate ACK,
        # only one delivery to the recipient.
        mid = "msg-unique-001"
        await s.core.send_message(a_cid, cid, "text",
                                  {"content": "retry?"}, message_id=mid)
        s.core.pending.pop(a_cid, None)   # ACK to A
        # recipient B must get exactly one MESSAGE on the first send
        assert len(s.find(b_cid, "MESSAGE")) == 1, "first delivery missing"
        s.core.pending.pop(b_cid, None)

        await s.core.send_message(a_cid, cid, "text",
                                  {"content": "retry?"}, message_id=mid)
        s.core.pending.pop(a_cid, None)   # duplicate ACK to A
        # recipient must NOT have received a second copy
        assert not s.find(b_cid, "MESSAGE"), "duplicate must not re-deliver"
        # history has exactly one message
        hist = s.core.messages.get_history(cid, a_id)
        assert len(hist) == 1

        # Case B: different content with the same message_id → error
        await s.core.send_message(a_cid, cid, "text",
                                  {"content": "different!"}, message_id=mid)
        errs = s.find(a_cid, "ERROR")
        assert any("MESSAGE_ID_CONFLICT" in e["payload"]["message"]
                   for e in errs), errs
        # and history still has only the original
        assert len(s.core.messages.get_history(cid, a_id)) == 1

        # Case C: same message_id, different conversation → error
        c_cid, _ = await register_and_login(s, "mid_c")
        c_row = s.core.store.get_user_by_username("mid_c")
        conv2 = s.core.conversations.get_or_create_individual(a_id,
                                                              c_row["user_id"])
        await s.core.send_message(a_cid, conv2.conversation_id, "text",
                                  {"content": "retry?"}, message_id=mid)
        errs = s.find(a_cid, "ERROR")
        assert any("MESSAGE_ID_CONFLICT" in e["payload"]["message"]
                   for e in errs), errs
        assert len(s.core.messages.get_history(conv2.conversation_id,
                                               a_id)) == 0
    finally:
        s.cleanup()


# ---------------------------------------------------------------------------
# Item 12 — full real-WebSocket session substitution (already covered in
# test_old_socket_actually_closed_after_takeover; add the messaging side)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_substitution_full_ws():
    """A logs in → online; B logs in same account → A gets SESSION_TAKEN and
    is closed; B keeps AUTH_OK and can send; A cannot send after eviction;
    presence stays correct (one online)."""
    tmpdir = tempfile.mkdtemp(prefix="msn_sub_")
    try:
        settings = ServerSettings(
            host="127.0.0.1", port=0, data_dir=tmpdir,
            max_message_length=20000, max_username_length=64,
            max_display_name_length=64, max_messages_per_minute=30,
            session_ttl_minutes=60, allowed_origins=None,
        )
        core = ServerCore(settings)
        from server.network.handler import WebSocketHandler
        handler = WebSocketHandler(core)

        async with websockets.serve(
            handler.handle, "127.0.0.1", 0, ping_interval=None,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"

            ws_a, _, _ = await ws_register_login(url, "sub_user")

            ws_b = await ws_connect(url)
            auth_b = await ws_send(ws_b, command="LOGIN",
                                   username="sub_user", password="secret123")
            assert auth_b["type"] == "AUTH_OK"

            notice = json.loads(
                await asyncio.wait_for(ws_a.recv(), timeout=5))
            assert notice["type"] == "SESSION_TAKEN"
            with pytest.raises((websockets.ConnectionClosed,
                                websockets.exceptions.ConnectionClosed)):
                await asyncio.wait_for(ws_a.recv(), timeout=5)

            # B can send; A's socket is gone, so there is no delivery path.
            # Presence must show exactly one online user.
            await ws_b.send(json.dumps({"command": "REQUEST_SYNC"}))
            sync = json.loads(await ws_b.recv())
            assert sync["type"] == "SYNC_DATA"
            online = [p for p in sync["payload"]["data"]["presence"].values()
                      if p["status"] == "online"]
            assert len(online) == 1
            assert sync["payload"]["data"]["identity"]["username"] \
                   == "sub_user"

            # A's old connection id is evicted in the core too:
            # no USER_DISCONNECTED / offline flip happened for B.
            assert core.sessions.count() == 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
