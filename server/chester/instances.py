"""Which stored instance a study is represented by.

A chest exam frequently arrives as two images under one Study Instance UID: the
frontal projection and the lateral one. Only the frontal is analysed, so the
frontal is also what the thumbnail shows and what the report sheet draws --
otherwise a study would be scored from one image and illustrated with another.

The worker, the thumbnail rebuild and the report all have to make that choice
the same way, so it is made here once. The ordering is unchanged where nothing
distinguishes the instances: oldest first, which is the one ingestion drew from.

Reading the projection costs a fetch per instance, so a study holding a single
instance -- almost all of them -- is answered without touching storage at all.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from chester.imaging.validation import FRONTAL, LATERAL, UNKNOWN_PROJECTION, projection
from chester.models import Instance, Study

logger = logging.getLogger(__name__)

DICOM_CONTENT_TYPES = ("application/dicom", "application/octet-stream")


def stored_instances(db: Session, study: Study) -> list[Instance]:
    """The study's instances that carry bytes, oldest first."""
    return (
        db.query(Instance)
        .filter(Instance.study_id == study.id, Instance.object_key.isnot(None))
        .order_by(Instance.created_at.asc(), Instance.id.asc())
        .all()
    )


def instance_projection(db: Session, instance: Instance) -> str:
    """Frontal, lateral or unknown, read from the stored instance's own header.

    Unknown is the answer for anything that is not a DICOM and for anything that
    cannot be read: a projection is only ever asserted from metadata that says
    so, never assumed from a failure.
    """
    content_type = instance.content_type or ""
    if not instance.object_key or not content_type.startswith(DICOM_CONTENT_TYPES):
        return UNKNOWN_PROJECTION

    from chester.imaging.dicom import extract_metadata
    from chester.storage import retrieve_bytes

    try:
        import pydicom
        from pydicom.filebase import DicomBytesIO

        raw = retrieve_bytes(instance.object_key, session=db)
        dataset = pydicom.dcmread(DicomBytesIO(raw), stop_before_pixels=True, force=True)
        return projection(extract_metadata(dataset))
    except Exception as exc:
        logger.warning("Could not read the projection of instance %s: %s", instance.id, exc)
        return UNKNOWN_PROJECTION


def representative_instance(db: Session, study: Study) -> Instance | None:
    """The instance that stands for the study: its frontal image where there is one.

    A lateral is chosen only when the study holds nothing else, which happens
    when a lateral-only study is being redrawn -- the picture still has to come
    from somewhere. Nothing here queues analysis; a lateral-only study is refused
    at validation and never reaches a worker.
    """
    candidates = stored_instances(db, study)
    if len(candidates) < 2:
        return candidates[0] if candidates else None

    fallback: Instance | None = None
    for instance in candidates:
        view = instance_projection(db, instance)
        if view == FRONTAL:
            return instance
        if view != LATERAL and fallback is None:
            fallback = instance

    return fallback or candidates[0]
