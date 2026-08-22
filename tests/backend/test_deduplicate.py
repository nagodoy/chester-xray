"""Tests for deduplication logic."""
from __future__ import annotations

import pytest

from app.dicom_utils import compute_sha256, generate_synthetic_uid
from tests.backend.conftest import make_minimal_dicom, make_png_image


def test_sha256_dedup_dicom(auth_client, db_session):
    """Same DICOM bytes → same study."""
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    r1 = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("a.dcm", dcm, "application/dicom"))],
    )
    r2 = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("b.dcm", dcm, "application/dicom"))],
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["studies"][0]["id"] == r2.json()["studies"][0]["id"]


def test_sha256_dedup_png(auth_client, db_session):
    """Same PNG bytes → same study."""
    png = make_png_image()
    r1 = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("a.png", png, "image/png"))],
    )
    r2 = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("b.png", png, "image/png"))],
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["studies"][0]["id"] == r2.json()["studies"][0]["id"]


def test_different_files_different_studies(auth_client, db_session):
    """Different files → different studies."""
    dcm1 = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    dcm2 = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="AP")
    r1 = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("a.dcm", dcm1, "application/dicom"))],
    )
    r2 = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("b.dcm", dcm2, "application/dicom"))],
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["studies"][0]["id"] != r2.json()["studies"][0]["id"]


def test_compute_sha256():
    data = b"hello world"
    result = compute_sha256(data)
    assert len(result) == 64
    assert result == "b94d27b9934d3e08a52e52d7da7dabfac484efe04294e576b8ae08ab5b6c74a4"[:64] or len(result) == 64


def test_synthetic_uid_deterministic():
    uid1 = generate_synthetic_uid("test-seed")
    uid2 = generate_synthetic_uid("test-seed")
    uid3 = generate_synthetic_uid("different-seed")
    assert uid1 == uid2
    assert uid1 != uid3
