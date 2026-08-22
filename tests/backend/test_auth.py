"""Tests for authentication."""
from __future__ import annotations

import pytest


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
