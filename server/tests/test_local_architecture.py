from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import socket
import tempfile
from pathlib import Path

import pytest
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve

from launcher.launcher import Endpoint, LocalServerController, WebSocketProbe
from server.core import ServerCore
from server.network.handler import WebSocketHandler
from server.network.protocol import ProtocolError, parse_client_message
from server.tests.helpers import make_settings
from server.tests.test_sync_persistence import ws_send


async def _close_all(*sockets) -> None:
    await asyncio.gather(*(socket.close() for socket in sockets), return_exceptions=True)


async def _ws_register_login(url: str, username: str, password: str = "secret123"):
    websocket = await ws_connect(url)
    registration = await ws_send(
        websocket,
        command="REGISTER",
        username=username,
        display_name=username.capitalize(),
        password=password,
    )
    assert registration["type"] == "REGISTER_OK", registration
    code = registration["payload"]["recovery_code"]
    authentication = await ws_send(
        websocket,
        command="LOGIN",
        username=username,
        password=password,
    )
    assert authentication["type"] == "AUTH_OK", authentication
    return websocket, authentication["payload"], code


@pytest.mark.asyncio
async def test_local_server_without_smtp_keeps_login_chat_and_recovery_alive() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="msn_local_no_smtp_"))
    try:
        settings = make_settings(str(tmpdir), port=0)
        core = ServerCore(settings)
        handler = WebSocketHandler(core)
        async with ws_serve(handler.handle, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            ws_a, _, code_a = await _ws_register_login(url, "no_smtp_a")
            ws_b, _, _ = await _ws_register_login(url, "no_smtp_b")
            try:
                group = await ws_send(
                    ws_a,
                    command="CREATE_GROUP",
                    name="Local recovery",
                    participants=["no_smtp_b"],
                )
                group_id = group["payload"]["conversation"]["conversation_id"]
                invitation = json.loads(await ws_b.recv())
                assert invitation["type"] == "CONVERSATION_CREATED"

                reset_response = await ws_send(
                    ws_a,
                    command="RESET_PASSWORD",
                    username="no_smtp_a",
                    code=code_a,
                    new_password="changed123",
                )
                assert reset_response["type"] == "PASSWORD_RESET_OK"

                ack = await ws_send(
                    ws_a,
                    command="SEND_MESSAGE",
                    conversation_id=group_id,
                    type="text",
                    payload={"content": "local recovery keeps chat available"},
                )
                assert ack["type"] == "MESSAGE_ACK"
                message = json.loads(await ws_b.recv())
                assert message["type"] == "MESSAGE"
                assert message["payload"]["message"]["payload"]["content"] == "local recovery keeps chat available"
                assert len(core.online_users()) == 2
            finally:
                await _close_all(ws_a, ws_b)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.asyncio
async def test_three_clients_stay_connected_during_concurrent_local_recovery() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="msn_three_local_recovery_"))
    try:
        settings = make_settings(str(tmpdir), port=0)
        core = ServerCore(settings)
        handler = WebSocketHandler(core)
        async with ws_serve(handler.handle, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            clients = await asyncio.gather(
                _ws_register_login(url, "three_a"),
                _ws_register_login(url, "three_b"),
                _ws_register_login(url, "three_c"),
            )
            sockets = [item[0] for item in clients]
            try:
                group = await ws_send(
                    sockets[0],
                    command="CREATE_GROUP",
                    name="Three clients",
                    participants=["three_b", "three_c"],
                )
                group_id = group["payload"]["conversation"]["conversation_id"]
                invitations = await asyncio.gather(sockets[1].recv(), sockets[2].recv())
                assert all(json.loads(item)["type"] == "CONVERSATION_CREATED" for item in invitations)

                responses = await asyncio.gather(*(
                    ws_send(
                        websocket,
                        command="RESET_PASSWORD",
                        username=username,
                        code=code,
                        new_password="changed123",
                    )
                    for websocket, username, code in zip(
                        sockets,
                        ("three_a", "three_b", "three_c"),
                        (clients[0][2], clients[1][2], clients[2][2]),
                    )
                ))
                assert all(item["type"] == "PASSWORD_RESET_OK" for item in responses)
                assert len(core.online_users()) == 3

                ack = await ws_send(
                    sockets[0],
                    command="SEND_MESSAGE",
                    conversation_id=group_id,
                    type="text",
                    payload={"content": "three local recoveries completed"},
                )
                assert ack["type"] == "MESSAGE_ACK"
                delivered = await asyncio.gather(sockets[1].recv(), sockets[2].recv())
                assert all(json.loads(item)["type"] == "MESSAGE" for item in delivered)
            finally:
                await _close_all(*sockets)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_local_protocol_does_not_require_email_or_email_reset() -> None:
    registration = parse_client_message(json.dumps({
        "command": "REGISTER",
        "username": "local_user",
        "display_name": "Local User",
        "password": "secret123",
    }))
    assert "email" not in registration
    reset = parse_client_message(json.dumps({
        "command": "RESET_PASSWORD",
        "username": "local_user",
        "code": "ABCDEFGH23456789",
        "new_password": "changed123",
    }))
    assert reset["username"] == "local_user"
    with pytest.raises(ProtocolError):
        parse_client_message(json.dumps({
            "command": "REQUEST_PASSWORD_RESET",
            "email": "user@example.com",
        }))


def test_multiple_local_accounts_receive_different_codes() -> None:
    tmpdir = tempfile.mkdtemp(prefix="msn_local_unique_codes_")
    try:
        import asyncio as _asyncio

        async def scenario() -> tuple[str, str]:
            core = ServerCore(make_settings(tmpdir))
            core.client_connected("unique_a")
            core.client_connected("unique_b")
            await core.register("unique_a", "unique_a", "Unique A", "secret123")
            await core.register("unique_b", "unique_b", "Unique B", "secret123")
            codes = [
                item["payload"]["recovery_code"]
                for cid in ("unique_a", "unique_b")
                for item in core.pending[cid]
                if item["type"] == "REGISTER_OK"
            ]
            return codes[0], codes[1]

        code_a, code_b = _asyncio.run(scenario())
        assert code_a != code_b
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


def test_smtp_settings_remain_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.config.settings import ServerSettings

    for key in (
        "MSN_SMTP_HOST",
        "MSN_SMTP_PORT",
        "MSN_SMTP_USERNAME",
        "MSN_SMTP_PASSWORD",
        "MSN_SMTP_FROM",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = ServerSettings.from_env(data_dir="/tmp/msn_settings_test")
    assert settings.smtp_port == 587
    assert not settings.smtp_username
    assert not settings.smtp_password
    assert not settings.smtp_from


def test_launcher_bundle_contains_local_recovery_flow() -> None:
    project_dir = Path(__file__).resolve().parents[2]
    public_dir = project_dir / "client" / "public"
    html = (public_dir / "index.html").read_text(encoding="utf-8")
    script_ref = re.search(r'assets/index-[^" ]+\.js', html)
    style_ref = re.search(r'assets/index-[^" ]+\.css', html)
    assert script_ref is not None
    assert style_ref is not None
    script = (public_dir / script_ref.group(0)).read_text(encoding="utf-8")
    assert "recovery_code" in script
    assert "requestPasswordReset" not in script
    assert "Código de recuperação" in script


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
