from __future__ import annotations

import asyncio
import json
import logging
import shutil
import smtplib
import socket
import tempfile
from pathlib import Path

import pytest
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve

from launcher.launcher import Endpoint, LocalServerController, WebSocketProbe
from server.core import ServerCore
from server.email import service as email_service
from server.tests.helpers import make_settings
from server.tests.test_sync_persistence import ws_register_login, ws_send


async def _close_all(*sockets) -> None:
    await asyncio.gather(*(socket.close() for socket in sockets), return_exceptions=True)


@pytest.mark.asyncio
async def test_local_server_without_smtp_keeps_login_chat_and_websocket_alive() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="msn_local_no_smtp_"))
    try:
        settings = make_settings(str(tmpdir), port=0)
        core = ServerCore(settings)
        from server.network.handler import WebSocketHandler

        handler = WebSocketHandler(core)
        async with ws_serve(handler.handle, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            ws_a, _, _ = await ws_register_login(url, "no_smtp_a")
            ws_b, _, _ = await ws_register_login(url, "no_smtp_b")
            try:
                group = await ws_send(
                    ws_a,
                    command="CREATE_GROUP",
                    name="No SMTP",
                    participants=["no_smtp_b"],
                )
                group_id = group["payload"]["conversation"]["conversation_id"]
                invitation = json.loads(await ws_b.recv())
                assert invitation["type"] == "CONVERSATION_CREATED"

                reset_response = await ws_send(
                    ws_a,
                    command="REQUEST_PASSWORD_RESET",
                    email="no_smtp_a@example.com",
                )
                assert reset_response["type"] == "PASSWORD_RESET_REQUESTED"
                user = core.store.get_user_by_email("no_smtp_a@example.com")
                assert user is not None
                assert core.store.get_password_reset_for_user(user["user_id"]) is None

                ack = await ws_send(
                    ws_a,
                    command="SEND_MESSAGE",
                    conversation_id=group_id,
                    type="text",
                    payload={"content": "chat remains available"},
                )
                assert ack["type"] == "MESSAGE_ACK"
                message = json.loads(await ws_b.recv())
                assert message["type"] == "MESSAGE"
                assert message["payload"]["message"]["payload"]["content"] == "chat remains available"
                assert len(core.online_users()) == 2
            finally:
                await _close_all(ws_a, ws_b)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_configured_smtp_connects_authenticates_sends_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSMTP:
        instances: list["FakeSMTP"] = []

        def __init__(self, host: str, port: int, timeout: int) -> None:
            self.host = host
            self.port = port
            self.timeout = timeout
            self.events: list[str] = []
            self.login_args: tuple[str, str] | None = None
            self.message = None
            self.closed = False
            self.__class__.instances.append(self)

        def __enter__(self) -> "FakeSMTP":
            self.events.append("enter")
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            self.events.append("exit")
            self.closed = True

        def ehlo(self) -> None:
            self.events.append("ehlo")

        def starttls(self, context=None) -> None:
            self.events.append("starttls")

        def login(self, username: str, password: str) -> None:
            self.events.append("login")
            self.login_args = (username, password)

        def send_message(self, message) -> None:
            self.events.append("send_message")
            self.message = message

    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    settings = make_settings(
        "/tmp/msn_smtp_fake",
        smtp_host="smtp.test.invalid",
        smtp_port=587,
        smtp_username="sender.test.invalid",
        smtp_password="not-a-real-credential",
        smtp_from="sender.test.invalid",
    )

    assert email_service.smtp_is_configured(settings) is True
    await email_service.send_password_reset_email(
        settings,
        "recipient.test.invalid",
        "ABCD2345",
    )

    assert len(FakeSMTP.instances) == 1
    smtp = FakeSMTP.instances[0]
    assert smtp.host == "smtp.test.invalid"
    assert smtp.port == 587
    assert smtp.events == ["enter", "ehlo", "starttls", "ehlo", "login", "send_message", "exit"]
    assert smtp.closed is True
    assert smtp.login_args is not None
    assert smtp.login_args[0] == "sender.test.invalid"
    assert smtp.message["From"] == "sender.test.invalid"
    assert smtp.message["To"] == "recipient.test.invalid"
    assert "ABCD2345" in smtp.message.get_content()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["authentication", "unavailable"])
