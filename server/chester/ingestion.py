"""Turning an uploaded file into a study, an instance and a queued job.

Deduplication is scoped to the receiving organization. Doing it globally, as the
previous implementation did, meant one tenant's upload could be refused because
another tenant already held the same bytes, and the refusal message answered
whether a given instance existed elsewhere in the system.
"""

from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from chester import network_log
from chester.imaging.dicom import (
    compute_sha256,
    extract_metadata,
    generate_thumbnail,
    looks_like_dicom,
    make_synthetic_uids,
    parse_dicom_bytes,
    render_frame,
)
from chester.imaging.validation import (
    CHEST,
    CODE_IMAGE_ONLY,
    CODE_LATERAL_VIEW,
    LATERAL,
    NON_CHEST,
    UNCERTAIN,
    outcome,
    projection,
    validate_study,
)
from chester.models import AnalysisJob, AuditEvent, Instance, Study, User
from chester.pseudonymize import pseudonymize_patient_id
from chester.storage import store_bytes

logger = logging.getLogger(__name__)

DICOM_CONTENT_TYPE = "application/dicom"
IMAGE_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg"})

STATUS_QUEUED = "queued"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_REJECTED = "rejected"
STATUS_VALIDATING = "validating"


@dataclass
class IngestResult:
    study_id: uuid.UUID | None = None
    sop_instance_uid: str = ""
    deduplicated: bool = False
    error: str | None = None
    filename: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


def ingest_file(
    *,
    data: bytes,
    filename: str,
    content_type: str,
    owner: User,
    actor: str,
    db: Session,
    source: str = "upload",
    origin: str | None = None,
) -> IngestResult:
    """Ingest one file into the owner's worklist.

    Every outcome -- accepted, duplicate or refused -- is written to the network
    log here rather than at each call site, so an upload, a STOW-RS post and a
    forwarded C-STORE are all recorded the same way.
    """
    try:
        if looks_like_dicom(data, filename, content_type):
            result = _ingest_dicom(data, filename, owner, actor, db, source)
        else:
            result = _ingest_image(data, filename, content_type, owner, actor, db, source)
    except Exception as exc:
        logger.exception("Ingestion failed for %s", filename)
        result = IngestResult(error=str(exc), filename=filename)

    _record_receipt(db, result, owner=owner, actor=actor, channel=source, origin=origin)
    return result


def _record_receipt(
    db: Session,
    result: IngestResult,
    *,
    owner: User,
    actor: str,
    channel: str,
    origin: str | None,
) -> None:
    if not result.ok:
        status = network_log.FAILURE
    elif result.deduplicated:
        status = network_log.DUPLICATE
    else:
        status = network_log.SUCCESS

    network_log.record(
        db,
        organization_id=owner.organization_id,
        direction=network_log.RECEIVED,
        channel=channel,
        status=status,
        study_id=result.study_id,
        peer=origin,
        actor=actor,
        reference=result.sop_instance_uid or None,
        message=result.error,
        detail={"filename": result.filename or None, **result.detail},
    )


def _existing_instance(db: Session, owner: User, *, sha256: str = "", sop_uid: str = ""):
    """Find a matching instance within the owner's organization only."""
    query = db.query(Instance).filter(Instance.organization_id == owner.organization_id)
    if sha256:
        found = query.filter(Instance.sha256 == sha256).first()
        if found is not None:
            return found
    if sop_uid:
        return query.filter(Instance.sop_instance_uid == sop_uid).first()
    return None


def _deduplicated(db: Session, instance: Instance, actor: str, filename: str, reason: str):
    _audit(
        db,
        instance.study_id,
        actor,
        f"deduplicated_{reason}",
        {"filename": filename, "sha256": instance.sha256},
    )
    return IngestResult(
        study_id=instance.study_id,
        sop_instance_uid=instance.sop_instance_uid or "",
        deduplicated=True,
        filename=filename,
        detail={"deduplicated_by": reason},
    )


