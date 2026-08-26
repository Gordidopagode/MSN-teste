from __future__ import annotations

import hashlib
import hmac
import mimetypes
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from server.shared_types import now_iso
from urllib.parse import urlencode


class AttachmentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


DEFAULT_ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/zip",
    "application/json",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class AttachmentManager:
    """Persistent local attachment storage.

    Bytes live under ``<data_dir>/attachments`` and SQLite stores only metadata.
    Upload state is kept in memory and is bound to the authenticated connection;
    completed attachments are durable and are authorized by the core.
    """

    def __init__(self, store: Any, settings: Any) -> None:
        self.store = store
        self.settings = settings
        self.root = store.db_path.parent / "attachments"
        self.root.mkdir(parents=True, exist_ok=True)
        self._uploads: dict[str, dict[str, Any]] = {}
        self._secret = self._load_or_create_secret()

    def _load_or_create_secret(self) -> bytes:
        path = self.store.db_path.parent / ".attachment-signing-key"
        try:
            if path.exists():
                value = path.read_bytes()
                if len(value) >= 32:
                    return value
            value = secrets.token_bytes(32)
            path.write_bytes(value)
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return value
        except OSError as exc:
            raise AttachmentError("ATTACHMENT_STORAGE_UNAVAILABLE", "O armazenamento de anexos não está disponível.") from exc

    @property
    def max_bytes(self) -> int:
        return int(getattr(self.settings, "attachment_max_bytes", 25 * 1024 * 1024))

    @property
    def chunk_bytes(self) -> int:
        return int(getattr(self.settings, "attachment_chunk_bytes", 128 * 1024))

    @property
    def allowed_mime_types(self) -> set[str]:
        configured = getattr(self.settings, "attachment_allowed_mime_types", None)
        return set(configured or DEFAULT_ALLOWED_MIME_TYPES)

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        value = Path(filename or "arquivo").name.replace("\x00", "").strip()
        value = "".join(char for char in value if char.isprintable())
        if not value or value in {".", ".."}:
            raise AttachmentError("INVALID_ATTACHMENT_NAME", "O nome do arquivo é inválido.")
        if len(value) > 180:
            suffix = Path(value).suffix[:20]
            value = value[: 180 - len(suffix)] + suffix
        return value

    def _validate_metadata(self, filename: str, mime: str, size: int) -> tuple[str, str, int]:
        clean_name = self.sanitize_filename(filename)
        clean_mime = (mime or mimetypes.guess_type(clean_name)[0] or "application/octet-stream").lower().split(";", 1)[0].strip()
        if clean_mime not in self.allowed_mime_types:
            raise AttachmentError("ATTACHMENT_MIME_NOT_ALLOWED", "Este tipo de arquivo não é permitido.")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise AttachmentError("INVALID_ATTACHMENT_SIZE", "O tamanho do arquivo é inválido.")
        if size > self.max_bytes:
            raise AttachmentError("ATTACHMENT_TOO_LARGE", f"O arquivo deve ter no máximo {self.max_bytes} bytes.")
        return clean_name, clean_mime, size

    def begin_upload(self, connection_id: str, user_id: str, conversation_id: str,
                     filename: str, mime: str, size: int) -> dict[str, Any]:
        clean_name, clean_mime, clean_size = self._validate_metadata(filename, mime, size)
        upload_id = secrets.token_urlsafe(18)
        temp_dir = self.root / ".uploads"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / f"{upload_id}.part"
        temp_path.touch()
        self._uploads[upload_id] = {
            "connection_id": connection_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "filename": clean_name,
            "mime": clean_mime,
            "size": clean_size,
            "received": 0,
            "path": temp_path,
            "started": time.time(),
        }
        return {"upload_id": upload_id, "chunk_size": self.chunk_bytes, "max_bytes": self.max_bytes}

    def assert_upload_owner(self, connection_id: str, user_id: str, upload_id: str) -> None:
        upload = self._uploads.get(upload_id)
        if upload is None or upload["connection_id"] != connection_id:
            raise AttachmentError("UPLOAD_NOT_FOUND", "Upload não encontrado.")
        if upload["user_id"] != user_id:
            raise AttachmentError("UPLOAD_FORBIDDEN", "Este upload não pertence a essa conta.")

    def upload_conversation(self, connection_id: str, user_id: str, upload_id: str) -> str:
        self.assert_upload_owner(connection_id, user_id, upload_id)
        return str(self._uploads[upload_id]["conversation_id"])

    def append_chunk(self, connection_id: str, upload_id: str, data: bytes) -> dict[str, Any]:
        upload = self._uploads.get(upload_id)
        if upload is None or upload["connection_id"] != connection_id:
            raise AttachmentError("UPLOAD_NOT_FOUND", "Upload não encontrado.")
        if not data or len(data) > self.chunk_bytes:
            raise AttachmentError("INVALID_UPLOAD_CHUNK", "Bloco de upload inválido.")
        if upload["received"] + len(data) > upload["size"]:
            raise AttachmentError("UPLOAD_SIZE_MISMATCH", "O upload excede o tamanho declarado.")
        with upload["path"].open("ab") as handle:
            handle.write(data)
        upload["received"] += len(data)
        return {"upload_id": upload_id, "received": upload["received"], "size": upload["size"]}

    def abort_upload(self, connection_id: str, upload_id: str) -> None:
        upload = self._uploads.get(upload_id)
        if upload is None:
            return
        if upload["connection_id"] != connection_id:
            raise AttachmentError("UPLOAD_NOT_FOUND", "Upload não encontrado.")
        self._discard_upload(upload_id)

    def finish_upload(self, connection_id: str, upload_id: str) -> dict[str, Any]:
        upload = self._uploads.get(upload_id)
        if upload is None or upload["connection_id"] != connection_id:
            raise AttachmentError("UPLOAD_NOT_FOUND", "Upload não encontrado.")
        if upload["received"] != upload["size"]:
            raise AttachmentError("UPLOAD_INCOMPLETE", "O upload ainda não foi concluído.")
        attachment_id = secrets.token_urlsafe(18)
        final_dir = self.root / attachment_id[:2]
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / attachment_id
        upload["path"].replace(final_path)
        digest = hashlib.sha256(final_path.read_bytes()).hexdigest()
        attachment = {
            "attachment_id": attachment_id,
            "conversation_id": upload["conversation_id"],
            "owner_id": upload["user_id"],
            "original_name": upload["filename"],
            "mime_type": upload["mime"],
            "size": upload["size"],
            "storage_ref": str(final_path.relative_to(self.root)),
            "sha256": digest,
            "created_at": now_iso(),
        }
        try:
            self.store.create_attachment(attachment)
        except Exception:
            final_path.unlink(missing_ok=True)
            raise
        self._uploads.pop(upload_id, None)
        return attachment

    def _discard_upload(self, upload_id: str) -> None:
        upload = self._uploads.pop(upload_id, None)
        if upload:
            Path(upload["path"]).unlink(missing_ok=True)

    def discard_connection_uploads(self, connection_id: str) -> None:
        for upload_id, upload in list(self._uploads.items()):
            if upload["connection_id"] == connection_id:
                self._discard_upload(upload_id)

    def get_attachment(self, attachment_id: str) -> Optional[dict[str, Any]]:
        return self.store.get_attachment(attachment_id)

    def open_attachment(self, attachment: dict[str, Any]):
        path = (self.root / attachment["storage_ref"]).resolve()
        if self.root.resolve() not in path.parents or not path.is_file():
            raise AttachmentError("ATTACHMENT_NOT_FOUND", "Anexo não encontrado.")
        return path.open("rb")

    def signed_download_url(self, attachment_id: str, user_id: str,
                            base_url: str = "") -> str:
        expires = int(time.time()) + 3600
        message = f"{attachment_id}:{user_id}:{expires}".encode()
        signature = hmac.new(self._secret, message, hashlib.sha256).hexdigest()
        query = urlencode({"user": user_id, "expires": expires, "sig": signature})
        return f"{base_url.rstrip('/')}/attachments/{attachment_id}?{query}"

    def verify_download_signature(self, attachment_id: str, user_id: str,
                                  expires: str, signature: str) -> bool:
        try:
            expiry = int(expires)
        except (TypeError, ValueError):
            return False
        if expiry < int(time.time()) or not signature or not user_id:
            return False
        message = f"{attachment_id}:{user_id}:{expiry}".encode()
        expected = hmac.new(self._secret, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
