export type ServerMessageType =
  | "REGISTER_OK"
  | "PASSWORD_RESET_REQUESTED"
  | "PASSWORD_RESET_OK"
  | "AUTH_OK"
  | "RECONNECT_OK"
  | "SYNC_DATA"
  | "MESSAGE_ACK"
  | "MESSAGE"
  | "HISTORY"
  | "CONVERSATION_CREATED"
  | "USER_STATUS_CHANGED"
  | "FRIENDSHIPS_UPDATED"
  | "FRIENDSHIP_UPDATED"
  | "FRIENDSHIP_REMOVED"
  | "SEARCH_USERS_RESULT"
  | "PROFILE_UPDATED"
  | "LOGOUT_OK"
  | "SESSION_TAKEN"
  | "ERROR";

export type ClientCommand =
  | "REGISTER"
  | "LOGIN"
  | "REQUEST_PASSWORD_RESET"
  | "RESET_PASSWORD"
  | "RECONNECT"
  | "REQUEST_SYNC"
  | "CHANGE_STATUS"
  | "SEND_MESSAGE"
  | "GET_HISTORY"
  | "CREATE_GROUP"
  | "SEARCH_USERS"
  | "SEND_FRIEND_REQUEST"
  | "RESPOND_FRIEND_REQUEST"
  | "REMOVE_FRIEND"
  | "OPEN_CONVERSATION"
  | "SET_AVATAR"
  | "SET_CUSTOM_STATUS"
  | "LOGOUT";

export type PresenceStatus = "online" | "away" | "busy" | "offline";

export interface ServerEnvelope<T = unknown> {
  type: ServerMessageType | string;
  payload: T;
}

export interface ErrorPayload {
  code: string;
  message: string;
  [key: string]: unknown;
}

export interface ProfilePayload {
  user_id: string;
  username: string;
  display_name: string;
  avatar_data?: string | null;
  avatar_mime?: string | null;
  custom_status?: string;
}

export interface IdentityPayload extends ProfilePayload {}

export interface AuthPayload extends IdentityPayload {
  session_id: string;
}

export interface PresencePayload extends Partial<ProfilePayload> {
  status: PresenceStatus;
  status_message: string;
  custom_status?: string;
}

export interface FriendshipPayload extends ProfilePayload {
  friendship_id: string;
  friendship_status: "pending" | "accepted";
  requested_by: string;
  incoming: boolean;
  status: PresenceStatus;
  status_message: string;
  created_at: string;
  updated_at: string;
}

export interface MessagePayload {
  message_id: string;
  conversation_id: string;
  sender_id: string;
  timestamp: string;
  type: "text" | string;
  payload: {
    content?: string;
    [key: string]: unknown;
  };
  metadata?: Record<string, unknown>;
}

export interface ConversationPayload {
  conversation_id: string;
  name: string | null;
  is_group: boolean;
  participants: string[];
  created_at: string;
  last_message_at: string | null;
}

export interface SyncDataPayload {
  version: string;
  timestamp: string;
  data: {
    identity: IdentityPayload;
    session: { session_id: string };
    presence: Record<string, PresencePayload>;
    friends: FriendshipPayload[];
    conversations: ConversationPayload[];
    history: Record<string, MessagePayload[]>;
  };
}

export interface HistoryPayload {
  conversation_id: string;
  messages: MessagePayload[];
  before?: string | null;
}

export interface MessageAckPayload {
  message_id: string;
  conversation_id: string;
  duplicate: boolean;
}

export interface StatusChangedPayload extends PresencePayload {
  user_id: string;
  username: string;
  display_name: string;
}

export interface ConversationCreatedPayload {
  conversation: ConversationPayload;
  invited_by?: string;
}

export interface SessionTakenPayload {
  reason?: string;
}

export interface SearchUsersPayload {
  users: ProfilePayload[];
}

export interface FriendshipsUpdatedPayload {
  friends: FriendshipPayload[];
}

export interface FriendshipUpdatedPayload {
  friendship_id: string;
  status: "pending" | "accepted" | "declined";
}

export interface FriendshipRemovedPayload {
  friendship_id: string;
}

export interface ProfileUpdatedPayload {
  user: ProfilePayload;
}

export function isServerEnvelope(value: unknown): value is ServerEnvelope {
  return Boolean(
    value &&
      typeof value === "object" &&
      "type" in value &&
      typeof (value as { type?: unknown }).type === "string" &&
      "payload" in value,
  );
}

export function errorMessage(payload: unknown): string {
  if (payload && typeof payload === "object") {
    const candidate = payload as Partial<ErrorPayload>;
    if (typeof candidate.message === "string" && candidate.message.trim()) {
      return candidate.message;
    }
    if (typeof candidate.code === "string") return candidate.code;
  }
  return "O servidor retornou um erro inesperado.";
}
