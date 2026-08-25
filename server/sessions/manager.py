"""Session + connection mapping manager.

Maintains the strict separation required by the specification:

    CONNECTION  !=  SESSION  !=  USER

- A connection is a transport channel (a WebSocket).
- A session is an authenticated identity (may exist without a connection,
  e.g. after an unexpected disconnect, awaiting reconnection).
- A user is an account in the database.

A session can be bound to at most ONE live connection. If a new connection
arrives claiming an already-bound session (re-login from another place),
the previous connection is closed so presence stays consistent.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Iterator

from server.shared_types import Session

log = logging.getLogger("msn.sessions")


class ConnectionHandle:
    """Opaque reference to a live client connection held by the network layer."""

    def __init__(self, connection_id: str, protocol: Any) -> None:
        self.connection_id = connection_id
        self.protocol = protocol  # the WebSocketServerProtocol instance


class SessionManager:
    def __init__(self) -> None:
        # session_id -> dict with Session + bound ConnectionHandle (optional)
        self._sessions: dict[str, dict[str, Any]] = {}
        # connection_id -> session_id
        self._connection_map: dict[str, str] = {}
        # username(lower) -> user dict (in-memory directory, refreshed on login)
        self._users: dict[str, dict[str, Any]] = {}

    # -- session lifecycle ---------------------------------------------------

    def add_session(self, session: Session, user: dict[str, Any]) -> None:
        entry = {"session": session, "connection": None, "user": user}
        self._sessions[session.session_id] = entry
        self._users[user["username"].lower()] = user

    def remove_session(self, session_id: str) -> Optional[dict[str, Any]]:
        entry = self._sessions.pop(session_id, None)
        if entry is not None and entry["connection"]:
            self._connection_map.pop(entry["connection"].connection_id, None)
        return entry

    def get_session(self, session_id: str) -> Optional[Session]:
        entry = self._sessions.get(session_id)
        return entry["session"] if entry else None

    def get_session_entry(self, session_id: str) -> Optional[dict[str, Any]]:
        return self._sessions.get(session_id)

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        for entry in self._sessions.values():
            if entry["user"]["user_id"] == user_id:
                return entry["user"]
        return None

    # -- connection binding --------------------------------------------------

    def bind_connection(self, session_id: str, connection: ConnectionHandle,
                        previous_connection: Optional[ConnectionHandle] = None
                        ) -> Optional[ConnectionHandle]:
        """Bind a connection to a session. Returns the previous connection
        that should be closed (if the session was already bound elsewhere)."""
        entry = self._sessions.get(session_id)
        if entry is None:
            return None
        previous = None
        if entry["connection"]:
            if entry["connection"].connection_id == connection.connection_id:
                return None  # re-bind of the same connection: no-op
            previous = entry["connection"]
            self._connection_map.pop(previous.connection_id, None)
        elif previous_connection:
            self._connection_map.pop(previous_connection.connection_id, None)
        entry["connection"] = connection
        self._connection_map[connection.connection_id] = session_id
        return previous

    def unbind_connection(self, connection_id: str) -> Optional[str]:
        """Remove a connection from its session (disconnect). Returns the
        session_id that was bound to it, if any."""
        session_id = self._connection_map.pop(connection_id, None)
        if session_id:
            entry = self._sessions.get(session_id)
            if entry and entry["connection"] and \
               entry["connection"].connection_id == connection_id:
                entry["connection"] = None
        return session_id

    def connection_session_id(self, connection_id: str) -> Optional[str]:
        return self._connection_map.get(connection_id)

    # -- queries -------------------------------------------------------------

    @property
    def all_users(self) -> list[dict[str, Any]]:
        return list(self._users.values())

    def get_user_by_id(self, user_id: str) -> Optional[dict[str, Any]]:
        for u in self._users.values():
            if u["user_id"] == user_id:
                return u
        return None

    def list_online_user_ids(self) -> set[str]:
        return {
            e["user"]["user_id"]
            for e in self._sessions.values()
            if e["connection"] is not None
        }

    def count(self) -> int:
        return len(self._sessions)

    # -- stabilization APIs (Item 9) -----------------------------------------

    def iter_sessions(self) -> Iterator[dict[str, Any]]:
        """Public iterator over all active session entries."""
        # Use list() to avoid "dictionary changed size during iteration"
        yield from list(self._sessions.values())

    def iter_sessions_for_user(self, user_id: str) -> Iterator[dict[str, Any]]:
        """Public iterator over all sessions belonging to a specific user."""
        # Use list() to avoid "dictionary changed size during iteration"
        for entry in list(self._sessions.values()):
            if entry["user"]["user_id"] == user_id:
                yield entry

    def get_connection_for_user(self, user_id: str) -> Optional[str]:
        """Find the active connection_id for a user, if any."""
        for entry in list(self._sessions.values()):
            if entry["user"]["user_id"] == user_id and entry["connection"]:
                return entry["connection"].connection_id
        return None
