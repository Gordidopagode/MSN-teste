from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from server.attachments.manager import AttachmentError, AttachmentManager
from server.core import ServerCore
from server.network.protocol import ProtocolError, parse_client_message
from server.tests.helpers import FakeServer, make_settings
from server.tests.test_sessions import register_and_login


@pytest.fixture
def server():
    value = FakeServer()
    yield value
    value.cleanup()


@pytest.mark.asyncio
async def test_forged_attachment_command_is_rejected(server: FakeServer):
    cid, _ = await register_and_login(server, "forger")
    user_id = server.core.store.get_user_by_username("forger")["user_id"]
    target_cid, _ = await register_and_login(server, "target")
    target_id = server.core.store.get_user_by_username("target")["user_id"]
    conversation = server.core.conversations.get_or_create_individual(user_id, target_id)

    await server.core.send_message(
        cid,
        conversation.conversation_id,
        "attachment",
        {"attachment": {"attachment_id": "fake", "original_name": "x.txt", "mime_type": "text/plain", "size": 1}},
    )
    assert any(item["payload"]["code"] == "MESSAGE_FAILED" for item in server.find(cid, "ERROR"))
    assert server.core.store.list_conversation_messages(conversation.conversation_id) == []

    with pytest.raises(ProtocolError):
        parse_client_message({
            "command": "SEND_MESSAGE",
            "conversation_id": conversation.conversation_id,
            "type": "attachment",
            "payload": {},
        })
    assert target_cid


@pytest.mark.asyncio
async def test_attachment_upload_is_chunked_persistent_and_recipient_scoped(server: FakeServer):
    sender_cid, _ = await register_and_login(server, "sender")
    recipient_cid, _ = await register_and_login(server, "recipient")
    sender_id = server.core.store.get_user_by_username("sender")["user_id"]
    recipient_id = server.core.store.get_user_by_username("recipient")["user_id"]
    conversation = server.core.conversations.get_or_create_individual(sender_id, recipient_id)

    await server.core.begin_attachment_upload(
        sender_cid, conversation.conversation_id, "../notes.txt", "text/plain", 11
    )
    ready = server.find(sender_cid, "ATTACHMENT_UPLOAD_READY")[0]["payload"]
    server.flush(sender_cid)
    await server.core.receive_attachment_chunk(sender_cid, b"hello ")
    await server.core.receive_attachment_chunk(sender_cid, b"world")
    await server.core.finish_attachment_upload(sender_cid, ready["upload_id"])

    complete = server.find(sender_cid, "ATTACHMENT_UPLOAD_COMPLETE")[0]["payload"]
    attachment = complete["attachment"]
    assert attachment["original_name"] == "notes.txt"
    assert attachment["mime_type"] == "text/plain"
    assert attachment["size"] == 11
    assert attachment["download_url"]
    assert len(server.core.store.list_conversation_messages(conversation.conversation_id)) == 1

    delivered = [item for item in server.pending_for(recipient_cid) if item["type"] == "MESSAGE"]
    assert len(delivered) == 1
    recipient_attachment = delivered[0]["payload"]["message"]["payload"]["attachment"]
    assert recipient_attachment["download_url"]
    sender_query = parse_qs(urlsplit(attachment["download_url"]).query)
    recipient_query = parse_qs(urlsplit(recipient_attachment["download_url"]).query)
    assert sender_query["user"] == [sender_id]
    assert recipient_query["user"] == [recipient_id]
    assert sender_query["sig"] != recipient_query["sig"]

    stored = server.core.attachments.get_attachment(attachment["attachment_id"])
    assert stored is not None
    with server.core.attachments.open_attachment(stored) as handle:
        assert handle.read() == b"hello world"

    restarted = ServerCore(make_settings(server.tmpdir, port=10001))
    persisted = restarted.attachments.get_attachment(attachment["attachment_id"])
    assert persisted is not None
    with restarted.attachments.open_attachment(persisted) as handle:
        assert handle.read() == b"hello world"


@pytest.mark.asyncio
async def test_attachment_limits_and_path_traversal_are_enforced(server: FakeServer):
    sender_cid, _ = await register_and_login(server, "limits")
    recipient_cid, _ = await register_and_login(server, "receiver")
    sender_id = server.core.store.get_user_by_username("limits")["user_id"]
    recipient_id = server.core.store.get_user_by_username("receiver")["user_id"]
    conversation = server.core.conversations.get_or_create_individual(sender_id, recipient_id)

    await server.core.begin_attachment_upload(
        sender_cid, conversation.conversation_id, "bad.exe", "application/x-msdownload", 4
    )
    assert any(item["payload"]["code"] == "ATTACHMENT_MIME_NOT_ALLOWED" for item in server.find(sender_cid, "ERROR"))
    server.flush(sender_cid)
    await server.core.begin_attachment_upload(
        sender_cid, conversation.conversation_id, "large.txt", "text/plain", server.settings.attachment_max_bytes + 1
    )
    assert any(item["payload"]["code"] == "ATTACHMENT_TOO_LARGE" for item in server.find(sender_cid, "ERROR"))

    manager = server.core.attachments
    with pytest.raises(AttachmentError):
        manager.open_attachment({"storage_ref": "../msn_server.db"})
    assert recipient_id


