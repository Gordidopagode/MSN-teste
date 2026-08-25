"""Shared test helpers: a fresh in-memory/temporary-db server core and
connection-id bookkeeping, so tests can simulate clients against the real
ServerCore without opening any socket."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from server.config.settings import ServerSettings
from server.core import ServerCore


def make_settings(tmpdir: str, **overrides: Any) -> ServerSettings:
    base = dict(
        host="127.0.0.1",
        port=9999,
        data_dir=tmpdir,
        max_message_length=20000,
        max_username_length=64,
        max_display_name_length=64,
        max_messages_per_minute=30,
        session_ttl_minutes=60,
        allowed_origins=None,
    )
    base.update(overrides)
    return ServerSettings(**base)


class FakeServer:
    """Wraps ServerCore with automatic sequential connection ids."""

    def __init__(self, settings: ServerSettings | None = None,
                 tmpdir: str | None = None) -> None:
        self.tmpdir = tmpdir or tempfile.mkdtemp(prefix="msn_test_")
        self.settings = settings or make_settings(self.tmpdir)
        self.core = ServerCore(self.settings)
        self._conn_seq = 0

    def new_connection(self) -> str:
        self._conn_seq += 1
        cid = f"conn_{self._conn_seq:03d}"
        self.core.client_connected(cid)
        return cid

    def pending_for(self, connection_id: str) -> list[dict[str, Any]]:
        return list(self.core.pending.get(connection_id, []))

    def find(self, connection_id: str, envelope_type: str) -> list[dict[str, Any]]:
        return [e for e in self.pending_for(connection_id)
                if e["type"] == envelope_type]

    def cleanup(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def flush(self, connection_id: str) -> None:
        self.core.pending.pop(connection_id, None)
