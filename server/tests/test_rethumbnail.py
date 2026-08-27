"""The thumbnail backfill: repairing pictures written before the ratio fix."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from chester import rethumbnail
from chester.models import Instance, Study
from chester.security.roles import ROLE_TECHNICIAN
from chester.storage import ObjectNotFound, retrieve_bytes, store_bytes


@pytest.fixture
def owner(make_user):
    return make_user("thumbs@example.com", ROLE_TECHNICIAN)


@pytest.fixture
def portrait_png() -> bytes:
    """A 400x1000 source: the shape a square thumbnail would have flattened."""
    array = np.tile(np.linspace(0, 255, 1000, dtype=np.uint8)[:, None], (1, 400))
    buffer = io.BytesIO()
    Image.fromarray(array, mode="L").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def study_with_stored_image(session, owner, portrait_png):
    """A study as the old code left it: a square, stretched thumbnail."""

    def _build(*, thumbnail: bytes | None) -> Study:
        study = Study(
            owner_user_id=owner.id,
            organization_id=owner.organization_id,
            status="completed",
        )
        session.add(study)
        session.flush()

        key = f"originals/{study.id}/source.png"
        store_bytes(key, portrait_png, "image/png", session=session)
        session.add(
            Instance(
                study_id=study.id,
                organization_id=owner.organization_id,
                object_key=key,
                content_type="image/png",
            )
        )
        if thumbnail is not None:
            store_bytes(f"thumbnails/{study.id}.png", thumbnail, "image/png", session=session)
            study.thumbnail_url = f"/api/studies/{study.id}/thumbnail"
        session.flush()
        return study

    return _build


@pytest.fixture
def squashed() -> bytes:
    """What the old generator produced: everything forced to 256x256."""
    buffer = io.BytesIO()
    Image.new("L", (256, 256), color=90).save(buffer, format="PNG")
    return buffer.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


def test_a_squashed_thumbnail_is_replaced_with_one_that_keeps_the_ratio(
    session, study_with_stored_image, squashed
):
    study = study_with_stored_image(thumbnail=squashed)

    outcome = rethumbnail.regenerate(session, study, dry_run=False)

    assert outcome == "written"
    width, height = _size(retrieve_bytes(f"thumbnails/{study.id}.png", session=session))
    assert height > width
    assert abs((width / height) - 0.4) < 0.01


def test_a_dry_run_reports_the_work_without_doing_it(session, study_with_stored_image, squashed):
    study = study_with_stored_image(thumbnail=squashed)

    outcome = rethumbnail.regenerate(session, study, dry_run=True)

    assert outcome == "would-write"
    assert retrieve_bytes(f"thumbnails/{study.id}.png", session=session) == squashed


def test_running_twice_changes_nothing_the_second_time(session, study_with_stored_image, squashed):
    """The backfill has to be safe to re-run after an interruption."""
    study = study_with_stored_image(thumbnail=squashed)
    rethumbnail.regenerate(session, study, dry_run=False)

    assert rethumbnail.regenerate(session, study, dry_run=False) == "already-current"


def test_a_study_whose_thumbnail_never_got_written_is_repaired(session, study_with_stored_image):
    study = study_with_stored_image(thumbnail=None)
    study.thumbnail_url = None
    session.flush()

    outcome = rethumbnail.regenerate(session, study, dry_run=False)

    assert outcome == "written"
    assert study.thumbnail_url == f"/api/studies/{study.id}/thumbnail"
    assert retrieve_bytes(f"thumbnails/{study.id}.png", session=session)


def test_a_study_with_no_instance_is_reported_rather_than_failing(session, owner):
    study = Study(owner_user_id=owner.id, organization_id=owner.organization_id, status="rejected")
    session.add(study)
    session.flush()

    assert rethumbnail.regenerate(session, study, dry_run=False) == "no-source"


def test_bytes_missing_from_storage_are_reported_rather_than_failing(
    session, study_with_stored_image, squashed
):
    """One study whose object was already deleted must not stop the run."""
    study = study_with_stored_image(thumbnail=squashed)
    instance = rethumbnail._source_instance(session, study)
    instance.object_key = "originals/gone/never-stored.png"
    session.flush()

    assert rethumbnail.regenerate(session, study, dry_run=False) == "missing-bytes"


def test_an_undecodable_source_is_reported_rather_than_failing(
    session, study_with_stored_image, squashed
):
    study = study_with_stored_image(thumbnail=squashed)
    instance = rethumbnail._source_instance(session, study)
    store_bytes(instance.object_key, b"not an image at all", "image/png", session=session)
    session.flush()

    assert rethumbnail.regenerate(session, study, dry_run=False) == "unreadable"
    # The old thumbnail is left in place rather than cleared.
    assert retrieve_bytes(f"thumbnails/{study.id}.png", session=session) == squashed


def test_a_dicom_instance_goes_through_the_dicom_path(session, owner, make_dicom):
    """DICOM needs the LUT and windowing that a plain image decode skips."""
    study = Study(owner_user_id=owner.id, organization_id=owner.organization_id, status="completed")
    session.add(study)
    session.flush()
    key = f"originals/{study.id}/source.dcm"
    store_bytes(key, make_dicom(), "application/dicom", session=session)
    session.add(
        Instance(
            study_id=study.id,
            organization_id=owner.organization_id,
            object_key=key,
            content_type="application/dicom",
        )
    )
    session.flush()

    assert rethumbnail.regenerate(session, study, dry_run=False) == "written"
    assert _size(retrieve_bytes(f"thumbnails/{study.id}.png", session=session))


def test_the_oldest_instance_is_the_one_drawn_from(session, study_with_stored_image, squashed):
    """Ingestion draws from the first to arrive; the backfill must not differ."""
    study = study_with_stored_image(thumbnail=squashed)
    session.add(
        Instance(
            study_id=study.id,
            organization_id=study.organization_id,
            object_key="originals/later/second.png",
            content_type="image/png",
        )
    )
    session.flush()

    chosen = rethumbnail._source_instance(session, study)

    assert chosen.object_key == f"originals/{study.id}/source.png"


def test_an_instance_with_no_stored_object_is_not_chosen(
    session, study_with_stored_image, squashed
):
    study = study_with_stored_image(thumbnail=squashed)
    bare = Instance(study_id=study.id, organization_id=study.organization_id)
    session.add(bare)
    session.flush()

    assert rethumbnail._source_instance(session, study).object_key is not None


def test_a_thumbnail_that_cannot_be_read_back_is_still_written(session, study_with_stored_image):
    """A missing thumbnail object is a repair case, not an error."""
    study = study_with_stored_image(thumbnail=None)

    assert rethumbnail.regenerate(session, study, dry_run=False) == "written"
    with pytest.raises(ObjectNotFound):
        retrieve_bytes("thumbnails/does-not-exist.png", session=session)
