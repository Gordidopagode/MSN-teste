from __future__ import annotations

import shutil
import sqlite3
import tempfile

import pytest

from server.auth.security import hash_password
from server.core import ServerCore
from server.tests.helpers import make_settings


async def register_with_code(core: ServerCore, connection_id: str, username: str,
                             password: str = "oldpass") -> str:
    core.client_connected(connection_id)
    await core.register(connection_id, username, username.capitalize(), password)
    response = [
        item for item in core.pending.get(connection_id, [])
        if item["type"] == "REGISTER_OK"
    ][-1]
    code = response["payload"]["recovery_code"]
    assert len(code) == 16
    assert code.isalnum() and code == code.upper()
    return code


@pytest.mark.asyncio
async def test_registration_delivers_one_time_code_and_reset_invalidates_it() -> None:
    tmpdir = tempfile.mkdtemp(prefix="msn_local_recovery_")
    try:
        core = ServerCore(make_settings(tmpdir))
        code = await register_with_code(core, "recovery_conn", "alice")
        user = core.store.get_user_by_username("alice")
        assert user is not None
        record = core.store.get_recovery_code_for_user(user["user_id"])
        assert record is not None
        assert code not in record["code_hash"]

        await core.reset_password("recovery_conn", "alice", code, "newpass")
        assert any(
            item["type"] == "PASSWORD_RESET_OK"
            for item in core.pending["recovery_conn"]
        )
        assert await core.auth.authenticate("alice", "newpass")
        assert core.store.get_recovery_code_for_user(user["user_id"])["used_at"] is not None

        second_connection = "recovery_conn_2"
        core.client_connected(second_connection)
        await core.reset_password(second_connection, "alice", code, "thirdpass")
        error = [
            item for item in core.pending[second_connection]
            if item["type"] == "ERROR"
        ][-1]
        assert error["payload"]["code"] == "PASSWORD_RESET_FAILED"
        assert await core.auth.authenticate("alice", "newpass")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_recovery_code_is_hashed_and_not_stored_in_plaintext() -> None:
    tmpdir = tempfile.mkdtemp(prefix="msn_local_recovery_db_")
    try:
        core = ServerCore(make_settings(tmpdir))
        code = await register_with_code(core, "db_conn", "db_user")
        user = core.store.get_user_by_username("db_user")
        assert user is not None
        with sqlite3.connect(f"{tmpdir}/msn_server.db") as connection:
            rows = connection.execute(
                "SELECT code_hash FROM recovery_codes WHERE user_id = ?",
                (user["user_id"],),
            ).fetchall()
            password_hash = connection.execute(
                "SELECT password_hash FROM users WHERE user_id = ?",
                (user["user_id"],),
            ).fetchone()[0]
        assert len(rows) == 1
        assert code not in rows[0][0]
        assert code not in password_hash
        assert rows[0][0].startswith(("$argon2", "pbkdf2_sha256$"))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_wrong_codes_are_generic_and_rate_limited() -> None:
    tmpdir = tempfile.mkdtemp(prefix="msn_local_recovery_limit_")
    try:
        settings = make_settings(tmpdir, reset_max_attempts=3)
        core = ServerCore(settings)
        await register_with_code(core, "limit_conn", "limited")
        core.pending["limit_conn"].clear()

        for _ in range(3):
            await core.reset_password("limit_conn", "limited", "WRONGCODE1234567", "newpass")
            error = [
                item for item in core.pending["limit_conn"]
                if item["type"] == "ERROR"
            ][-1]
            assert error["payload"]["code"] == "PASSWORD_RESET_FAILED"
            assert error["payload"]["message"] == "Código inválido ou expirado."
            core.pending["limit_conn"].clear()

        record = core.store.get_recovery_code_for_user(
            core.store.get_user_by_username("limited")["user_id"]
        )
        assert record["attempts"] == 3
        await core.reset_password("limit_conn", "limited", "ANOTHERWRONGCODE", "newpass")
        error = [
            item for item in core.pending["limit_conn"]
            if item["type"] == "ERROR"
        ][-1]
        assert error["payload"]["message"] == "Código inválido ou expirado."
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_code_cannot_be_used_for_another_username() -> None:
    tmpdir = tempfile.mkdtemp(prefix="msn_local_recovery_isolation_")
    try:
        core = ServerCore(make_settings(tmpdir))
        code_a = await register_with_code(core, "isolation_a", "account_a")
        await register_with_code(core, "isolation_b", "account_b")
        core.pending["isolation_a"].clear()
        core.pending["isolation_b"].clear()
        await core.reset_password("isolation_b", "account_b", code_a, "newpass")
        error = [
            item for item in core.pending["isolation_b"]
            if item["type"] == "ERROR"
        ][-1]
        assert error["payload"]["code"] == "PASSWORD_RESET_FAILED"
        assert await core.auth.authenticate("account_b", "oldpass")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_code_remains_valid_after_restart() -> None:
    tmpdir = tempfile.mkdtemp(prefix="msn_local_recovery_restart_")
    try:
        settings = make_settings(tmpdir)
        core_one = ServerCore(settings)
        code = await register_with_code(core_one, "restart_conn", "restart_user")
        user_id = core_one.store.get_user_by_username("restart_user")["user_id"]
        del core_one

        core_two = ServerCore(settings)
        core_two.client_connected("restart_reset")
        await core_two.reset_password("restart_reset", "restart_user", code, "afterrestart")
        assert any(
            item["type"] == "PASSWORD_RESET_OK"
            for item in core_two.pending["restart_reset"]
        )
        assert await core_two.auth.authenticate("restart_user", "afterrestart")
        assert core_two.store.get_recovery_code_for_user(user_id)["used_at"] is not None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_legacy_account_gets_code_on_first_successful_login() -> None:
    tmpdir = tempfile.mkdtemp(prefix="msn_local_recovery_legacy_")
    try:
        settings = make_settings(tmpdir)
        core = ServerCore(settings)
        user_id = "legacy-user"
        core.store.create_user(
            user_id,
            "legacy",
            "Legacy",
            hash_password("legacy-pass"),
            "2026-01-01T00:00:00+00:00",
            None,
        )

        core.client_connected("legacy_conn")
        await core.authenticate("legacy_conn", "legacy", "legacy-pass")
        auth = [
            item for item in core.pending["legacy_conn"]
            if item["type"] == "AUTH_OK"
        ][-1]
        code = auth["payload"].get("recovery_code")
        assert isinstance(code, str) and len(code) == 16
        assert core.store.get_recovery_code_for_user(user_id) is not None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
