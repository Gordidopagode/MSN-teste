"""MSN Messenger launcher.

The launcher is deliberately outside the Messenger domain. It only:

* discovers or starts the Python server;
* waits for an actual WebSocket handshake;
* serves the already-built Hub frontend;
* opens the Hub in the default browser;
* records operational logs; and
* stops only a server process that it started itself.

It never authenticates users, forwards chat frames, stores messages, or
implements a second backend.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

try:
    from tkinter import BooleanVar, StringVar, Tk, messagebox, ttk
except ModuleNotFoundError:  # The headless test environment may not ship Tcl/Tk.
    BooleanVar = StringVar = Tk = messagebox = ttk = None  # type: ignore[assignment]
from urllib.parse import quote


APP_NAME = "MSN Messenger"
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8765
DEFAULT_FRONTEND_PORT = 0
PROBE_TIMEOUT = 1.25
READY_TIMEOUT = 20.0


class LauncherError(RuntimeError):
    """A user-facing launcher failure with no traceback required in the GUI."""


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int

    @classmethod
    def from_values(cls, host: str, port: str | int) -> "Endpoint":
        clean_host = str(host).strip()
        if not clean_host:
            raise LauncherError("Informe o endereço do servidor.")
        if "://" in clean_host or any(char.isspace() for char in clean_host):
            raise LauncherError("O endereço deve conter apenas host ou IP, sem ws://.")
        try:
            clean_port = int(port)
        except (TypeError, ValueError) as exc:
            raise LauncherError("A porta deve ser um número inteiro.") from exc
        if not (1 <= clean_port <= 65535):
            raise LauncherError("A porta deve estar entre 1 e 65535.")
        return cls(clean_host, clean_port)

    @property
    def websocket_url(self) -> str:
        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"ws://{host}:{self.port}"


@dataclass
class LauncherConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    mode: str = "local"
    shutdown_local_server: bool = True


class SingleInstanceGuard:
    """Prevents unnecessary duplicate launcher windows across platforms."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                self.handle.write("0")
                self.handle.flush()
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            self.handle.close()
            self.handle = None
            return False
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


