"""Conversation management.

Supports:
- individual (1:1) conversations — created lazily on first message between
  two users (MSN-style)
- group conversations — created explicitly via CREATE_CONVERSATION

Future resources (files, images, calls, system messages, notifications) fit
into this structure by extending MessageType / adding message-level flags —
no architectural change needed.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from server.shared_types import Conversation, new_id, now_iso

log = logging.getLogger("msn.conversations")


class ConversationError(Exception):
    pass


class ConversationManager:
    def __init__(self, store: Any) -> None:
        self._store = store

    # -- lookup / creation ---------------------------------------------------

    def get_or_create_individual(self, user_a: str, user_b: str) -> Conversation:
        """Return the existing 1:1 conversation between two users, or create
        one lazily."""
        if user_a == user_b:
            raise ConversationError("Um usuário não pode conversar consigo mesmo.")

        existing = self._store.find_conversation(user_a, user_b)
        if existing:
            row = self._store.get_conversation(existing)
            assert row is not None
            return Conversation.from_dict(row)

        conv = Conversation(
            conversation_id=new_id(),
            name=None,
            is_group=False,
            participants=[user_a, user_b],
        )
        self._store.create_conversation(
            conv.conversation_id, conv.name, conv.is_group,
            conv.participants, conv.created_at,
        )
        return conv

    def create_group(self, name: str, creator_id: str,
                     participants: list[str]) -> Conversation:
        if not participants:
            raise ConversationError(
                "Uma conversa em grupo precisa de pelo menos um participante além do criador.")
        all_ids = set(participants)
        all_ids.add(creator_id)
        if len(all_ids) < 2:
            raise ConversationError(
                "Os participantes devem ser distintos do criador.")

        conv = Conversation(
            conversation_id=new_id(),
            name=name.strip()[:120] or None,
            is_group=True,
            participants=sorted(all_ids),
        )
        self._store.create_conversation(
            conv.conversation_id, conv.name, conv.is_group,
            conv.participants, conv.created_at,
        )
        return conv

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        row = self._store.get_conversation(conversation_id)
        return Conversation.from_dict(row) if row else None

    def list_user_conversations(self, user_id: str) -> list[Conversation]:
        return [
            Conversation.from_dict(r)
            for r in self._store.list_user_conversations(user_id)
        ]

    def is_participant(self, conversation_id: str, user_id: str) -> bool:
        conv = self.get_conversation(conversation_id)
        return conv is not None and user_id in conv.participants
