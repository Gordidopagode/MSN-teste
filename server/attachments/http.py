from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qs, quote, urlsplit

from server.attachments.manager import AttachmentError

log = logging.getLogger("msn.attachments.http")


class AttachmentHTTPServer:
    """Minimal dependency-free HTTP listener for signed attachment downloads.

    The WebSocket server intentionally handles only WebSocket upgrades. This
    listener serves ordinary browser GET requests on a separate port, using the
    same core authorization and persistent attachment storage.
    """

    def __init__(self, core) -> None:
        self.core = core
        self.server: asyncio.AbstractServer | None = None

    async def start(self, host: str, port: int) -> None:
        self.server = await asyncio.start_server(
            self._handle_client,
            host,
            port,
            limit=64 * 1024,
        )

    @property
    def sockets(self):
        return self.server.sockets if self.server else []

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    @staticmethod
    def _response(status: str, body: bytes, headers: dict[str, str] | None = None) -> bytes:
        values = {
            "Content-Length": str(len(body)),
            "Connection": "close",
            "Content-Type": "text/plain; charset=utf-8",
        }
        if headers:
            values.update(headers)
        lines = [f"HTTP/1.1 {status}", *[f"{key}: {value}" for key, value in values.items()], "", ""]
        return ("\r\n".join(lines)).encode("ascii", "replace") + body

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            request_line = raw.split(b"\r\n", 1)[0].decode("ascii", "replace")
            parts = request_line.split(" ", 2)
            if len(parts) != 3 or parts[0] != "GET" or parts[2] not in {"HTTP/1.0", "HTTP/1.1"}:
                response = self._response("405 Method Not Allowed", b"Method not allowed")
            else:
                response = self._download_response(parts[1])
            writer.write(response)
            await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            pass
        except Exception:
            log.exception("Falha interna ao servir anexo")
            try:
                writer.write(self._response("500 Internal Server Error", b"Internal server error"))
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _download_response(self, request_target: str) -> bytes:
        parsed = urlsplit(request_target)
        prefix = "/attachments/"
        if not parsed.path.startswith(prefix):
            return self._response("404 Not Found", b"Not found")
        attachment_id = parsed.path[len(prefix):].strip("/")
        query = parse_qs(parsed.query)
        user_id = query.get("user", [""])[0]
        expires = query.get("expires", [""])[0]
        signature = query.get("sig", [""])[0]
        inline = query.get("inline", [""])[0]
        if not attachment_id or not self.core.attachments.verify_download_signature(attachment_id, user_id, expires, signature, inline):
            return self._response("403 Forbidden", b"Download not authorized")
        attachment = self.core.attachments.get_attachment(attachment_id)
        if attachment is None or not self.core.conversations.is_participant(attachment["conversation_id"], user_id):
            return self._response("404 Not Found", b"Attachment not found")
        try:
            with self.core.attachments.open_attachment(attachment) as handle:
                body = handle.read()
        except (AttachmentError, OSError):
            return self._response("404 Not Found", b"Attachment not found")
        filename = attachment["original_name"].replace('"', "_").replace("\r", "_").replace("\n", "_")
        ascii_filename = "".join(char if 32 <= ord(char) < 127 else "_" for char in filename)
        disposition = "inline" if inline == "1" and self.core.attachments.preview_kind(attachment) else "attachment"
        encoded_filename = quote(filename, safe="!#$&+-.^_`|~")
        content_disposition = (
            f'{disposition}; filename="{ascii_filename}"; '
            f"filename*=UTF-8''{encoded_filename}"
        )
        return self._response(
            "200 OK",
            body,
            {
                "Content-Type": attachment["mime_type"],
                "Content-Disposition": content_disposition,
                "Cache-Control": "private, max-age=3600",
            },
        )
