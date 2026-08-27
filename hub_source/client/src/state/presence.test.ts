import { describe, expect, it } from "vitest";
import type { ConversationPayload, FriendshipPayload, PresencePayload } from "@/network/protocol";
import { deriveConversationPresentation, deriveFriendPresence, type KnownUser } from "./presence";

const identity = (userId: string, username: string, displayName: string, avatarData: string | null): KnownUser => ({
  userId,
  username,
  displayName,
  avatar_data: avatarData,
  avatar_mime: avatarData ? "image/jpeg" : null,
  custom_status: "",
});

const presenceFor = (
  user: KnownUser,
  status: PresencePayload["status"],
): PresencePayload => ({
  status,
  status_message: "",
  username: user.username,
  display_name: user.displayName,
  avatar_data: user.avatar_data,
  avatar_mime: user.avatar_mime,
  custom_status: user.custom_status || "",
});

const friendFor = (user: KnownUser): FriendshipPayload => ({
  user_id: user.userId,
  username: user.username,
  display_name: user.displayName,
  avatar_data: user.avatar_data,
  avatar_mime: user.avatar_mime,
  custom_status: "",
  friendship_id: "friendship-1",
  friendship_status: "accepted",
  requested_by: "user-a",
  incoming: false,
  status: "offline",
  status_message: "",
  created_at: "",
  updated_at: "",
});

const privateConversation = (a: KnownUser, b: KnownUser): ConversationPayload => ({
  conversation_id: `conversation-${a.userId}-${b.userId}`,
  name: null,
  is_group: false,
  participants: [a.userId, b.userId],
  created_at: "",
  last_message_at: null,
});

describe("derivação de identidade e presença", () => {
  it("mantém a lista de amigos e o cabeçalho da conversa no mesmo status", () => {
    const alice = identity("a", "alice", "Alice", null);
    const bob = identity("b", "bob", "Bob", "data:image/jpeg;base64,bob");
    const presence: Record<string, PresencePayload> = {
      [alice.userId]: presenceFor(alice, "online"),
      [bob.userId]: presenceFor(bob, "online"),
    };
    const friends = deriveFriendPresence(friendFor(bob), presence);
    const conversation = deriveConversationPresentation(
      privateConversation(alice, bob),
      alice.userId,
      presence,
      { [alice.userId]: alice, [bob.userId]: bob },
    );

    expect(friends.status).toBe("online");
    expect(conversation.status).toBe("online");

    presence[bob.userId] = presenceFor(bob, "offline");
    const offlineFriend = deriveFriendPresence(friendFor(bob), presence);
    const offlineConversation = deriveConversationPresentation(
      privateConversation(alice, bob),
      alice.userId,
      presence,
      { [alice.userId]: alice, [bob.userId]: bob },
    );
    expect(offlineFriend.status).toBe("offline");
    expect(offlineConversation.status).toBe("offline");

    presence[bob.userId] = presenceFor(bob, "online");
    expect(deriveFriendPresence(friendFor(bob), presence).status).toBe("online");
    expect(deriveConversationPresentation(privateConversation(alice, bob), alice.userId, presence, { [alice.userId]: alice, [bob.userId]: bob }).status).toBe("online");
  });

  it("não deixa o avatar de B contaminar A quando qualquer usuário atualiza o perfil", () => {
    const alice = identity("a", "alice", "Alice", null);
    const bob = identity("b", "bob", "Bob", "data:image/jpeg;base64,bob-x");
    const knownUsers = { [alice.userId]: alice, [bob.userId]: bob };
    const presence: Record<string, PresencePayload> = {
      [alice.userId]: presenceFor(alice, "online"),
      [bob.userId]: presenceFor(bob, "online"),
    };

    const aliceViewOfBob = deriveConversationPresentation(privateConversation(alice, bob), alice.userId, presence, knownUsers);
    const bobViewOfAlice = deriveConversationPresentation(privateConversation(alice, bob), bob.userId, presence, knownUsers);
    expect(aliceViewOfBob.avatarData).toBe("data:image/jpeg;base64,bob-x");
    expect(bobViewOfAlice.avatarData).toBeNull();

    presence[bob.userId] = { ...presence[bob.userId], avatar_data: "data:image/jpeg;base64,bob-y", avatar_mime: "image/jpeg" };
    const bobAfterUpdate = deriveConversationPresentation(privateConversation(alice, bob), alice.userId, presence, knownUsers);
    const aliceStillWithoutAvatar = deriveConversationPresentation(privateConversation(alice, bob), bob.userId, presence, knownUsers);
    expect(bobAfterUpdate.avatarData).toBe("data:image/jpeg;base64,bob-y");
    expect(aliceStillWithoutAvatar.avatarData).toBeNull();

    presence[alice.userId] = { ...presence[alice.userId], avatar_data: "data:image/jpeg;base64,alice-z", avatar_mime: "image/jpeg" };
    const bobStillHasOwnAvatar = deriveConversationPresentation(privateConversation(alice, bob), alice.userId, presence, knownUsers);
    const aliceAfterUpdate = deriveConversationPresentation(privateConversation(alice, bob), bob.userId, presence, knownUsers);
    expect(bobStillHasOwnAvatar.avatarData).toBe("data:image/jpeg;base64,bob-y");
    expect(aliceAfterUpdate.avatarData).toBe("data:image/jpeg;base64,alice-z");
  });

  it("não usa avatar de participante para representar uma conversa em grupo", () => {
    const alice = identity("a", "alice", "Alice", null);
    const bob = identity("b", "bob", "Bob", "data:image/jpeg;base64,bob");
    const group: ConversationPayload = {
      conversation_id: "group-1",
      name: "Grupo",
      is_group: true,
      participants: [alice.userId, bob.userId],
      created_at: "",
      last_message_at: null,
    };
    const pollutedGroup = { ...group, avatarData: "data:image/jpeg;base64,stale-bob", avatarMime: "image/jpeg" };
    const presentation = deriveConversationPresentation(pollutedGroup, alice.userId, {
      [alice.userId]: presenceFor(alice, "online"),
      [bob.userId]: presenceFor(bob, "online"),
    }, { [alice.userId]: alice, [bob.userId]: bob });
    expect(presentation.avatarData).toBeNull();
    expect(presentation.avatarMime).toBeNull();
  });
});
