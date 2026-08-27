import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { MSN_SERVER_URL } from "@/network/config";
import {
  AuthPayload,
  RegistrationPayload,
  AttachmentPayload,
  AttachmentUploadReadyPayload,
  AttachmentUploadCompletePayload,
  MessageSearchPayload,
  MessagePinnedPayload,
  ConversationCreatedPayload,
  FriendshipPayload,
  FriendshipsUpdatedPayload,
  ProfilePayload,
  ProfileUpdatedPayload,
  SearchUsersPayload,
  ConversationPayload,
  ErrorPayload,
  errorMessage,
  HistoryPayload,
  MessageAckPayload,
  MessagePayload,
  FriendshipUpdatedPayload,
  FriendshipRemovedPayload,
  PresencePayload,
  ServerEnvelope,
  StatusChangedPayload,
  SyncDataPayload,
} from "@/network/protocol";
import {
  ConnectionState,
  MessengerWebSocket,
} from "@/network/websocket";
import { deriveConversationPresentation, deriveFriendPresence, type KnownUser } from "./presence";

export type Status = "online" | "away" | "busy" | "offline";

export interface Identity {
  userId: string;
  user_id?: string;
  username: string;
  displayName: string;
  display_name?: string;
  sessionId: string;
  avatar_data?: string | null;
  avatar_mime?: string | null;
  custom_status?: string;
}

export type Friend = FriendshipPayload;

export interface NotificationItem {
  id: string;
  kind: "message" | "connection";
  title: string;
  body: string;
  conversationId?: string;
}

export interface ChatMessage {
  id: string;
  author: "them" | "me";
  authorName: string;
  text: string;
  time: string;
  type: string;
  isPinned: boolean;
  attachment?: AttachmentPayload;
}

export interface Conversation {
  id: string;
  name: string;
  initials: string;
  status: Status;
  lastMessage: string;
  time: string;
  color: string;
  avatarData?: string | null;
  avatarMime?: string | null;
  customStatus: string;
  kind: "person" | "group";
  messages: ChatMessage[];
  participantIds: string[];
}

type PendingRequest = {
  expected: string[];
  resolve: (payload: unknown) => void;
  reject: (error: Error) => void;
};

export interface MessengerContextValue {
  session: Identity | null;
  connectionState: ConnectionState;
  serverUrl: string;
  error: string | null;
  busy: boolean;
  status: Status;
  conversations: Conversation[];
  friends: Friend[];
  presence: Record<string, PresencePayload>;
  login: (username: string, password: string) => Promise<string | null>;
  register: (username: string, displayName: string, password: string) => Promise<string>;
  resetPassword: (username: string, code: string, newPassword: string) => Promise<void>;
  logout: () => Promise<void>;
  changeStatus: (status: Status, statusMessage?: string) => Promise<void>;
  sendMessage: (conversationId: string, text: string) => Promise<void>;
  requestHistory: (conversationId: string, limit?: number) => Promise<void>;
  createGroup: (name: string, participants: string[]) => Promise<void>;
  searchUsers: (query: string) => Promise<ProfilePayload[]>;
  sendFriendRequest: (username: string) => Promise<void>;
  respondFriendRequest: (friendshipId: string, action: "accept" | "decline") => Promise<void>;
  removeFriend: (friendshipId: string) => Promise<void>;
  openConversation: (username: string) => Promise<void>;
  setAvatar: (file: File) => Promise<void>;
  setCustomStatus: (message: string) => Promise<void>;
  sendAttachment: (conversationId: string, file: File) => Promise<void>;
  searchMessages: (conversationId: string, query: string, before?: string) => Promise<MessagePayload[]>;
  pinMessage: (conversationId: string, messageId: string, pinned: boolean) => Promise<void>;
  listPinnedMessages: (conversationId: string) => Promise<MessagePayload[]>;
  updateDisplayName: (displayName: string) => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  reconnectNow: () => Promise<void>;
  notifications: NotificationItem[];
  attachmentProgress: { received: number; size: number } | null;
  dismissNotification: (id: string) => void;
  clearError: () => void;
}

const MessengerContext = createContext<MessengerContextValue | null>(null);

const palette = ["#84b9d8", "#d8b4a1", "#b7c58b", "#b7a7cb", "#d3b47e"];

function displayTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function initialsFor(name: string, fallbackId: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return `${words[0][0]}${words[1][0]}`.toUpperCase();
  if (words.length === 1 && words[0].length >= 2) return words[0].slice(0, 2).toUpperCase();
  return fallbackId.slice(0, 2).toUpperCase() || "CO";
}

