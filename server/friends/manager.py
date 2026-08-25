from __future__ import annotations

from typing import Any

from server.persistence.store import Persistence
from server.shared_types import new_id, now_iso


class FriendshipError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FriendshipManager:
    """Persistent friendship requests backed by the existing user table."""

    def __init__(self, store: Persistence) -> None:
        self.store = store

    @staticmethod
    def _ordered_pair(user_a_id: str, user_b_id: str) -> tuple[str, str]:
        if user_a_id == user_b_id:
            raise FriendshipError("SELF_FRIEND_REQUEST", "Você não pode adicionar a si mesmo.")
        return tuple(sorted((user_a_id, user_b_id)))

    @staticmethod
    def _user_view(row: dict[str, Any], presence: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": row["other_user_id"],
            "username": row["other_username"],
            "display_name": row["other_display_name"],
            "avatar_data": row.get("other_avatar_data"),
            "avatar_mime": row.get("other_avatar_mime"),
            "custom_status": row.get("other_custom_status") or "",
            "status": presence.get("status", "offline"),
            "status_message": presence.get("status_message", ""),
        }

    @staticmethod
    def _request_view(row: dict[str, Any], own_user_id: str) -> dict[str, Any]:
        return {
            "friendship_id": row["friendship_id"],
            "friendship_status": row["status"],
            "requested_by": row["requested_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "incoming": row["requested_by"] != own_user_id,
        }

    def search(self, query: str, own_user_id: str) -> list[dict[str, Any]]:
        if len(query.strip()) < 1:
            return []
        return self.store.search_users(query, own_user_id)

    def list_for_user(self, user_id: str, presence_by_user: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for row in self.store.list_friendships(user_id):
            presence = presence_by_user.get(row["other_user_id"], {})
            result.append(
                self._user_view(row, presence)
                | self._request_view(row, user_id)
            )
        return result

    def request(self, requester_id: str, target_username: str) -> dict[str, Any]:
        target = self.store.get_user_by_username(target_username.strip().lower())
        if target is None:
            raise FriendshipError("USER_NOT_FOUND", f"Usuário não encontrado: {target_username.strip()}")
        target_id = target["user_id"]
        user_a_id, user_b_id = self._ordered_pair(requester_id, target_id)
        existing = self.store.get_friendship_between(user_a_id, user_b_id)
        if existing:
            if existing["status"] == "accepted":
                raise FriendshipError("ALREADY_FRIENDS", "Vocês já são amigos.")
            raise FriendshipError("REQUEST_ALREADY_PENDING", "Já existe uma solicitação de amizade pendente.")
        timestamp = now_iso()
        self.store.create_friendship(
            new_id(), user_a_id, user_b_id, "pending", requester_id, timestamp, timestamp
        )
        created = self.store.get_friendship_between(user_a_id, user_b_id)
        assert created is not None
        return created

    def _require_participant(self, user_id: str, friendship_id: str) -> dict[str, Any]:
        friendship = self.store.get_friendship(friendship_id)
        if friendship is None or user_id not in (friendship["user_a_id"], friendship["user_b_id"]):
            raise FriendshipError("FRIENDSHIP_NOT_FOUND", "Solicitação ou amizade não encontrada.")
        return friendship

    def respond(self, user_id: str, friendship_id: str, action: str) -> dict[str, Any]:
        friendship = self._require_participant(user_id, friendship_id)
        if friendship["status"] != "pending":
            raise FriendshipError("FRIENDSHIP_NOT_PENDING", "Essa solicitação já foi resolvida.")
        if action == "accept":
            if friendship["requested_by"] == user_id:
                raise FriendshipError("REQUEST_NOT_RECEIVED", "A solicitação enviada por você ainda não pode ser aceita.")
            self.store.update_friendship_status(friendship_id, "accepted", now_iso())
            updated = self.store.get_friendship(friendship_id)
            assert updated is not None
            return updated
        if action == "decline":
            self.store.delete_friendship(friendship_id)
            return friendship
        raise FriendshipError("INVALID_FRIENDSHIP_ACTION", "Ação de amizade inválida.")

    def remove(self, user_id: str, friendship_id: str) -> dict[str, Any]:
        friendship = self._require_participant(user_id, friendship_id)
        if friendship["status"] != "accepted":
            raise FriendshipError("FRIENDSHIP_NOT_ACCEPTED", "A solicitação ainda não é uma amizade.")
        self.store.delete_friendship(friendship_id)
        return friendship
