"""Authentication: credential hashing and validation.

Password storage: argon2-cffi (memory-hard, OWASP-recommended). If argon2 is
not installed, a PBKDF2-SHA256 fallback from stdlib is used — never plaintext,
never bare MD5/SHA.
"""

from __future__ import annotations

import hashlib
import secrets

try:
    from argon2 import PasswordHasher, exceptions as argon2_exc  # type: ignore
    _HAS_ARGON2 = True
except ImportError:  # pragma: no cover - optional optimization
    _HAS_ARGON2 = False


class _FallbackHasher:
    """PBKDF2-SHA256 fallback with a 26-char hex salt, 600k iterations."""

    def hash(self, password: str) -> str:
        salt = secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("ascii"), 600_000
        )
        return f"pbkdf2_sha256${salt}${dk.hex()}"

    def verify(self, stored: str, password: str) -> bool:
        if not stored.startswith("pbkdf2_sha256$"):
            return False
        _, salt, expected = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("ascii"), 600_000
        )
        return secrets.compare_digest(dk.hex(), expected)


def _hasher():
    return PasswordHasher() if _HAS_ARGON2 else _FallbackHasher()


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        if _HAS_ARGON2 and stored_hash.startswith("$argon2"):
            return PasswordHasher().verify(stored_hash, password)
        return _FallbackHasher().verify(stored_hash, password)
    except (argon2_exc.VerifyMismatchError, argon2_exc.VerificationError, ValueError):
        return False
