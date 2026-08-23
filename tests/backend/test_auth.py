"""Tests for authentication."""
from __future__ import annotations

import pytest

def test_studies_requires_auth(client):
    """A browser API endpoint requires a database-backed session token."""
    resp = client.get("/api/studies")
    assert resp.status_code == 401


def test_upload_requires_confirm_deidentified(auth_client):
    """Upload without confirm_deidentified should fail."""
    from tests.backend.conftest import make_png_image
    png = make_png_image()
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "false"},
        files=[("files", ("test.png", png, "image/png"))],
    )
    assert resp.status_code == 400
    assert "confirm_deidentified" in resp.json()["detail"].lower()


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("nelsonagodoy@gmail.com", True),
        ("NELSONAGODOY@GMAIL.COM", True),
        ("other@example.com", False),
        ("nelsonagodoy@gmail.com ", True),
        ("", False),
        (None, False),
    ],
)
def test_only_configured_email_is_authorized(email, expected):
    from app.auth import is_authorized_email

    assert is_authorized_email(email) is expected


def test_unknown_email_has_no_access(db_session):
    from app.api.auth_deps import resolve_access

    assert resolve_access(db_session, "user-without-rule@example.com") is None
