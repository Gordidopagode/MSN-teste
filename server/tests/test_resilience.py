"""Resilience tests required by the project specification.

These tests complement the original suite and stabilization tests. They use
real WebSocket connections so the exercise covers the complete network path.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import tempfile

import pytest
import websockets
from websockets.asyncio.client import connect as ws_connect

from server.config.settings import ServerSettings
from server.core import ServerCore
from server.tests.test_sync_persistence import ws_register_login, ws_send


@pytest.mark.asyncio
async def test_five_clients_burst_history_and_reconnect():
    """Five simultaneous clients exchange 100 messages, then two clients
    disconnect and recover their state through RECONNECT + REQUEST_SYNC."""
    tmpdir = tempfile.mkdtemp(prefix="msn_stress_")
    sockets = []
    try:
        settings = ServerSettings(
            host="127.0.0.1", port=0, data_dir=tmpdir,
            max_message_length=20000, max_username_length=64,
            max_display_name_length=64, max_messages_per_minute=200,
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
            clients = []
            sessions = []
            usernames = [f"load_user_{i}" for i in range(5)]
            for username in usernames:
                ws, sid, _ = await ws_register_login(url, username)
                clients.append(ws)
                sockets.append(ws)
                sessions.append(sid)

            group = await ws_send(
                clients[0], command="CREATE_GROUP", name="Load Team",
                participants=usernames[1:],
            )
            assert group["type"] == "CONVERSATION_CREATED"
            conversation_id = group["payload"]["conversation"]["conversation_id"]
            for ws in clients[1:]:
                invitation = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert invitation["type"] == "CONVERSATION_CREATED"

            for index in range(100):
                ack = await ws_send(
                    clients[0], command="SEND_MESSAGE",
                    conversation_id=conversation_id, type="text",
                    payload={"content": f"burst-{index}"},
                    message_id=f"burst-message-{index}",
                )
                assert ack["type"] == "MESSAGE_ACK"
                assert ack["payload"]["duplicate"] is False

            async def receive_burst(ws):
                messages = []
                for _ in range(100):
                    item = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    assert item["type"] == "MESSAGE"
                    messages.append(item["payload"]["message"]["payload"]["content"])
                return messages

            received = await asyncio.gather(*(receive_burst(ws) for ws in clients[1:]))
            expected = {f"burst-{index}" for index in range(100)}
            assert all(set(messages) == expected for messages in received)

            # Simulate two random client drops after the burst. Their sessions
            # remain valid and must restore the same conversation history.
            await clients[1].close()
            await clients[2].close()
            await asyncio.sleep(0.1)

            recovered = []
            for index in (1, 2):
                ws = await ws_connect(url)
                sockets.append(ws)
                recovered.append(ws)
                response = await ws_send(ws, command="RECONNECT", session_id=sessions[index])
                assert response["type"] == "RECONNECT_OK"
                sync = await ws_send(ws, command="REQUEST_SYNC")
                assert sync["type"] == "SYNC_DATA"
                recent_history = sync["payload"]["data"]["history"][conversation_id]
                assert len(recent_history) == 50
                assert {item["payload"]["content"] for item in recent_history} == {
                    f"burst-{index}" for index in range(50, 100)
                }
                complete = await ws_send(
                    ws, command="GET_HISTORY", conversation_id=conversation_id,
                    limit=100,
                )
                assert complete["type"] == "HISTORY"
                history = complete["payload"]["messages"]
                assert len(history) == 100
                assert {item["payload"]["content"] for item in history} == expected

            assert len(core.online_users()) == 5
    finally:
        for ws in sockets:
            try:
                await ws.close()
            except Exception:
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_database_failure_isolated_from_server_process():
    """A transient SQLite failure produces INTERNAL_ERROR for one client and
    the server continues serving subsequent requests."""
    tmpdir = tempfile.mkdtemp(prefix="msn_failure_db_")
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
                original = core.store.get_user_by_username

                def failing_lookup(username):
                    raise sqlite3.OperationalError("database temporarily unavailable")

                core.store.get_user_by_username = failing_lookup
                await ws.send(json.dumps({
                    "command": "REGISTER", "username": "db_fail",
                    "display_name": "Database Failure", "email": "db_fail@example.com", "password": "secret123",
                }))
                response = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert response["type"] == "ERROR"
                assert response["payload"]["code"] == "INTERNAL_ERROR"

                core.store.get_user_by_username = original
                recovered = await ws_send(
                    ws, command="REGISTER", username="db_recovered",
                    display_name="Recovered", email="db_recovered@example.com", password="secret123",
                )
                assert recovered["type"] == "REGISTER_OK"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
