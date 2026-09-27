"""The backfill for exams refused because their lateral arrived first."""

from __future__ import annotations

import uuid

import pytest
from pydicom.uid import generate_uid

from chester import revalidate
from chester.models import AnalysisJob, Study
from chester.security.roles import ROLE_ADMIN


@pytest.fixture
def upload(client, signed_in, make_user):
    make_user("backfill@example.com", ROLE_ADMIN)
    headers, _ = signed_in("backfill@example.com")

    def _upload(*files: tuple[str, bytes]):
        return client.post(
            "/api/uploads",
            data={"confirm_deidentified": "true"},
            files=[("files", (name, data, "application/dicom")) for name, data in files],
            headers=headers,
        ).json()["studies"][0]

    return _upload


@pytest.fixture
def refused_two_view_exam(upload, make_dicom, session):
    """A PA and a lateral left refused, as studies filed before the fix were."""

    def _build(frontal_series: str = "PA") -> Study:
        common = {
            "study_uid": generate_uid(),
            "modality": "CR",
            "body_part": "TORAX",
            "view_position": "",
            "study_description": "RX TORAX 2 INCIDENCIAS",
        }
        upload(("perfil.dcm", make_dicom(series_description="PERFIL", **common)))
        summary = upload(("pa.dcm", make_dicom(series_description=frontal_series, **common)))

        study = session.get(Study, uuid.UUID(summary["id"]))
        session.query(AnalysisJob).filter_by(study_id=study.id).delete()
        study.status = "rejected"
        study.validation_state = "non_chest"
        study.validation_reason_code = "lateral_view"
        session.flush()
        return study

    return _build


def test_the_frontal_already_stored_queues_the_study(refused_two_view_exam, session):
    study = refused_two_view_exam()

    assert revalidate.revalidate(session, study, dry_run=False) == "queued"
    assert study.status == "queued"
    assert study.validation_state == "chest"
    assert session.query(AnalysisJob).filter_by(study_id=study.id).count() == 1


def test_a_dry_run_changes_nothing(refused_two_view_exam, session):
    study = refused_two_view_exam()

    assert revalidate.revalidate(session, study, dry_run=True) == "would-queue"
    assert study.status == "rejected"
    assert session.query(AnalysisJob).filter_by(study_id=study.id).count() == 0


def test_an_unconfirmed_second_film_is_held_for_review(upload, make_dicom, session):
    """Not lateral, not confirmed either: a human decides, the exam is not discarded."""
    study_uid = generate_uid()
    upload(("perfil.dcm", make_dicom(study_uid=study_uid, view_position="LL")))
    bare = {"modality": "", "body_part": "", "view_position": "", "study_description": ""}
    summary = upload(("outra.dcm", make_dicom(study_uid=study_uid, **bare)))
    study = session.get(Study, uuid.UUID(summary["id"]))
    study.status = "rejected"
    study.validation_state = "non_chest"
    study.validation_reason_code = "lateral_view"
    session.flush()

    assert revalidate.revalidate(session, study, dry_run=False) == "held-for-review"
    assert study.status == "needs_review"
    assert session.query(AnalysisJob).filter_by(study_id=study.id).count() == 0


def test_a_lateral_only_study_stays_refused(upload, make_dicom, session):
    summary = upload(("perfil.dcm", make_dicom(view_position="LL")))
    study = session.get(Study, uuid.UUID(summary["id"]))

    assert revalidate.revalidate(session, study, dry_run=False) == "only-lateral"
    assert study.status == "rejected"
