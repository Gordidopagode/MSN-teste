"""WebSocket network layer.

Technology choice justification:
- WebSockets (websockets library) provide full-duplex, low-latency, text-frame
  communication with native JSON payloads — ideal for a real-time chat server.
- The websockets library has built-in ping/pong keepalive and automatic close-
  frame handling, which simplifies disconnect detection.
- It runs on asyncio, keeping the whole server single-threaded and
  straightforward to reason about.
- Alternative considered: raw TCP with a custom framing protocol — more
  control but unnecessarily complex for ~5 users; SSE — server-to-client
  only, unusable for chat; HTTP polling — wrong fit for real time.

The handler is a thin translator: it parses inbound JSON, dispatches commands
to ServerCore, and flushes pending envelopes back to the socket. All business
logic lives in ServerCore, which makes the network layer trivially testable
with a fake core (see tests).

Fan-out: when ServerCore pushes envelopes to connections OTHER than the one
that issued a command (presence broadcasts, SESSION_TAKEN evictions), those
queues are drained asynchronously through the ``broadcast_flush`` hook that
every handler registers on construction.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import websockets
from websockets.server import ServerConnection

from server.core import ServerCore
from server.network.protocol import (
    format_server_message, parse_client_message, ProtocolError,
)

log = logging.getLogger("msn.network")


class WebSocketHandler:
    """Binds a websocket connection lifecycle to ServerCore.

    One handler instance is created per accepted connection. Envelopes for the
    *sender* are flushed synchronously at the end of each dispatch cycle.
    Unsolicited envelopes addressed to other connections are written through a
    class-level registry of live sockets drained by the core's broadcast hook.
    """

    # shared connection registry, keyed by connection_id
    _registry: dict[str, ServerConnection] = {}
    _core: Optional[ServerCore] = None

    def __init__(self, core: ServerCore, connection_id: Optional[str] = None) -> None:
        self.core = core
        self._connection_id = connection_id
        # One-time wiring: the core learns how to flush its own pending queues
        # through the live sockets. Idempotent.
        WebSocketHandler._core = core
        if core._broadcast_flush is None:
            core.set_broadcast_flush(self.broadcast_flush)

    @classmethod
    async def broadcast_flush(cls, connection_ids: list[str]) -> None:
        """Drain pending queues for the given live connections."""
        core = cls._core
        if core is None:
            return
        for cid in connection_ids:
            ws = cls._registry.get(cid)
            if ws is None:
                continue
            for envelope in core.pending.pop(cid, []):
                try:
                    await ws.send(format_server_message(envelope))
                except websockets.ConnectionClosed:
                    break

            # A takeover is initiated by another socket while this handler is
            # idle in async-for. Close it here, after the queued notice has had
            # a chance to reach the client.
            if cid in core._closed_connections:
                try:
                    await ws.close(code=4001, reason="SESSION_TAKEN")
                except Exception:
                    pass

    async def handle(self, websocket: ServerConnection) -> None:
        """Entry point for websockets.serve."""
        from server.shared_types import new_id
        connection_id = self._connection_id or new_id()
        self.core.client_connected(connection_id)
        self._registry[connection_id] = websocket
        log.info("Connection accepted: %s", connection_id)

        try:
            async for raw in websocket:
                try:
                    command = parse_client_message(raw)
                except ProtocolError as exc:
                    envelope = ServerCore.error_envelope(
                        "INVALID_MESSAGE", str(exc))
                    await websocket.send(format_server_message(envelope))
                    continue

                try:
                    await self._dispatch(connection_id, command)
                    await self.core.touch_activity(connection_id)
                except Exception:  # isolate per-client failures
                    log.exception(
                        "Internal error processing %s from %s",
                        command.get("command"), connection_id,
                    )
                    await websocket.send(format_server_message(
                        ServerCore.error_envelope(
                            "INTERNAL_ERROR",
                            "O servidor encontrou um erro interno.",
                        )))
                finally:
                    await self._flush(connection_id, websocket)

                if command.get("command") == "LOGOUT":
                    try:
                        await websocket.close(code=1000, reason="LOGOUT")
                    except Exception:
                        pass
                    break

        except websockets.ConnectionClosed:
            pass
        finally:
            self._registry.pop(connection_id, None)
            self.core.client_disconnected(connection_id)
            await self._flush_and_close(connection_id, websocket)
            log.info("Connection exit: %s", connection_id)

    async def _dispatch(self, connection_id: str, command: dict[str, Any]) -> None:
        cmd = command["command"]
        if cmd == "REGISTER":
            await self.core.register(
                connection_id,
                command["username"],
                command["display_name"],
                command["password"],
                command["email"],
            )
        elif cmd == "REQUEST_PASSWORD_RESET":
            await self.core.request_password_reset(
                connection_id, command["email"])
        elif cmd == "RESET_PASSWORD":
            await self.core.reset_password(
                connection_id,
                command["email"],
                command["code"],
                command["new_password"],
            )
        elif cmd == "LOGIN":
            await self.core.authenticate(
                connection_id,
                command["username"],
                command["password"],
            )
        elif cmd == "RECONNECT":
            await self.core.reconnect(connection_id, command["session_id"])
        elif cmd == "REQUEST_SYNC":
            await self.core.request_sync(connection_id)
        elif cmd == "CHANGE_STATUS":
            await self.core.change_status(
                connection_id,
                command["status"],
                command.get("status_message"),
            )
        elif cmd == "SEND_MESSAGE":
            await self.core.send_message(
                connection_id,
                command["conversation_id"],
                command["type"],
                command["payload"],
                command.get("message_id"),
            )
        elif cmd == "GET_HISTORY":
            await self.core.get_history(
                connection_id,
                command["conversation_id"],
                limit=command.get("limit", 100),
                before=command.get("before"),
            )
        elif cmd == "CREATE_GROUP":
            await self.core.create_group(
                connection_id, command["name"], command["participants"])
        elif cmd == "SEARCH_USERS":
            await self.core.search_users(connection_id, command["query"])
        elif cmd == "SEND_FRIEND_REQUEST":
            await self.core.send_friend_request(connection_id, command["username"])
        elif cmd == "RESPOND_FRIEND_REQUEST":
            await self.core.respond_friend_request(
                connection_id, command["friendship_id"], command["action"])
        elif cmd == "REMOVE_FRIEND":
            await self.core.remove_friend(connection_id, command["friendship_id"])
        elif cmd == "OPEN_CONVERSATION":
            await self.core.open_conversation(connection_id, command["username"])
        elif cmd == "SET_AVATAR":
            await self.core.set_avatar(
                connection_id, command["data"], command["filename"], command["mime"])
        elif cmd == "SET_CUSTOM_STATUS":
            await self.core.set_custom_status(connection_id, command["message"])
        elif cmd == "LOGOUT":
            await self.core.logout(connection_id)
        else:  # pragma: no cover - guarded by parser
            raise ProtocolError(f"Comando não implementado: {cmd!r}")

    async def _flush(self, connection_id: str, websocket: ServerConnection) -> None:
        """Flush envelopes queued for THIS connection (sender path)."""
        for envelope in self.core.pending.pop(connection_id, []):
            try:
                txt = format_server_message(envelope)
                await websocket.send(txt)
            except websockets.ConnectionClosed:
                break

    async def _flush_and_close(self, connection_id: str,
                               websocket: ServerConnection) -> None:
        """Flush final envelopes (LOGOUT_OK, SESSION_TAKEN) before closing."""
        for envelope in self.core.pending.pop(connection_id, []):
            try:
                await websocket.send(format_server_message(envelope))
            except websockets.ConnectionClosed:
                break
        try:
            await asyncio.wait_for(websocket.close(), timeout=2.0)
        except Exception:
            pass
