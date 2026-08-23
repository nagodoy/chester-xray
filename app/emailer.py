"""SMTP delivery for one-time authentication codes."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


def send_otp_email(recipient: str, code: str) -> None:
    """Send an OTP without logging or returning the code."""
    if not all((settings.smtp_from, settings.smtp_host, settings.smtp_password)):
        raise RuntimeError("SMTP email delivery is not configured")

    message = EmailMessage()
    message["Subject"] = "Código de acesso — Chester AI"
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message.set_content(
        "Seu código de acesso ao Chester AI é:\n\n"
        f"{code}\n\n"
        "O código expira em poucos minutos e só pode ser usado uma vez. "
        "Se você não solicitou este acesso, ignore esta mensagem."
    )

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(settings.smtp_from, settings.smtp_password)
        smtp.send_message(message)