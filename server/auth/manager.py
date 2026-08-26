"""Account authentication and password-recovery workflows."""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from server.auth.security import hash_password, verify_password
from server.shared_types import Session, User, new_id, now_iso

log = logging.getLogger("msn.auth")

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_RECOVERY_CODE_LENGTH = 16


def _utcnow() -> datetime:
    """Current UTC time (mockable in tests)."""
    return datetime.now(timezone.utc)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.fullmatch(email))


def _new_recovery_code() -> str:
    return "".join(
        secrets.choice(_RECOVERY_ALPHABET)
        for _ in range(_RECOVERY_CODE_LENGTH)
    )


class AuthError(Exception):
    """Controlled authentication failure with a user-safe message."""
    pass


@dataclass(frozen=True)
class RegistrationResult:
    user: User
    recovery_code: str


class AuthManager:
    def __init__(self, store: Any, settings: Any, bus: Any) -> None:
        self._store = store
        self._settings = settings
        self._bus = bus

    # -- registration --------------------------------------------------------

    async def register(self, username: str, display_name: str,
                       password: str, email: Optional[str] = None) -> RegistrationResult:
        username = username.strip().lower()
        display_name = display_name.strip()[: self._settings.max_display_name_length]
        email = _normalize_email(email) if email else None

        if not username or not password:
            raise AuthError("Usuário e senha são obrigatórios.")
        if len(username) > self._settings.max_username_length:
            raise AuthError(f"Nome de usuário muito longo (máx. {self._settings.max_username_length}).")
        if len(password) < 6:
            raise AuthError("A senha deve ter no mínimo 6 caracteres.")
        if email is not None and not _valid_email(email):
            raise AuthError("Informe um e-mail válido.")

        if self._store.get_user_by_username(username):
            raise AuthError("Este nome de usuário já está em uso.")
        if email is not None and self._store.get_user_by_email(email):
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
        recovery_code = self._create_recovery_code(user.user_id)
        return RegistrationResult(user=user, recovery_code=recovery_code)

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
        session.recovery_code = self._ensure_recovery_code(row["user_id"])
        self._store.upsert_session(
            session.session_id, session.user_id,
            session.started_at, session.last_seen_at,
        )
        return session

    def _create_recovery_code(self, user_id: str) -> str:
        for _ in range(5):
            code = _new_recovery_code()
            if self._store.create_recovery_code(
                recovery_id=new_id(),
                user_id=user_id,
                code_hash=hash_password(code),
                created_at=now_iso(),
            ):
                return code
        raise AuthError("Não foi possível criar o código de recuperação.")

    def _ensure_recovery_code(self, user_id: str) -> Optional[str]:
        if self._store.get_recovery_code_for_user(user_id) is not None:
            return None
        try:
            return self._create_recovery_code(user_id)
        except AuthError:
            # A concurrent login may have created the record between the
            # initial lookup and the INSERT OR IGNORE. Do not fail login.
            if self._store.get_recovery_code_for_user(user_id) is not None:
                return None
            raise

    # -- password recovery ---------------------------------------------------

    async def reset_password(self, username: str, code: str,
                             new_password: str) -> str:
        normalized_username = username.strip().lower()
        normalized_code = code.strip().upper()
        if not normalized_username or not normalized_code:
            raise AuthError("Código inválido ou expirado.")
        if len(normalized_code) > 64:
            raise AuthError("Código inválido ou expirado.")
        if len(new_password) < 6:
            raise AuthError("A nova senha deve ter no mínimo 6 caracteres.")

        user = self._store.get_user_by_username(normalized_username)
        if not user:
            raise AuthError("Código inválido ou expirado.")

        record = self._store.get_recovery_code_for_user(user["user_id"])
        if record is None:
            raise AuthError("Código inválido ou expirado.")
        if record["used_at"] is not None:
            raise AuthError("Código inválido ou expirado.")
        if int(record["attempts"]) >= self._settings.reset_max_attempts:
            raise AuthError("Código inválido ou expirado.")

        if not verify_password(record["code_hash"], normalized_code):
            attempts = self._store.increment_recovery_code_attempts(record["recovery_id"])
            if attempts >= self._settings.reset_max_attempts:
                log.warning("Recovery-code attempt limit reached for user id %s", user["user_id"])
            raise AuthError("Código inválido ou expirado.")

        completed = self._store.complete_recovery_code(
            record["recovery_id"], user["user_id"],
            hash_password(new_password), now_iso(),
        )
        if not completed:
            raise AuthError("Código inválido ou expirado.")
        log.info("Password reset completed for user id %s", user["user_id"])
        return user["user_id"]

    # -- account settings -----------------------------------------------------

    async def update_display_name(self, user_id: str, display_name: str) -> dict[str, Any]:
        value = display_name.strip()
        if not value:
            raise AuthError("O nome exibido é obrigatório.")
        if len(value) > self._settings.max_display_name_length:
            raise AuthError(f"Nome exibido muito longo (máx. {self._settings.max_display_name_length}).")
        user = self._store.get_user(user_id)
        if user is None:
            raise AuthError("Usuário não encontrado.")
        self._store.update_display_name(user_id, value)
        updated = self._store.get_user(user_id)
        assert updated is not None
        return updated

    async def change_password(self, user_id: str, current_password: str,
                              new_password: str, keep_session_id: Optional[str] = None) -> None:
        if len(new_password) < 6:
            raise AuthError("A nova senha deve ter no mínimo 6 caracteres.")
        user = self._store.get_user(user_id)
        if user is None or not verify_password(user["password_hash"], current_password):
            raise AuthError("A senha atual está incorreta.")
        self._store.update_password_hash(user_id, hash_password(new_password))
        if keep_session_id:
            self._store.delete_user_sessions_except(user_id, keep_session_id)
        else:
            self._store.delete_user_sessions(user_id)

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
