"""User presence management.

Tracks per-user presence state (online/away/busy/offline) kept consistent
across login, logout, connection, disconnection, reconnection and unexpected
failures. Presence updates are always routed through the event bus so the
network layer can broadcast STATUS notifications.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from server.shared_types import PresenceStatus

log = logging.getLogger("msn.users")


class PresenceManager:
    def __init__(self, bus: Any, store: Any = None) -> None:
        self._bus = bus
        self._store = store
        # user_id -> presence dict {"status": PresenceStatus, "status_message": str}
        self._presence: dict[str, dict[str, Any]] = {}

    # -- persistence ---------------------------------------------------------

    def _persist(self, user_id: str) -> None:
        if self._store is None:
            return
        p = self._presence.get(user_id, {
            "status": PresenceStatus.OFFLINE, "status_message": "",
        })
        self._store.save_presence(user_id, p["status"].value, p["status_message"])

    def restore(self, stored: dict[str, dict[str, str]]) -> None:
        """Reload presence from disk after a server restart."""
        for user_id, row in stored.items():
            self._presence[user_id] = {
                "status": PresenceStatus(row.get("status", "offline")),
                "status_message": row.get("status_message", ""),
            }

    # -- lifecycle -----------------------------------------------------------

    def set_online(self, user_id: str, status_message: Optional[str] = None) -> None:
        current = self._presence.get(user_id, {
            "status": PresenceStatus.OFFLINE,
            "status_message": "",
        })
        self._presence[user_id] = {
            "status": PresenceStatus.ONLINE,
            "status_message": current["status_message"] if status_message is None else status_message[:200],
        }

    def set_offline(self, user_id: str) -> None:
        current = self._presence.get(user_id, {
            "status": PresenceStatus.OFFLINE,
            "status_message": "",
        })
        self._presence[user_id] = {
            "status": PresenceStatus.OFFLINE,
            "status_message": current["status_message"],
        }

    def change_status(self, user_id: str, status: PresenceStatus,
                      status_message: Optional[str] = None) -> dict[str, Any]:
        current = self._presence.setdefault(user_id, {
            "status": PresenceStatus.OFFLINE,
            "status_message": "",
        })
        current["status"] = status
        if status_message is not None:
            current["status_message"] = status_message[:200]
        self._persist(user_id)
        return dict(current)

    # -- queries -------------------------------------------------------------

    def get_presence(self, user_id: str) -> dict[str, Any]:
        return dict(self._presence.get(user_id, {
            "status": PresenceStatus.OFFLINE,
            "status_message": "",
        }))

    def all_presence(self) -> dict[str, dict[str, Any]]:
        return {uid: dict(p) for uid, p in self._presence.items()}

    def reset(self) -> None:
        self._presence.clear()