function colorFor(id: string): string {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) | 0;
  return palette[Math.abs(hash) % palette.length];
}

function textFromMessage(message: MessagePayload): string {
  if (typeof message.payload?.content === "string") return message.payload.content;
  if (message.type === "attachment" && message.payload?.attachment?.original_name) {
    return `Anexo: ${message.payload.attachment.original_name}`;
  }
  return `[${message.type}]`;
}

function statusForConversation(
  conversation: ConversationPayload,
  ownUserId: string,
  presence: Record<string, PresencePayload>,
): Status {
  const otherIds = conversation.participants.filter((id) => id !== ownUserId);
  if (!otherIds.length) return "offline";
  const statuses = otherIds.map((id) => presence[id]?.status ?? "offline");
  if (statuses.includes("online")) return "online";
  if (statuses.includes("busy")) return "busy";
  if (statuses.includes("away")) return "away";
  return "offline";
}

function mapMessage(
  message: MessagePayload,
  identity: Identity,
  users: Record<string, KnownUser>,
): ChatMessage {
  const sender = users[message.sender_id];
  return {
    id: message.message_id,
    author: message.sender_id === identity.userId ? "me" : "them",
    authorName:
      message.sender_id === identity.userId
        ? identity.displayName
        : sender?.displayName || sender?.username || "Contato",
    text: textFromMessage(message),
    time: displayTime(message.timestamp),
    type: message.type,
    isPinned: Boolean(message.is_pinned),
    attachment: message.payload?.attachment,
  };
}

function mapConversation(
  conversation: ConversationPayload,
  history: MessagePayload[],
  identity: Identity,
  presence: Record<string, PresencePayload>,
  users: Record<string, KnownUser>,
): Conversation {
  const peerId = conversation.participants.find((id) => id !== identity.userId) || "";
  const peer = users[peerId];
  const name = conversation.is_group
    ? conversation.name || "Grupo sem nome"
    : peer?.displayName || peer?.username || `Contato ${peerId.slice(0, 6)}`;
  const messages = history.map((message) => mapMessage(message, identity, users));
  const last = history[history.length - 1];
  return {
    id: conversation.conversation_id,
    name,
    initials: initialsFor(name, conversation.conversation_id),
    status: statusForConversation(conversation, identity.userId, presence),
    lastMessage: last ? textFromMessage(last) : "Nenhuma mensagem ainda",
    time: last ? displayTime(last.timestamp) : displayTime(conversation.last_message_at),
    color: colorFor(conversation.conversation_id),
    avatarData: peer?.avatar_data,
    avatarMime: peer?.avatar_mime,
    customStatus: peer?.custom_status || "",
    kind: conversation.is_group ? "group" : "person",
    messages,
    participantIds: conversation.participants,
  };
}

function asError(payload: unknown): Error {
  return new Error(errorMessage(payload));
}

