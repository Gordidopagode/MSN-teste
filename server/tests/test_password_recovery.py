from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from server.auth import manager as auth_manager
from server.core import ServerCore
from server.tests.helpers import make_settings


@pytest.mark.asyncio
async def test_password_recovery_uses_email_code_once_and_invalidates_sessions():
    tmpdir = tempfile.mkdtemp(prefix="msn_recovery_")
    sent: list[tuple[str, str]] = []

    async def fake_sender(settings, recipient: str, code: str) -> None:
        sent.append((recipient, code))

    try:
        core = ServerCore(make_settings(tmpdir), email_sender=fake_sender)
        cid = "recovery_conn"
        core.client_connected(cid)
        await core.register(cid, "alice", "Alice", "oldpass", "alice@example.com")
        core.pending.pop(cid, None)
        await core.authenticate(cid, "alice", "oldpass")
        session_id = core.pending[cid][0]["payload"]["session_id"]
        core.pending.pop(cid, None)

        await core.request_password_reset(cid, "alice@example.com")
        assert [e for e in core.pending.get(cid, []) if e["type"] == "PASSWORD_RESET_REQUESTED"]
        assert sent and sent[0][0] == "alice@example.com"
        code = sent[0][1]
        token = core.store.get_password_reset_for_user(
            core.store.get_user_by_email("alice@example.com")["user_id"]
        )
        assert token is not None
        assert code not in token["token_hash"]

        await core.reset_password(cid, "alice@example.com", code, "newpass")
        assert [e for e in core.pending.get(cid, []) if e["type"] == "PASSWORD_RESET_OK"]
        assert core.store.get_session(session_id) is None
        assert await core.auth.authenticate("alice", "newpass")

        cid2 = "recovery_conn_2"
        core.client_connected(cid2)
        await core.reset_password(cid2, "alice@example.com", code, "thirdpass")
        error = [e for e in core.pending.get(cid2, []) if e["type"] == "ERROR"][-1]
        assert error["payload"]["code"] == "PASSWORD_RESET_FAILED"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_password_recovery_does_not_enumerate_unknown_addresses():
    tmpdir = tempfile.mkdtemp(prefix="msn_recovery_unknown_")
    sent: list[str] = []

    async def fake_sender(settings, recipient: str, code: str) -> None:
        sent.append(recipient)

    try:
        core = ServerCore(make_settings(tmpdir), email_sender=fake_sender)
        cid = "unknown_conn"
        core.client_connected(cid)
        await core.request_password_reset(cid, "unknown@example.com")
        response = [e for e in core.pending.get(cid, []) if e["type"] == "PASSWORD_RESET_REQUESTED"][-1]
        assert response["payload"]["message"]
        assert sent == []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_password_recovery_code_expires():
    tmpdir = tempfile.mkdtemp(prefix="msn_recovery_expiry_")
    sent: list[str] = []
    clock = {"now": datetime.now(timezone.utc)}
    original = auth_manager._utcnow
    auth_manager._utcnow = lambda: clock["now"]

    async def fake_sender(settings, recipient: str, code: str) -> None:
        sent.append(code)

    try:
        core = ServerCore(make_settings(tmpdir, reset_token_ttl_minutes=1), email_sender=fake_sender)
        cid = "expiry_conn"
        core.client_connected(cid)
        await core.register(cid, "bob", "Bob", "oldpass", "bob@example.com")
        core.pending.pop(cid, None)
        await core.request_password_reset(cid, "bob@example.com")
        clock["now"] += timedelta(minutes=2)
        await core.reset_password(cid, "bob@example.com", sent[0], "newpass")
        error = [e for e in core.pending.get(cid, []) if e["type"] == "ERROR"][-1]
        assert error["payload"]["code"] == "PASSWORD_RESET_FAILED"
    finally:
        auth_manager._utcnow = original
        shutil.rmtree(tmpdir, ignore_errors=True)
