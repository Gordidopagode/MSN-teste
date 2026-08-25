"""Tests: registration and authentication."""

import pytest

from server.tests.helpers import FakeServer


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.cleanup()


@pytest.mark.asyncio
async def test_register_and_login(server: FakeServer):
    cid = server.new_connection()
    await server.core.register(cid, "alice", "Alice", "secret123", "alice@example.com")
    ok = server.find(cid, "REGISTER_OK")
    assert len(ok) == 1
    assert ok[0]["payload"]["username"] == "alice"

    await server.core.authenticate(cid, "alice", "secret123")
    auth_ok = server.find(cid, "AUTH_OK")
    assert len(auth_ok) == 1
    assert auth_ok[0]["payload"]["session_id"]
    assert auth_ok[0]["payload"]["username"] == "alice"


@pytest.mark.asyncio
async def test_wrong_password(server: FakeServer):
    cid = server.new_connection()
    await server.core.register(cid, "bob", "Bob", "secret123", "bob@example.com")
    server.flush(cid)
    await server.core.authenticate(cid, "bob", "errada")
    errors = server.find(cid, "ERROR")
    assert len(errors) == 1
    assert errors[0]["payload"]["code"] == "AUTH_INVALID"
    # user must not be online after failed login
    assert server.core.online_users() == []


@pytest.mark.asyncio
async def test_nonexistent_user(server: FakeServer):
    cid = server.new_connection()
    await server.core.authenticate(cid, "nao_existe", "senha123")
    errors = server.find(cid, "ERROR")
    assert any(e["payload"]["code"] == "AUTH_INVALID" for e in errors)


@pytest.mark.asyncio
async def test_register_duplicate_username(server: FakeServer):
    cid = server.new_connection()
    await server.core.register(cid, "carol", "Carol", "secret123", "carol@example.com")
    server.flush(cid)
    await server.core.register(cid, "carol", "Carol 2", "senha456", "carol2@example.com")
    errors = server.find(cid, "ERROR")
    assert any(e["payload"]["code"] == "REGISTER_FAILED" for e in errors)


@pytest.mark.asyncio
async def test_invalid_inputs(server: FakeServer):
    cid = server.new_connection()
    # empty username
    await server.core.register(cid, "  ", "X", "secret123", "empty@example.com")
    assert any(e["payload"]["code"] == "REGISTER_FAILED"
               for e in server.find(cid, "ERROR"))
    server.flush(cid)
    # short password
    await server.core.register(cid, "dave", "Dave", "123", "dave@example.com")
    assert any(e["payload"]["code"] == "REGISTER_FAILED"
               for e in server.find(cid, "ERROR"))
    server.flush(cid)
    # case-insensitive duplicate
    await server.core.register(cid, "Carol", "Carol", "secret123", "carol3@example.com")
    server.flush(cid)
    await server.core.register(cid, "CAROL", "Carol", "secret123", "carol4@example.com")
    assert any(e["payload"]["code"] == "REGISTER_FAILED"
               for e in server.find(cid, "ERROR"))
