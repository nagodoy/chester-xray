"""SMTP delivery for one-time access codes."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from html import escape

from chester.config import settings

logger = logging.getLogger(__name__)

SUBJECT = "Código de acesso — Torax AI"

# The card sits on the same three-step stack as the sign-in screen it hands the
# reader off to: #020617 ground, #0f172a card, #1e293b for the code itself, with
# emerald on the code box because that is the colour the six inputs turn as it
# is typed in. Layout follows the Telemetry MR MSK console (RM-QC-v4): one
# centred 480px card, a brand header over a hairline, the code alone in the
# middle, and the fine print under a second hairline.
#
# Written as tables with inline styles because that is what mail clients render.
# The code box is a nested table rather than an inline-block div, which Outlook's
# Word engine does not lay out; its left padding is heavier than its right to
# balance the trailing space that letter-spacing adds after the last digit.
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="color-scheme" content="dark">
</head>
<body style="margin:0;padding:0;background:#020617;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#020617;padding:40px 0;">
  <tr><td align="center">
    <table role="presentation" width="480" cellpadding="0" cellspacing="0"
           style="background:#0f172a;border-radius:16px;overflow:hidden;">
      <tr><td style="padding:32px 40px 20px;border-bottom:1px solid #1e293b;text-align:center;">
        <h1 style="margin:0;color:#34d399;font-size:22px;font-weight:700;">Torax AI</h1>
        <p style="margin:8px 0 0;color:#94a3b8;font-size:13px;">Radiografia torácica</p>
      </td></tr>
      <tr><td style="padding:36px 40px;text-align:center;">
        <p style="margin:0 0 8px;color:#94a3b8;font-size:14px;">Seu código de acesso é:</p>
        <table role="presentation" align="center" cellpadding="0" cellspacing="0"
               style="margin:12px auto;">
          <tr>
            <td style="background:#1e293b;border:2px solid #10b981;border-radius:12px;
                       padding:18px 28px 18px 40px;">
              <span style="font-size:40px;font-weight:700;color:#34d399;
                           letter-spacing:12px;">{code}</span>
            </td>
          </tr>
        </table>
        <p style="margin:16px 0 0;color:#64748b;font-size:13px;">
          O código vale por
          <strong style="color:#f8fafc;">{validity}</strong>
          e só pode ser usado uma vez.<br>
          Se você não solicitou este acesso, ignore esta mensagem.
        </p>
      </td></tr>
      <tr><td style="padding:16px 40px 28px;border-top:1px solid #1e293b;text-align:center;">
        <p style="margin:0;color:#475569;font-size:11px;">
          Torax AI — pesquisa somente, dados de teste ou desidentificados.<br>
          Não é um dispositivo médico e não serve para diagnóstico.
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


class EmailNotConfigured(RuntimeError):
    """Raised when SMTP delivery has not been configured."""


def email_delivery_configured() -> bool:
    """Whether SMTP is set up well enough to attempt a send."""
    return all((settings.smtp_from, settings.smtp_host, settings.smtp_password))


def _validity() -> str:
    minutes = settings.auth_otp_minutes
    return "1 minuto" if minutes == 1 else f"{minutes} minutos"


def render_otp_text(code: str) -> str:
    """The plain-text part, and the whole message for text-only clients."""
    return (
        "Seu código de acesso ao Torax AI é:\n\n"
        f"{code}\n\n"
        f"O código vale por {_validity()} e só pode ser usado uma vez. "
        "Se você não solicitou este acesso, ignore esta mensagem."
    )


def render_otp_html(code: str) -> str:
    """The HTML part. The code is the only thing interpolated into it."""
    return _HTML_TEMPLATE.format(code=escape(code), validity=escape(_validity()))


def send_otp_email(recipient: str, code: str) -> None:
    """Send a code. The code is never logged or returned."""
    if not email_delivery_configured():
        raise EmailNotConfigured("SMTP email delivery is not configured")

    message = EmailMessage()
    message["Subject"] = SUBJECT
    message["From"] = settings.smtp_from
    message["To"] = recipient
    # Text first, then HTML: the last alternative added is the one a client
    # prefers, and a text part still has to exist for the ones that cannot
    # render HTML at all.
    message.set_content(render_otp_text(code))
    message.add_alternative(render_otp_html(code), subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(settings.smtp_from, settings.smtp_password)
        smtp.send_message(message)
    logger.info("Sent access code to %s", recipient)
