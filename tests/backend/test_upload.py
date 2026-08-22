"""Tests for file upload and ingestion."""
from __future__ import annotations

import io

import pytest

from tests.backend.conftest import make_minimal_dicom, make_png_image


def test_upload_dicom(auth_client, db_session):
    """Upload a valid DICOM file."""
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("test.dcm", dcm, "application/dicom"))],
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["studies"]) == 1
    assert data["errors"] == []
    study = data["studies"][0]
    assert study["status"] in ("queued", "needs_review", "rejected", "validating")
    assert study["id"]


def test_upload_png(auth_client, db_session):
    """Upload a PNG image."""
    png = make_png_image(128, 128)
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("xray.png", png, "image/png"))],
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["studies"]) == 1
    assert data["errors"] == []
    study = data["studies"][0]
    # PNG uploads are uncertain (needs_review)
    assert study["status"] == "needs_review"
    assert study["validation_state"] == "uncertain"


def test_upload_deduplication_sha256(auth_client, db_session):
    """Uploading the same file twice returns deduplicated result."""
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST")
    # First upload
    resp1 = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("test.dcm", dcm, "application/dicom"))],
    )
    assert resp1.status_code == 200
    study_id1 = resp1.json()["studies"][0]["id"]

    # Second upload (same bytes)
    resp2 = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("test2.dcm", dcm, "application/dicom"))],
    )
    assert resp2.status_code == 200
    study_id2 = resp2.json()["studies"][0]["id"]

    # Should return the same study
    assert study_id1 == study_id2


def test_upload_multiple_files(auth_client, db_session):
    """Upload multiple files in one request."""
    dcm1 = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    dcm2 = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="AP")
    png = make_png_image()

    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[
            ("files", ("a.dcm", dcm1, "application/dicom")),
            ("files", ("b.dcm", dcm2, "application/dicom")),
            ("files", ("c.png", png, "image/png")),
        ],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["studies"]) == 3
    assert data["errors"] == []


def test_upload_non_chest_dicom(auth_client, db_session):
    """Non-chest modality DICOM should be rejected."""
    dcm = make_minimal_dicom(modality="CT", body_part="HEAD", view_position="")
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("ct.dcm", dcm, "application/dicom"))],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["studies"]) == 1
    study = data["studies"][0]
    assert study["status"] == "rejected"
    assert study["validation_state"] == "non_chest"


def test_upload_chest_dicom_queued(auth_client, db_session):
    """Chest DICOM with strong evidence should be queued."""
    dcm = make_minimal_dicom(
        modality="DX", body_part="CHEST", view_position="PA"
    )
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("chest.dcm", dcm, "application/dicom"))],
    )
    assert resp.status_code == 200
    data = resp.json()
    study = data["studies"][0]
    assert study["status"] == "queued"
    assert study["validation_state"] == "chest"


def test_upload_multiframe_dicom(auth_client, db_session):
    """Multi-frame DICOM should be accepted (uses first frame)."""
    dcm = make_minimal_dicom(
        modality="DX", body_part="CHEST", view_position="PA", frame_count=3
    )
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("multi.dcm", dcm, "application/dicom"))],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["studies"]) == 1


def test_upload_empty_file(auth_client, db_session):
    """Empty file should result in an error."""
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("empty.dcm", b"", "application/dicom"))],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["errors"]) == 1
    assert "empty" in data["errors"][0]["error"].lower()
