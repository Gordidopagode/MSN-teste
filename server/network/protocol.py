"""Wire protocol parser.

Every WebSocket frame is a single JSON object. Client -> server messages
always carry at minimum a "command" field; server -> client messages always
carry a "type" field (see protocol documentation).

This module translates raw JSON into validated command dicts and vice versa,
raising ProtocolError for malformed or forbidden input — never crashing the
server.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("msn.protocol")

MAX_FRAME_BYTES = 512 * 1024  # 512 KiB hard frame limit (avatar data URLs)


class ProtocolError(Exception):
    pass


# Expected client commands: command -> required keys (excluding 'command')
CLIENT_COMMANDS: dict[str, tuple[str, ...]] = {
    "REGISTER": ("username", "display_name", "password", "email"),
    "LOGIN": ("username", "password"),
    "REQUEST_PASSWORD_RESET": ("email",),
    "RESET_PASSWORD": ("email", "code", "new_password"),
    "RECONNECT": ("session_id",),
    "REQUEST_SYNC": (),
    "CHANGE_STATUS": ("status",),
    "SEND_MESSAGE": ("conversation_id", "type", "payload"),
    "GET_HISTORY": ("conversation_id",),
    "CREATE_GROUP": ("name", "participants"),
    "SEARCH_USERS": ("query",),
    "SEND_FRIEND_REQUEST": ("username",),
    "RESPOND_FRIEND_REQUEST": ("friendship_id", "action"),
    "REMOVE_FRIEND": ("friendship_id",),
    "OPEN_CONVERSATION": ("username",),
    "SET_AVATAR": ("data", "filename", "mime"),
    "SET_CUSTOM_STATUS": ("message",),
    "LOGOUT": (),
}


def _require_string(data: dict[str, Any], key: str, *, nonempty: bool = True) -> None:
    value = data.get(key)
    if not isinstance(value, str):
        raise ProtocolError(f"Campo '{key}' deve ser texto.")
    if nonempty and not value.strip():
        raise ProtocolError(f"Campo '{key}' não pode ser vazio.")


def parse_client_message(raw: Any) -> dict[str, Any]:
    """Parse and validate one inbound message.

    Raises ProtocolError for anything invalid. The returned dict always
    contains 'command' plus the validated fields.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ProtocolError("Pacote não é um texto válido (UTF-8).")

    if not isinstance(raw, str):
        raise ProtocolError("Pacote deve ser texto.")
    if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ProtocolError("Pacote muito grande.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ProtocolError("JSON inválido.")

    if not isinstance(data, dict):
        raise ProtocolError("A mensagem deve ser um objeto JSON.")

    command = data.get("command")
    if not isinstance(command, str):
        raise ProtocolError("Campo 'command' ausente ou inválido.")

    required = CLIENT_COMMANDS.get(command)
    if required is None:
        raise ProtocolError(f"Comando desconhecido: {command!r}")

    out: dict[str, Any] = {"command": command}
    for key in required:
        if key not in data:
            raise ProtocolError(f"Campo obrigatório ausente: {key!r} no comando {command}")
        out[key] = data[key]

    if command in {"REGISTER", "LOGIN"}:
        _require_string(data, "username")
        _require_string(data, "password")
        if command == "REGISTER":
            _require_string(data, "display_name")
            _require_string(data, "email")
            out["email"] = data["email"]

    elif command == "REQUEST_PASSWORD_RESET":
        _require_string(data, "email")
        out["email"] = data["email"]

    elif command == "RESET_PASSWORD":
        _require_string(data, "email")
        _require_string(data, "code")
        _require_string(data, "new_password")
        if len(data["code"].strip()) > 32:
            raise ProtocolError("Código de recuperação inválido.")
        if len(data["new_password"]) > 1024:
            raise ProtocolError("A nova senha é muito longa.")
        out["email"] = data["email"]
        out["code"] = data["code"]
        out["new_password"] = data["new_password"]

    elif command == "RECONNECT":
        _require_string(data, "session_id")

    elif command == "CHANGE_STATUS":
        _require_string(data, "status")
        if "status_message" in data:
            _require_string(data, "status_message", nonempty=False)
            out["status_message"] = data["status_message"]

    elif command == "SEARCH_USERS":
        _require_string(data, "query")
        out["query"] = data["query"]

    elif command == "SEND_FRIEND_REQUEST":
        _require_string(data, "username")
        out["username"] = data["username"]

    elif command == "RESPOND_FRIEND_REQUEST":
        _require_string(data, "friendship_id")
        _require_string(data, "action")
        if data["action"] not in {"accept", "decline"}:
            raise ProtocolError("Campo 'action' deve ser 'accept' ou 'decline'.")
        out["action"] = data["action"]

    elif command == "REMOVE_FRIEND":
        _require_string(data, "friendship_id")

    elif command == "OPEN_CONVERSATION":
        _require_string(data, "username")

    elif command == "SET_AVATAR":
        _require_string(data, "data", nonempty=False)
        _require_string(data, "filename", nonempty=False)
        _require_string(data, "mime", nonempty=False)

    elif command == "SET_CUSTOM_STATUS":
        _require_string(data, "message", nonempty=False)

    elif command == "SEND_MESSAGE":
        _require_string(data, "conversation_id")
        _require_string(data, "type")
        if not isinstance(data["payload"], dict):
            raise ProtocolError("Campo 'payload' deve ser um objeto JSON.")
        if "message_id" in data:
            _require_string(data, "message_id")
            out["message_id"] = data["message_id"]

    elif command == "GET_HISTORY":
        _require_string(data, "conversation_id")
        if "limit" in data:
            limit = data["limit"]
            if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
                raise ProtocolError("Campo 'limit' deve ser um inteiro positivo.")
            out["limit"] = min(limit, 200)
        if "before" in data:
            _require_string(data, "before")
            out["before"] = data["before"]

    elif command == "CREATE_GROUP":
        _require_string(data, "name")
        participants = data["participants"]
        if not isinstance(participants, list) or not participants:
            raise ProtocolError("'participants' deve ser uma lista não vazia.")
        if not all(isinstance(name, str) and name.strip() for name in participants):
            raise ProtocolError("'participants' deve conter apenas nomes válidos.")

    return out


def format_server_message(message: dict[str, Any]) -> str:
    """Serialize one outbound envelope to JSON text."""
    if "type" not in message or "payload" not in message:
        raise ValueError("Server envelope must have type and payload")
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))
