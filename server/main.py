"""Server bootstrap: configuration, logging, initialization, clean shutdown.

Startup sequence (see specification, section 3):
  load config -> validate -> init components -> open storage -> start network
  -> await connections

Any essential component failing to start aborts the whole process with a
controlled error message (the server never runs half-initialized).

Shutdown: on SIGINT/SIGTERM, the asyncio loop closes the WebSocket server,
flushes, and exits cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from server.config.settings import ServerSettings
from server.core import ServerCore
from server.network.handler import WebSocketHandler
from websockets.asyncio.server import serve


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


def validate_settings(settings: ServerSettings) -> None:
    """Fail-fast checks for essential configuration."""
    if not settings.host:
        raise ValueError("MSN_HOST não pode ser vazio.")
    if not (1 <= settings.port <= 65535):
        raise ValueError(f"Porta inválida: {settings.port}")
    if settings.max_message_length <= 0:
        raise ValueError("MSN_MAX_MESSAGE_LENGTH deve ser positivo.")


async def run_server(settings: ServerSettings) -> None:
    setup_logging()
    log = logging.getLogger("msn.main")

    validate_settings(settings)

    log.info("Iniciando MSN Messenger Server...")
    log.info("Host: %s | Porta: %d | Dados: %s",
             settings.host, settings.port, settings.data_dir)

    core = ServerCore(settings)
    ws_handler = WebSocketHandler(core)

    stop = asyncio.get_running_loop().create_future()

    def _request_stop() -> None:
        if not stop.done():
            stop.set_result(None)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # Windows
            pass

    async with serve(
        ws_handler.handle,
        settings.host,
        settings.port,
        ping_interval=20,
        ping_timeout=60,
        max_size=256 * 1024,
        origins=settings.allowed_origins,
    ) as server:
        log.info("Servidor pronto. Aguardando conexões em %s:%d",
                 settings.host, settings.port)
        await stop
        log.info("Desligamento solicitado. Encerrando conexões...")

    log.info("MSN Messenger Server desligado com sucesso.")


def main() -> None:
    settings = ServerSettings.from_env()
    try:
        asyncio.run(run_server(settings))
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:  # controlled startup failure
        logging.getLogger("msn.main").error(
            "Falha durante a inicialização: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