async def test_smtp_failure_is_contained_and_chat_continues(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    class FailingSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            if failure_kind == "unavailable":
                raise OSError("test SMTP unavailable")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            return None

        def ehlo(self) -> None:
            return None

        def starttls(self, context=None) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"test authentication failure")

        def send_message(self, message) -> None:
            raise AssertionError("send_message must not run after SMTP failure")

    monkeypatch.setattr(email_service.smtplib, "SMTP", FailingSMTP)
    tmpdir = Path(tempfile.mkdtemp(prefix=f"msn_smtp_{failure_kind}_"))
    try:
        settings = make_settings(
            str(tmpdir),
            port=0,
            smtp_host="smtp.test.invalid",
            smtp_port=587,
            smtp_username="sender.test.invalid",
            smtp_password="not-a-real-credential",
            smtp_from="sender.test.invalid",
        )
        core = ServerCore(settings)
        from server.network.handler import WebSocketHandler

        handler = WebSocketHandler(core)
        async with ws_serve(handler.handle, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            ws_a, _, _ = await ws_register_login(url, f"smtp_{failure_kind}_a")
            ws_b, _, _ = await ws_register_login(url, f"smtp_{failure_kind}_b")
            try:
                group = await ws_send(
                    ws_a,
                    command="CREATE_GROUP",
                    name="SMTP failure",
                    participants=[f"smtp_{failure_kind}_b"],
                )
                group_id = group["payload"]["conversation"]["conversation_id"]
                invitation = json.loads(await ws_b.recv())
                assert invitation["type"] == "CONVERSATION_CREATED"

                reset_response = await ws_send(
                    ws_a,
                    command="REQUEST_PASSWORD_RESET",
                    email=f"smtp_{failure_kind}_a@example.com",
                )
                assert reset_response["type"] == "PASSWORD_RESET_REQUESTED"
                assert "535" not in json.dumps(reset_response)
                user = core.store.get_user_by_email(f"smtp_{failure_kind}_a@example.com")
                assert user is not None
                assert core.store.get_password_reset_for_user(user["user_id"]) is None

                ack = await ws_send(
                    ws_a,
                    command="SEND_MESSAGE",
                    conversation_id=group_id,
                    type="text",
                    payload={"content": f"after {failure_kind}"},
                )
                assert ack["type"] == "MESSAGE_ACK"
                message = json.loads(await ws_b.recv())
                assert message["type"] == "MESSAGE"
                assert len(core.online_users()) == 2
            finally:
                await _close_all(ws_a, ws_b)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_three_clients_stay_online_during_concurrent_recovery() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="msn_three_recovery_"))
    sent_recipients: list[str] = []

    async def fake_sender(settings, recipient: str, code: str) -> None:
        sent_recipients.append(recipient)
        await asyncio.sleep(0.02)

    try:
        settings = make_settings(str(tmpdir), port=0)
        core = ServerCore(settings, email_sender=fake_sender)
        from server.network.handler import WebSocketHandler

        handler = WebSocketHandler(core)
        async with ws_serve(handler.handle, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            ws_a, _, _ = await ws_register_login(url, "three_a")
            ws_b, _, _ = await ws_register_login(url, "three_b")
            ws_c, _, _ = await ws_register_login(url, "three_c")
            try:
                group = await ws_send(
                    ws_a,
                    command="CREATE_GROUP",
                    name="Three clients",
                    participants=["three_b", "three_c"],
                )
                group_id = group["payload"]["conversation"]["conversation_id"]
                invitations = await asyncio.gather(ws_b.recv(), ws_c.recv())
                assert all(json.loads(item)["type"] == "CONVERSATION_CREATED" for item in invitations)

                responses = await asyncio.gather(
                    ws_send(ws_a, command="REQUEST_PASSWORD_RESET", email="three_a@example.com"),
                    ws_send(ws_b, command="REQUEST_PASSWORD_RESET", email="three_b@example.com"),
                    ws_send(ws_c, command="REQUEST_PASSWORD_RESET", email="three_c@example.com"),
                )
                assert all(item["type"] == "PASSWORD_RESET_REQUESTED" for item in responses)
                assert sorted(sent_recipients) == [
                    "three_a@example.com",
                    "three_b@example.com",
                    "three_c@example.com",
                ]
                assert len(core.online_users()) == 3

                ack = await ws_send(
                    ws_a,
                    command="SEND_MESSAGE",
                    conversation_id=group_id,
                    type="text",
                    payload={"content": "three clients still connected"},
                )
                assert ack["type"] == "MESSAGE_ACK"
                delivered = await asyncio.gather(ws_b.recv(), ws_c.recv())
                assert all(json.loads(item)["type"] == "MESSAGE" for item in delivered)
            finally:
                await _close_all(ws_a, ws_b, ws_c)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_restart_preserves_database_history_and_reconnects_session() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="msn_restart_local_"))
    session_id = ""
    group_id = ""
    try:
        settings = make_settings(str(tmpdir), port=0)
        core_one = ServerCore(settings)
        from server.network.handler import WebSocketHandler

        handler_one = WebSocketHandler(core_one)
        async with ws_serve(handler_one.handle, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            ws_a, session_id, _ = await ws_register_login(url, "restart_a")
            ws_b, _, _ = await ws_register_login(url, "restart_b")
            try:
                group = await ws_send(
                    ws_a,
                    command="CREATE_GROUP",
                    name="Restart history",
                    participants=["restart_b"],
                )
                group_id = group["payload"]["conversation"]["conversation_id"]
                invitation = json.loads(await ws_b.recv())
                assert invitation["type"] == "CONVERSATION_CREATED"
                ack = await ws_send(
                    ws_a,
                    command="SEND_MESSAGE",
                    conversation_id=group_id,
                    type="text",
                    payload={"content": "persist across restart"},
                )
                assert ack["type"] == "MESSAGE_ACK"
                assert json.loads(await ws_b.recv())["type"] == "MESSAGE"
            finally:
                await _close_all(ws_a, ws_b)
        del handler_one
        del core_one

        core_two = ServerCore(settings)
        handler_two = WebSocketHandler(core_two)
        async with ws_serve(handler_two.handle, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            ws_reconnected = await ws_connect(url)
            try:
                reconnect = await ws_send(
                    ws_reconnected,
                    command="RECONNECT",
                    session_id=session_id,
                )
                assert reconnect["type"] == "RECONNECT_OK"
                sync = await ws_send(ws_reconnected, command="REQUEST_SYNC")
                data = sync["payload"]["data"]
                assert data["identity"]["username"] == "restart_a"
                assert any(
                    item["payload"]["content"] == "persist across restart"
                    for item in data["history"].get(group_id, [])
                )
            finally:
                await ws_reconnected.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_local_launcher_starts_real_server_without_inherited_external_origins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutil.copytree(Path(__file__).resolve().parents[2] / "server", tmp_path / "server")
    controller = LocalServerController(tmp_path, logging.getLogger("launcher-real-local-test"))
    endpoint = Endpoint("127.0.0.1", _free_port())
    monkeypatch.setenv("MSN_ALLOWED_ORIGINS", "https://old-external.example")
    try:
        controller.start(endpoint)
        controller.wait_until_ready(endpoint, timeout=8)
        assert controller.owned is True
        assert WebSocketProbe.check(endpoint)
    finally:
        controller.stop()


def test_local_launcher_drops_inherited_external_origins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "main.py").write_text("# test server\n", encoding="utf-8")
    controller = LocalServerController(tmp_path, logging.getLogger("launcher-local-test"))
    endpoint = Endpoint("127.0.0.1", 8765)
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 4242

        def __init__(self) -> None:
            self.running = True
            self.returncode = None

        def poll(self):
            return None if self.running else self.returncode

        def terminate(self) -> None:
            self.running = False
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self) -> None:
            self.running = False
            self.returncode = -9

    process = FakeProcess()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return process

    monkeypatch.setenv("MSN_ALLOWED_ORIGINS", "https://old-external.example")
    monkeypatch.setattr(WebSocketProbe, "check", staticmethod(lambda endpoint, timeout=1.25: False))
    monkeypatch.setattr("launcher.launcher.subprocess.Popen", fake_popen)
    try:
        controller.start(endpoint)
        env = captured["env"]
        assert isinstance(env, dict)
        assert "MSN_ALLOWED_ORIGINS" not in env
        assert env["MSN_HOST"] == "127.0.0.1"
        assert env["MSN_PORT"] == "8765"
    finally:
        controller.stop()


