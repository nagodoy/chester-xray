"""The identity behind the pseudonym: what is stored, and who is sent it.

Two complaints shaped this. Revealing sensitive data showed the pseudonym --
the only identifier a study carried -- so the toggle appeared to do nothing.
And the accession number, which is how an exam is named everywhere else in the
department, was nowhere on the screen at all.
"""

from __future__ import annotations

import pytest

from chester.ingestion import ingest_file
from chester.models import Study
from chester.security.roles import ROLE_ADMIN, ROLE_TECHNICIAN


@pytest.fixture
def filed(session, make_user, make_dicom):
    """One study filed from a DICOM that names its patient and its order."""

    def _file(**kwargs):
        owner = kwargs.pop("owner", None) or make_user("tech@example.com", ROLE_TECHNICIAN)
        payload = make_dicom(
            patient_id="MRN-4471",
            patient_name="SILVA^JOAO^^^",
            accession_number="ACC-99120",
            **kwargs,
        )
        result = ingest_file(
            data=payload,
            filename="chest.dcm",
            content_type="application/dicom",
            owner=owner,
            actor=owner.email,
            db=session,
        )
        assert result.ok, result.error
        session.flush()
        return session.get(Study, result.study_id)

    return _file


def test_ingestion_keeps_the_identity_beside_the_pseudonym(filed):
    study = filed()

    assert study.patient_name == "SILVA JOAO"  # the carets are a wire format
    assert study.patient_id_source == "MRN-4471"
    assert study.accession_number == "ACC-99120"
    # The pseudonym is still what the row is keyed by on screen.
    assert study.patient_id and study.patient_id.startswith("P-")
    assert study.patient_id != study.patient_id_source


def test_a_second_instance_does_not_overwrite_the_identity(session, make_user, make_dicom, filed):
    """A study is one patient. The first instance names them."""
    owner = make_user("tech@example.com", ROLE_TECHNICIAN)
    study = filed(owner=owner)

    ingest_file(
        data=make_dicom(
            study_uid=study.study_instance_uid,
            view_position="AP",
            patient_id="MRN-OTHER",
            patient_name="OUTRO^NOME",
            accession_number="ACC-OTHER",
        ),
        filename="second.dcm",
        content_type="application/dicom",
        owner=owner,
        actor=owner.email,
        db=session,
    )
    session.flush()
    session.refresh(study)

    assert study.patient_name == "SILVA JOAO"
    assert study.accession_number == "ACC-99120"


def test_a_role_that_may_reveal_is_sent_the_name(client, signed_in, make_user, filed):
    make_user("chief@example.com", ROLE_ADMIN)
    study = filed()
    headers = signed_in("chief@example.com")[0]

    body = client.get(f"/api/studies/{study.id}", headers=headers).json()
    assert body["patient_name"] == "SILVA JOAO"
    assert body["patient_id_source"] == "MRN-4471"
    assert body["accession_number"] == "ACC-99120"


def test_a_role_that_may_not_reveal_is_not_sent_it_at_all(client, signed_in, make_user, filed):
    """Omitted, not masked: masking happens in the browser, and so can unmasking."""
    owner = make_user("tech@example.com", ROLE_TECHNICIAN)
    study = filed(owner=owner)
    headers = signed_in("tech@example.com")[0]

    body = client.get(f"/api/studies/{study.id}", headers=headers).json()
    assert body["patient_name"] is None
    assert body["patient_id_source"] is None
    # The pseudonym and the accession number still travel: one is what the row is
    # keyed by, the other names the order rather than the person.
    assert body["patient_id"] == study.patient_id
    assert body["accession_number"] == "ACC-99120"

    listed = client.get("/api/studies", headers=headers).json()["items"]
    assert [item["patient_name"] for item in listed] == [None]


def test_the_worklist_finds_a_study_by_its_accession_number(client, signed_in, make_user, filed):
    owner = make_user("tech@example.com", ROLE_TECHNICIAN)
    study = filed(owner=owner)
    headers = signed_in("tech@example.com")[0]

    found = client.get("/api/studies", params={"search": "ACC-991"}, headers=headers).json()
    assert [item["id"] for item in found["items"]] == [str(study.id)]


def test_searching_by_name_is_only_offered_to_a_role_that_may_see_one(
    client, signed_in, make_user, filed
):
    """Otherwise the worklist answers the question the response refuses to answer."""
    owner = make_user("tech@example.com", ROLE_TECHNICIAN)
    filed(owner=owner)
    make_user("chief@example.com", ROLE_ADMIN)

    def found_by_name(headers: dict[str, str]) -> int:
        response = client.get("/api/studies", params={"search": "SILVA"}, headers=headers)
        return response.json()["total"]

    assert found_by_name(signed_in("tech@example.com")[0]) == 0
    assert found_by_name(signed_in("chief@example.com")[0]) == 1


def test_an_image_upload_has_no_identity_to_show(session, make_user, make_png):
    """A PNG carries no patient, and the columns stay empty rather than guessing."""
    owner = make_user("tech@example.com", ROLE_TECHNICIAN)
    result = ingest_file(
        data=make_png(),
        filename="chest.png",
        content_type="image/png",
        owner=owner,
        actor=owner.email,
        db=session,
    )
    assert result.ok, result.error
    session.flush()
    study = session.get(Study, result.study_id)

    assert study.patient_name is None
    assert study.accession_number is None


def test_the_report_sheet_prints_the_name_without_its_carets(monkeypatch, make_dicom):
    """The sheet is read by a person, and `SILVA^JOAO^^^` is a wire format."""
    import numpy as np

    from chester import dicom_report

    captured: dict[str, str] = {}
    original = dicom_report.render_report

    def _capture(pixels, **kwargs):
        captured.update(kwargs)
        return original(pixels, **kwargs)

    monkeypatch.setattr(dicom_report, "render_report", _capture)
    dicom_report.build_report_dataset(
        make_dicom(patient_name="SILVA^JOAO^^^", accession_number="ACC-99120"),
        np.zeros((64, 64), dtype=np.float32),
        _Result(),
    )

    assert captured["patient_name"] == "SILVA JOAO"
    assert captured["accession_number"] == "ACC-99120"


class _Result:
    """The fields chester.report.finding_rows reads off a result."""

    model_version = "test-model"
    preprocessing_version = "2.0.0"
    raw_scores: dict = {}
    op_normalized_scores: dict = {}
    thresholds: dict = {}
    above_threshold: dict = {}
    above_threshold_findings: list = []
