"""Integration coverage for friends and persistent profile features."""

import shutil
import tempfile

import pytest

from server.core import ServerCore
from server.tests.helpers import FakeServer, make_settings
from server.tests.test_sessions import register_and_login


PNG_1X1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
PNG_DATA_URL = f"data:image/png;base64,{PNG_1X1}"


@pytest.mark.asyncio
async def test_friend_request_accept_remove_and_open_conversation():
    server = FakeServer()
    try:
        alice_cid, _ = await register_and_login(server, "alice")
        bob_cid, _ = await register_and_login(server, "bob")

        await server.core.search_users(alice_cid, "bo")
        result = server.find(alice_cid, "SEARCH_USERS_RESULT")[-1]
        assert [user["username"] for user in result["payload"]["users"]] == ["bob"]
        server.flush(alice_cid)

        await server.core.send_friend_request(alice_cid, "bob")
        friendship = server.core.store.get_friendship_between(
            server.core.store.get_user_by_username("alice")["user_id"],
            server.core.store.get_user_by_username("bob")["user_id"],
        )
        assert friendship is not None and friendship["status"] == "pending"
        assert server.find(bob_cid, "FRIENDSHIPS_UPDATED")[-1]["payload"]["friends"][0]["incoming"] is True
        server.flush(alice_cid)
        server.flush(bob_cid)

        await server.core.respond_friend_request(bob_cid, friendship["friendship_id"], "accept")
        alice_friends = server.find(alice_cid, "FRIENDSHIPS_UPDATED")[-1]["payload"]["friends"]
        assert alice_friends[0]["friendship_status"] == "accepted"
        server.flush(alice_cid)
        server.flush(bob_cid)

        await server.core.open_conversation(alice_cid, "bob")
        opened = server.find(alice_cid, "CONVERSATION_CREATED")[-1]
        assert opened["payload"]["conversation"]["is_group"] is False
        server.flush(alice_cid)
        server.flush(bob_cid)

        await server.core.remove_friend(alice_cid, friendship["friendship_id"])
        assert server.core.store.get_friendship(friendship["friendship_id"]) is None
        assert server.find(bob_cid, "FRIENDSHIPS_UPDATED")[-1]["payload"]["friends"] == []
    finally:
        server.cleanup()


@pytest.mark.asyncio
async def test_avatar_and_custom_status_persist_and_broadcast():
    tmpdir = tempfile.mkdtemp(prefix="msn_social_persist_")
    try:
        core = ServerCore(make_settings(tmpdir))
        alice_cid = "alice_conn"
        bob_cid = "bob_conn"
        core.client_connected(alice_cid)
        core.client_connected(bob_cid)
        await core.register(alice_cid, "alice", "Alice", "secret123", "alice@example.com")
        core.pending.pop(alice_cid, None)
        await core.authenticate(alice_cid, "alice", "secret123")
        core.pending.pop(alice_cid, None)
        await core.register(bob_cid, "bob", "Bob", "secret123", "bob@example.com")
        core.pending.pop(bob_cid, None)
        await core.authenticate(bob_cid, "bob", "secret123")
        core.pending.pop(bob_cid, None)

        alice_id = core.store.get_user_by_username("alice")["user_id"]
        await core.set_custom_status(alice_cid, "Em reunião")
        await core.change_status(alice_cid, "away")
        assert core.presence.get_presence(alice_id)["status"].value == "away"
        assert core.presence.get_presence(alice_id)["status_message"] == "Em reunião"
        status_notice = [event for event in core.pending.get(bob_cid, []) if event["type"] == "USER_STATUS_CHANGED"][-1]
        assert status_notice["payload"]["custom_status"] == "Em reunião"
        core.pending.pop(bob_cid, None)

        await core.set_avatar(alice_cid, PNG_DATA_URL, "avatar.png", "image/png")
        profile_notice = [event for event in core.pending.get(bob_cid, []) if event["type"] == "PROFILE_UPDATED"][-1]
        assert profile_notice["payload"]["user"]["avatar_mime"] == "image/jpeg"
        core.pending.pop(bob_cid, None)

        alice = core.store.get_user_by_username("alice")
        assert alice["custom_status"] == "Em reunião"
        assert alice["avatar_data"].startswith("data:image/jpeg;base64,")

        restarted = ServerCore(make_settings(tmpdir))
        restored = restarted.store.get_user_by_username("alice")
        assert restored["custom_status"] == "Em reunião"
        assert restored["avatar_mime"] == "image/jpeg"
        assert restored["avatar_data"].startswith("data:image/jpeg;base64,")

        core2 = ServerCore(make_settings(tmpdir))
        cid = "clear_status_conn"
        core2.client_connected(cid)
        await core2.authenticate(cid, "alice", "secret123")
        core2.pending.pop(cid, None)
        await core2.set_custom_status(cid, "")
        assert core2.store.get_user_by_username("alice")["custom_status"] == ""
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_friendship_rules_return_domain_errors():
    server = FakeServer()
    try:
        alice_cid, _ = await register_and_login(server, "alice")
        await server.core.send_friend_request(alice_cid, "alice")
        assert server.find(alice_cid, "ERROR")[-1]["payload"]["code"] == "SELF_FRIEND_REQUEST"
        server.flush(alice_cid)
        await server.core.send_friend_request(alice_cid, "missing")
        assert server.find(alice_cid, "ERROR")[-1]["payload"]["code"] == "USER_NOT_FOUND"
    finally:
        server.cleanup()


@pytest.mark.asyncio
async def test_duplicate_requests_and_invalid_avatars_are_rejected():
    server = FakeServer()
    try:
        alice_cid, _ = await register_and_login(server, "alice")
        bob_cid, _ = await register_and_login(server, "bob")

        await server.core.send_friend_request(alice_cid, "bob")
        server.flush(alice_cid)
        server.flush(bob_cid)
        await server.core.send_friend_request(alice_cid, "bob")
        assert server.find(alice_cid, "ERROR")[-1]["payload"]["code"] == "REQUEST_ALREADY_PENDING"
        server.flush(alice_cid)

        friendship = server.core.store.get_friendship_between(
            server.core.store.get_user_by_username("alice")["user_id"],
            server.core.store.get_user_by_username("bob")["user_id"],
        )
        await server.core.respond_friend_request(bob_cid, friendship["friendship_id"], "accept")
        server.flush(alice_cid)
        server.flush(bob_cid)
        await server.core.send_friend_request(alice_cid, "bob")
        assert server.find(alice_cid, "ERROR")[-1]["payload"]["code"] == "ALREADY_FRIENDS"
        server.flush(alice_cid)

        await server.core.set_avatar(alice_cid, "data:image/png;base64,not-a-real-image", "avatar.png", "image/png")
        assert server.find(alice_cid, "ERROR")[-1]["payload"]["code"] == "INVALID_AVATAR"
        server.flush(alice_cid)

        huge_data = "data:image/png;base64," + ("A" * 400_000)
        await server.core.set_avatar(alice_cid, huge_data, "avatar.png", "image/png")
        assert server.find(alice_cid, "ERROR")[-1]["payload"]["code"] == "AVATAR_TOO_LARGE"
    finally:
        server.cleanup()