def test_smtp_settings_are_environment_driven_and_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSN_ALLOWED_ORIGINS", "")
    from server.config.settings import ServerSettings

    local_settings = ServerSettings.from_env(data_dir="/tmp/msn_settings_test")
    assert local_settings.allowed_origins is None

    monkeypatch.setenv("MSN_SMTP_HOST", "smtp.test.invalid")
    monkeypatch.setenv("MSN_SMTP_PORT", "587")
    monkeypatch.setenv("MSN_SMTP_USERNAME", "sender.test.invalid")
    monkeypatch.setenv("MSN_SMTP_PASSWORD", "")
    monkeypatch.setenv("MSN_SMTP_FROM", "sender.test.invalid")

    settings = ServerSettings.from_env(data_dir="/tmp/msn_settings_test")
    assert settings.smtp_host == "smtp.test.invalid"
    assert settings.smtp_port == 587
    assert settings.smtp_username == "sender.test.invalid"
    assert settings.smtp_from == "sender.test.invalid"
    assert email_service.smtp_is_configured(settings) is False

    monkeypatch.setenv("MSN_SMTP_PASSWORD", "not-a-real-credential")
    configured = ServerSettings.from_env(data_dir="/tmp/msn_settings_test")
    assert email_service.smtp_is_configured(configured) is True


@pytest.mark.asyncio
async def test_local_launcher_hub_origin_connects_to_local_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = Path(__file__).resolve().parents[2]
    shutil.copytree(project_dir / "server", tmp_path / "server")
    public_dir = tmp_path / "client" / "public"
    public_dir.mkdir(parents=True)
    (public_dir / "index.html").write_text("<h1>MSN local</h1>", encoding="utf-8")

    from launcher.launcher import LauncherCoordinator

    coordinator = LauncherCoordinator(tmp_path)
    endpoint = Endpoint("127.0.0.1", _free_port())
    monkeypatch.setenv("MSN_ALLOWED_ORIGINS", "https://old-external.example")
    try:
        hub_url = coordinator.prepare(endpoint, "local", open_browser=False)
        assert "server=ws%3A%2F%2F127.0.0.1" in hub_url
        assert coordinator.frontend.port is not None
        origin = f"http://127.0.0.1:{coordinator.frontend.port}"
        async with ws_connect(endpoint.websocket_url, origin=origin) as websocket:
            response = await ws_send(websocket, command="REQUEST_SYNC")
            assert response["type"] == "ERROR"
            assert response["payload"]["code"] == "AUTH_REQUIRED"
    finally:
        coordinator.shutdown()
