"""SMTP delivery for one-time access codes."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from chester.config import settings

logger = logging.getLogger(__name__)

SUBJECT = "Código de acesso — Chester AI"


class EmailNotConfigured(RuntimeError):
    """Raised when SMTP delivery has not been configured."""


def email_delivery_configured() -> bool:
    """Whether SMTP is set up well enough to attempt a send."""
    return all((settings.smtp_from, settings.smtp_host, settings.smtp_password))


def send_otp_email(recipient: str, code: str) -> None:
    """Send a code. The code is never logged or returned."""
    if not email_delivery_configured():
        raise EmailNotConfigured("SMTP email delivery is not configured")

    message = EmailMessage()
    message["Subject"] = SUBJECT
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
    logger.info("Sent access code to %s", recipient)