@pytest.mark.asyncio
async def test_server_search_and_pins_are_authorized_and_idempotent(server: FakeServer):
    a_cid, _ = await register_and_login(server, "searcher")
    b_cid, _ = await register_and_login(server, "reader")
    outsider_cid, _ = await register_and_login(server, "outsider")
    a_id = server.core.store.get_user_by_username("searcher")["user_id"]
    b_id = server.core.store.get_user_by_username("reader")["user_id"]
    outsider_id = server.core.store.get_user_by_username("outsider")["user_id"]
    conversation = server.core.conversations.get_or_create_individual(a_id, b_id)

    await server.core.send_message(a_cid, conversation.conversation_id, "text", {"content": "alpha importante"})
    server.flush(b_cid)
    await server.core.send_message(a_cid, conversation.conversation_id, "text", {"content": "beta comum"})
    server.flush(b_cid)
    messages = server.core.store.list_conversation_messages(conversation.conversation_id)
    target = next(item for item in messages if item["payload"]["content"] == "alpha importante")

    await server.core.search_messages(a_cid, conversation.conversation_id, "IMPORTANTE", limit=1)
    search = server.find(a_cid, "MESSAGE_SEARCH_RESULT")[-1]["payload"]
    assert len(search["messages"]) == 1
    assert search["messages"][0]["message_id"] == target["message_id"]

    await server.core.pin_message(b_cid, conversation.conversation_id, target["message_id"], True)
    assert len(server.core.store.list_pinned_messages(conversation.conversation_id)) == 1
    await server.core.pin_message(b_cid, conversation.conversation_id, target["message_id"], True)
    assert len(server.core.store.list_pinned_messages(conversation.conversation_id)) == 1
    await server.core.list_pinned_messages(a_cid, conversation.conversation_id)
    assert len(server.find(a_cid, "PINNED_MESSAGES")[-1]["payload"]["messages"]) == 1

    await server.core.pin_message(outsider_cid, conversation.conversation_id, target["message_id"], True)
    assert any(item["payload"]["code"] == "MESSAGE_FAILED" for item in server.find(outsider_cid, "ERROR"))
    assert outsider_id not in conversation.participants

    await server.core.pin_message(a_cid, conversation.conversation_id, target["message_id"], False)
    assert server.core.store.list_pinned_messages(conversation.conversation_id) == []


@pytest.mark.asyncio
async def test_user_search_returns_display_name_and_canonical_presence(server: FakeServer):
    finder_cid, _ = await register_and_login(server, "finder")
    target_cid, _ = await register_and_login(server, "displayuser")
    target = server.core.store.get_user_by_username("displayuser")
    assert target is not None
    target["display_name"] = "Pessoa Encontrável"
    server.core.store.update_display_name(target["user_id"], "Pessoa Encontrável")
    await server.core.change_status(target_cid, "busy", "em chamada")
    await server.core.set_custom_status(target_cid, "em chamada")
    server.flush(finder_cid)

    await server.core.search_users(finder_cid, "Encontrável")
    users = server.find(finder_cid, "SEARCH_USERS_RESULT")[-1]["payload"]["users"]
    result = next(item for item in users if item["username"] == "displayuser")
    assert result["display_name"] == "Pessoa Encontrável"
    assert result["presence"]["status"] == "busy"
    assert result["presence"]["custom_status"] == "em chamada"


def test_attachment_manager_rejects_invalid_metadata(server: FakeServer):
    manager: AttachmentManager = server.core.attachments
    with pytest.raises(AttachmentError):
        manager.begin_upload("conn", "user", "conv", "../../secret.bin", "application/octet-stream", 10)
    with pytest.raises(AttachmentError):
        manager.begin_upload("conn", "user", "conv", "file.txt", "text/plain", 0)
    with pytest.raises(AttachmentError):
        manager.begin_upload("conn", "user", "conv", "file.txt", "text/plain", server.settings.attachment_max_bytes + 1)
    assert Path(server.tmpdir).exists()
