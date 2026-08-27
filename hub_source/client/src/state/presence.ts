import type {
  ConversationPayload,
  FriendshipPayload,
  PresencePayload,
  PresenceStatus,
} from "@/network/protocol";

export type KnownUser = {
  userId: string;
  username: string;
  displayName: string;
  avatar_data?: string | null;
  avatar_mime?: string | null;
  custom_status?: string;
};

function hasField<T extends object>(value: T | undefined, field: keyof T): boolean {
  return Boolean(value && Object.prototype.hasOwnProperty.call(value, field));
}

function statusForParticipants(
  participantIds: string[],
  ownUserId: string,
  presence: Record<string, PresencePayload>,
): PresenceStatus {
  const statuses = participantIds
    .filter((userId) => userId !== ownUserId)
    .map((userId) => presence[userId]?.status ?? "offline");
  if (statuses.includes("online")) return "online";
  if (statuses.includes("busy")) return "busy";
  if (statuses.includes("away")) return "away";
  return "offline";
}

export function deriveFriendPresence(
  friend: FriendshipPayload,
  presence: Record<string, PresencePayload>,
): FriendshipPayload {
  const current = presence[friend.user_id];
  if (!current) return friend;
  return {
    ...friend,
    status: current.status,
    status_message: current.status_message,
    display_name: current.display_name || friend.display_name,
    avatar_data: hasField(current, "avatar_data") ? current.avatar_data ?? null : friend.avatar_data,
    avatar_mime: hasField(current, "avatar_mime") ? current.avatar_mime ?? null : friend.avatar_mime,
    custom_status: hasField(current, "custom_status") ? current.custom_status || "" : friend.custom_status || "",
  };
}

export function deriveConversationPresentation(
  conversation: ConversationPayload,
  ownUserId: string,
  presence: Record<string, PresencePayload>,
  knownUsers: Record<string, KnownUser>,
): {
  name: string;
  status: PresenceStatus;
  avatarData: string | null | undefined;
  avatarMime: string | null | undefined;
  customStatus: string;
} {
  const status = statusForParticipants(conversation.participants, ownUserId, presence);
  if (conversation.is_group) {
    return {
      name: conversation.name || "Grupo sem nome",
      status,
      avatarData: null,
      avatarMime: null,
      customStatus: "",
    };
  }

  const peerId = conversation.participants.find((userId) => userId !== ownUserId) || "";
  const peerPresence = presence[peerId];
  const peerUser = knownUsers[peerId];
  const name = peerPresence?.display_name || peerUser?.displayName || conversation.name || `Contato ${peerId.slice(0, 6)}`;
  const avatarData = hasField(peerPresence, "avatar_data")
    ? peerPresence?.avatar_data ?? null
    : peerUser?.avatar_data ?? null;
  const avatarMime = hasField(peerPresence, "avatar_mime")
    ? peerPresence?.avatar_mime ?? null
    : peerUser?.avatar_mime ?? null;
  const customStatus = hasField(peerPresence, "custom_status")
    ? peerPresence?.custom_status || ""
    : peerUser?.custom_status || "";

  return { name, status, avatarData, avatarMime, customStatus };
}