function prepareAvatar(file: File): Promise<{ data: string; filename: string; mime: string }> {
  return new Promise((resolve, reject) => {
    if (file.type && !file.type.startsWith("image/")) {
      reject(new Error("Escolha um arquivo de imagem compatível."));
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      reject(new Error("A imagem original deve ter no máximo 10 MB."));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Não foi possível ler a imagem."));
    reader.onload = () => {
      const image = new Image();
      image.onerror = () => reject(new Error("O arquivo não contém uma imagem válida."));
      image.onload = () => {
        if (image.naturalWidth > 10000 || image.naturalHeight > 10000) {
          reject(new Error("A imagem possui dimensões excessivas."));
          return;
        }
        const maxDimension = 512;
        const scale = Math.min(1, maxDimension / Math.max(image.naturalWidth, image.naturalHeight));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        const context = canvas.getContext("2d");
        if (!context) {
          reject(new Error("Não foi possível processar a imagem."));
          return;
        }
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        resolve({
          data: canvas.toDataURL("image/jpeg", 0.82),
          filename: "profile.jpg",
          mime: "image/jpeg",
        });
      };
      image.src = String(reader.result);
    };
    reader.readAsDataURL(file);
  });
}

export function MessengerProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Identity | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>("disconnected");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [presence, setPresence] = useState<Record<string, PresencePayload>>({});
  const [friends, setFriends] = useState<Friend[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [knownUsers, setKnownUsers] = useState<Record<string, KnownUser>>({});
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [attachmentProgress, setAttachmentProgress] = useState<{ received: number; size: number } | null>(null);

  const sessionRef = useRef<Identity | null>(null);
  const presenceRef = useRef(presence);
  const knownUsersRef = useRef(knownUsers);
  const pendingRef = useRef<PendingRequest[]>([]);
  const handleMessageRef = useRef<(message: ServerEnvelope) => void>(() => undefined);
  const connectedRef = useRef<() => Promise<void>>(async () => undefined);
  const notificationIdsRef = useRef<Set<string>>(new Set());
  const connectionNoticeRef = useRef<ConnectionState>("disconnected");

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  useEffect(() => {
    presenceRef.current = presence;
  }, [presence]);

  useEffect(() => {
    knownUsersRef.current = knownUsers;
  }, [knownUsers]);

  const pushNotification = useCallback((item: NotificationItem) => {
    setNotifications((items) => {
      if (notificationIdsRef.current.has(item.id)) return items;
      notificationIdsRef.current.add(item.id);
      const next = [item, ...items].slice(0, 5);
      if (notificationIdsRef.current.size > 100) {
        const oldest = Array.from(notificationIdsRef.current).slice(0, 50);
        oldest.forEach((id) => notificationIdsRef.current.delete(id));
      }
      return next;
    });
  }, []);

  const dismissNotification = useCallback((id: string) => {
    setNotifications((items) => items.filter((item) => item.id !== id));
  }, []);

  const client = useMemo(
    () =>
      new MessengerWebSocket({
        url: MSN_SERVER_URL,
          onStateChange: (nextState) => {
          const previousState = connectionNoticeRef.current;
          connectionNoticeRef.current = nextState;
          setConnectionState(nextState);
          if (sessionRef.current && nextState === "disconnected" && previousState !== "disconnected") {
            pushNotification({ id: `connection-lost-${Date.now()}`, kind: "connection", title: "Conexão interrompida", body: "O Hub tentará reconectar automaticamente." });
          }
          if (sessionRef.current && nextState === "connected" && previousState !== "connected") {
            pushNotification({ id: `connection-restored-${Date.now()}`, kind: "connection", title: "Conexão ativa", body: "O Messenger voltou a sincronizar com o servidor." });
          }
          if (nextState === "disconnected" && sessionRef.current) {
            const disconnectError = new Error("A conexão com o servidor foi encerrada.");
            pendingRef.current.splice(0).forEach((pending) => pending.reject(disconnectError));
          }
        },
        onMessage: (message) => handleMessageRef.current(message),
        onConnected: () => void connectedRef.current(),
        getSessionId: () => sessionRef.current?.sessionId ?? null,
        onError: (networkError) => {
          if (sessionRef.current) {
            setError("A conexão com o servidor foi interrompida. Tentando reconectar...");
          }
        },
      }),
    [],
  );

  useEffect(() => () => client.close(), [client]);

  const clearSession = useCallback(() => {
    sessionRef.current = null;
    setSession(null);
    setPresence({});
    presenceRef.current = {};
    setFriends([]);
    setConversations([]);
    setAttachmentProgress(null);
    setNotifications([]);
    notificationIdsRef.current.clear();
  }, []);

  const request = useCallback(
    <T,>(expected: string[], command: Parameters<MessengerWebSocket["send"]>[0], fields: Record<string, unknown> = {}) =>
      new Promise<T>((resolve, reject) => {
        pendingRef.current.push({
          expected,
          resolve: (payload) => resolve(payload as T),
          reject,
        });
        try {
          client.send(command, fields);
        } catch (requestError) {
          pendingRef.current = pendingRef.current.filter((item) => item.reject !== reject);
          reject(asError(requestError));
        }
      }),
    [client],
  );

  const applySync = useCallback((payload: SyncDataPayload) => {
    const identityData = payload.data.identity;
    const currentSession = sessionRef.current;
    if (!currentSession) return;
    const identity: Identity = {
      ...currentSession,
      userId: identityData.user_id,
      username: identityData.username,
      displayName: identityData.display_name,
      avatar_data: identityData.avatar_data,
      avatar_mime: identityData.avatar_mime,
      custom_status: identityData.custom_status || "",
      sessionId: payload.data.session.session_id,
    };
    const nextPresence = payload.data.presence || {};
    const nextUsers: Record<string, KnownUser> = {
      ...knownUsersRef.current,
      [identity.userId]: {
        userId: identity.userId,
        username: identity.username,
        displayName: identity.displayName,
        avatar_data: identity.avatar_data,
        avatar_mime: identity.avatar_mime,
        custom_status: identity.custom_status,
      },
    };
    for (const [userId, userPresence] of Object.entries(nextPresence)) {
      if (userPresence.username && userPresence.display_name) {
        nextUsers[userId] = {
          userId,
          username: userPresence.username,
          displayName: userPresence.display_name,
          avatar_data: userPresence.avatar_data,
          avatar_mime: userPresence.avatar_mime,
          custom_status: userPresence.custom_status || "",
        };
      }
    }
    const nextConversations = payload.data.conversations.map((conversation) =>
      mapConversation(
        conversation,
        payload.data.history[conversation.conversation_id] || [],
        identity,
        nextPresence,
        nextUsers,
      ),
    );
    sessionRef.current = identity;
    setSession(identity);
    setKnownUsers(nextUsers);
    knownUsersRef.current = nextUsers;
    setPresence(nextPresence);
    presenceRef.current = nextPresence;
    setFriends(payload.data.friends || []);
    setConversations(nextConversations);
  }, []);

  const mergeHistory = useCallback((conversationId: string, messages: MessagePayload[], replace = true) => {
    const identity = sessionRef.current;
    if (!identity) return;
    setConversations((items) =>
      items.map((conversation) => {
        if (conversation.id !== conversationId) return conversation;
        if (!replace) {
          const incoming = messages.map((message) =>
            mapMessage(message, identity, knownUsersRef.current),
          );
          const existingIds = new Set(conversation.messages.map((message) => message.id));
          const nextMessages = [
            ...conversation.messages,
            ...incoming.filter((message) => !existingIds.has(message.id)),
          ];
          const last = nextMessages[nextMessages.length - 1];
          return {
            ...conversation,
            customStatus: conversation.customStatus,
            messages: nextMessages,
            lastMessage: last?.text || conversation.lastMessage,
            time: last?.time || conversation.time,
          };
        }
        const mapped = messages.map((message) =>
          mapMessage(message, identity, knownUsersRef.current),
        );
        const last = mapped[mapped.length - 1];
        return {
          ...conversation,
          messages: mapped,
          lastMessage: last?.text || conversation.lastMessage,
          time: last?.time || conversation.time,
        };
      }),
    );
  }, []);

  const updatePinnedMessage = useCallback((payload: MessagePinnedPayload) => {
    setConversations((items) => items.map((conversation) => {
      if (conversation.id !== payload.conversation_id) return conversation;
      return {
        ...conversation,
        messages: conversation.messages.map((message) => message.id === payload.message.message_id
          ? { ...message, isPinned: payload.is_pinned }
          : message),
      };
    }));
  }, []);

  const addConversation = useCallback((payload: ConversationCreatedPayload) => {
    const identity = sessionRef.current;
    if (!identity) return;
    setConversations((items) => {
      if (items.some((item) => item.id === payload.conversation.conversation_id)) return items;
      return [
        mapConversation(
          payload.conversation,
          [],
          identity,
          presenceRef.current,
          knownUsersRef.current,
        ),
        ...items,
      ];
    });
  }, []);

  const handleMessage = useCallback(
    (message: ServerEnvelope) => {
      const payload = message.payload;
      if (message.type === "ERROR") {
        const errorPayload = payload as ErrorPayload;
        const pending = pendingRef.current.shift();
        const messageError = asError(payload);
        if (pending) pending.reject(messageError);
        setError(messageError.message);
        if (errorPayload.code === "RECONNECT_INVALID") {
          client.setAutoReconnect(false);
          client.close();
          clearSession();
        }
        return;
      }

      switch (message.type) {
        case "AUTH_OK": {
          const auth = payload as AuthPayload;
          const nextSession: Identity = {
            sessionId: auth.session_id,
            userId: auth.user_id,
            username: auth.username,
            displayName: auth.display_name,
          };
          sessionRef.current = nextSession;
          setSession(nextSession);
          break;
        }
        case "SYNC_DATA":
          applySync(payload as SyncDataPayload);
          break;
        case "USER_STATUS_CHANGED": {
          const status = payload as StatusChangedPayload;
          const nextPresence = {
            ...presenceRef.current,
            [status.user_id]: {
              ...presenceRef.current[status.user_id],
              status: status.status,
              status_message: status.status_message,
              username: status.username,
              display_name: status.display_name,
              custom_status: status.custom_status || "",
              avatar_data: status.avatar_data,
              avatar_mime: status.avatar_mime,
            },
          };
          const nextUsers = {
            ...knownUsersRef.current,
            [status.user_id]: {
              userId: status.user_id,
              username: status.username,
              displayName: status.display_name,
              avatar_data: status.avatar_data,
              avatar_mime: status.avatar_mime,
              custom_status: status.custom_status || "",
            },
          };
          presenceRef.current = nextPresence;
          knownUsersRef.current = nextUsers;
          setPresence(nextPresence);
          setKnownUsers(nextUsers);
          if (sessionRef.current?.userId === status.user_id) {
            setSession((current) => current ? { ...current, custom_status: status.custom_status || "", avatar_data: status.avatar_data, avatar_mime: status.avatar_mime } : current);
          }
          break;
        }
        case "FRIENDSHIPS_UPDATED": {
          const friendships = payload as FriendshipsUpdatedPayload;
          setFriends(friendships.friends || []);
          const friendUsers = (friendships.friends || []).reduce<Record<string, KnownUser>>((users, friend) => ({
            ...users,
            [friend.user_id]: {
              userId: friend.user_id,
              username: friend.username,
              displayName: friend.display_name,
              avatar_data: friend.avatar_data,
              avatar_mime: friend.avatar_mime,
              custom_status: friend.custom_status || "",
            },
          }), {});
          const mergedUsers = { ...knownUsersRef.current, ...friendUsers };
          knownUsersRef.current = mergedUsers;
          setKnownUsers(mergedUsers);
          break;
        }
        case "ATTACHMENT_UPLOAD_PROGRESS": {
          const progress = payload as { upload_id: string; received: number; size: number };
          setAttachmentProgress({ received: progress.received, size: progress.size });
          break;
        }
        case "MESSAGE_PINNED":
          updatePinnedMessage(payload as MessagePinnedPayload);
          break;
        case "FRIENDSHIP_REMOVED": {
          const removed = payload as FriendshipRemovedPayload;
          setFriends((items) => items.filter((friend) => friend.friendship_id !== removed.friendship_id));
          break;
        }
        case "PROFILE_UPDATED": {
          const updated = payload as ProfileUpdatedPayload;
          const user = updated.user;
          const nextPresence = {
            ...presenceRef.current,
            [user.user_id]: {
              ...presenceRef.current[user.user_id],
              username: user.username,
              display_name: user.display_name,
              avatar_data: user.avatar_data ?? null,
              avatar_mime: user.avatar_mime ?? null,
              custom_status: user.custom_status || "",
            },
          };
          const nextUsers = {
            ...knownUsersRef.current,
            [user.user_id]: {
              userId: user.user_id,
              username: user.username,
              displayName: user.display_name,
              avatar_data: user.avatar_data ?? null,
              avatar_mime: user.avatar_mime ?? null,
              custom_status: user.custom_status || "",
            },
          };
          presenceRef.current = nextPresence;
          knownUsersRef.current = nextUsers;
          setPresence(nextPresence);
          setKnownUsers(nextUsers);
          setFriends((items) => items.map((friend) => friend.user_id === user.user_id
            ? { ...friend, display_name: user.display_name, avatar_data: user.avatar_data ?? null, avatar_mime: user.avatar_mime ?? null, custom_status: user.custom_status || "" }
            : friend));
          if (sessionRef.current?.userId === user.user_id) {
            const nextSession = { ...sessionRef.current, displayName: user.display_name, avatar_data: user.avatar_data ?? null, avatar_mime: user.avatar_mime ?? null, custom_status: user.custom_status || "" };
            sessionRef.current = nextSession;
            setSession(nextSession);
          }
          break;
        }
        case "MESSAGE": {
          const liveMessage = payload as { message: MessagePayload };
          mergeHistory(liveMessage.message.conversation_id, [liveMessage.message], false);
          if (liveMessage.message.sender_id !== sessionRef.current?.userId) {
            const sender = knownUsersRef.current[liveMessage.message.sender_id];
            pushNotification({
              id: `message-${liveMessage.message.message_id}`,
              kind: "message",
              title: sender?.displayName || sender?.username || "Nova mensagem",
              body: textFromMessage(liveMessage.message),
              conversationId: liveMessage.message.conversation_id,
            });
          }
          break;
        }
        case "HISTORY": {
          const history = payload as HistoryPayload;
          mergeHistory(history.conversation_id, history.messages, true);
          break;
        }
        case "CONVERSATION_CREATED":
          addConversation(payload as ConversationCreatedPayload);
          break;
        case "SESSION_TAKEN":
          setError("Esta sessão foi assumida por outra conexão. Faça login novamente.");
          pendingRef.current.splice(0).forEach((pending) => pending.reject(new Error("Sessão assumida por outra conexão.")));
          client.setAutoReconnect(false);
          client.close();
          clearSession();
          break;
        default:
          break;
      }

      const pending = pendingRef.current[0];
      if (pending && pending.expected.includes(message.type)) {
        pendingRef.current.shift();
        pending.resolve(payload);
      }
    },
    [addConversation, applySync, clearSession, client, mergeHistory, pushNotification, updatePinnedMessage],
  );

  handleMessageRef.current = handleMessage;

  const connectAndLogin = useCallback(
    async (username: string, password: string): Promise<string | null> => {
      client.setAutoReconnect(false);
      setError(null);
      await client.connect();
      const auth = await request<AuthPayload>(["AUTH_OK"], "LOGIN", {
        username,
        password,
      });
      const nextSession: Identity = {
        sessionId: auth.session_id,
        userId: auth.user_id,
        username: auth.username,
        displayName: auth.display_name,
      };
      sessionRef.current = nextSession;
      setSession(nextSession);
      client.setAutoReconnect(true);
      await request<SyncDataPayload>(["SYNC_DATA"], "REQUEST_SYNC");
      return auth.recovery_code || null;
    },
    [client, request],
  );

  const connectedWithSession = useCallback(async () => {
    if (!sessionRef.current || pendingRef.current.length > 0) return;
    try {
      await request(["RECONNECT_OK"], "RECONNECT", {
        session_id: sessionRef.current.sessionId,
      });
      await request<SyncDataPayload>(["SYNC_DATA"], "REQUEST_SYNC");
      setError(null);
    } catch (reconnectError) {
      setError(asError(reconnectError).message);
      client.setAutoReconnect(false);
      clearSession();
    }
  }, [clearSession, client, request]);

  connectedRef.current = connectedWithSession;

  const login = useCallback(
    async (username: string, password: string): Promise<string | null> => {
      setBusy(true);
      try {
        return await connectAndLogin(username.trim(), password);
      } catch (loginError) {
        client.setAutoReconnect(false);
        client.close();
        clearSession();
        throw asError(loginError);
      } finally {
        setBusy(false);
      }
    },
    [clearSession, client, connectAndLogin],
  );

  const register = useCallback(
    async (username: string, displayName: string, password: string): Promise<string> => {
      setBusy(true);
      try {
        client.setAutoReconnect(false);
        await client.connect();
        const registration = await request<RegistrationPayload>(["REGISTER_OK"], "REGISTER", {
          username: username.trim(),
          display_name: displayName.trim(),
          password,
        });
        await connectAndLogin(username.trim(), password);
        return registration.recovery_code;
      } catch (registrationError) {
        client.setAutoReconnect(false);
        client.close();
        clearSession();
        throw asError(registrationError);
      } finally {
        setBusy(false);
      }
    },
    [clearSession, client, connectAndLogin, request],
  );

  const resetPassword = useCallback(async (username: string, code: string, newPassword: string) => {
    setBusy(true);
    setError(null);
    try {
      client.setAutoReconnect(false);
      await client.connect();
      await request(["PASSWORD_RESET_OK"], "RESET_PASSWORD", {
        username: username.trim(),
        code: code.trim(),
        new_password: newPassword,
      });
      client.close();
      clearSession();
    } catch (resetError) {
      setError(asError(resetError).message);
      throw asError(resetError);
    } finally {
      setBusy(false);
    }
  }, [clearSession, client, request]);

  const logout = useCallback(async () => {
    setBusy(true);
    try {
      await request(["LOGOUT_OK"], "LOGOUT");
      client.setAutoReconnect(false);
      client.close();
      clearSession();
    } catch (logoutError) {
      setError(asError(logoutError).message);
      throw asError(logoutError);
    } finally {
      setBusy(false);
    }
  }, [clearSession, client, request]);

  const changeStatus = useCallback(
    async (nextStatus: Status, statusMessage = "") => {
      setError(null);
      try {
        await request(["USER_STATUS_CHANGED"], "CHANGE_STATUS", {
          status: nextStatus,
          status_message: statusMessage,
        });
      } catch (statusError) {
        setError(asError(statusError).message);
        throw asError(statusError);
      }
    },
    [request],
  );

  const requestHistory = useCallback(
    async (conversationId: string, limit = 100) => {
      const history = await request<HistoryPayload>(["HISTORY"], "GET_HISTORY", {
        conversation_id: conversationId,
        limit,
      });
      mergeHistory(history.conversation_id, history.messages, true);
    },
    [mergeHistory, request],
  );

  const sendMessage = useCallback(
    async (conversationId: string, text: string) => {
      const messageId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
      try {
        await request<MessageAckPayload>(["MESSAGE_ACK"], "SEND_MESSAGE", {
          conversation_id: conversationId,
          type: "text",
          payload: { content: text },
          message_id: messageId,
        });
        // MESSAGE_ACK deliberately contains no client-authored content. Read
        // the persisted history so the UI renders the server-confirmed row.
        await requestHistory(conversationId, 100);
      } catch (messageError) {
        setError(asError(messageError).message);
        throw asError(messageError);
      }
    },
    [request, requestHistory],
  );

  const sendAttachment = useCallback(
    async (conversationId: string, file: File) => {
      try {
        const ready = await request<AttachmentUploadReadyPayload>(["ATTACHMENT_UPLOAD_READY"], "BEGIN_ATTACHMENT_UPLOAD", {
          conversation_id: conversationId,
          filename: file.name,
          mime: file.type || "application/octet-stream",
          size: file.size,
        });
        for (let offset = 0; offset < file.size; offset += ready.chunk_size) {
          client.sendBinary(file.slice(offset, Math.min(file.size, offset + ready.chunk_size)));
        }
        await request<AttachmentUploadCompletePayload>(["ATTACHMENT_UPLOAD_COMPLETE"], "FINISH_ATTACHMENT_UPLOAD", {
          upload_id: ready.upload_id,
        });
        setAttachmentProgress(null);
        await requestHistory(conversationId, 100);
      } catch (attachmentError) {
        setAttachmentProgress(null);
        setError(asError(attachmentError).message);
        throw asError(attachmentError);
      }
    },
    [client, request, requestHistory],
  );

  const searchMessages = useCallback(async (conversationId: string, query: string, before?: string) => {
    try {
      const result = await request<MessageSearchPayload>(["MESSAGE_SEARCH_RESULT"], "SEARCH_MESSAGES", {
        conversation_id: conversationId,
        query,
        limit: 100,
        ...(before ? { before } : {}),
      });
      return result.messages;
    } catch (searchError) {
      setError(asError(searchError).message);
      throw asError(searchError);
    }
  }, [request]);

  const listPinnedMessages = useCallback(async (conversationId: string) => {
    try {
      const result = await request<{ conversation_id: string; messages: MessagePayload[] }>(["PINNED_MESSAGES"], "LIST_PINNED_MESSAGES", { conversation_id: conversationId });
      return result.messages;
    } catch (pinError) {
      setError(asError(pinError).message);
      throw asError(pinError);
    }
  }, [request]);

  const pinMessage = useCallback(async (conversationId: string, messageId: string, pinned: boolean) => {
    try {
      await request<MessagePinnedPayload>(["MESSAGE_PINNED"], pinned ? "PIN_MESSAGE" : "UNPIN_MESSAGE", {
        conversation_id: conversationId,
        message_id: messageId,
      });
    } catch (pinError) {
      setError(asError(pinError).message);
      throw asError(pinError);
    }
  }, [request]);

  const updateDisplayName = useCallback(async (displayName: string) => {
    try {
      await request<ProfileUpdatedPayload>(["PROFILE_UPDATED"], "UPDATE_PROFILE", { display_name: displayName });
    } catch (profileError) {
      setError(asError(profileError).message);
      throw asError(profileError);
    }
  }, [request]);

  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    try {
      await request(["PASSWORD_CHANGED"], "CHANGE_PASSWORD", { current_password: currentPassword, new_password: newPassword });
    } catch (passwordError) {
      setError(asError(passwordError).message);
      throw asError(passwordError);
    }
  }, [request]);

  const createGroup = useCallback(
    async (name: string, participants: string[]) => {
      try {
        await request<ConversationCreatedPayload>(["CONVERSATION_CREATED"], "CREATE_GROUP", {
          name,
          participants,
        });
      } catch (groupError) {
        setError(asError(groupError).message);
        throw asError(groupError);
      }
    },
    [request],
  );

  const searchUsers = useCallback(async (query: string) => {
    try {
      const result = await request<SearchUsersPayload>(["SEARCH_USERS_RESULT"], "SEARCH_USERS", { query });
      const searchedUsers = result.users.reduce<Record<string, KnownUser>>((users, user) => ({
        ...users,
        [user.user_id]: {
          userId: user.user_id,
          username: user.username,
          displayName: user.display_name,
          avatar_data: user.avatar_data,
          avatar_mime: user.avatar_mime,
          custom_status: user.custom_status || "",
        },
      }), {});
      const mergedUsers = { ...knownUsersRef.current, ...searchedUsers };
      knownUsersRef.current = mergedUsers;
      setKnownUsers(mergedUsers);
      return result.users;
    } catch (searchError) {
      setError(asError(searchError).message);
      throw asError(searchError);
    }
  }, [request]);

  const sendFriendRequest = useCallback(async (username: string) => {
    try {
      await request<FriendshipUpdatedPayload>(["FRIENDSHIP_UPDATED"], "SEND_FRIEND_REQUEST", { username });
    } catch (friendError) {
      setError(asError(friendError).message);
      throw asError(friendError);
    }
  }, [request]);

  const respondFriendRequest = useCallback(async (friendshipId: string, action: "accept" | "decline") => {
    try {
      await request<FriendshipUpdatedPayload>(["FRIENDSHIP_UPDATED"], "RESPOND_FRIEND_REQUEST", { friendship_id: friendshipId, action });
    } catch (friendError) {
      setError(asError(friendError).message);
      throw asError(friendError);
    }
  }, [request]);

  const removeFriend = useCallback(async (friendshipId: string) => {
    try {
      await request<FriendshipRemovedPayload>(["FRIENDSHIP_REMOVED"], "REMOVE_FRIEND", { friendship_id: friendshipId });
    } catch (friendError) {
      setError(asError(friendError).message);
      throw asError(friendError);
    }
  }, [request]);

  const openConversation = useCallback(async (username: string) => {
    try {
      await request<ConversationCreatedPayload>(["CONVERSATION_CREATED"], "OPEN_CONVERSATION", { username });
    } catch (conversationError) {
      setError(asError(conversationError).message);
      throw asError(conversationError);
    }
  }, [request]);

  const setAvatar = useCallback(async (file: File) => {
    try {
      const prepared = await prepareAvatar(file);
      await request<ProfileUpdatedPayload>(["PROFILE_UPDATED"], "SET_AVATAR", prepared);
    } catch (avatarError) {
      setError(asError(avatarError).message);
      throw asError(avatarError);
    }
  }, [request]);

  const setCustomStatus = useCallback(async (message: string) => {
    try {
      await request<StatusChangedPayload>(["USER_STATUS_CHANGED"], "SET_CUSTOM_STATUS", { message });
    } catch (statusError) {
      setError(asError(statusError).message);
      throw asError(statusError);
    }
  }, [request]);

  const reconnectNow = useCallback(async () => {
    if (!sessionRef.current) return;
    client.setAutoReconnect(true);
    try {
      await client.connect();
    } catch (connectionError) {
      setError(asError(connectionError).message);
    }
  }, [client]);

  const visibleFriends = useMemo(
    () => friends.map((friend) => deriveFriendPresence(friend, presence)),
    [friends, presence],
  );

  const visibleConversations = useMemo(
    () => conversations.map((conversation) => {
      const payload: ConversationPayload = {
        conversation_id: conversation.id,
        name: conversation.kind === "group" ? conversation.name : null,
        is_group: conversation.kind === "group",
        participants: conversation.participantIds,
        created_at: "",
        last_message_at: null,
      };
      const presented = deriveConversationPresentation(
        payload,
        session?.userId || "",
        presence,
        knownUsers,
      );
      return {
        ...conversation,
        name: presented.name,
        initials: initialsFor(presented.name, conversation.id),
        status: presented.status,
        customStatus: presented.customStatus,
        avatarData: presented.avatarData,
        avatarMime: presented.avatarMime,
      };
    }),
    [conversations, knownUsers, presence, session?.userId],
  );

  const value = useMemo<MessengerContextValue>(
    () => ({
      session,
      connectionState,
      serverUrl: MSN_SERVER_URL,
      error,
      busy,
      status: session ? presence[session.userId]?.status || "online" : "offline",
      conversations: visibleConversations,
      friends: visibleFriends,
      presence,
      login,
      register,
      resetPassword,
      logout,
      changeStatus,
      sendMessage,
      requestHistory,
      createGroup,
      searchUsers,
      sendFriendRequest,
      respondFriendRequest,
      removeFriend,
      openConversation,
      setAvatar,
      setCustomStatus,
      sendAttachment,
      searchMessages,
      pinMessage,
      listPinnedMessages,
      updateDisplayName,
      changePassword,
      reconnectNow,
      dismissNotification,
      notifications,
      attachmentProgress,
      clearError: () => setError(null),
    }),
    [
      busy,
      changeStatus,
      connectionState,
      conversations,
      createGroup,
      friends,
      error,
      login,
      logout,
      resetPassword,
      presence,
      reconnectNow,
      visibleConversations,
      visibleFriends,
      register,
      requestHistory,
      searchUsers,
      sendFriendRequest,
      respondFriendRequest,
      removeFriend,
      openConversation,
      setAvatar,
      setCustomStatus,
      sendMessage,
      sendAttachment,
      searchMessages,
      pinMessage,
      listPinnedMessages,
      updateDisplayName,
      changePassword,
      session,
      dismissNotification,
      notifications,
      attachmentProgress,
    ],
  );

  return <MessengerContext.Provider value={value}>{children}</MessengerContext.Provider>;
}

export function useMessenger(): MessengerContextValue {
  const value = useContext(MessengerContext);
  if (!value) throw new Error("useMessenger precisa estar dentro de MessengerProvider.");
  return value;
}
