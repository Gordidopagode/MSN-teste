"""Tests: conversations, message send/receive, history, deduplication."""

import pytest

from server.tests.helpers import FakeServer
from server.tests.test_sessions import register_and_login


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.cleanup()


def messages_of(connection_id, pending):
    return [e for e in pending if e["type"] == "MESSAGE"]


@pytest.mark.asyncio
async def test_send_and_receive_individual(server: FakeServer):
    a_cid, _ = await register_and_login(server, "rosa")
    b_cid, _ = await register_and_login(server, "sam")

    # send_message creates the 1:1 conversation lazily
    rosa_id = server.core.online_users()[0]["user_id"]
    sam_row = server.core.store.get_user_by_username("sam")
    sam_id = sam_row["user_id"]
    conv = server.core.conversations.get_or_create_individual(rosa_id, sam_id)

    await server.core.send_message(
        a_cid, conv.conversation_id, "text", {"content": "Olá, Sam!"})

    acks = server.find(a_cid, "MESSAGE_ACK")
    assert len(acks) == 1
    received = messages_of(b_cid, server.pending_for(b_cid))
    assert len(received) == 1
    msg = received[0]["payload"]["message"]
    assert msg["content"] if isinstance(msg.get("content"), str) else \
        msg["payload"]["content"] == "Olá, Sam!"
    # sender field is set by the server
    assert msg["sender_id"] == rosa_id
    assert msg["conversation_id"] == conv.conversation_id


@pytest.mark.asyncio
async def test_sender_id_never_trusted(server: FakeServer):
    """Client-supplied sender metadata must never override the server value."""
    a_cid, _ = await register_and_login(server, "tina")
    b_cid, _ = await register_and_login(server, "uri")

    a_id = server.core.online_users()[0]["user_id"]
    b_row = server.core.store.get_user_by_username("uri")
    conv = server.core.conversations.get_or_create_individual(a_id, b_row["user_id"])

    payload = {"content": "teste", "sender_id": "invasor_fake"}
    await server.core.send_message(a_cid, conv.conversation_id, "text", payload)
    received = messages_of(b_cid, server.pending_for(b_cid))
    assert received[0]["payload"]["message"]["sender_id"] == a_id


@pytest.mark.asyncio
async def test_message_to_non_participant_rejected(server: FakeServer):
    a_cid, _ = await register_and_login(server, "victor")
    b_cid, _ = await register_and_login(server, "wendy")
    server.flush(a_cid)

    a_id = server.core.online_users()[0]["user_id"]
    b_row = server.core.store.get_user_by_username("wendy")
    conv = server.core.conversations.get_or_create_individual(a_id, b_row["user_id"])

    # third user never joined this conversation
    c_cid, _ = await register_and_login(server, "xander")
    await server.core.send_message(
        c_cid, conv.conversation_id, "text", {"content": "bisbilhoteiro"})
    assert any(e["payload"]["code"] == "MESSAGE_FAILED"
               for e in server.find(c_cid, "ERROR"))


@pytest.mark.asyncio
async def test_empty_and_oversized_message_rejected(server: FakeServer):
    a_cid, _ = await register_and_login(server, "yara")
    b_cid, _ = await register_and_login(server, "zeno")
    a_id = server.core.online_users()[0]["user_id"]
    z_row = server.core.store.get_user_by_username("zeno")
    conv = server.core.conversations.get_or_create_individual(a_id, z_row["user_id"])

    await server.core.send_message(a_cid, conv.conversation_id, "text",
                                   {"content": "   "})
    assert any(e["payload"]["code"] == "MESSAGE_FAILED"
               for e in server.find(a_cid, "ERROR"))
    server.flush(a_cid)

    await server.core.send_message(
        a_cid, conv.conversation_id, "text",
        {"content": "x" * (server.settings.max_message_length + 1)})
    assert any(e["payload"]["code"] == "MESSAGE_FAILED"
               for e in server.find(a_cid, "ERROR"))


@pytest.mark.asyncio
async def test_history(server: FakeServer):
    a_cid, _ = await register_and_login(server, "anna")
    b_cid, _ = await register_and_login(server, "beto")
    a_id = server.core.online_users()[0]["user_id"]
    b_row = server.core.store.get_user_by_username("beto")
    conv = server.core.conversations.get_or_create_individual(a_id, b_row["user_id"])

    for i in range(5):
        await server.core.send_message(
            a_cid, conv.conversation_id, "text", {"content": f"msg {i}"})
        server.flush(b_cid)

    await server.core.get_history(b_cid, conv.conversation_id, limit=50)
    history = server.find(b_cid, "HISTORY")
    assert len(history) == 1
    msgs = history[0]["payload"]["messages"]
    assert len(msgs) == 5
    assert all(m["conversation_id"] == conv.conversation_id for m in msgs)
    # chronological order
    ts = [m["timestamp"] for m in msgs]
    assert ts == sorted(ts)


@pytest.mark.asyncio
async def test_message_deduplication(server: FakeServer):
    """A client-specified message_id that already exists must not duplicate."""
    a_cid, _ = await register_and_login(server, "caco")
    b_cid, _ = await register_and_login(server, "deca")
    a_id = server.core.online_users()[0]["user_id"]
    d_row = server.core.store.get_user_by_username("deca")
    conv = server.core.conversations.get_or_create_individual(a_id, d_row["user_id"])

    await server.core.send_message(
        a_cid, conv.conversation_id, "text", {"content": "única"},
        message_id="id_deterministico")
    server.flush(b_cid)

    await server.core.send_message(
        a_cid, conv.conversation_id, "text", {"content": "duplicada!"},
        message_id="id_deterministico")
    # second delivery must not reach b
    assert len(messages_of(b_cid, server.pending_for(b_cid))) == 0
    # history still contains exactly one message
    history = server.core.messages.get_history(conv.conversation_id, a_id)
    assert len(history) == 1
    assert history[0]["payload"]["content"] == "única"


@pytest.mark.asyncio
async def test_group_conversation(server: FakeServer):
    a_cid, _ = await register_and_login(server, "elmo")
    b_cid, _ = await register_and_login(server, "foca")
    c_cid, _ = await register_and_login(server, "guga")

    await server.core.create_group(a_cid, "Amigos", ["foca", "guga"])
    for cid in (a_cid, b_cid, c_cid):
        assert server.find(cid, "CONVERSATION_CREATED"), cid

    group = server.core.conversations.list_user_conversations(
        server.core.online_users()[0]["user_id"])[0]
    await server.core.send_message(
        a_cid, group.conversation_id, "text", {"content": "Oi grupo!"})

    for cid in (b_cid, c_cid):
        assert len(messages_of(cid, server.pending_for(cid))) == 1
    # sender must not receive his own echo
    assert len(messages_of(a_cid, server.pending_for(a_cid))) == 0


@pytest.mark.asyncio
async def test_invalid_command_handled(server: FakeServer):
    from server.network.protocol import parse_client_message, ProtocolError

    with pytest.raises(ProtocolError):
        parse_client_message('{"command": "COMANDO_MALUCO"}')
    with pytest.raises(ProtocolError):
        parse_client_message('not json at all')
    with pytest.raises(ProtocolError):
        parse_client_message('{"command": "SEND_MESSAGE", "type": "text"}')
    with pytest.raises(ProtocolError):
        parse_client_message({"command": "LOGIN"})
    with pytest.raises(ProtocolError):
        parse_client_message("x" * (256 * 1024 + 1))