def _ingest_dicom(
    data: bytes, filename: str, owner: User, actor: str, db: Session, source: str
) -> IngestResult:
    sha256 = compute_sha256(data)

    duplicate = _existing_instance(db, owner, sha256=sha256)
    if duplicate is not None:
        return _deduplicated(db, duplicate, actor, filename, "sha256")

    dataset = parse_dicom_bytes(data)
    meta = extract_metadata(dataset)

    sop_uid = meta.get("sop_instance_uid", "")
    if sop_uid:
        duplicate = _existing_instance(db, owner, sop_uid=sop_uid)
        if duplicate is not None:
            return _deduplicated(db, duplicate, actor, filename, "sop_uid")

    frame_count = meta.get("frame_count", 1)
    audit_note = None
    if frame_count > 1:
        audit_note = f"Multi-frame DICOM ({frame_count} frames); frame 0 used for analysis"
        logger.info("Multi-frame DICOM with %d frames; using frame 0", frame_count)

    pixels = None
    try:
        pixels = render_frame(dataset, frame_index=0)
    except Exception as exc:
        logger.warning("Could not render pixel data for %s: %s", filename, exc)

    validation = validate_study(meta, pixels)

    # Instances sharing a Study Instance UID belong to one worklist study.
    study_uid = meta.get("study_instance_uid") or None
    study = (
        db.query(Study)
        .filter(
            Study.study_instance_uid == study_uid,
            Study.organization_id == owner.organization_id,
        )
        .first()
        if study_uid
        else None
    )

    is_new_study = study is None
    if is_new_study:
        study = Study(
            owner_user_id=owner.id,
            organization_id=owner.organization_id,
            patient_id=pseudonymize_patient_id(meta.get("raw_patient_id", "")) or None,
            patient_age=meta.get("patient_age") or None,
            patient_sex=meta.get("patient_sex") or None,
            study_date=meta.get("study_date") or None,
            modality=meta.get("modality") or None,
            body_part=meta.get("body_part") or None,
            view_position=meta.get("view_position") or None,
            description=meta.get("description") or None,
            source=source,
            status=STATUS_VALIDATING,
            validation_state=validation.state,
            validation_reason_code=validation.code,
            validation_reason=validation.reason,
            study_instance_uid=study_uid,
        )
        db.add(study)
        db.flush()
    else:
        _fill_missing_metadata(study, meta)

    drew_thumbnail = False
    if pixels is not None and not study.thumbnail_url:
        _store_thumbnail(db, study, pixels)
        drew_thumbnail = True

    object_key = f"originals/{study.id}/{sha256[:8]}.dcm"
    store_bytes(object_key, data, DICOM_CONTENT_TYPE, session=db)

    instance = Instance(
        study_id=study.id,
        organization_id=owner.organization_id,
        sop_instance_uid=sop_uid or None,
        sop_class_uid=meta.get("sop_class_uid") or None,
        series_instance_uid=meta.get("series_instance_uid") or None,
        transfer_syntax_uid=meta.get("transfer_syntax_uid") or None,
        frame_count=frame_count,
        rows=meta.get("rows"),
        columns=meta.get("columns"),
        bits_allocated=meta.get("bits_allocated"),
        object_key=object_key,
        sha256=sha256,
        file_size=len(data),
        content_type=DICOM_CONTENT_TYPE,
        audit_note=audit_note,
    )
    db.add(instance)
    db.flush()
    _attach_stored_object(db, object_key, instance)

    if is_new_study:
        _finalize_status(db, study, validation.state)
    elif validation.state == CHEST and _awaiting_a_frontal(study):
        # A later instance supplied the evidence the first one lacked -- often
        # the frontal film of an exam whose lateral was sent first, which is
        # also the image the study should be represented by from here on.
        study.validation_state = validation.state
        study.validation_reason_code = validation.code
        study.validation_reason = validation.reason
        # The row describes the image the study is scored from, so the view of
        # the instance that reopened it replaces whatever the earlier one wrote.
        study.view_position = meta.get("view_position") or study.view_position
        if pixels is not None and not drew_thumbnail:
            # The study was pictured by the film it is no longer scored from.
            _store_thumbnail(db, study, pixels)
        _finalize_status(db, study, validation.state)

    _audit(
        db,
        study.id,
        actor,
        "study_created" if is_new_study else "instance_added",
        {
            "filename": filename,
            "sha256": sha256,
            "validation_state": validation.state,
            "validation_reason_code": validation.code,
            "source": source,
        },
    )
    return IngestResult(
        study_id=study.id,
        sop_instance_uid=sop_uid,
        filename=filename,
        detail={"validation_state": validation.state, "study_status": study.status},
    )


