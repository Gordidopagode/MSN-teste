from __future__ import annotations

import base64
import binascii
from io import BytesIO
from typing import Any, Optional

from PIL import Image, ImageOps, UnidentifiedImageError

from server.persistence.store import Persistence

MAX_AVATAR_INPUT_BYTES = 256 * 1024
MAX_AVATAR_BYTES = 256 * 1024
MAX_AVATAR_DIMENSION = 4096
MAX_AVATAR_OUTPUT_DIMENSION = 512
MAX_CUSTOM_STATUS_LENGTH = 200


class ProfileError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _extract_data_url(data_url: str, declared_mime: str) -> tuple[bytes, str]:
    if not data_url.startswith("data:") or ";base64," not in data_url:
        raise ProfileError("INVALID_AVATAR", "A imagem enviada não está em um formato de dados válido.")
    header, encoded = data_url.split(",", 1)
    mime_from_data = header[5:].split(";", 1)[0].strip().lower()
    mime = (declared_mime.strip().lower() or mime_from_data)
    if not mime.startswith("image/") or mime_from_data != mime:
        raise ProfileError("INVALID_AVATAR_FORMAT", "O arquivo precisa ser uma imagem válida.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProfileError("INVALID_AVATAR", "Não foi possível ler a imagem enviada.") from exc
    if not raw:
        raise ProfileError("INVALID_AVATAR", "A imagem enviada está vazia.")
    if len(raw) > MAX_AVATAR_INPUT_BYTES:
        raise ProfileError("AVATAR_TOO_LARGE", "A imagem processada deve ter no máximo 256 KB.")
    return raw, mime


def _encode_avatar(raw: bytes) -> str:
    try:
        with Image.open(BytesIO(raw)) as source:
            source.load()
            width, height = source.size
            if width <= 0 or height <= 0 or width > MAX_AVATAR_DIMENSION or height > MAX_AVATAR_DIMENSION:
                raise ProfileError("AVATAR_DIMENSIONS", "A imagem possui dimensões muito grandes.")
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except ProfileError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ProfileError("INVALID_AVATAR", "O arquivo não contém uma imagem válida ou suportada.") from exc

    # JPEG is the canonical storage format. A white background keeps transparent
    # PNG/WebP/GIF pixels visually correct after conversion.
    background = Image.new("RGBA", image.size, (255, 255, 255, 255))
    background.alpha_composite(image)
    flattened = background.convert("RGB")

    for dimension in (MAX_AVATAR_OUTPUT_DIMENSION, 384, 256, 192):
        candidate = flattened.copy()
        candidate.thumbnail((dimension, dimension), Image.Resampling.LANCZOS)
        for quality in (86, 76, 66, 56, 46):
            output = BytesIO()
            candidate.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
            if output.tell() <= MAX_AVATAR_BYTES:
                encoded = base64.b64encode(output.getvalue()).decode("ascii")
                return f"data:image/jpeg;base64,{encoded}"

    raise ProfileError("AVATAR_TOO_LARGE", "Não foi possível reduzir a imagem para 256 KB.")


def validate_avatar(data_url: str, filename: str, mime: str) -> tuple[str, str]:
    """Decode and normalize an uploaded image, independent of its extension.

    ``filename`` is accepted for protocol compatibility but is not trusted for
    validation. The actual bytes are decoded by Pillow and stored as canonical
    JPEG, preventing spoofed MIME types and unsupported binary payloads.
    """
    del filename
    if not data_url:
        return "", ""
    raw, _ = _extract_data_url(data_url, mime)
    return _encode_avatar(raw), "image/jpeg"


class ProfileManager:
    def __init__(self, store: Persistence) -> None:
        self.store = store

    def set_avatar(self, user_id: str, data_url: str, filename: str, mime: str) -> dict[str, Any]:
        data, normalized_mime = validate_avatar(data_url, filename, mime)
        self.store.update_avatar(user_id, data or None, normalized_mime or None)
        user = self.store.get_user(user_id)
        assert user is not None
        return user

    def set_custom_status(self, user_id: str, message: str) -> dict[str, Any]:
        value = message.strip()
        if len(value) > MAX_CUSTOM_STATUS_LENGTH:
            raise ProfileError("STATUS_TOO_LONG", "O status personalizado deve ter no máximo 200 caracteres.")
        self.store.update_custom_status(user_id, value)
        user = self.store.get_user(user_id)
        assert user is not None
        return user

    @staticmethod
    def public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": user["user_id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "avatar_data": user.get("avatar_data"),
            "avatar_mime": user.get("avatar_mime"),
            "custom_status": user.get("custom_status") or "",
        }
