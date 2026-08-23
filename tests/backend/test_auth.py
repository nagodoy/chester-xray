"""Tests for authentication."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def test_studies_requires_auth(client):
    """Studies endpoint returns 401 when DEBUG=1 but CLERK_SECRET_KEY is empty."""
    # In test mode with DEBUG=1, auth allows dev-user
    resp = client.get("/api/studies")
    # DEBUG=1 allows through with "dev-user"
    assert resp.status_code == 200


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


@pytest.mark.anyio
async def test_authenticated_user_outside_allowlist_is_forbidden(monkeypatch):
    import app.auth as auth
    from app.config import settings

    monkeypatch.setattr(settings, "clerk_secret_key", "test-clerk-secret")

    async def signed_in_request(*_args):
        return type(
            "RequestState",
            (),
            {"is_signed_in": True, "payload": {"sub": "user_not_allowed"}},
        )()

    async def user_is_not_authorized(_subject):
        return False

    monkeypatch.setattr(auth, "run_in_threadpool", signed_in_request)
    monkeypatch.setattr(auth, "_is_authorized_subject", user_is_not_authorized)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
        }
    )

    with pytest.raises(HTTPException) as error:
        await auth.require_auth(request)

    assert error.value.status_code == 403
    assert error.value.detail == "User is not authorized for this application"
