"""Server core: wires all managers together and implements the protocol
state machine that the network layer invokes.

The core never touches sockets directly. It receives abstract client actions
(e.g. "user X authenticated with session S on connection C") and returns
outgoing envelope dicts. The network layer is responsible for actually
sending those envelopes.

This separation keeps the protocol logic unit-testable without any network.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from server.attachments.manager import AttachmentError, AttachmentManager
from server.auth.manager import AuthError, AuthManager
from server.conversations.manager import ConversationError, ConversationManager
from server.config.settings import ServerSettings
from server.events.bus import (
    AUTH_FAILED, CONVERSATION_CREATED, Event, EventBus, MESSAGE_DELIVERED,
    MESSAGE_SENT, SYNC_COMPLETED, SYNC_STARTED, USER_AUTHENTICATED,
    USER_CONNECTED, USER_DISCONNECTED, USER_LOGGED_OUT, USER_ONLINE,
    USER_STATUS_CHANGED,
)
from server.friends.manager import FriendshipError, FriendshipManager
from server.messages.manager import MessageError, MessageManager
from server.persistence.store import Persistence
from server.sessions.manager import ConnectionHandle, SessionManager
from server.shared_types import PresenceStatus
from server.sync.manager import SyncManager
from server.users.presence import PresenceManager
from server.users.profile import ProfileError, ProfileManager

log = logging.getLogger("msn.core")

class ServerCore:
    """The brain of the server."""

    def __init__(self, settings: ServerSettings) -> None:  # noqa: C901
        self.settings = settings
        self.store = Persistence(
            settings.data_dir + "/msn_server.db"
            if not settings.data_dir.endswith(".db") else settings.data_dir
        )
        self.bus = EventBus()
        self.sessions = SessionManager()
        self.presence = PresenceManager(self.bus, self.store)
        self.presence.restore(self.store.load_presence())
        self.profiles = ProfileManager(self.store)
        self.friendships = FriendshipManager(self.store)
        self.auth = AuthManager(self.store, settings, self.bus)
        self.conversations = ConversationManager(self.store)
        self.messages = MessageManager(
            self.store, settings, self.conversations, self.bus
        )
        self.attachments = AttachmentManager(self.store, settings)
        self.sync = SyncManager(
            settings, self.sessions, self.presence,
            self.conversations, self.messages, self.store,
            self.friendships, self.attachments,
            self._attachment_public_url,
        )
        # spam protection: per-user message timestamps
        self._msg_log: dict[str, list[float]] = defaultdict(list)
        # pending envelopes queued for delivery (connection_id -> list)
        self.pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._closed_connections: set[str] = set()
        self._attachment_upload_counts: dict[str, int] = defaultdict(int)
        self._binary_upload_for_connection: dict[str, str] = {}
        # optional async broadcast flush hook wired by the network layer
        self._broadcast_flush = None

    # -- envelope helpers ----------------------------------------------------

    @staticmethod
    def _envelope(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"type": kind, "payload": payload}

    @staticmethod
    def error_envelope(code: str, message: str, extra: Optional[dict[str, Any]] = None
                       ) -> dict[str, Any]:
        body: dict[str, Any] = {"code": code, "message": message}
        if extra:
            body.update(extra)
        return ServerCore._envelope("ERROR", body)

    def _push(self, connection_id: str, envelope: dict[str, Any]) -> None:
        """Queue one envelope for a connection.

        The *sender* connection is flushed by the network handler after it
        finishes dispatching the command. Pushes targeting OTHER connections
        (broadcasts, presence notifications, SESSION_TAKEN evictions) need an
        asynchronous flush, which the network layer wires up through
        ``set_broadcast_flush`` so the core stays transport-agnostic."""
        if connection_id in self._closed_connections:
            return
        self.pending[connection_id].append(envelope)
        if self._broadcast_flush is not None:
            import asyncio
            asyncio.ensure_future(self._broadcast_flush([connection_id]))

    def set_broadcast_flush(self, fn) -> None:
        """Register an async callback(connection_ids) that the core invokes
        whenever envelopes are pushed to connections other than the one that
        triggered the command. Receivers must see them without waiting for a
        request of their own."""
        self._broadcast_flush = fn

    # -- connection lifecycle ------------------------------------------------

    def client_connected(self, connection_id: str) -> None:
        self._closed_connections.discard(connection_id)
        # A raw connection does nothing until it authenticates.
        # Emit event only after authentication (USER_CONNECTED in the spec
        # marks the authenticated moment; we follow it semantically).

    def client_disconnected(self, connection_id: str) -> None:
        """Synchronous cleanup: transport drops can happen anywhere, including
        inside flush loops. Presence state is updated synchronously here so
        tests and callers remain deterministic."""
        self.attachments.discard_connection_uploads(connection_id)
        self._attachment_upload_counts.pop(connection_id, None)
        self._binary_upload_for_connection.pop(connection_id, None)
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            return  # never authenticated or already cleaned up

        entry = self.sessions.get_session_entry(session_id)
        if entry is None:
            return
        user = entry["user"]
        user_id = user["user_id"]

        # If the session was already re-bound to a NEWER connection (e.g. the
        # client is reconnecting in parallel), the old socket going away is
        # expected and MUST NOT mark the user offline or emit a disconnect
        # event: presence follows the live session, not the discarded socket.
        bound = entry["connection"]
        if bound is not None and bound.connection_id != connection_id:
            log.info(
                "Stale socket closed for %s; session now on another "
                "connection — presence untouched.", user["username"],
            )
            return

        self.sessions.unbind_connection(connection_id)
        self._closed_connections.add(connection_id)
        self.pending.pop(connection_id, None)
        self._msg_log.pop(session_id, None)
        self.presence.set_offline(user_id)
        self._push_presence_update(user_id)
        self.bus._recorded.append(Event(USER_DISCONNECTED, f"connection:{connection_id}", {
            "user_id": user_id,
            "username": user["username"],
            "session_id": session_id,
        }))
        log.info(
            "User disconnected (session preserved for reconnection): %s",
            user["username"],
        )

    # -- authentication ------------------------------------------------------

    async def authenticate(self, connection_id: str, username: str,
                           password: str) -> None:
        """Handle LOGIN. On success the client becomes online; on failure an
        ERROR envelope is pushed."""
        try:
            session = await self.auth.authenticate(username, password)
        except AuthError as exc:
            await self.bus.emit(Event(AUTH_FAILED, f"connection:{connection_id}", {
                "username": username.strip().lower(),
            }))
            self._push(connection_id, self.error_envelope(
                "AUTH_INVALID", str(exc), {"username_required": True}
            ))
            log.warning("Login failed for %s: %s", username, exc)
            return

        user = self.store.get_user(session.user_id)
        assert user is not None
        recovery_code = session.recovery_code
        session.recovery_code = None

        # If the same user already has a live session (re-login from another
        # place), close the old one so presence stays consistent.
        for e in self.sessions.iter_sessions_for_user(session.user_id):
            if e["session"].session_id != session.session_id:
                self._force_close_session(e["session"].session_id)

        self.sessions.add_session(session, user)
        handle = ConnectionHandle(connection_id, None)
        self.sessions.bind_connection(session.session_id, handle)

        self.presence.set_online(user["user_id"], user.get("custom_status") or None)
        await self.bus.emit(Event(USER_CONNECTED, f"connection:{connection_id}", {
            "user_id": user["user_id"],
            "username": user["username"],
            "session_id": session.session_id,
        }))
        await self.bus.emit(Event(USER_AUTHENTICATED, f"user:{user['user_id']}", {
            "user_id": user["user_id"],
            "username": user["username"],
        }))
        await self.bus.emit(Event(USER_ONLINE, f"user:{user['user_id']}", {
            "user_id": user["user_id"],
            "username": user["username"],
        }))

        log.info("User authenticated: %s", user["username"])
        auth_payload = {
            "session_id": session.session_id,
            "user_id": user["user_id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "avatar_data": user.get("avatar_data"),
            "avatar_mime": user.get("avatar_mime"),
            "custom_status": user.get("custom_status") or "",
        }
        if recovery_code:
            auth_payload["recovery_code"] = recovery_code
        self._push(connection_id, self._envelope("AUTH_OK", auth_payload))
        # Publish the canonical online state after AUTH_OK so the logging-in
        # client and all peers consume the same user-id keyed snapshot.
        self._push_presence_update(user["user_id"], exclude_connection_id=connection_id)

    async def reconnect(self, connection_id: str, session_id: str) -> None:
        """Handle RECONNECT. Reuses a valid (possibly dormant) session."""
        session = await self.auth.restore_session(session_id)
        if session is None:
            self._push(connection_id, self.error_envelope(
                "RECONNECT_INVALID", "Sessão inválida ou expirada. Faça login novamente."
            ))
            return

        user = self.store.get_user(session.user_id)
        assert user is not None

        # On a cold restart the in-memory session directory is empty even
        # though the session record exists in the database (RECONNECT_OK must
        # still be possible after the server comes back up).
        if self.sessions.get_session_entry(session.session_id) is None:
            self.sessions.add_session(session, user)

        old = self.sessions.get_session_entry(session.session_id)
        if old and old["connection"]:
            prev_conn = old["connection"].connection_id
            # The eviction notice MUST be delivered to the old socket
            # (via the broadcast flush hook) and NOT discarded here:
            # `pending.pop` was silently dropping SESSION_TAKEN before the
            # asynchronous flush got a chance to send it.
            self._push(prev_conn, self._envelope("SESSION_TAKEN", {
                "reason": "Nova conexão assumiu esta sessão.",
            }))
            self._closed_connections.add(prev_conn)

        self.sessions.bind_connection(session.session_id, ConnectionHandle(connection_id, None))
        self.presence.set_online(user["user_id"], user.get("custom_status") or None)
        await self.bus.emit(Event(USER_CONNECTED, f"connection:{connection_id}", {
            "user_id": user["user_id"],
            "username": user["username"],
            "session_id": session.session_id,
            "reconnect": True,
        }))
        await self.bus.emit(Event(USER_ONLINE, f"user:{user['user_id']}", {
            "user_id": user["user_id"],
            "username": user["username"],
        }))
        log.info("User reconnected: %s", user["username"])
        self._push(connection_id, self._envelope("RECONNECT_OK", {
            "session_id": session.session_id,
        }))
        self._push_presence_update(user["user_id"], exclude_connection_id=connection_id)

    def _force_close_session(self, session_id: str) -> None:
        entry = self.sessions.get_session_entry(session_id)
        if entry is None or not entry["connection"]:
            self.sessions.remove_session(session_id)
            return
        conn = entry["connection"].connection_id
        # Push the eviction notice FIRST: the network layer flushes pending
        # envelopes for that connection before actually closing the socket,
        # so the client receives SESSION_TAKEN on the wire.
        self._push(conn, self._envelope("SESSION_TAKEN", {
            "reason": "Sessão assumida por outra conexão.",
        }))
        self._closed_connections.add(conn)
        self.sessions.unbind_connection(conn)
        self.sessions.remove_session(session_id)
        self.attachments.discard_connection_uploads(conn)
        self._attachment_upload_counts.pop(conn, None)
        self._binary_upload_for_connection.pop(conn, None)

    # -- registration --------------------------------------------------------

    async def register(self, connection_id: str, username: str,
                       display_name: str, password: str, email: Optional[str] = None) -> None:
        try:
            result = await self.auth.register(username, display_name, password, email)
        except AuthError as exc:
            self._push(connection_id, self.error_envelope(
                "REGISTER_FAILED", str(exc)))
            log.warning("Registration failed: %s", exc)
            return
        self._push(connection_id, self._envelope("REGISTER_OK", {
            "username": result.user.username,
            "recovery_code": result.recovery_code,
        }))
        log.info("New user registered: %s", result.user.username)

    async def reset_password(self, connection_id: str, username: str,
                             code: str, new_password: str) -> None:
        try:
            await self.auth.reset_password(username, code, new_password)
        except AuthError as exc:
            self._push(connection_id, self.error_envelope(
                "PASSWORD_RESET_FAILED", str(exc)))
            return
        self._push(connection_id, self._envelope("PASSWORD_RESET_OK", {
            "message": "Senha alterada. Você já pode entrar com a nova senha."
        }))

    # -- sync ----------------------------------------------------------------

    async def request_sync(self, connection_id: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope(
                "AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return

        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        user_id = entry["user"]["user_id"]

        await self.bus.emit(Event(SYNC_STARTED, f"connection:{connection_id}", {
            "user_id": user_id,
        }))
        payload = self.sync.build_sync(session_id, user_id)
        self._push(connection_id, self._envelope("SYNC_DATA", payload))
        await self.bus.emit(Event(SYNC_COMPLETED, f"connection:{connection_id}", {
            "user_id": user_id,
        }))

    def _public_presence(self, user_id: str) -> dict[str, Any]:
        user = self.store.get_user(user_id)
        current = self.presence.get_presence(user_id)
        if user is None:
            return {
                "status": current["status"].value,
                "status_message": current.get("status_message", ""),
                "custom_status": "",
            }
        return {
            "status": current["status"].value,
            "status_message": current.get("status_message", ""),
            "username": user["username"],
            "display_name": user["display_name"],
            "avatar_data": user.get("avatar_data"),
            "avatar_mime": user.get("avatar_mime"),
            "custom_status": user.get("custom_status") or "",
        }

    def _all_presence(self) -> dict[str, dict[str, Any]]:
        return {user["user_id"]: self._public_presence(user["user_id"])
                for user in self.store.list_users()}

    def _push_presence_update(self, user_id: str, exclude_connection_id: Optional[str] = None) -> None:
        envelope = self._envelope("USER_STATUS_CHANGED", {
            "user_id": user_id,
            **self._public_presence(user_id),
        })
        for entry in self.sessions.iter_sessions():
            connection = entry["connection"]
            if connection and connection.connection_id != exclude_connection_id:
                self._push(connection.connection_id, envelope)

    def _push_to_all(self, envelope: dict[str, Any], user_ids: Optional[set[str]] = None) -> None:
        for entry in self.sessions.iter_sessions():
            if entry["connection"] and (user_ids is None or entry["user"]["user_id"] in user_ids):
                self._push(entry["connection"].connection_id, envelope)

    def _push_friendship_snapshots(self, user_ids: set[str]) -> None:
        presence = self._all_presence()
        for user_id in user_ids:
            friends = self.friendships.list_for_user(user_id, presence)
            self._push_to_all(
                self._envelope("FRIENDSHIPS_UPDATED", {"friends": friends}),
                {user_id},
            )

    def _friendship_users(self, friendship: dict[str, Any]) -> set[str]:
        return {friendship["user_a_id"], friendship["user_b_id"]}

    # -- presence ------------------------------------------------------------

    async def change_status(self, connection_id: str, status: str,
                            status_message: Optional[str] = None) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope(
                "AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return

        try:
            status_enum = PresenceStatus.from_string(status)
        except ValueError:
            self._push(connection_id, self.error_envelope(
                "INVALID_STATUS", f"Status desconhecido: {status!r}"))
            return

        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        user = self.store.get_user(entry["user"]["user_id"]) or entry["user"]

        if status_enum == PresenceStatus.OFFLINE:
            self._push(connection_id, self.error_envelope(
                "INVALID_STATUS", "Não é possível definir status offline manualmente. Use LOGOUT."))
            return

        new_presence = self.presence.change_status(
            user["user_id"], status_enum, status_message)
        await self.bus.emit(Event(USER_STATUS_CHANGED, f"user:{user['user_id']}", {
            "user_id": user["user_id"],
            "username": user["username"],
            "status": new_presence["status"].value,
            "status_message": new_presence["status_message"],
            "custom_status": user.get("custom_status") or "",
        }))
        log.info("Status changed: %s -> %s", user["username"], status_enum.value)

        # Notify every other online user (and echo to sender)
        notification = self._envelope("USER_STATUS_CHANGED", {
            "user_id": user["user_id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "status": new_presence["status"].value,
            "status_message": new_presence["status_message"],
            "custom_status": user.get("custom_status") or "",
            "avatar_data": user.get("avatar_data"),
            "avatar_mime": user.get("avatar_mime"),
        })
        for e in self.sessions.iter_sessions():
            if e["connection"]:
                self._push(e["connection"].connection_id, notification)

    # -- friendships ---------------------------------------------------------

    async def search_users(self, connection_id: str, query: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        users = []
        for user in self.friendships.search(query, entry["user"]["user_id"]):
            profile = self.profiles.public_user(user)
            profile["presence"] = self._public_presence(user["user_id"])
            users.append(profile)
        self._push(connection_id, self._envelope("SEARCH_USERS_RESULT", {"users": users}))

    async def send_friend_request(self, connection_id: str, username: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        requester_id = entry["user"]["user_id"]
        try:
            friendship = self.friendships.request(requester_id, username)
        except FriendshipError as exc:
            self._push(connection_id, self.error_envelope(exc.code, str(exc)))
            return
        users = self._friendship_users(friendship)
        self._push_friendship_snapshots(users)
        self._push(connection_id, self._envelope("FRIENDSHIP_UPDATED", {
            "friendship_id": friendship["friendship_id"],
            "status": friendship["status"],
        }))

    async def respond_friend_request(self, connection_id: str, friendship_id: str, action: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        try:
            friendship = self.friendships.respond(entry["user"]["user_id"], friendship_id, action)
        except FriendshipError as exc:
            self._push(connection_id, self.error_envelope(exc.code, str(exc)))
            return
        users = self._friendship_users(friendship)
        self._push_friendship_snapshots(users)
        self._push(connection_id, self._envelope("FRIENDSHIP_UPDATED", {
            "friendship_id": friendship["friendship_id"],
            "status": "accepted" if action == "accept" else "declined",
        }))

    async def remove_friend(self, connection_id: str, friendship_id: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        try:
            friendship = self.friendships.remove(entry["user"]["user_id"], friendship_id)
        except FriendshipError as exc:
            self._push(connection_id, self.error_envelope(exc.code, str(exc)))
            return
        users = self._friendship_users(friendship)
        self._push_friendship_snapshots(users)
        self._push_to_all(self._envelope("FRIENDSHIP_REMOVED", {
            "friendship_id": friendship["friendship_id"],
        }), users)

    async def open_conversation(self, connection_id: str, username: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        own_id = entry["user"]["user_id"]
        target = self.store.get_user_by_username(username.strip().lower())
        if target is None:
            self._push(connection_id, self.error_envelope("USER_NOT_FOUND", "Usuário não encontrado."))
            return
        friendship = self.store.get_friendship_between(own_id, target["user_id"])
        if friendship is None or friendship["status"] != "accepted":
            self._push(connection_id, self.error_envelope("FRIENDSHIP_REQUIRED", "Só é possível conversar com um amigo aceito."))
            return
        conversation = self.conversations.get_or_create_individual(own_id, target["user_id"])
        notification = self._envelope("CONVERSATION_CREATED", {
            "conversation": conversation.to_dict(),
            "invited_by": entry["user"]["username"],
        })
        self._push_to_all(notification, {own_id, target["user_id"]})

    # -- profile -------------------------------------------------------------

    async def set_avatar(self, connection_id: str, data: str, filename: str, mime: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        try:
            user = self.profiles.set_avatar(entry["user"]["user_id"], data, filename, mime)
        except ProfileError as exc:
            self._push(connection_id, self.error_envelope(exc.code, str(exc)))
            return
        self._push_to_all(self._envelope("PROFILE_UPDATED", {
            "user": self.profiles.public_user(user),
        }))

    async def set_custom_status(self, connection_id: str, message: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        user_id = entry["user"]["user_id"]
        try:
            user = self.profiles.set_custom_status(user_id, message)
        except ProfileError as exc:
            self._push(connection_id, self.error_envelope(exc.code, str(exc)))
            return
        self.presence.change_status(user_id, self.presence.get_presence(user_id)["status"], message)
        await self.bus.emit(Event(USER_STATUS_CHANGED, f"user:{user_id}", {
            "user_id": user_id,
            "username": user["username"],
            "display_name": user["display_name"],
            "status": self.presence.get_presence(user_id)["status"].value,
            "status_message": message,
            "custom_status": message,
        }))
        self._push_presence_update(user_id)

    async def update_profile(self, connection_id: str, display_name: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        try:
            user = await self.auth.update_display_name(entry["user"]["user_id"], display_name)
        except AuthError as exc:
            self._push(connection_id, self.error_envelope("PROFILE_UPDATE_FAILED", str(exc)))
            return
        entry["user"]["display_name"] = user["display_name"]
        self._push_to_all(self._envelope("PROFILE_UPDATED", {"user": self.profiles.public_user(user)}))
        self._push_presence_update(user["user_id"])

    async def change_password(self, connection_id: str, current_password: str,
                              new_password: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        try:
            await self.auth.change_password(entry["user"]["user_id"], current_password, new_password, session_id)
        except AuthError as exc:
            self._push(connection_id, self.error_envelope("PASSWORD_CHANGE_FAILED", str(exc)))
            return
        for other in list(self.sessions.iter_sessions_for_user(entry["user"]["user_id"])):
            if other["session"].session_id != session_id:
                self._force_close_session(other["session"].session_id)
        self._push(connection_id, self._envelope("PASSWORD_CHANGED", {}))

    # -- attachments ----------------------------------------------------------

    def _attachment_public_url(self, attachment_id: str, user_id: str, inline: bool = False) -> str:
        base = self.settings.public_base_url
        if not base:
            host = "127.0.0.1" if self.settings.host in {"0.0.0.0", "::"} else self.settings.host
            port = getattr(self.settings, "attachment_http_port", 0) or (self.settings.port + 1)
            base = f"http://{host}:{port}"
        return self.attachments.signed_download_url(attachment_id, user_id, base, inline=inline)

    def _public_attachment(self, attachment: dict[str, Any], user_id: str) -> dict[str, Any]:
        public = {
            "attachment_id": attachment["attachment_id"],
            "original_name": attachment["original_name"],
            "mime_type": attachment["mime_type"],
            "size": attachment["size"],
            "sha256": attachment["sha256"],
            "created_at": attachment["created_at"],
            "download_url": self._attachment_public_url(attachment["attachment_id"], user_id),
        }
        kind = self.attachments.preview_kind(attachment)
        if kind:
            public["preview_kind"] = kind
            public["preview_url"] = self._attachment_public_url(attachment["attachment_id"], user_id, inline=True)
        return public

    async def begin_attachment_upload(self, connection_id: str, conversation_id: str,
                                      filename: str, mime: str, size: int) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        user_id = entry["user"]["user_id"]
        if not self.conversations.is_participant(conversation_id, user_id):
            self._push(connection_id, self.error_envelope("MESSAGE_FAILED", "Você não participa dessa conversa."))
            return
        if self._attachment_upload_counts[connection_id] >= self.settings.attachment_max_per_message:
            self._push(connection_id, self.error_envelope("UPLOAD_LIMITED", "Há uploads demais em andamento."))
            return
        try:
            result = self.attachments.begin_upload(connection_id, user_id, conversation_id, filename, mime, size)
        except AttachmentError as exc:
            self._push(connection_id, self.error_envelope(exc.code, str(exc)))
            return
        self._attachment_upload_counts[connection_id] += 1
        self.select_attachment_upload(connection_id, result["upload_id"])
        self._push(connection_id, self._envelope("ATTACHMENT_UPLOAD_READY", result))

    async def receive_attachment_chunk(self, connection_id: str, data: bytes) -> None:
        # Binary frames carry the upload id in a short JSON header frame first;
        # the handler stores the selected upload id on the connection.
        upload_id = getattr(self, "_binary_upload_for_connection", {}).get(connection_id)
        if not upload_id:
            self._push(connection_id, self.error_envelope("UPLOAD_NOT_SELECTED", "Nenhum upload binário foi selecionado."))
            return
        try:
            progress = self.attachments.append_chunk(connection_id, upload_id, data)
        except AttachmentError as exc:
            self._push(connection_id, self.error_envelope(exc.code, str(exc)))
            return
        self._push(connection_id, self._envelope("ATTACHMENT_UPLOAD_PROGRESS", progress))

    def select_attachment_upload(self, connection_id: str, upload_id: str) -> None:
        if not hasattr(self, "_binary_upload_for_connection"):
            self._binary_upload_for_connection = {}
        self._binary_upload_for_connection[connection_id] = upload_id

    async def finish_attachment_upload(self, connection_id: str, upload_id: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        try:
            conversation_id = self.attachments.upload_conversation(connection_id, entry["user"]["user_id"], upload_id)
            if not self.conversations.is_participant(conversation_id, entry["user"]["user_id"]):
                self.attachments.abort_upload(connection_id, upload_id)
                raise AttachmentError("UPLOAD_FORBIDDEN", "Você não participa mais dessa conversa.")
            attachment = self.attachments.finish_upload(connection_id, upload_id)
            if attachment["owner_id"] != entry["user"]["user_id"] or not self.conversations.is_participant(attachment["conversation_id"], entry["user"]["user_id"]):
                raise AttachmentError("UPLOAD_FORBIDDEN", "Este upload não pertence a essa conta ou conversa.")
            public_attachment = self._public_attachment(attachment, entry["user"]["user_id"])
            stored_attachment = {key: value for key, value in public_attachment.items() if key not in {"download_url", "preview_url"}}
            payload = {"attachment": stored_attachment}
            result = await self.messages.send_message(
                entry["user"]["user_id"], attachment["conversation_id"], "attachment", payload,
                trusted_attachment=True,
            )
            self.store.link_attachment_message(attachment["attachment_id"], result["message"].message_id)
        except (AttachmentError, MessageError) as exc:
            code = getattr(exc, "code", "ATTACHMENT_FAILED")
            self._push(connection_id, self.error_envelope(code, str(exc)))
            return
        finally:
            self._attachment_upload_counts[connection_id] = max(0, self._attachment_upload_counts[connection_id] - 1)
            if hasattr(self, "_binary_upload_for_connection"):
                self._binary_upload_for_connection.pop(connection_id, None)
        message = result["message"]
        self._push(connection_id, self._envelope("MESSAGE_ACK", {"message_id": message.message_id, "conversation_id": message.conversation_id, "duplicate": False}))
        conv = self.conversations.get_conversation(message.conversation_id)
        if conv is not None:
            for participant_id in conv.participants:
                if participant_id == entry["user"]["user_id"]:
                    continue
                target_conn = self._find_connection_for_user(participant_id)
                if target_conn:
                    recipient_payload = {"attachment": self._public_attachment(attachment, participant_id)}
                    delivery = self._envelope("MESSAGE", {"message": message.to_dict() | {"payload": recipient_payload, "type": "attachment"}})
                    self._push(target_conn, delivery)
        self._push(connection_id, self._envelope("ATTACHMENT_UPLOAD_COMPLETE", {"attachment": public_attachment, "message_id": message.message_id}))

    async def abort_attachment_upload(self, connection_id: str, upload_id: str) -> None:
        try:
            self.attachments.abort_upload(connection_id, upload_id)
        except AttachmentError as exc:
            self._push(connection_id, self.error_envelope(exc.code, str(exc)))
        finally:
            self._attachment_upload_counts[connection_id] = max(0, self._attachment_upload_counts[connection_id] - 1)
            if hasattr(self, "_binary_upload_for_connection"):
                self._binary_upload_for_connection.pop(connection_id, None)

    # -- message search and pins --------------------------------------------

    async def search_messages(self, connection_id: str, conversation_id: str,
                              query: str, limit: int = 50,
                              before: Optional[str] = None) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        if not self.conversations.is_participant(conversation_id, entry["user"]["user_id"]):
            self._push(connection_id, self.error_envelope("MESSAGE_FAILED", "Você não participa dessa conversa."))
            return
        messages = self.store.search_messages(conversation_id, query, limit=min(limit, 100), before=before)
        messages = [self.sync.hydrate_message(message, entry["user"]["user_id"]) for message in messages]
        self._push(connection_id, self._envelope("MESSAGE_SEARCH_RESULT", {"conversation_id": conversation_id, "query": query, "messages": messages, "before": before}))

    async def list_pinned_messages(self, connection_id: str, conversation_id: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        if not self.conversations.is_participant(conversation_id, entry["user"]["user_id"]):
            self._push(connection_id, self.error_envelope("MESSAGE_FAILED", "Você não participa dessa conversa."))
            return
        messages = [self.sync.hydrate_message(message, entry["user"]["user_id"]) for message in self.store.list_pinned_messages(conversation_id)]
        self._push(connection_id, self._envelope("PINNED_MESSAGES", {"conversation_id": conversation_id, "messages": messages}))

    async def pin_message(self, connection_id: str, conversation_id: str, message_id: str, pinned: bool) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope("AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return
        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        user_id = entry["user"]["user_id"]
        if not self.conversations.is_participant(conversation_id, user_id):
            self._push(connection_id, self.error_envelope("MESSAGE_FAILED", "Você não participa dessa conversa."))
            return
        message = self.store.get_message(message_id)
        if message is None or message["conversation_id"] != conversation_id:
            self._push(connection_id, self.error_envelope("MESSAGE_NOT_FOUND", "Mensagem não encontrada."))
            return
        changed = self.store.pin_message(conversation_id, message_id, user_id, datetime.now(timezone.utc).isoformat()) if pinned else self.store.unpin_message(conversation_id, message_id)
        current = self.store.get_message(message_id) or message
        conv = self.conversations.get_conversation(conversation_id)
        if conv:
            for participant_id in conv.participants:
                target_conn = self._find_connection_for_user(participant_id)
                if target_conn:
                    hydrated = self.sync.hydrate_message(current, participant_id)
                    self._push(target_conn, self._envelope("MESSAGE_PINNED", {"conversation_id": conversation_id, "message": hydrated, "is_pinned": pinned, "changed": changed}))
        else:
            hydrated = self.sync.hydrate_message(current, user_id)
            self._push(connection_id, self._envelope("MESSAGE_PINNED", {"conversation_id": conversation_id, "message": hydrated, "is_pinned": pinned, "changed": changed}))

    # -- conversations -------------------------------------------------------

    async def create_group(self, connection_id: str, name: str,
                           participants: list[str]) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope(
                "AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return

        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        user = entry["user"]

        # Resolve usernames -> user_ids
        resolved: list[str] = []
        missing: list[str] = []
        invalid: list[str] = []
        for uname in participants:
            row = self.store.get_user_by_username(uname.strip().lower())
            if row is None:
                missing.append(uname)
            elif row["user_id"] == user["user_id"]:
                invalid.append(uname)
            elif row["user_id"] not in resolved:
                resolved.append(row["user_id"])

        if missing:
            self._push(connection_id, self.error_envelope(
                "USER_NOT_FOUND",
                f"Usuários não encontrados: {', '.join(missing)}"))
            return
        if invalid:
            self._push(connection_id, self.error_envelope("CONVERSATION_FAILED", "Você não pode adicionar a si mesmo ao grupo."))
            return
        if not resolved:
            self._push(connection_id, self.error_envelope("CONVERSATION_FAILED", "Selecione pelo menos um participante."))
            return

        try:
            conv = self.conversations.create_group(name, user["user_id"], resolved)
        except ConversationError as exc:
            self._push(connection_id, self.error_envelope("CONVERSATION_FAILED", str(exc)))
            return

        await self.bus.emit(Event(CONVERSATION_CREATED, f"user:{user['user_id']}", {
            "conversation_id": conv.conversation_id,
            "is_group": True,
        }))
        notification = self._envelope("CONVERSATION_CREATED", {
            "conversation": conv.to_dict(),
            "invited_by": user["username"],
        })
        for uname in participants:
            row = self.store.get_user_by_username(uname.strip().lower())
            for e in self.sessions.iter_sessions():
                if e["connection"] and e["user"]["user_id"] == row["user_id"]:
                    self._push(e["connection"].connection_id, notification)
        # echo to creator
        self._push(connection_id, notification)
        log.info("Group conversation created: %s by %s", conv.conversation_id, user["username"])

    # -- messages ------------------------------------------------------------

    async def send_message(self, connection_id: str, conversation_id: str,
                           msg_type: str, payload: Any,
                           message_id: Optional[str] = None) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope(
                "AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return

        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        user = entry["user"]
        user_id = user["user_id"]

        # basic rate limiting
        cutoff = time.time() - 60.0
        recent = [t for t in self._msg_log[user_id] if t > cutoff]
        if len(recent) >= self.settings.max_messages_per_minute:
            self._push(connection_id, self.error_envelope(
                "RATE_LIMITED",
                f"Muitas mensagens. Aguarde ({self.settings.max_messages_per_minute}/min)."))
            return
        self._msg_log[user_id] = recent + [time.time()]

        try:
            result = await self.messages.send_message(
                user_id, conversation_id, msg_type, payload, message_id
            )
        except MessageError as exc:
            self._push(connection_id, self.error_envelope(
                "MESSAGE_FAILED", str(exc)))
            log.warning("Message rejected for %s: %s", user["username"], exc)
            return

        message = result["message"]
        inserted = result["inserted"]

        # Idempotency: even a duplicate retry gets an ACK so the client knows
        # the message was stored, but nothing is re-delivered.
        if not inserted:
            log.info("Duplicate message ignored (same message_id): %s",
                     message.message_id)
        self._push(connection_id, self._envelope("MESSAGE_ACK", {
            "message_id": message.message_id,
            "conversation_id": message.conversation_id,
            "duplicate": not inserted,
        }))
        log.info(
            "Message sent by %s in %s: %s",
            user["username"],
            message.conversation_id,
            message.payload.get("content", "")[:MAX_CONTENT_PREVIEW],
        )

        # Deliver to the other participants (only for a genuinely new message)
        if inserted:
            conv = self.conversations.get_conversation(conversation_id)
            assert conv is not None
            delivery_notification = self._envelope("MESSAGE", {
                "message": message.to_dict(),
            })
            delivered_count = 0
            for participant_id in conv.participants:
                if participant_id == user_id:
                    continue
                target_conn = self._find_connection_for_user(participant_id)
                if target_conn is None:
                    continue  # offline — persisted, delivered on reconnect via sync
                self._push(target_conn, delivery_notification)
                delivered_count += 1

            await self.bus.emit(Event(MESSAGE_DELIVERED, f"user:{user_id}", {
                "message_id": message.message_id,
                "delivered_to": delivered_count,
                "conversation_id": conversation_id,
            }))

    async def get_history(self, connection_id: str, conversation_id: str,
                          limit: int = 100, before: Optional[str] = None) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope(
                "AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return

        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None

        try:
            history = self.messages.get_history(
                conversation_id, entry["user"]["user_id"],
                limit=limit, before=before,
            )
        except MessageError as exc:
            self._push(connection_id, self.error_envelope(
                "MESSAGE_FAILED", str(exc)))
            return

        history = [self.sync.hydrate_message(message, entry["user"]["user_id"]) for message in history]
        self._push(connection_id, self._envelope("HISTORY", {
            "conversation_id": conversation_id,
            "messages": history,
            "before": before,
        }))

    # -- logout --------------------------------------------------------------

    async def logout(self, connection_id: str) -> None:
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            self._push(connection_id, self.error_envelope(
                "AUTH_REQUIRED", "É necessário fazer login primeiro."))
            return

        entry = self.sessions.get_session_entry(session_id)
        assert entry is not None
        user = entry["user"]
        user_id = user["user_id"]

        self._push(connection_id, self._envelope("LOGOUT_OK", {}))

        await self.bus.emit(Event(USER_LOGGED_OUT, f"user:{user_id}", {
            "user_id": user_id,
            "username": user["username"],
        }))
        self.presence.set_offline(user_id)
        await self.bus.emit(Event(USER_STATUS_CHANGED, f"user:{user_id}", {
            "user_id": user_id,
            "username": user["username"],
            "status": PresenceStatus.OFFLINE.value,
            "status_message": self.presence.get_presence(user_id)["status_message"],
            "custom_status": user.get("custom_status") or "",
        }))
        logout_presence = self._envelope("USER_STATUS_CHANGED", {
            "user_id": user_id,
            **self._public_presence(user_id),
        })
        for active in self.sessions.iter_sessions():
            if active["connection"] and active["connection"].connection_id != connection_id:
                self._push(active["connection"].connection_id, logout_presence)

        # session is destroyed on explicit logout (different from disconnect):
        # only now, so events above still resolve the user identity correctly
        self.sessions.remove_session(session_id)
        await self.auth.invalidate_session(session_id)

        log.info("User logged out: %s", user["username"])


    # -- utilities -----------------------------------------------------------

    def _find_connection_for_user(self, user_id: str) -> Optional[str]:
        return self.sessions.get_connection_for_user(user_id)

    def online_users(self) -> list[dict[str, Any]]:
        out = []
        for e in self.sessions.iter_sessions():
            if e["connection"] is not None:
                user = e["user"]
                p = self.presence.get_presence(user["user_id"])
                out.append({
                    "user_id": user["user_id"],
                    "username": user["username"],
                    "display_name": user["display_name"],
                    "status": p["status"].value,
                    "status_message": p["status_message"],
                })
        return out

    async def touch_activity(self, connection_id: str) -> None:
        """Update the last_seen_at of the session bound to a connection.

        Called by the network layer after every successfully dispatched
        (authenticated) command. If the connection has no session yet (never
        authenticated, or already evicted), this is a harmless no-op.
        """
        session_id = self.sessions.connection_session_id(connection_id)
        if session_id is None:
            return
        session = self.sessions.get_session(session_id)
        if session is None:  # evicted between lookup and touch: harmless
            return
        self.auth.touch_session(session)


MAX_CONTENT_PREVIEW = 80