class ConfigStore:
    """Persists harmless launcher preferences, never passwords or sessions."""

    def __init__(self, base_dir: Path) -> None:
        self.path = base_dir / "config" / "launcher.json"

    def load(self) -> LauncherConfig:
        if not self.path.exists():
            return LauncherConfig()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return LauncherConfig(
                host=str(raw.get("host", DEFAULT_HOST)),
                port=int(raw.get("port", DEFAULT_PORT)),
                mode=raw.get("mode", "local") if raw.get("mode") in {"local", "existing"} else "local",
                shutdown_local_server=bool(raw.get("shutdown_local_server", True)),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return LauncherConfig()

    def save(self, config: LauncherConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "host": config.host,
            "port": config.port,
            "mode": config.mode,
            "shutdown_local_server": config.shutdown_local_server,
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def application_dir() -> Path:
    """Resolve resources relative to source or the portable packaged executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return application_dir()


def build_logger(base_dir: Path) -> logging.Logger:
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("msn.launcher")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(log_dir / "launcher.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
        logger.addHandler(handler)
    return logger


class WebSocketProbe:
    """Checks the actual WebSocket upgrade, not merely whether a TCP port opens."""

    @staticmethod
    def check(endpoint: Endpoint, timeout: float = PROBE_TIMEOUT) -> bool:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        host_header = endpoint.host
        if ":" in host_header and not host_header.startswith("["):
            host_header = f"[{host_header}]"
        request = (
            "GET / HTTP/1.1\r\n"
            f"Host: {host_header}:{endpoint.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        try:
            with socket.create_connection((endpoint.host, endpoint.port), timeout=timeout) as conn:
                conn.settimeout(timeout)
                conn.sendall(request)
                response = b""
                while b"\r\n\r\n" not in response and len(response) < 16384:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    response += chunk
        except (OSError, socket.timeout):
            return False

        header_block = response.decode("iso-8859-1", errors="replace").lower()
        first_line = header_block.split("\r\n", 1)[0]
        return (
            first_line.startswith("http/1.1 101")
            and "upgrade: websocket" in header_block
            and "connection: upgrade" in header_block
        )


class LocalServerController:
    """Owns only the process created by this launcher instance."""

    def __init__(self, base_dir: Path, logger: logging.Logger) -> None:
        self.base_dir = base_dir
        self.logger = logger
        self.process: Optional[subprocess.Popen[bytes]] = None
        self.owned = False
        self._output_handle = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _bundled_server_command(self) -> Optional[list[str]]:
        candidates = [
            executable_dir() / "server_bundle" / "msn-server.exe",
            executable_dir() / "server_bundle" / "msn-server",
            self.base_dir / "server_bundle" / "msn-server.exe",
            self.base_dir / "server_bundle" / "msn-server",
        ]
        for candidate in candidates:
            if candidate.exists():
                return [str(candidate)]
        return None

    def _source_server_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            raise LauncherError(
                "O executável do servidor local não foi encontrado no pacote. "
                "Reinstale a distribuição do MSN Messenger."
            )
        server_main = self.base_dir / "server" / "main.py"
        if not server_main.exists():
            raise LauncherError("A pasta do servidor local não foi encontrada.")
        return [sys.executable, "-m", "server.main"]

    def start(self, endpoint: Endpoint) -> None:
        if self.running:
            return
        if WebSocketProbe.check(endpoint):
            self.owned = False
            self.logger.info("Servidor local já encontrado em %s", endpoint.websocket_url)
            return

        command = self._bundled_server_command() or self._source_server_command()
        self.base_dir.joinpath("data").mkdir(parents=True, exist_ok=True)
        self.base_dir.joinpath("logs").mkdir(parents=True, exist_ok=True)
        output_path = self.base_dir / "logs" / "server.log"
        self._output_handle = output_path.open("ab")
        env = os.environ.copy()
        env.update({
            "MSN_HOST": "127.0.0.1",
            "MSN_PORT": str(endpoint.port),
            "MSN_DATA_DIR": str(self.base_dir / "data"),
            "PYTHONPATH": str(self.base_dir),
        })
        creationflags = 0
        startupinfo = None
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(self.base_dir),
                env=env,
                stdout=self._output_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
        except OSError as exc:
            self._close_output()
            raise LauncherError(f"Não foi possível iniciar o servidor: {exc}") from exc
        self.owned = True
        self.logger.info("Servidor local iniciado pelo launcher; PID=%s", self.process.pid)

    def wait_until_ready(self, endpoint: Endpoint, timeout: float = READY_TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if WebSocketProbe.check(endpoint):
                self.logger.info("Servidor confirmado por handshake WebSocket em %s", endpoint.websocket_url)
                return
            if self.process is not None and self.process.poll() is not None:
                code = self.process.returncode
                self.logger.error("Servidor encerrou durante a inicialização; código=%s", code)
                self._close_output()
                raise LauncherError(
                    "Não foi possível iniciar o servidor. "
                    "A porta pode estar ocupada ou há uma dependência ausente."
                )
            time.sleep(0.25)
        raise LauncherError(
            "O servidor não respondeu a tempo. Verifique a porta e o arquivo logs/server.log."
        )

    def stop(self) -> None:
        if not self.owned or self.process is None:
            return
        if self.process.poll() is None:
            self.logger.info("Encerrando somente o servidor iniciado por esta instância")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.logger.warning("Servidor não encerrou em 5s; finalizando o processo próprio")
                self.process.kill()
                self.process.wait(timeout=3)
        self._close_output()
        self.process = None
        self.owned = False

    def _close_output(self) -> None:
        if self._output_handle is not None:
            self._output_handle.close()
            self._output_handle = None


class QuietStaticHandler(SimpleHTTPRequestHandler):
    """Avoids writing request logs to a nonexistent console in windowed builds."""

    def log_message(self, format: str, *args: object) -> None:
        return


class FrontendServer:
    """Serves the compiled Hub without requiring Node/npm on end-user machines."""

    def __init__(self, public_dir: Path, logger: logging.Logger) -> None:
        self.public_dir = public_dir
        self.logger = logger
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.port: Optional[int] = None

    def start(self) -> int:
        if self.server is not None and self.port is not None:
            return self.port
        if not self.public_dir.is_dir():
            raise LauncherError("O frontend compilado não foi encontrado no pacote.")

        handler = partial(QuietStaticHandler, directory=str(self.public_dir))
        self.server = ThreadingHTTPServer(("127.0.0.1", DEFAULT_FRONTEND_PORT), handler)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="msn-frontend-server",
            daemon=True,
        )
        self.thread.start()
        self.logger.info("Frontend local disponível em http://127.0.0.1:%s", self.port)
        return self.port

    def url(self, endpoint: Endpoint) -> str:
        if self.port is None:
            raise LauncherError("O frontend ainda não foi iniciado.")
        return f"http://127.0.0.1:{self.port}/?server={quote(endpoint.websocket_url, safe='')}"

    def open(self, endpoint: Endpoint) -> str:
        url = self.url(endpoint)
        if not webbrowser.open(url):
            raise LauncherError(f"Não foi possível abrir o navegador. Acesse: {url}")
        self.logger.info("Hub aberto em %s", url)
        return url

    def stop(self) -> None:
        if self.server is not None:
            self.logger.info("Frontend encerrado")
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.port = None
            self.thread = None


class LauncherCoordinator:
    """Coordinates startup while keeping backend and frontend responsibilities separate."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = base_dir or application_dir()
        self.logger = build_logger(self.base_dir)
        self.config_store = ConfigStore(self.base_dir)
        self.server = LocalServerController(self.base_dir, self.logger)
        self.frontend = FrontendServer(self.base_dir / "client" / "public", self.logger)
        self.endpoint: Optional[Endpoint] = None
        self.mode: Optional[str] = None
        self.active = False

    def prepare(
        self,
        endpoint: Endpoint,
        mode: str,
        open_browser: bool = True,
        shutdown_local_server: bool = True,
    ) -> str:
        if mode not in {"local", "existing"}:
            raise LauncherError("Modo de conexão inválido.")
        self.logger.info("Preparando conexão: modo=%s endpoint=%s", mode, endpoint.websocket_url)
        if mode == "local" and endpoint.host not in {"localhost", "127.0.0.1", "::1"}:
            raise LauncherError("O modo local exige o endereço localhost.")
        self.endpoint = endpoint
        self.mode = mode
        try:
            if mode == "existing":
                if not WebSocketProbe.check(endpoint):
                    raise LauncherError(
                        "Não foi possível conectar ao servidor informado. "
                        "Confira o endereço e a porta."
                    )
            else:
                self.server.start(endpoint)
                self.server.wait_until_ready(endpoint)
            self.frontend.start()
            self.active = True
            self.config_store.save(LauncherConfig(
                host=endpoint.host,
                port=endpoint.port,
                mode=mode,
                shutdown_local_server=shutdown_local_server,
            ))
            if open_browser:
                return self.frontend.open(endpoint)
            return self.frontend.url(endpoint)
        except Exception:
            self.frontend.stop()
            if self.server.owned:
                self.server.stop()
            self.active = False
            raise

    def reopen_hub(self) -> str:
        if not self.active or self.endpoint is None:
            raise LauncherError("O Hub ainda não foi preparado.")
        return self.frontend.open(self.endpoint)

    def shutdown(self) -> None:
        self.logger.info("Encerrando launcher")
        self.frontend.stop()
        self.server.stop()
        self.active = False


class LauncherApp:
    def __init__(self, root: Tk, coordinator: LauncherCoordinator) -> None:
        self.root = root
        self.coordinator = coordinator
        self.config = coordinator.config_store.load()
        self.root.title(APP_NAME)
        self.root.geometry("500x430")
        self.root.minsize(460, 390)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.mode = StringVar(value=self.config.mode)
        self.host = StringVar(value=self.config.host)
        self.port = StringVar(value=str(self.config.port))
        self.status = StringVar(value="Escolha como deseja conectar.")
        self.detail = StringVar(value="Servidor local: ainda não verificado")
        self.stop_local_on_close = BooleanVar(value=self.config.shutdown_local_server)
        self.start_button: Optional[ttk.Button] = None
        self.open_button: Optional[ttk.Button] = None
        self._busy = False
        self._closing = False
        self._build()
        self.root.after(150, self.detect_server)
        self.root.after(1000, self.monitor_server)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=APP_NAME, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Escolha um servidor. O Hub será aberto quando a conexão real estiver pronta.",
            wraplength=430,
        ).pack(anchor="w", pady=(4, 18))

        choice = ttk.LabelFrame(outer, text="Como deseja conectar?", padding=12)
        choice.pack(fill="x")
        ttk.Radiobutton(
            choice, text="Iniciar ou usar servidor local", variable=self.mode,
            value="local", command=self.detect_server,
        ).pack(anchor="w")
        ttk.Radiobutton(
            choice, text="Entrar em servidor existente", variable=self.mode,
            value="existing", command=self.detect_server,
        ).pack(anchor="w", pady=(7, 0))

        endpoint = ttk.LabelFrame(outer, text="Servidor", padding=12)
        endpoint.pack(fill="x", pady=14)
        ttk.Label(endpoint, text="Endereço").grid(row=0, column=0, sticky="w")
        ttk.Entry(endpoint, textvariable=self.host, width=30).grid(row=1, column=0, sticky="ew", padx=(0, 10))
        ttk.Label(endpoint, text="Porta").grid(row=0, column=1, sticky="w")
        ttk.Entry(endpoint, textvariable=self.port, width=10).grid(row=1, column=1, sticky="ew")
        endpoint.columnconfigure(0, weight=1)
        ttk.Label(endpoint, textvariable=self.detail, wraplength=400).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

        options = ttk.Frame(outer)
        options.pack(fill="x")
        ttk.Checkbutton(
            options, text="Encerrar servidor local ao fechar o MSN",
            variable=self.stop_local_on_close,
        ).pack(anchor="w")

        self.start_button = ttk.Button(outer, text="Continuar", command=self.start)
        self.start_button.pack(fill="x", pady=(18, 7))
        self.open_button = ttk.Button(outer, text="Abrir Hub novamente", command=self.open_hub, state="disabled")
        self.open_button.pack(fill="x")
        ttk.Label(outer, textvariable=self.status, wraplength=430).pack(anchor="w", pady=(16, 0))

    def detect_server(self) -> None:
        try:
            endpoint = Endpoint.from_values(self.host.get(), self.port.get())
        except LauncherError as exc:
            self.detail.set(str(exc))
            return
        found = WebSocketProbe.check(endpoint)
        if found:
            self.detail.set(f"Servidor encontrado em {endpoint.websocket_url}")
        elif self.mode.get() == "local":
            self.detail.set("Nenhum servidor local encontrado; ele será iniciado ao continuar.")
        else:
            self.detail.set("Servidor não encontrado neste endereço.")

    def start(self) -> None:
        if self._busy:
            return
        try:
            endpoint = Endpoint.from_values(self.host.get(), self.port.get())
        except LauncherError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self.root)
            return
        self._busy = True
        self.status.set("Verificando servidor e preparando o Hub...")
        self.start_button.configure(state="disabled")
        thread = threading.Thread(
            target=self._prepare_background,
            args=(endpoint, self.mode.get(), self.stop_local_on_close.get()),
            daemon=True,
        )
        thread.start()

    def _prepare_background(self, endpoint: Endpoint, mode: str, shutdown_local_server: bool) -> None:
        try:
            url = self.coordinator.prepare(
                endpoint,
                mode,
                open_browser=True,
                shutdown_local_server=shutdown_local_server,
            )
        except LauncherError as exc:
            self.root.after(0, lambda: self._failed(str(exc)))
            return
        except Exception:
            self.coordinator.logger.exception("Falha inesperada no launcher")
            self.root.after(0, lambda: self._failed("Não foi possível preparar o MSN. Consulte logs/launcher.log."))
            return
        self.root.after(0, lambda: self._ready(url))

    def _failed(self, text: str) -> None:
        self._busy = False
        self.start_button.configure(state="normal")
        self.status.set(text)
        messagebox.showerror(APP_NAME, text, parent=self.root)

    def _ready(self, url: str) -> None:
        self._busy = False
        self.start_button.configure(text="Hub aberto", state="normal")
        self.open_button.configure(state="normal")
        self.status.set(f"MSN pronto. Hub aberto em {url}")
        self.detail.set("Servidor confirmado por WebSocket real.")

    def open_hub(self) -> None:
        try:
            url = self.coordinator.reopen_hub()
            self.status.set(f"Hub aberto em {url}")
        except LauncherError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self.root)

    def monitor_server(self) -> None:
        if self._closing:
            return
        if self.coordinator.server.owned and not self.coordinator.server.running and self.coordinator.active:
            self.coordinator.active = False
            self.status.set("O servidor local foi encerrado inesperadamente.")
            self.start_button.configure(text="Tentar novamente", state="normal")
            messagebox.showerror(
                APP_NAME,
                "O servidor local foi encerrado inesperadamente. Consulte logs/server.log.",
                parent=self.root,
            )
        self.root.after(1000, self.monitor_server)

    def close(self) -> None:
        if self._closing:
            return
        if self.coordinator.server.owned and self.coordinator.server.running and self.stop_local_on_close.get():
            should_close = messagebox.askyesno(
                APP_NAME,
                "O MSN iniciou um servidor local. Deseja encerrá-lo agora?",
                parent=self.root,
            )
            if not should_close:
                return
        self._closing = True
        try:
            self.coordinator.shutdown()
        finally:
            self.root.destroy()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MSN Messenger launcher")
    parser.add_argument("--probe", nargs=2, metavar=("HOST", "PORT"), help="testa um WebSocket e encerra")
    args = parser.parse_args(argv)
    if args.probe:
        try:
            endpoint = Endpoint.from_values(args.probe[0], args.probe[1])
        except LauncherError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        ready = WebSocketProbe.check(endpoint)
        print("Servidor encontrado" if ready else "Servidor não encontrado")
        return 0 if ready else 1

    guard = SingleInstanceGuard(application_dir() / "config" / "launcher.lock")
    if not guard.acquire():
        print("O MSN Messenger já está aberto nesta máquina.")
        return 0
    try:
        try:
            import tkinter as tk  # noqa: F401 — gives a clear import error below if unavailable
            root = Tk()
        except Exception as exc:
            print(
                "Não foi possível abrir a interface do launcher. "
                "Verifique se o Python foi instalado com Tcl/Tk.",
                file=sys.stderr,
            )
            print(f"Detalhe técnico: {exc}", file=sys.stderr)
            return 1
        LauncherApp(root, LauncherCoordinator())
        root.mainloop()
        return 0
    finally:
        guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