def _ingest_image(
    data: bytes,
    filename: str,
    content_type: str,
    owner: User,
    actor: str,
    db: Session,
    source: str,
) -> IngestResult:
    import numpy as np
    from PIL import Image

    sha256 = compute_sha256(data)
    duplicate = _existing_instance(db, owner, sha256=sha256)
    if duplicate is not None:
        return _deduplicated(db, duplicate, actor, filename, "sha256")

    uids = make_synthetic_uids(data)
    duplicate = _existing_instance(db, owner, sop_uid=uids["sop_instance_uid"])
    if duplicate is not None:
        return _deduplicated(db, duplicate, actor, filename, "sop_uid")

    try:
        with Image.open(io.BytesIO(data)) as image:
            grayscale = image.convert("L")
            rows, columns = grayscale.size[1], grayscale.size[0]
            pixels = np.array(grayscale, dtype=np.float32)
    except Exception as exc:
        logger.warning("Cannot decode image %s: %s", filename, exc)
        return IngestResult(error=f"Cannot decode image: {exc}", filename=filename)

    meta = {
        "modality": "",
        "body_part": "",
        "view_position": "",
        "description": filename,
        "rows": rows,
        "columns": columns,
        **uids,
    }
    validation = validate_study(meta, pixels)
    if validation.state == CHEST:
        # A bare image carries no modality or body part, so nothing here can
        # actually establish it is a chest radiograph. Always ask a human.
        validation = outcome(UNCERTAIN, CODE_IMAGE_ONLY)

    study = Study(
        owner_user_id=owner.id,
        organization_id=owner.organization_id,
        source=source,
        status=STATUS_VALIDATING,
        validation_state=validation.state,
        validation_reason_code=validation.code,
        validation_reason=validation.reason,
        study_instance_uid=uids["study_instance_uid"],
        description=filename,
    )
    db.add(study)
    db.flush()

    _store_thumbnail(db, study, pixels)

    extension = ".png" if "png" in content_type else ".jpg"
    object_key = f"originals/{study.id}/{sha256[:8]}{extension}"
    store_bytes(object_key, data, content_type, session=db)

    instance = Instance(
        study_id=study.id,
        organization_id=owner.organization_id,
        sop_instance_uid=uids["sop_instance_uid"],
        series_instance_uid=uids["series_instance_uid"],
        frame_count=1,
        rows=rows,
        columns=columns,
        object_key=object_key,
        sha256=sha256,
        file_size=len(data),
        content_type=content_type,
    )
    db.add(instance)
    db.flush()
    _attach_stored_object(db, object_key, instance)

    _finalize_status(db, study, validation.state)
    _audit(
        db,
        study.id,
        actor,
        "study_created",
        {
            "filename": filename,
            "sha256": sha256,
            "validation_state": validation.state,
            "validation_reason_code": validation.code,
            "source": source,
        },
    )
    return IngestResult(
        study_id=study.id,
        sop_instance_uid=uids["sop_instance_uid"],
        filename=filename,
        detail={"validation_state": validation.state, "study_status": study.status},
    )


def _awaiting_a_frontal(study: Study) -> bool:
    """Whether a study can still be turned into one worth analysing.

    A study held for review can be: the evidence it lacked may arrive with a
    later instance. So can one refused for being lateral, because an exam whose
    lateral was sent first is not a lateral exam -- the frontal film is on its
    way. Nothing else is reopened by an arriving instance: a study refused for
    its modality or its body part stays refused.
    """
    if study.status == STATUS_NEEDS_REVIEW:
        return True
    return study.status == STATUS_REJECTED and study.validation_reason_code == CODE_LATERAL_VIEW


def _fill_missing_metadata(study: Study, meta: dict) -> None:
    """Fill gaps only. The first instance's pseudonym is never overwritten."""
    study.patient_age = study.patient_age or meta.get("patient_age") or None
    study.patient_sex = study.patient_sex or meta.get("patient_sex") or None
    study.study_date = study.study_date or meta.get("study_date") or None
    study.modality = study.modality or meta.get("modality") or None
    study.description = study.description or meta.get("description") or None
    # A lateral instance never names the study's view. The row describes the
    # image the study is scored from, and that is never the lateral one.
    if projection(meta) != LATERAL:
        study.view_position = study.view_position or meta.get("view_position") or None


def _store_thumbnail(db: Session, study: Study, pixels) -> None:
    try:
        thumbnail = generate_thumbnail(pixels)
    except Exception as exc:
        logger.warning("Thumbnail generation failed for study %s: %s", study.id, exc)
        return
    store_bytes(f"thumbnails/{study.id}.png", thumbnail, "image/png", session=db)
    study.thumbnail_url = f"/api/studies/{study.id}/thumbnail"


def _attach_stored_object(db: Session, object_key: str, instance: Instance) -> None:
    """Link database-backed bytes to the instance they belong to.

    A no-op when the object went to S3, where there is no row to link.
    """
    from chester.models import StoredObject

    db.query(StoredObject).filter_by(object_key=object_key).update({"instance_id": instance.id})


def _finalize_status(db: Session, study: Study, validation_state: str) -> None:
    """Set the study's status and queue analysis when it is confidently chest."""
    if validation_state == CHEST:
        study.status = STATUS_QUEUED
        db.add(AnalysisJob(study_id=study.id, status=STATUS_QUEUED))
    elif validation_state == NON_CHEST:
        study.status = STATUS_REJECTED
    else:
        study.status = STATUS_NEEDS_REVIEW
    db.flush()


def _audit(db: Session, study_id, actor: str, event_type: str, detail: dict) -> None:
    db.add(AuditEvent(study_id=study_id, actor=actor, event_type=event_type, detail=detail))
    db.flush()
