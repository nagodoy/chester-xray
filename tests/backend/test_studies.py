"""Tests for study CRUD endpoints."""
from __future__ import annotations

import pytest

from app.models import AnalysisJob, Study
from tests.backend.conftest import make_minimal_dicom


def _create_study(auth_client, db_session, **kwargs):
    """Helper to create a study via upload."""
    dcm = make_minimal_dicom(**kwargs)
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("test.dcm", dcm, "application/dicom"))],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["studies"]
    return data["studies"][0]


def test_list_studies_empty(auth_client):
    resp = auth_client.get("/api/studies")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "counts" in data


def test_list_studies_after_upload(auth_client, db_session):
    study = _create_study(
        auth_client, db_session, modality="DX", body_part="CHEST", view_position="PA"
    )
    resp = auth_client.get("/api/studies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    ids = [s["id"] for s in data["items"]]
    assert study["id"] in ids


def test_get_study(auth_client, db_session):
    study = _create_study(
        auth_client, db_session, modality="DX", body_part="CHEST", view_position="PA"
    )
    resp = auth_client.get(f"/api/studies/{study['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == study["id"]
    assert "instances" in data
    assert "results" in data


def test_get_study_not_found(auth_client):
    resp = auth_client.get("/api/studies/nonexistent-id")
    assert resp.status_code == 404


def test_study_filter_status(auth_client, db_session):
    # Upload a DICOM that will be queued
    _create_study(auth_client, db_session, modality="DX", body_part="CHEST", view_position="PA")

    resp = auth_client.get("/api/studies?status=queued")
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["status"] == "queued"


def test_study_filter_invalid_status(auth_client):
    resp = auth_client.get("/api/studies?status=invalid_status")
    assert resp.status_code == 400


def test_study_filter_search(auth_client, db_session):
    _create_study(
        auth_client, db_session, modality="DX", body_part="CHEST", view_position="PA"
    )
    resp = auth_client.get("/api/studies?search=CHEST")
    assert resp.status_code == 200


def test_study_pagination(auth_client, db_session):
    # Upload 3 studies
    for i in range(3):
        _create_study(
            auth_client, db_session,
            modality="DX", body_part="CHEST", view_position="PA",
            sop_uid=None,  # generate unique UIDs
        )
    resp = auth_client.get("/api/studies?limit=2&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 2


def test_review_approve(auth_client, db_session):
    """Approve a needs_review study."""
    from app.models import Study as StudyModel
    # Create a study with needs_review status
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="AP")
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("test.dcm", dcm, "application/dicom"))],
    )
    study_id = resp.json()["studies"][0]["id"]

    # Force status to needs_review
    study = db_session.query(StudyModel).filter_by(id=study_id).first()
    study.status = "needs_review"
    db_session.commit()

    resp = auth_client.post(
        f"/api/studies/{study_id}/review",
        json={"decision": "approve"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"


def test_review_reject(auth_client, db_session):
    """Reject a needs_review study."""
    from app.models import Study as StudyModel
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="AP")
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("test.dcm", dcm, "application/dicom"))],
    )
    study_id = resp.json()["studies"][0]["id"]

    study = db_session.query(StudyModel).filter_by(id=study_id).first()
    study.status = "needs_review"
    db_session.commit()

    resp = auth_client.post(
        f"/api/studies/{study_id}/review",
        json={"decision": "reject"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"


def test_review_invalid_decision(auth_client, db_session):
    from app.models import Study as StudyModel
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="AP")
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("test.dcm", dcm, "application/dicom"))],
    )
    study_id = resp.json()["studies"][0]["id"]
    study = db_session.query(StudyModel).filter_by(id=study_id).first()
    study.status = "needs_review"
    db_session.commit()

    resp = auth_client.post(
        f"/api/studies/{study_id}/review",
        json={"decision": "invalid"},
    )
    assert resp.status_code == 400


def test_retry_error_study(auth_client, db_session):
    """Retry an errored study."""
    from app.models import Study as StudyModel
    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    resp = auth_client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("test.dcm", dcm, "application/dicom"))],
    )
    study_id = resp.json()["studies"][0]["id"]

    study = db_session.query(StudyModel).filter_by(id=study_id).first()
    study.status = "error"
    study.error_message = "simulated error"
    db_session.commit()

    resp = auth_client.post(f"/api/studies/{study_id}/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"


def test_studies_are_isolated_by_session_identity(auth_client, db_session):
    """A second authenticated session identity cannot read or mutate an owner's study."""
    from app.api.auth_deps import AccessContext, get_current_access
    from app.main import app

    study = _create_study(
        auth_client,
        db_session,
        modality="DX",
        body_part="CHEST",
        view_position="PA",
    )
    original_override = app.dependency_overrides[get_current_access]

    def other_user():
        return AccessContext(
            email="other-user-456",
            role="admin",
            allowed_pages=None,
            is_admin=True,
        )

    app.dependency_overrides[get_current_access] = other_user
    try:
        listed = auth_client.get("/api/studies")
        assert listed.status_code == 200
        assert study["id"] not in {item["id"] for item in listed.json()["items"]}
        assert auth_client.get(f"/api/studies/{study['id']}").status_code == 404
        assert auth_client.get(f"/api/studies/{study['id']}/thumbnail").status_code == 404
        assert auth_client.post(f"/api/studies/{study['id']}/retry").status_code == 404
    finally:
        app.dependency_overrides[get_current_access] = original_override
