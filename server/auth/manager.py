"""Account authentication and password-recovery workflows."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from server.auth.security import hash_password, verify_password
from server.email.service import EmailDeliveryError, send_password_reset_email
from server.shared_types import Session, User, new_id, now_iso

log = logging.getLogger("msn.auth")

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
_RESET_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _utcnow() -> datetime:
    """Current UTC time (mockable in tests)."""
    return datetime.now(timezone.utc)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.fullmatch(email))


def _hash_reset_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _new_reset_code() -> str:
    return "".join(secrets.choice(_RESET_ALPHABET) for _ in range(8))


class AuthError(Exception):
    """Controlled authentication failure with a user-safe message."""
    pass


EmailSender = Callable[[Any, str, str], Awaitable[None]]


class AuthManager:
    def __init__(self, store: Any, settings: Any, bus: Any,
                 email_sender: EmailSender = send_password_reset_email) -> None:
        self._store = store
        self._settings = settings
        self._bus = bus
        self._email_sender = email_sender
        self._reset_requests: dict[str, list[datetime]] = {}

    # -- registration --------------------------------------------------------

    async def register(self, username: str, display_name: str,
                       password: str, email: str) -> User:
        username = username.strip().lower()
        display_name = display_name.strip()[: self._settings.max_display_name_length]
        email = _normalize_email(email)

        if not username or not password or not email:
            raise AuthError("Usuário, e-mail e senha são obrigatórios.")
        if len(username) > self._settings.max_username_length:
            raise AuthError(f"Nome de usuário muito longo (máx. {self._settings.max_username_length}).")
        if len(password) < 6:
            raise AuthError("A senha deve ter no mínimo 6 caracteres.")
        if not _valid_email(email):
            raise AuthError("Informe um e-mail válido para recuperar sua conta.")

        if self._store.get_user_by_username(username):
            raise AuthError("Este nome de usuário já está em uso.")
        if self._store.get_user_by_email(email):
            raise AuthError("Este e-mail já está vinculado a outra conta.")

        user = User(
            user_id=new_id(),
            username=username,
            display_name=display_name,
            password_hash=hash_password(password),
            email=email,
        )
        self._store.create_user(
            user.user_id, user.username, user.display_name,
            user.password_hash, user.created_at, user.email,
        )
        return user

    # -- login ---------------------------------------------------------------

    async def authenticate(self, username: str, password: str) -> Session:
        username = username.strip().lower()
        if not username or not password:
            raise AuthError("Usuário e senha são obrigatórios.")

        row = self._store.get_user_by_username(username)
        if not row:
            raise AuthError("Usuário ou senha inválidos.")

        if not verify_password(row["password_hash"], password):
            raise AuthError("Usuário ou senha inválidos.")

        # A user is online from exactly one session at a time. The server core
        # is responsible for evicting the in-memory session.
        self._store.delete_user_sessions(row["user_id"])

        session = Session(session_id=new_id(), user_id=row["user_id"])
        self._store.upsert_session(
            session.session_id, session.user_id,
            session.started_at, session.last_seen_at,
        )
        return session

    # -- password recovery ---------------------------------------------------

    async def request_password_reset(self, email: str) -> None:
        """Create and email a short-lived code.

        The method deliberately has the same externally observable result for a
        missing address, an invalid address, an unconfigured SMTP service, and a
        delivery failure. This avoids turning the public recovery form into an
        account-enumeration oracle.
        """
        normalized_email = _normalize_email(email)
        if not _valid_email(normalized_email):
            return

        now = _utcnow()
        recent = [
            request_time for request_time in self._reset_requests.get(normalized_email, [])
            if now - request_time < timedelta(hours=1)
        ]
        if len(recent) >= self._settings.reset_requests_per_hour:
            return
        self._reset_requests[normalized_email] = recent + [now]

        user = self._store.get_user_by_email(normalized_email)
        if not user:
            return

        code = _new_reset_code()
        created_at = _utcnow()
        expires_at = created_at + timedelta(
            minutes=self._settings.reset_token_ttl_minutes
        )
        self._store.delete_password_reset_tokens_for_user(user["user_id"])
        self._store.create_password_reset(
            reset_id=new_id(),
            user_id=user["user_id"],
            token_hash=_hash_reset_code(code),
            expires_at=expires_at.isoformat(),
            created_at=created_at.isoformat(),
        )

        try:
            await self._email_sender(self._settings, normalized_email, code)
        except EmailDeliveryError:
            self._store.delete_password_reset_tokens_for_user(user["user_id"])
            log.warning("Password reset email delivery failed for user id %s", user["user_id"])
        except Exception:
            self._store.delete_password_reset_tokens_for_user(user["user_id"])
            log.exception("Unexpected password reset email failure for user id %s", user["user_id"])

    async def reset_password(self, email: str, code: str,
                             new_password: str) -> str:
        normalized_email = _normalize_email(email)
        normalized_code = code.strip().upper()
        if not _valid_email(normalized_email) or not normalized_code:
            raise AuthError("Código inválido ou expirado.")
        if len(new_password) < 6:
            raise AuthError("A nova senha deve ter no mínimo 6 caracteres.")

        user = self._store.get_user_by_email(normalized_email)
        if not user:
            raise AuthError("Código inválido ou expirado.")

        token = self._store.get_password_reset_for_user(user["user_id"])
        if token is None:
            raise AuthError("Código inválido ou expirado.")

        try:
            expires_at = datetime.fromisoformat(token["expires_at"])
        except (TypeError, ValueError):
            expires_at = _utcnow() - timedelta(seconds=1)

        if token["used_at"] is not None or _utcnow() >= expires_at:
            self._store.delete_password_reset_tokens_for_user(user["user_id"])
            raise AuthError("Código inválido ou expirado.")

        if int(token["attempts"]) >= self._settings.reset_max_attempts:
            self._store.delete_password_reset_tokens_for_user(user["user_id"])
            raise AuthError("Código inválido ou expirado.")

        if not secrets.compare_digest(token["token_hash"], _hash_reset_code(normalized_code)):
            attempts = self._store.increment_password_reset_attempts(token["reset_id"])
            if attempts >= self._settings.reset_max_attempts:
                self._store.delete_password_reset_tokens_for_user(user["user_id"])
            raise AuthError("Código inválido ou expirado.")

        completed = self._store.complete_password_reset(
            token["reset_id"], user["user_id"],
            hash_password(new_password), now_iso(),
        )
        if not completed:
            raise AuthError("Código inválido ou expirado.")
        log.info("Password reset completed for user id %s", user["user_id"])
        return user["user_id"]

    # -- session restore (reconnection) -------------------------------------

    async def restore_session(self, session_id: str) -> Optional[Session]:
        row = self._store.get_session(session_id)
        if not row:
            return None

        last_seen = datetime.fromisoformat(row["last_seen_at"])
        age = _utcnow() - last_seen
        if age.total_seconds() > (self._settings.session_ttl_minutes * 60):
            self._store.delete_session(session_id)
            return None

        return Session(
            session_id=row["session_id"],
            user_id=row["user_id"],
            started_at=row["started_at"],
            last_seen_at=row["last_seen_at"],
        )

    # -- logout --------------------------------------------------------------

    async def invalidate_session(self, session_id: str) -> None:
        self._store.delete_session(session_id)

    # -- activity ------------------------------------------------------------

    def touch_session(self, session: Session) -> None:
        """Update the session's last_seen_at in memory and SQLite."""
        session.last_seen_at = now_iso()
        self._store.touch_session(session.session_id, session.last_seen_at)
