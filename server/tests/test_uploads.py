"""Browser upload, validation routing and deduplication."""

from __future__ import annotations

import pytest

from chester.models import Instance, Study
from chester.security.roles import ROLE_ADMIN


@pytest.fixture
def uploader(make_user):
    return make_user("uploader@example.com", ROLE_ADMIN)


@pytest.fixture
def upload(client, signed_in, uploader):
    headers, _ = signed_in("uploader@example.com")

    def _upload(files, confirm: str = "true"):
        return client.post(
            "/api/uploads",
            data={"confirm_deidentified": confirm},
            files=files,
            headers=headers,
        )

    return _upload


def test_chest_dicom_is_accepted_and_queued(upload, make_dicom, session):
    response = upload([("files", ("chest.dcm", make_dicom(), "application/dicom"))])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["errors"] == []
    assert len(body["studies"]) == 1
    assert body["studies"][0]["status"] == "queued"
    assert body["studies"][0]["validation_state"] == "chest"


def test_non_chest_dicom_is_rejected_not_dropped(upload, make_dicom):
    """A rejected study still exists and says why, rather than vanishing."""
    data = make_dicom(modality="CT", body_part="HEAD", study_description="HEAD CT")
    response = upload([("files", ("head.dcm", data, "application/dicom"))])

    assert response.status_code == 200
    study = response.json()["studies"][0]
    assert study["status"] == "rejected"
    assert study["validation_state"] == "non_chest"
    assert study["validation_reason"]


def test_a_plain_image_always_goes_to_review(upload, make_png):
    """An image carries no modality or body part, so nothing can confirm it."""
    response = upload([("files", ("scan.png", make_png(), "image/png"))])

    assert response.status_code == 200
    study = response.json()["studies"][0]
    assert study["status"] == "needs_review"
    assert study["validation_state"] == "uncertain"


def test_the_deidentification_confirmation_is_required(upload, make_dicom):
    response = upload(
        [("files", ("chest.dcm", make_dicom(), "application/dicom"))], confirm="false"
    )
    assert response.status_code == 400


def test_identical_bytes_are_deduplicated(upload, make_dicom, session):
    data = make_dicom()

    first = upload([("files", ("chest.dcm", data, "application/dicom"))])
    second = upload([("files", ("again.dcm", data, "application/dicom"))])

    assert first.status_code == second.status_code == 200
    assert session.query(Study).count() == 1
    assert session.query(Instance).count() == 1


def test_different_files_become_different_studies(upload, make_dicom, session):
    upload([("files", ("one.dcm", make_dicom(), "application/dicom"))])
    upload([("files", ("two.dcm", make_dicom(), "application/dicom"))])

    assert session.query(Study).count() == 2


def test_instances_sharing_a_study_uid_group_into_one_study(upload, make_dicom, session):
    from pydicom.uid import generate_uid

    study_uid = generate_uid()
    upload([("files", ("a.dcm", make_dicom(study_uid=study_uid), "application/dicom"))])
    upload([("files", ("b.dcm", make_dicom(study_uid=study_uid), "application/dicom"))])

    assert session.query(Study).count() == 1
    assert session.query(Instance).count() == 2


def test_an_empty_file_is_reported_not_ingested(upload):
    response = upload([("files", ("empty.dcm", b"", "application/dicom"))])

    assert response.status_code == 200
    assert response.json()["studies"] == []
    assert response.json()["errors"][0]["error"] == "Empty file"


def test_an_oversized_file_is_refused(upload, monkeypatch):
    from chester.config import settings

    monkeypatch.setattr(settings, "dicom_max_upload_bytes", 16)

    response = upload([("files", ("big.png", b"x" * 64, "image/png"))])

    assert response.status_code == 200
    assert "too large" in response.json()["errors"][0]["error"].lower()


def test_undecodable_content_is_reported_per_file(upload, make_dicom):
    """One bad file must not lose the good ones in the same request."""
    response = upload(
        [
            ("files", ("good.dcm", make_dicom(), "application/dicom")),
            ("files", ("bad.png", b"not really a png", "image/png")),
        ]
    )

    assert response.status_code == 200
    assert len(response.json()["studies"]) == 1
    assert len(response.json()["errors"]) == 1


def test_a_multiframe_upload_records_which_frame_was_used(upload, make_dicom, session):
    import numpy as np

    frames = np.zeros((3, 64, 64), dtype=np.uint16)
    upload(
        [("files", ("multi.dcm", make_dicom(frame_count=3, pixels=frames), "application/dicom"))]
    )

    instance = session.query(Instance).one()
    assert instance.frame_count == 3
    assert "frame 0" in instance.audit_note


def test_upload_requires_a_session(client, make_dicom):
    response = client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("chest.dcm", make_dicom(), "application/dicom"))],
    )
    assert response.status_code == 401


def test_the_api_exposes_a_translatable_reason_code(upload, make_dicom):
    """The interface translates the code; the prose is only a fallback.

    Validation reasons used to reach the browser as English prose, which no
    amount of front-end work could localize.
    """
    data = make_dicom(modality="CT", body_part="HEAD", study_description="HEAD CT")
    study = upload([("files", ("head.dcm", data, "application/dicom"))]).json()["studies"][0]

    assert study["validation_reason_code"] == "non_chest_modality"
    # The parameter the message needs is carried on the study itself.
    assert study["modality"] == "CT"
    assert study["validation_reason"]


def test_the_body_part_is_recorded(upload, make_dicom):
    study = upload([("files", ("chest.dcm", make_dicom(), "application/dicom"))]).json()["studies"][
        0
    ]

    assert study["body_part"] == "CHEST"
