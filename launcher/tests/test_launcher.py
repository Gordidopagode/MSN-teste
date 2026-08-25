from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from launcher.launcher import (
    Endpoint,
    SingleInstanceGuard,
    FrontendServer,
    LauncherConfig,
    LauncherCoordinator,
    LauncherError,
    LocalServerController,
    WebSocketProbe,
)


PROJECT_DIR = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_endpoint_validation_and_url() -> None:
    endpoint = Endpoint.from_values("127.0.0.1", "8765")
    assert endpoint.websocket_url == "ws://127.0.0.1:8765"
    with pytest.raises(LauncherError):
        Endpoint.from_values("", 8765)
    with pytest.raises(LauncherError):
        Endpoint.from_values("ws://localhost", 8765)
    with pytest.raises(LauncherError):
        Endpoint.from_values("localhost", 70000)


def test_single_instance_guard_blocks_duplicate_launcher(tmp_path: Path) -> None:
    lock_path = tmp_path / "config" / "launcher.lock"
    first = SingleInstanceGuard(lock_path)
    second = SingleInstanceGuard(lock_path)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        second.release()
        first.release()
    assert second.acquire() is True
    second.release()


def test_probe_returns_false_when_no_server_is_active() -> None:
    endpoint = Endpoint("127.0.0.1", free_port())
    assert WebSocketProbe.check(endpoint, timeout=0.1) is False


def test_config_store_persists_only_connection_preferences(tmp_path: Path) -> None:
    from launcher.launcher import ConfigStore

    store = ConfigStore(tmp_path)
    config = LauncherConfig(host="192.168.0.20", port=8765, mode="existing")
    store.save(config)
    assert store.load() == config
    raw = json.loads((tmp_path / "config" / "launcher.json").read_text(encoding="utf-8"))
    assert "password" not in raw
    assert "token" not in raw


def test_frontend_server_serves_built_hub_and_encodes_endpoint(tmp_path: Path) -> None:
    public_dir = tmp_path / "public"
    public_dir.mkdir()
    (public_dir / "index.html").write_text("<h1>MSN</h1>", encoding="utf-8")
    frontend = FrontendServer(public_dir, logging_stub())
    try:
        port = frontend.start()
        url = frontend.url(Endpoint("192.168.0.20", 8765))
        assert port > 0
        assert "server=ws%3A%2F%2F192.168.0.20%3A8765" in url
        with socket.create_connection(("127.0.0.1", port), timeout=1) as sock:
            sock.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            assert b"200" in sock.recv(1024)
    finally:
        frontend.stop()


def test_existing_mode_refuses_an_unreachable_server(tmp_path: Path) -> None:
    coordinator = LauncherCoordinator(tmp_path)
    with pytest.raises(LauncherError, match="Não foi possível conectar"):
        coordinator.prepare(Endpoint("127.0.0.1", free_port()), "existing", open_browser=False)
    assert coordinator.active is False


def test_local_controller_does_not_start_a_duplicate_server(tmp_path: Path) -> None:
    # The fixture uses a copy of the real Python server and two controllers.
    server_src = PROJECT_DIR / "server"
    server_dst = tmp_path / "server"
    import shutil

    shutil.copytree(server_src, server_dst)
    controller_one = LocalServerController(tmp_path, logging_stub())
    controller_two = LocalServerController(tmp_path, logging_stub())
    endpoint = Endpoint("127.0.0.1", free_port())
    try:
        controller_one.start(endpoint)
        controller_one.wait_until_ready(endpoint, timeout=8)
        controller_two.start(endpoint)
        assert controller_one.owned is True
        assert controller_two.owned is False
        assert controller_two.process is None
        assert WebSocketProbe.check(endpoint)
    finally:
        controller_two.stop()
        controller_one.stop()


def test_coordinator_starts_server_and_frontend_then_stops_owned_process(tmp_path: Path) -> None:
    import shutil

    shutil.copytree(PROJECT_DIR / "server", tmp_path / "server")
    public_dir = tmp_path / "client" / "public"
    public_dir.mkdir(parents=True)
    (public_dir / "index.html").write_text("<h1>Hub real</h1>", encoding="utf-8")
    coordinator = LauncherCoordinator(tmp_path)
    endpoint = Endpoint("127.0.0.1", free_port())
    try:
        url = coordinator.prepare(endpoint, "local", open_browser=False)
        assert "server=ws%3A%2F%2F127.0.0.1" in url
        assert coordinator.active is True
        assert coordinator.server.owned is True
        assert WebSocketProbe.check(endpoint)
    finally:
        coordinator.shutdown()
    assert WebSocketProbe.check(endpoint, timeout=0.1) is False


def test_coordinator_existing_server_is_never_owned_or_stopped(tmp_path: Path) -> None:
    import shutil

    shutil.copytree(PROJECT_DIR / "server", tmp_path / "server")
    public_dir = tmp_path / "client" / "public"
    public_dir.mkdir(parents=True)
    (public_dir / "index.html").write_text("<h1>Hub real</h1>", encoding="utf-8")
    external = LocalServerController(tmp_path, logging_stub())
    endpoint = Endpoint("127.0.0.1", free_port())
    external.start(endpoint)
    external.wait_until_ready(endpoint, timeout=8)
    coordinator = LauncherCoordinator(tmp_path)
    try:
        coordinator.prepare(endpoint, "existing", open_browser=False)
        assert coordinator.server.owned is False
        coordinator.shutdown()
        assert WebSocketProbe.check(endpoint)
    finally:
        external.stop()


def test_probe_accepts_real_websocket_upgrade(tmp_path: Path) -> None:
    server_src = PROJECT_DIR / "server"
    server_dst = tmp_path / "server"
    import shutil

    shutil.copytree(server_src, server_dst)
    controller = LocalServerController(tmp_path, logging_stub())
    endpoint = Endpoint("127.0.0.1", free_port())
    try:
        controller.start(endpoint)
        controller.wait_until_ready(endpoint, timeout=8)
        assert WebSocketProbe.check(endpoint)
    finally:
        controller.stop()


def logging_stub():
    import logging

    logger = logging.getLogger("launcher-tests")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger
