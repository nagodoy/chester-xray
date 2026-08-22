"""Tests for thumbnail endpoint."""
from __future__ import annotations

import pytest

from tests.backend.conftest import make_minimal_dicom


def test_thumbnail_after_upload(auth_client, db_session):
    """Thumbnail should be available after uploading a DICOM."""
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("test.dcm", dcm, "application/dicom"))],
    )
    assert resp.status_code == 200
    study_id = resp.json()["studies"][0]["id"]

    thumb_resp = auth_client.get(f"/api/studies/{study_id}/thumbnail")
    assert thumb_resp.status_code == 200
    assert thumb_resp.headers["content-type"] == "image/png"
    assert len(thumb_resp.content) > 0


def test_thumbnail_not_found(auth_client, db_session):
    """Non-existent study returns 404."""
    resp = auth_client.get("/api/studies/does-not-exist/thumbnail")
    assert resp.status_code == 404
