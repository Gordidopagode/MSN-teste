"""SQLite persistence layer.

Choice justification: SQLite is a zero-administration embedded database —
perfect for a small private server with ~5 users and no distributed needs.
All writes are journal-guarded (WAL mode) and the connection is opened with
foreign keys enabled.

Schema:
- users:         accounts + hashed credentials (never plaintext passwords)
- sessions:      authenticated sessions (can outlive a connection)
- recovery_codes: one-time recovery-code hashes and attempt state
- conversations: 1:1 and group conversations
- participants: m:n user <-> conversation membership
- messages:     full message history
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("msn.persistence")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


class Persistence:
    """Thin, synchronous SQLite store.

    The store itself is thread-safe via a per-call connection (SQLite handles
    WAL concurrency well at this scale). Async wrappers are provided for the
    event-loop side.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        display_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        email TEXT,
        created_at TEXT NOT NULL,
        avatar_data TEXT,
        avatar_mime TEXT,
        custom_status TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS friendships (
        friendship_id TEXT PRIMARY KEY,
        user_a_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        user_b_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK(status IN ('pending', 'accepted')),
        requested_by TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(user_a_id, user_b_id)
    );

    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        started_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        reset_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        token_hash TEXT UNIQUE NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        used_at TEXT
    );

    CREATE TABLE IF NOT EXISTS recovery_codes (
        recovery_id TEXT PRIMARY KEY,
        user_id TEXT UNIQUE NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        code_hash TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        used_at TEXT
    );

    CREATE TABLE IF NOT EXISTS conversations (
        conversation_id TEXT PRIMARY KEY,
        name TEXT,
        is_group INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        last_message_at TEXT
    );

    CREATE TABLE IF NOT EXISTS participants (
        conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        PRIMARY KEY (conversation_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS user_presence (
        user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'offline',
        status_message TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS messages (
        message_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        sender_id TEXT NOT NULL REFERENCES users(user_id),
        timestamp TEXT NOT NULL,
        type TEXT NOT NULL DEFAULT 'text',
        payload TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}'
    );
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(db_path)
        for statement in self.SCHEMA.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(statement)
        self._migrate_users_table(conn)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        conn.commit()
        conn.close()

    @staticmethod
    def _migrate_users_table(conn: sqlite3.Connection) -> None:
        """Add profile columns to databases created before profile support."""
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "email" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        if "avatar_data" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN avatar_data TEXT")
        if "avatar_mime" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN avatar_mime TEXT")
        if "custom_status" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN custom_status TEXT NOT NULL DEFAULT ''"
            )

    def _conn(self) -> sqlite3.Connection:
        return _connect(self.db_path)

    # -- users ---------------------------------------------------------------

    def create_user(self, user_id: str, username: str, display_name: str,
                    password_hash: str, created_at: str, email: str | None = None) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO users "
                "(user_id, username, display_name, password_hash, email, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, display_name, password_hash, email, created_at),
            )
            conn.commit()
        finally:
            conn.close()

    def get_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_user_by_username(self, username: str) -> Optional[dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_avatar(self, user_id: str, avatar_data: Optional[str],
                      avatar_mime: Optional[str]) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE users SET avatar_data = ?, avatar_mime = ? WHERE user_id = ?",
                (avatar_data, avatar_mime, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def update_custom_status(self, user_id: str, custom_status: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE users SET custom_status = ? WHERE user_id = ?",
                (custom_status, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def list_users(self) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT user_id, username, display_name, avatar_data, avatar_mime, "
                "custom_status FROM users ORDER BY username"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def search_users(self, query: str, exclude_user_id: str,
                     limit: int = 20) -> list[dict[str, Any]]:
        term = query.strip().lower()
        if not term:
            return []
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT user_id, username, display_name, avatar_data, avatar_mime, "
                "custom_status FROM users "
                "WHERE user_id != ? AND (username LIKE ? OR display_name LIKE ?) "
                "ORDER BY username LIMIT ?",
                (exclude_user_id, f"%{term}%", f"%{term}%", limit),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    # -- password recovery ---------------------------------------------------

    def create_recovery_code(self, recovery_id: str, user_id: str,
                             code_hash: str, created_at: str) -> bool:
        """Create one recovery-code record, without replacing an existing one."""
        conn = self._conn()
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO recovery_codes "
                "(recovery_id, user_id, code_hash, created_at) VALUES (?, ?, ?, ?)",
                (recovery_id, user_id, code_hash, created_at),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def get_recovery_code_for_user(self, user_id: str) -> Optional[dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM recovery_codes WHERE user_id = ? LIMIT 1",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def increment_recovery_code_attempts(self, recovery_id: str) -> int:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE recovery_codes SET attempts = attempts + 1 "
                "WHERE recovery_id = ? AND used_at IS NULL",
                (recovery_id,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT attempts FROM recovery_codes WHERE recovery_id = ?",
                (recovery_id,),
            ).fetchone()
            return int(row["attempts"]) if row else 0
        finally:
            conn.close()

    def complete_recovery_code(self, recovery_id: str, user_id: str,
                               password_hash: str, used_at: str) -> bool:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            record = conn.execute(
                "SELECT recovery_id FROM recovery_codes "
                "WHERE recovery_id = ? AND user_id = ? AND used_at IS NULL",
                (recovery_id, user_id),
            ).fetchone()
            if record is None:
                conn.rollback()
                return False
            updated = conn.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                (password_hash, user_id),
            ).rowcount
            if updated != 1:
                conn.rollback()
                return False
            conn.execute(
                "UPDATE recovery_codes SET used_at = ? WHERE recovery_id = ?",
                (used_at, recovery_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete_password_reset_tokens_for_user(self, user_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()

    def create_password_reset(self, reset_id: str, user_id: str,
                              token_hash: str, expires_at: str,
                              created_at: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO password_reset_tokens "
                "(reset_id, user_id, token_hash, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (reset_id, user_id, token_hash, expires_at, created_at),
            )
            conn.commit()
        finally:
            conn.close()

    def get_password_reset_by_hash(self, token_hash: str) -> Optional[dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM password_reset_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_password_reset_for_user(self, user_id: str) -> Optional[dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM password_reset_tokens WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def increment_password_reset_attempts(self, reset_id: str) -> int:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE password_reset_tokens SET attempts = attempts + 1 "
                "WHERE reset_id = ? AND used_at IS NULL",
                (reset_id,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT attempts FROM password_reset_tokens WHERE reset_id = ?",
                (reset_id,),
            ).fetchone()
            return int(row["attempts"]) if row else 0
        finally:
            conn.close()

    def complete_password_reset(self, reset_id: str, user_id: str,
                                password_hash: str, used_at: str) -> bool:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            token = conn.execute(
                "SELECT reset_id FROM password_reset_tokens "
                "WHERE reset_id = ? AND user_id = ? AND used_at IS NULL",
                (reset_id, user_id),
            ).fetchone()
            if token is None:
                conn.rollback()
                return False
            updated = conn.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                (password_hash, user_id),
            ).rowcount
            if updated != 1:
                conn.rollback()
                return False
            conn.execute(
                "UPDATE password_reset_tokens SET used_at = ? WHERE reset_id = ?",
                (used_at, reset_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.execute(
                "DELETE FROM password_reset_tokens WHERE user_id = ? AND reset_id != ?",
                (user_id, reset_id),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- friendships ---------------------------------------------------------

    def create_friendship(self, friendship_id: str, user_a_id: str,
                          user_b_id: str, status: str, requested_by: str,
                          created_at: str, updated_at: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO friendships "
                "(friendship_id, user_a_id, user_b_id, status, requested_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (friendship_id, user_a_id, user_b_id, status, requested_by,
                 created_at, updated_at),
            )
            conn.commit()
        finally:
            conn.close()

    def get_friendship_between(self, user_a_id: str,
                               user_b_id: str) -> Optional[dict[str, Any]]:
        left, right = sorted((user_a_id, user_b_id))
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM friendships WHERE user_a_id = ? AND user_b_id = ?",
                (left, right),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_friendship(self, friendship_id: str) -> Optional[dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM friendships WHERE friendship_id = ?",
                (friendship_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_friendships(self, user_id: str) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT f.*, "
                "CASE WHEN f.user_a_id = ? THEN f.user_b_id ELSE f.user_a_id END "
                "AS other_user_id, "
                "u.username AS other_username, u.display_name AS other_display_name, "
                "u.avatar_data AS other_avatar_data, u.avatar_mime AS other_avatar_mime, "
                "u.custom_status AS other_custom_status "
                "FROM friendships f JOIN users u ON u.user_id = "
                "CASE WHEN f.user_a_id = ? THEN f.user_b_id ELSE f.user_a_id END "
                "WHERE f.user_a_id = ? OR f.user_b_id = ? "
                "ORDER BY f.updated_at DESC",
                (user_id, user_id, user_id, user_id),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def update_friendship_status(self, friendship_id: str, status: str,
                                 updated_at: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE friendships SET status = ?, updated_at = ? "
                "WHERE friendship_id = ?",
                (status, updated_at, friendship_id),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_friendship(self, friendship_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "DELETE FROM friendships WHERE friendship_id = ?",
                (friendship_id,),
            )
            conn.commit()
        finally:
            conn.close()

    # -- sessions ------------------------------------------------------------

    def upsert_session(self, session_id: str, user_id: str, started_at: str,
                       last_seen_at: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?)",
                (session_id, user_id, started_at, last_seen_at),
            )
            conn.commit()
        finally:
            conn.close()

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def touch_session(self, session_id: str, last_seen_at: str) -> None:
        """Update the session's last_seen_at timestamp (keep-alive heartbeat
        for the session TTL — stabilization item 5)."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
                (last_seen_at, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_message(self, message_id: str) -> Optional[dict[str, Any]]:
        """Load a persisted message by id (for message_id collision detection
        — stabilization item 11). payload/metadata are returned as JSON
        objects, matching list_conversation_messages."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if row is None:
                return None
            msg = dict(row)
            msg["payload"] = json.loads(msg["payload"])
            msg["metadata"] = json.loads(msg["metadata"])
            return msg
        finally:
            conn.close()

    def delete_session(self, session_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
        finally:
            conn.close()

    def delete_user_sessions(self, user_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()

    # -- conversations -------------------------------------------------------

    def create_conversation(self, conversation_id: str, name: Optional[str],
                            is_group: bool, participants: list[str],
                            created_at: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, NULL)",
                (conversation_id, name, 1 if is_group else 0, created_at),
            )
            conn.executemany(
                "INSERT INTO participants VALUES (?, ?)",
                [(conversation_id, pid) for pid in participants],
            )
            conn.commit()
        finally:
            conn.close()

    def get_conversation(self, conversation_id: str) -> Optional[dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if not row:
                return None
            conv = dict(row)
            conv["is_group"] = bool(conv["is_group"])
            conv["participants"] = [
                r["user_id"]
                for r in conn.execute(
                    "SELECT user_id FROM participants WHERE conversation_id = ?",
                    (conversation_id,),
                )
            ]
            return conv
        finally:
            conn.close()

    def list_user_conversations(self, user_id: str) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT c.* FROM conversations c JOIN participants p "
                "ON c.conversation_id = p.conversation_id WHERE p.user_id = ?",
                (user_id,),
            ).fetchall()
            result = []
            for r in rows:
                conv = dict(r)
                conv["is_group"] = bool(conv["is_group"])
                conv["participants"] = [
                    pr["user_id"]
                    for pr in conn.execute(
                        "SELECT user_id FROM participants WHERE conversation_id = ?",
                        (r["conversation_id"],),
                    )
                ]
                result.append(conv)
            return result
        finally:
            conn.close()

    def find_conversation(self, user_a: str, user_b: str) -> Optional[str]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT conversation_id FROM conversations c "
                "WHERE c.is_group = 0 "
                "AND (SELECT COUNT(*) FROM participants p "
                "     WHERE p.conversation_id = c.conversation_id "
                "     AND p.user_id IN (?, ?)) = 2 "
                "AND (SELECT COUNT(*) FROM participants p "
                "     WHERE p.conversation_id = c.conversation_id) = 2",
                (user_a, user_b),
            ).fetchone()
            return row["conversation_id"] if row else None
        finally:
            conn.close()

    # -- presence persistence -------------------------------------------------

    def save_presence(self, user_id: str, status: str,
                      status_message: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO user_presence VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET status = excluded.status, "
                "status_message = excluded.status_message",
                (user_id, status, status_message),
            )
            conn.commit()
        finally:
            conn.close()

    def load_presence(self) -> dict[str, dict[str, str]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT user_id, status, status_message FROM user_presence"
            ).fetchall()
            return {
                r["user_id"]: {"status": r["status"],
                               "status_message": r["status_message"]}
                for r in rows
            }
        finally:
            conn.close()

    def touch_conversation(self, conversation_id: str, last_message_at: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE conversations SET last_message_at = ? WHERE conversation_id = ?",
                (last_message_at, conversation_id),
            )
            conn.commit()
        finally:
            conn.close()

    # -- messages ------------------------------------------------------------

    def save_message(self, message: dict[str, Any]) -> bool:
        """Persist a message. INSERT OR IGNORE guarantees idempotency;
        returns True only if this call actually inserted a new row."""
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    message["message_id"],
                    message["conversation_id"],
                    message["sender_id"],
                    message["timestamp"],
                    message["type"],
                    json.dumps(message["payload"]),
                    json.dumps(message["metadata"]),
                ),
            )
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()

    def message_exists(self, message_id: str) -> bool:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def list_conversation_messages(self, conversation_id: str,
                                   limit: int = 100, before: Optional[str] = None
                                   ) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            if before:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE conversation_id = ? "
                    "AND timestamp < ? ORDER BY timestamp DESC LIMIT ?",
                    (conversation_id, before, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE conversation_id = ? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (conversation_id, limit),
                ).fetchall()
            out = []
            for r in rows:
                msg = dict(r)
                msg["payload"] = json.loads(msg["payload"])
                msg["metadata"] = json.loads(msg["metadata"])
                msg["is_group"] = bool(
                    conn.execute(
                        "SELECT is_group FROM conversations WHERE conversation_id = ?",
                        (conversation_id,),
                    ).fetchone()["is_group"]
                )
                out.append(msg)
            return out
        finally:
            conn.close()
