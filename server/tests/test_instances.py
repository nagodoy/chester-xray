"""Choosing the instance a study is represented by.

A chest exam arrives as one or two films under a single Study Instance UID. The
frontal is the one the model reads, so it is also the one the thumbnail, the
report sheet and the analysis must all draw from, whichever arrived first.
"""

from __future__ import annotations

import pytest

from chester.imaging.validation import FRONTAL, LATERAL, UNKNOWN_PROJECTION
from chester.instances import instance_projection, representative_instance
from chester.models import Instance, Study
from chester.security.roles import ROLE_ADMIN
from chester.storage import store_bytes


@pytest.fixture
def owner(make_user):
    return make_user("owner@example.com", ROLE_ADMIN)


@pytest.fixture
def study(session, owner):
    row = Study(owner_user_id=owner.id, organization_id=owner.organization_id, status="queued")
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def add_instance(session, study, owner):
    """Store bytes and file an instance for them, in call order."""

    def _add(data: bytes, *, name: str, content_type: str = "application/dicom") -> Instance:
        key = f"originals/{study.id}/{name}"
        store_bytes(key, data, content_type, session=session)
        instance = Instance(
            study_id=study.id,
            organization_id=owner.organization_id,
            object_key=key,
            content_type=content_type,
        )
        session.add(instance)
        session.flush()
        return instance

    return _add


def test_the_frontal_is_chosen_however_late_it_arrives(session, study, add_instance, make_dicom):
    add_instance(make_dicom(view_position="LL"), name="perfil.dcm")
    frontal = add_instance(make_dicom(view_position="PA"), name="frente.dcm")

    assert representative_instance(session, study) is frontal


def test_the_frontal_is_chosen_when_it_arrived_first(session, study, add_instance, make_dicom):
    frontal = add_instance(make_dicom(view_position="AP"), name="frente.dcm")
    add_instance(make_dicom(view_position="LL"), name="perfil.dcm")

    assert representative_instance(session, study) is frontal


def test_an_instance_of_unknown_projection_beats_a_lateral(
    session, study, add_instance, make_dicom
):
    """Unknown is not lateral: the film may well be the frontal one."""
    add_instance(make_dicom(view_position="LL"), name="perfil.dcm")
    unknown = add_instance(make_dicom(view_position="", study_description="TORAX"), name="x.dcm")

    assert representative_instance(session, study) is unknown


def test_a_lateral_only_study_still_has_a_picture(session, study, add_instance, make_dicom):
    """Nothing analyses it, but a thumbnail has to be drawn from something."""
    first = add_instance(make_dicom(view_position="LL"), name="perfil.dcm")
    add_instance(make_dicom(view_position="RL"), name="perfil2.dcm")

    assert representative_instance(session, study) is first


def test_a_single_instance_is_answered_without_reading_it(
    session, study, add_instance, make_dicom, monkeypatch
):
    """The common case must not pay for a fetch it cannot learn anything from."""
    only = add_instance(make_dicom(view_position="LL"), name="perfil.dcm")

    def refuse(*args, **kwargs):
        raise AssertionError("storage must not be touched for a single-instance study")

    monkeypatch.setattr("chester.storage.retrieve_bytes", refuse)

    assert representative_instance(session, study) is only


def test_a_study_with_no_stored_bytes_has_no_representative(session, study, owner):
    session.add(Instance(study_id=study.id, organization_id=owner.organization_id))
    session.flush()

    assert representative_instance(session, study) is None


def test_a_plain_image_has_no_projection_to_read(session, study, add_instance, make_png):
    instance = add_instance(make_png(), name="scan.png", content_type="image/png")

    assert instance_projection(session, instance) == UNKNOWN_PROJECTION


def test_bytes_that_cannot_be_read_are_unknown_rather_than_fatal(
    session, study, add_instance, make_dicom
):
    instance = add_instance(make_dicom(view_position="PA"), name="frente.dcm")
    store_bytes(instance.object_key, b"not a dicom at all", "application/dicom", session=session)

    assert instance_projection(session, instance) == UNKNOWN_PROJECTION


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"view_position": "PA"}, FRONTAL),
        ({"view_position": "LL"}, LATERAL),
        ({"view_position": "", "study_description": "TORAX PERFIL"}, LATERAL),
        ({"view_position": "", "study_description": "TORAX"}, UNKNOWN_PROJECTION),
    ],
)
def test_the_projection_is_read_from_the_stored_header(
    session, study, add_instance, make_dicom, fields, expected
):
    instance = add_instance(make_dicom(**fields), name="film.dcm")

    assert instance_projection(session, instance) == expected
