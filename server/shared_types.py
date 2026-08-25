"""Shared types and data models used across all modules of the MSN Messenger server.

This module is intentionally dependency-free (only stdlib + typing) so that every
other module can import from it without creating circular dependencies.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Optional

import json


def new_id() -> str:
    """Generate a URL-safe, unique identifier."""
    return uuid.uuid4().hex


def now_iso() -> str:
    """Current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PresenceStatus(str, Enum):
    """User presence states."""
    ONLINE = "online"
    AWAY = "away"
    BUSY = "busy"
    OFFLINE = "offline"

    @classmethod
    def from_string(cls, value: str) -> PresenceStatus:
        try:
            return cls(value)
        except ValueError:
            raise ValueError(f"Unknown presence status: {value!r}")


class MessageType(str, Enum):
    """Types of messages supported in conversations.

    Extensibility note: richer media (files, images, system messages) can be
    added here later without changing the protocol envelope, because every
    message travels as {type, payload}.
    """
    TEXT = "text"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class User:
    """A registered user account.

    NOTE: never store the plaintext password. Only the hashed version
    (password_hash) and its parameters live here.
    """

    def __init__(
        self,
        user_id: str,
        username: str,
        display_name: str,
        password_hash: str,
        created_at: Optional[str] = None,
        email: Optional[str] = None,
    ) -> None:
        self.user_id = user_id
        self.username = username
        self.display_name = display_name
        self.password_hash = password_hash
        self.email = email
        self.created_at = created_at or now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> User:
        return cls(
            user_id=data["user_id"],
            username=data["username"],
            display_name=data["display_name"],
            password_hash=data["password_hash"],
            created_at=data.get("created_at"),
            email=data.get("email"),
        )


class Session:
    """An authenticated session. A session is tied to a user, but a connection
    is only one transport channel that may or may not carry a session.

    A session can exist without an active connection (user disconnected
    unexpectedly, will reconnect later).
    """

    def __init__(
        self,
        session_id: str,
        user_id: str,
        started_at: Optional[str] = None,
        last_seen_at: Optional[str] = None,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.started_at = started_at or now_iso()
        self.last_seen_at = last_seen_at or self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "started_at": self.started_at,
            "last_seen_at": self.last_seen_at,
        }


class Conversation:
    """A conversation (individual or group)."""

    def __init__(
        self,
        conversation_id: str,
        name: Optional[str],
        is_group: bool,
        participants: list[str],
        created_at: Optional[str] = None,
        last_message_at: Optional[str] = None,
    ) -> None:
        self.conversation_id = conversation_id
        self.name = name
        self.is_group = is_group
        self.participants = participants
        self.created_at = created_at or now_iso()
        self.last_message_at = last_message_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "name": self.name,
            "is_group": self.is_group,
            "participants": self.participants,
            "created_at": self.created_at,
            "last_message_at": self.last_message_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Conversation:
        return cls(
            conversation_id=data["conversation_id"],
            name=data.get("name"),
            is_group=data.get("is_group", False),
            participants=data.get("participants", []),
            created_at=data.get("created_at"),
            last_message_at=data.get("last_message_at"),
        )


class Message:
    """A single message inside a conversation.

    Structured format (see specification, section 4.7):
    - id: unique message identifier (client-provided during send, for
          deduplication / idempotency; server generates one if absent)
    - conversation_id: which conversation it belongs to
    - sender_id: the user who sent it (set by the server, never trusted from
                 the client)
    - timestamp: ISO-8601 UTC, set by the server
    - type: MESSAGE_SENT_TYPE (currently only "text"; future types can be added)
    - payload: JSON-serializable content; for "text" it is {"content": "..."}
    - metadata: optional free-form dict (delivery info, edits, etc.)
    """

    def __init__(
        self,
        conversation_id: str,
        sender_id: str,
        msg_type: MessageType = MessageType.TEXT,
        payload: Optional[dict[str, Any]] = None,
        message_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.message_id = message_id or new_id()
        self.conversation_id = conversation_id
        self.sender_id = sender_id
        self.type = msg_type
        self.payload = payload or {"content": ""}
        self.timestamp = timestamp or now_iso()
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "sender_id": self.sender_id,
            "timestamp": self.timestamp,
            "type": self.type,
            "payload": self.payload,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(
            conversation_id=data["conversation_id"],
            sender_id=data["sender_id"],
            msg_type=MessageType(data.get("type", MessageType.TEXT)),
            payload=data.get("payload", {"content": ""}),
            message_id=data.get("message_id"),
            timestamp=data.get("timestamp"),
            metadata=data.get("metadata", {}),
        )
