"""SMTP delivery for password-recovery messages.

The service intentionally receives credentials through ServerSettings only. It
never persists them, returns them to callers, or logs them. Password-reset
codes are included in the email body but are not written to application logs.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any


class EmailDeliveryError(Exception):
    """A controlled error while delivering an email."""


def smtp_is_configured(settings: Any) -> bool:
    return bool(
        str(getattr(settings, "smtp_host", "")).strip()
        and str(getattr(settings, "smtp_username", "")).strip()
        and str(getattr(settings, "smtp_password", ""))
        and str(getattr(settings, "smtp_from", "")).strip()
    )


def _send_sync(settings: Any, recipient: str, code: str) -> None:
    host = str(settings.smtp_host).strip()
    port = int(settings.smtp_port)
    username = str(settings.smtp_username).strip()
    password = str(settings.smtp_password)
    sender = str(settings.smtp_from).strip() or username

    message = EmailMessage()
    message["Subject"] = "Código para recuperar sua conta do MSN"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "Olá,\n\n"
        "Recebemos uma solicitação para trocar a senha da sua conta no MSN.\n\n"
        f"Seu código temporário é: {code}\n\n"
        f"Ele expira em {settings.reset_token_ttl_minutes} minutos e só pode ser usado uma vez.\n"
        "Se você não solicitou esta troca, ignore esta mensagem.\n\n"
        "MSN Messenger\n"
    )

    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as smtp:
                smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError("Não foi possível enviar o e-mail de recuperação.") from exc


async def send_password_reset_email(settings: Any, recipient: str, code: str) -> None:
    """Send a reset code without blocking the asyncio event loop."""
    if not smtp_is_configured(settings):
        raise EmailDeliveryError("O envio de e-mail ainda não está configurado no servidor.")
    await asyncio.to_thread(_send_sync, settings, recipient, code)
