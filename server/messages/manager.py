"""Message management.

Responsibilities:
- validate incoming message payload (type, size limits)
- persist (deduplicated by message_id — INSERT OR IGNORE)
- emit MESSAGE_SENT / MESSAGE_DELIVERED events
- retrieve conversation history (chronological, paginated)

The sender_id is ALWAYS set by the server from the authenticated session,
never trusted from the client payload.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from server.shared_types import Message, MessageType

log = logging.getLogger("msn.messages")

MAX_CONTENT_PREVIEW = 80


class MessageError(Exception):
    pass


class MessageManager:
    def __init__(self, store: Any, settings: Any, conversations: Any,
                 bus: Any) -> None:
        self._store = store
        self._settings = settings
        self._conversations = conversations
        self._bus = bus

    # -- validation ----------------------------------------------------------

    def _validate_payload(self, msg_type: str, payload: Any) -> dict[str, Any]:
        if msg_type not in {t.value for t in MessageType}:
            raise MessageError(f"Tipo de mensagem desconhecido: {msg_type!r}")
        if msg_type == MessageType.TEXT:
            if not isinstance(payload, dict):
                raise MessageError("Payload de texto deve ser um objeto.")
            content = payload.get("content")
            if not isinstance(content, str):
                raise MessageError("Mensagens de texto precisam do campo 'content'.")
            content = content.strip()
            if not content:
                raise MessageError("Mensagens vazias não são permitidas.")
            if len(content) > self._settings.max_message_length:
                raise MessageError(
                    f"Mensagem muito longa (máx. {self._settings.max_message_length} caracteres)."
                )
            return {"content": content}
        raise MessageError(f"Tipo de mensagem não suportado: {msg_type!r}")

    # -- sending -------------------------------------------------------------

    async def send_message(self, sender_id: str, conversation_id: str,
                           msg_type: str, payload: Any,
                           message_id: Optional[str] = None) -> dict[str, Any]:
        if not self._conversations.is_participant(conversation_id, sender_id):
            raise MessageError("Você não participa dessa conversa.")

        validated = self._validate_payload(msg_type, payload)

        # Stabilization item 11: message_id collision check. If the client
        # provided a message_id that is already persisted, the retry is only
        # accepted when the stored message is semantically identical
        # (same conversation, sender, type and payload) — otherwise the
        # reuse is treated as a conflict error rather than silently
        # swallowing the new message.
        if message_id is not None:
            stored = self._store.get_message(message_id)
            if stored is not None:
                same_content = (
                    stored["conversation_id"] == conversation_id
                    and stored["sender_id"] == sender_id
                    and stored["type"] == msg_type
                    and json.dumps(stored["payload"], sort_keys=True)
                    == json.dumps(validated, sort_keys=True)
                )
                if not same_content:
                    raise MessageError(
                        f"MESSAGE_ID_CONFLICT: o message_id {message_id!r} "
                        "já foi utilizado por outra mensagem com conteúdo "
                        "diferente.")
                # get_message (stabilization item 11) already returns
                # payload/metadata as JSON objects — pass the row straight in.
                return {"message": Message.from_dict(stored),
                        "inserted": False}

        message = Message(
            conversation_id=conversation_id,
            sender_id=sender_id,
            msg_type=MessageType(msg_type),
            payload=validated,
            message_id=message_id,
        )

        # Idempotency: INSERT OR IGNORE inside the store returns False when
        # the same message_id was already persisted (e.g. retry after a
        # reconnection). In that case we still acknowledge the client but do
        # NOT re-emit or re-deliver, avoiding duplicates on the wire.
        inserted = self._store.save_message(message.to_dict())
        self._store.touch_conversation(message.conversation_id, message.timestamp)

        if inserted:
            from server.events.bus import MESSAGE_SENT, Event
            await self._bus.emit(Event(MESSAGE_SENT, f"user:{sender_id}", {
                "message": message.to_dict(),
            }))
            return {"message": message, "inserted": True}
        return {"message": message, "inserted": False}

    # -- history -------------------------------------------------------------

    def get_history(self, conversation_id: str, user_id: str,
                    limit: int = 100, before: Optional[str] = None
                    ) -> list[dict[str, Any]]:
        if not self._conversations.is_participant(conversation_id, user_id):
            raise MessageError("Você não participa dessa conversa.")
        messages = self._store.list_conversation_messages(
            conversation_id, limit=limit, before=before
        )
        messages.sort(key=lambda m: m["timestamp"])
        return messages
