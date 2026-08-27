"""The one-time-code message: both parts, and what must not leak into them."""

from __future__ import annotations

from email import policy
from email.parser import BytesParser

import pytest

from chester import emailer
from chester.config import settings


def _message(monkeypatch, code: str = "482913"):
    """Build the message send_otp_email would hand to SMTP, without sending it."""
    monkeypatch.setattr(settings, "smtp_from", "noreply@example.com")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_password", "secret")

    captured = {}

    class _Smtp:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setattr(emailer.smtplib, "SMTP", _Smtp)
    emailer.send_otp_email("reader@example.com", code)
    return captured["message"]


def test_the_message_carries_both_a_text_and_an_html_part(monkeypatch):
    message = _message(monkeypatch)

    assert message.get_content_type() == "multipart/alternative"
    assert [part.get_content_type() for part in message.iter_parts()] == [
        "text/plain",
        "text/html",
    ]
    # A client that renders HTML gets the card; one that cannot still gets the code.
    assert message.get_body(preferencelist=("html",)).get_content_type() == "text/html"
    assert message.get_body(preferencelist=("plain",)).get_content_type() == "text/plain"


def test_both_parts_carry_the_code_and_the_subject_does_not(monkeypatch):
    message = _message(monkeypatch, "135790")

    assert "135790" in message.get_body(preferencelist=("plain",)).get_content()
    assert "135790" in message.get_body(preferencelist=("html",)).get_content()
    # The code must not ride in the header: it shows up in lock-screen previews
    # and in every mail server's logs along the way.
    assert "135790" not in message["Subject"]


def test_the_message_survives_a_round_trip_through_the_wire_format(monkeypatch):
    """as_bytes/parse is what an SMTP server does; UTF-8 must come back intact."""
    message = _message(monkeypatch)
    parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())

    assert parsed["Subject"] == emailer.SUBJECT
    assert "código" in parsed.get_body(preferencelist=("plain",)).get_content()
    assert "Torax AI" in parsed.get_body(preferencelist=("html",)).get_content()


def test_the_stated_validity_follows_the_configured_lifetime(monkeypatch):
    monkeypatch.setattr(settings, "auth_otp_minutes", 10)
    assert "10 minutos" in emailer.render_otp_text("482913")
    assert "10 minutos" in emailer.render_otp_html("482913")

    monkeypatch.setattr(settings, "auth_otp_minutes", 1)
    assert "1 minuto" in emailer.render_otp_text("482913")
    assert "1 minutos" not in emailer.render_otp_html("482913")


def test_sending_without_smtp_configured_raises_rather_than_dropping_the_code(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")

    with pytest.raises(emailer.EmailNotConfigured):
        emailer.send_otp_email("reader@example.com", "482913")
