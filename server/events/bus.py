"""Event bus: lightweight, in-process event system.

The server is event-oriented where it makes sense. Every event has:
- type (string constant from EVENT_TYPES)
- source ("server", "client:<connection_id>", "user:<user_id>")
- event_id (unique)
- timestamp (when relevant)
- data (dict of associated data)

Handlers are plain async callables. Events are dispatched synchronously in
call order (deterministic, easy to debug) — appropriate for a ~5 user server.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable, Optional

from server.shared_types import new_id, now_iso

log = logging.getLogger("msn.events")

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

USER_CONNECTED = "USER_CONNECTED"
USER_AUTHENTICATED = "USER_AUTHENTICATED"
USER_ONLINE = "USER_ONLINE"
USER_STATUS_CHANGED = "USER_STATUS_CHANGED"
MESSAGE_SENT = "MESSAGE_SENT"
MESSAGE_DELIVERED = "MESSAGE_DELIVERED"
USER_DISCONNECTED = "USER_DISCONNECTED"
USER_LOGGED_OUT = "USER_LOGGED_OUT"
CONVERSATION_CREATED = "CONVERSATION_CREATED"
SYNC_STARTED = "SYNC_STARTED"
SYNC_COMPLETED = "SYNC_COMPLETED"
AUTH_FAILED = "AUTH_FAILED"

EVENT_TYPES = {
    USER_CONNECTED, USER_AUTHENTICATED, USER_ONLINE, USER_STATUS_CHANGED,
    MESSAGE_SENT, MESSAGE_DELIVERED, USER_DISCONNECTED, USER_LOGGED_OUT,
    CONVERSATION_CREATED, SYNC_STARTED, SYNC_COMPLETED, AUTH_FAILED,
}


class Event:
    def __init__(self, event_type: str, source: str, data: Optional[dict[str, Any]] = None) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type!r}")
        self.event_id = new_id()
        self.type = event_type
        self.source = source
        self.timestamp = now_iso()
        self.data = data or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "source": self.source,
            "timestamp": self.timestamp,
            "data": self.data,
        }


class EventBus:
    """In-process pub/sub event bus."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[Event], Awaitable[None]]]] = {}
        self._recorded: list[Event] = []  # for tests/assertions

    def on(self, event_type: str, handler: Callable[[Event], Awaitable[None]]) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    async def emit(self, event: Event) -> None:
        self._recorded.append(event)
        for handler in self._handlers.get(event.type, []):
            try:
                await handler(event)
            except Exception:  # isolate handler failures from the bus
                log.exception("Handler failed for event %s", event.type)

    def recorded_events(self, event_type: Optional[str] = None) -> list[Event]:
        if event_type:
            return [e for e in self._recorded if e.type == event_type]
        return list(self._recorded)

    def clear(self) -> None:
        self._recorded.clear()
