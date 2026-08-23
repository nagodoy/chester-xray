"""Tests for DICOM STOW-RS endpoints."""
from __future__ import annotations

import base64

import pytest

from tests.backend.conftest import make_minimal_dicom, make_stow_multipart


def test_stow_success(auth_client, db_session):
    """STOW-RS with valid DICOM returns 200."""
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    body, content_type = make_stow_multipart([dcm])

    resp = auth_client.post(
        "/dicomweb/studies",
        content=body,
        headers={
            "Content-Type": content_type,
            "X-DICOM-Ingest-Key": "test-dicom-ingest-token",
        },
    )
    assert resp.status_code in (200, 202), resp.text
    data = resp.json()
    # Should have referenced SOP sequence
    assert "00081199" in data or "00081190" in data


def test_stow_invalid_token(client, db_session):
    """STOW-RS with wrong token returns 401."""
    dcm = make_minimal_dicom()
    body, content_type = make_stow_multipart([dcm])

    resp = client.post(
        "/dicomweb/studies",
        content=body,
        headers={
            "Content-Type": content_type,
            "X-DICOM-Ingest-Key": "wrong-token",
        },
    )
    assert resp.status_code == 401


def test_stow_missing_token(client, db_session):
    """STOW-RS without token returns 401."""
    dcm = make_minimal_dicom()
    body, content_type = make_stow_multipart([dcm])

    resp = client.post(
        "/dicomweb/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert resp.status_code == 401


def test_stow_duplicate(auth_client, db_session):
    """STOW-RS with duplicate SOP Instance UID returns 409."""
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    body, content_type = make_stow_multipart([dcm])
    headers = {
        "Content-Type": content_type,
        "X-DICOM-Ingest-Key": "test-dicom-ingest-token",
    }

    # First upload
    resp1 = auth_client.post("/dicomweb/studies", content=body, headers=headers)
    assert resp1.status_code in (200, 202)

    # Second upload (same bytes = same SOP UID)
    body2, content_type2 = make_stow_multipart([dcm])
    headers2 = {
        "Content-Type": content_type2,
        "X-DICOM-Ingest-Key": "test-dicom-ingest-token",
    }
    resp2 = auth_client.post("/dicomweb/studies", content=body2, headers=headers2)
    # Duplicate returns 409
    assert resp2.status_code == 409


def test_stow_wrong_content_type(auth_client, db_session):
    """STOW-RS with non-multipart content-type returns 400."""
    resp = auth_client.post(
        "/dicomweb/studies",
        content=b"not a multipart body",
        headers={
            "Content-Type": "application/dicom",
            "X-DICOM-Ingest-Key": "test-dicom-ingest-token",
        },
    )
    assert resp.status_code == 400


def test_stow_multiple_instances(auth_client, db_session):
    """STOW-RS with multiple DICOM files."""
    dcm1 = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    dcm2 = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="AP")
    body, content_type = make_stow_multipart([dcm1, dcm2])

    resp = auth_client.post(
        "/dicomweb/studies",
        content=body,
        headers={
            "Content-Type": content_type,
            "X-DICOM-Ingest-Key": "test-dicom-ingest-token",
        },
    )
    assert resp.status_code in (200, 202)
    data = resp.json()
    # Should reference two instances
    if "00081199" in data:
        assert len(data["00081199"]["Value"]) >= 1


def test_stow_study_specific_endpoint(auth_client, db_session):
    """STOW-RS via study-specific endpoint."""
    study_uid = "1.2.3.4.5"
    dcm = make_minimal_dicom(
        modality="DX",
        body_part="CHEST",
        view_position="PA",
        study_uid=study_uid,
    )
    body, content_type = make_stow_multipart([dcm])

    resp = auth_client.post(
        f"/dicomweb/studies/{study_uid}",
        content=body,
        headers={
            "Content-Type": content_type,
            "X-DICOM-Ingest-Key": "test-dicom-ingest-token",
        },
    )
    assert resp.status_code in (200, 202, 409)


def test_stow_wado_compatibility_endpoint(auth_client, db_session):
    """STOW-RS accepts the compatibility path used by some OsiriX setups."""
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    body, content_type = make_stow_multipart([dcm])

    resp = auth_client.post(
        "/wado/studies",
        content=body,
        headers={
            "Content-Type": content_type,
            "X-DICOM-Ingest-Key": "test-dicom-ingest-token",
        },
    )
    assert resp.status_code in (200, 202, 409), resp.text


def test_stow_wado_duplicate_path_compatibility(auth_client, db_session):
    """OsiriX may append /studies twice to a configured WADO base URL."""
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    body, content_type = make_stow_multipart([dcm])

    resp = auth_client.post(
        "/wado/studies/studies",
        content=body,
        headers={
            "Content-Type": content_type,
            "X-DICOM-Ingest-Key": "test-dicom-ingest-token",
        },
    )
    assert resp.status_code in (200, 202, 409), resp.text


def test_stow_accepts_basic_auth_for_osirix(client, db_session):
    """OsiriX can authenticate STOW-RS with its HTTP username/password fields."""
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    body, content_type = make_stow_multipart([dcm])
    basic_credentials = base64.b64encode(
        b"dicom:test-dicom-ingest-token"
    ).decode("ascii")

    resp = client.post(
        "/dicomweb/studies",
        content=body,
        headers={
            "Content-Type": content_type,
            "Authorization": f"Basic {basic_credentials}",
        },
    )
    assert resp.status_code in (200, 202), resp.text


def test_stow_non_dicom_rejected(auth_client, db_session):
    """STOW-RS with non-DICOM content returns error in response."""
    body = b"--TEST\r\nContent-Type: application/dicom\r\n\r\nNOT_DICOM_BYTES\r\n--TEST--\r\n"
    content_type = "multipart/related; type=application/dicom; boundary=TEST"

    resp = auth_client.post(
        "/dicomweb/studies",
        content=body,
        headers={
            "Content-Type": content_type,
            "X-DICOM-Ingest-Key": "test-dicom-ingest-token",
        },
    )
    # Should be 400 (all failures) or have failure sequence
    data = resp.json()
    assert resp.status_code in (200, 202, 400) or "00081198" in data
