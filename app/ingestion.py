"""File ingestion: upload processing, deduplication, study/instance creation."""
from __future__ import annotations

import hashlib
import io
import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.dicom_utils import (
    PREPROCESSING_VERSION,
    compute_sha256,
    extract_dicom_metadata,
    generate_thumbnail,
    make_synthetic_uids,
    parse_dicom_bytes,
    render_dicom_frame,
    validate_study,
)
from app.models import AnalysisJob, AuditEvent, Instance, StoredObject, Study
from app.pseudonymize import pseudonymize_patient_id
from app.storage import store_bytes

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg"}
DICOM_CONTENT_TYPE = "application/dicom"


def ingest_file(
    data: bytes,
    filename: str,
    content_type: str,
    actor_id: str,
    session: Session,
    source: str = "upload",
    owner_id: Optional[str] = None,
) -> dict:
    """
    Ingest a single file upload.

    Returns a result dict with keys:
      - study_id: str (if successful)
      - sop_instance_uid: str
      - deduplicated: bool
      - error: str (if failed)
    """
    owner_id = owner_id or actor_id
    sha256 = compute_sha256(data)
    is_dicom = _is_dicom(data, filename, content_type)

    try:
        if is_dicom:
            return _ingest_dicom(
                data, sha256, filename, actor_id, owner_id, session, source
            )
        else:
            return _ingest_image(
                data, sha256, filename, content_type, actor_id, owner_id, session, source
            )
    except Exception as exc:
        logger.exception("Ingestion error for %s: %s", filename, exc)
        return {"error": str(exc), "filename": filename}


def _is_dicom(data: bytes, filename: str, content_type: str) -> bool:
    """Detect DICOM by magic bytes, filename, or content-type."""
    if filename.lower().endswith(".dcm"):
        return True
    if content_type in ("application/dicom", "application/octet-stream"):
        # Check DICOM magic: offset 128 has 'DICM'
        if len(data) > 132 and data[128:132] == b"DICM":
            return True
    if len(data) > 132 and data[128:132] == b"DICM":
        return True
    return False


def _ingest_dicom(
    data: bytes,
    sha256: str,
    filename: str,
    actor_id: str,
    owner_id: str,
    session: Session,
    source: str,
) -> dict:
    """Ingest a DICOM file."""
    # Deduplicate by SHA256
    existing = session.query(Instance).filter_by(sha256=sha256).first()
    if existing:
        if existing.study.owner_id != owner_id:
            return {"error": "Instance already exists in another worklist", "filename": filename}
        _audit(session, existing.study_id, actor_id, "deduplicated_sha256",
               {"sha256": sha256, "filename": filename})
        return {
            "study_id": existing.study_id,
            "sop_instance_uid": existing.sop_instance_uid or "",
            "deduplicated": True,
        }

    # Parse DICOM
    ds = parse_dicom_bytes(data)
    meta = extract_dicom_metadata(ds)

    # Deduplicate by SOP Instance UID
    sop_uid = meta.get("sop_instance_uid", "")
    if sop_uid:
        existing_uid = session.query(Instance).filter_by(sop_instance_uid=sop_uid).first()
        if existing_uid:
            if existing_uid.study.owner_id != owner_id:
                return {"error": "SOP Instance UID already exists in another worklist", "filename": filename}
            _audit(session, existing_uid.study_id, actor_id, "deduplicated_sop_uid",
                   {"sop_uid": sop_uid, "filename": filename})
            return {
                "study_id": existing_uid.study_id,
                "sop_instance_uid": sop_uid,
                "deduplicated": True,
            }

    # Extract pixel array for validation and thumbnail
    pixel_array = None
    frame_count = meta.get("frame_count", 1)
    audit_note = None
    if frame_count > 1:
        audit_note = f"Multi-frame DICOM ({frame_count} frames); using frame 0 for analysis"
        logger.info("Multi-frame DICOM: %d frames, using frame 0", frame_count)

    try:
        pixel_array = render_dicom_frame(ds, frame_index=0)
    except Exception as exc:
        logger.warning("Could not render DICOM pixel data: %s", exc)

    # Validate
    validation_state, validation_reason = validate_study(meta, pixel_array)

    # Pseudonymize patient ID
    patient_id_pseudo = pseudonymize_patient_id(meta.get("raw_patient_id", ""))

    # Group all instances with the same Study Instance UID under one worklist
    # study. SOP Instance UID and content checksum still enforce deduplication.
    study_uid = meta.get("study_instance_uid") or None
    study = (
        session.query(Study).filter_by(
            study_instance_uid=study_uid,
            owner_id=owner_id,
        ).first()
        if study_uid
        else None
    )
    is_new_study = study is None
    if is_new_study:
        study = Study(
            owner_id=owner_id,
            patient_id=patient_id_pseudo or None,
            patient_age=meta.get("patient_age") or None,
            patient_sex=meta.get("patient_sex") or None,
            study_date=meta.get("study_date") or None,
            modality=meta.get("modality") or None,
            view_position=meta.get("view_position") or None,
            description=meta.get("description") or None,
            source=source,
            status="validating",
            validation_state=validation_state,
            validation_reason=validation_reason,
            study_instance_uid=study_uid,
        )
        session.add(study)
        session.flush()
    else:
        # Fill only missing metadata; never overwrite the original pseudonym.
        study.patient_id = study.patient_id or patient_id_pseudo or None
        study.patient_age = study.patient_age or meta.get("patient_age") or None
        study.patient_sex = study.patient_sex or meta.get("patient_sex") or None
        study.study_date = study.study_date or meta.get("study_date") or None
        study.modality = study.modality or meta.get("modality") or None
        study.view_position = study.view_position or meta.get("view_position") or None
        study.description = study.description or meta.get("description") or None

    # Generate and store thumbnail
    thumbnail_key = None
    if pixel_array is not None and not study.thumbnail_url:
        try:
            thumb_bytes = generate_thumbnail(pixel_array)
        except Exception as exc:
            logger.warning("Thumbnail generation failed: %s", exc)
        else:
            thumb_key = f"thumbnails/{study.id}.png"
            store_bytes(thumb_key, thumb_bytes, "image/png", session=session, instance_id=None)
            study.thumbnail_url = f"/api/studies/{study.id}/thumbnail"
            thumbnail_key = thumb_key

    # Store original DICOM
    object_key = f"originals/{study.id}/{sha256[:8]}.dcm"
    store_bytes(object_key, data, DICOM_CONTENT_TYPE, session=session, instance_id=None)

    # Create Instance
    instance = Instance(
        study_id=study.id,
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
    session.add(instance)
    session.flush()
    session.query(StoredObject).filter_by(object_key=object_key).update(
        {"instance_id": instance.id}
    )

    # Update study status and queue job
    job = None
    if is_new_study:
        study, job = _finalize_study_status(study, validation_state, session)
    elif study.status == "needs_review" and validation_state == "chest":
        study.validation_state = validation_state
        study.validation_reason = validation_reason
        study, job = _finalize_study_status(study, validation_state, session)

    _audit(session, study.id, actor_id, "study_created", {
        "filename": filename,
        "sha256": sha256,
        "validation_state": validation_state,
        "source": source,
    })

    return {
        "study_id": study.id,
        "sop_instance_uid": sop_uid,
        "deduplicated": False,
    }


def _ingest_image(
    data: bytes,
    sha256: str,
    filename: str,
    content_type: str,
    actor_id: str,
    owner_id: str,
    session: Session,
    source: str,
) -> dict:
    """Ingest a common image (PNG/JPEG)."""
    # Deduplicate by SHA256
    existing = session.query(Instance).filter_by(sha256=sha256).first()
    if existing:
        if existing.study.owner_id != owner_id:
            return {"error": "Instance already exists in another worklist", "filename": filename}
        _audit(session, existing.study_id, actor_id, "deduplicated_sha256",
               {"sha256": sha256, "filename": filename})
        return {
            "study_id": existing.study_id,
            "sop_instance_uid": existing.sop_instance_uid or "",
            "deduplicated": True,
        }

    # Generate synthetic UIDs
    synthetic_uids = make_synthetic_uids(data)
    sop_uid = synthetic_uids["sop_instance_uid"]

    # Check by synthetic SOP UID too
    existing_uid = session.query(Instance).filter_by(sop_instance_uid=sop_uid).first()
    if existing_uid:
        if existing_uid.study.owner_id != owner_id:
            return {"error": "SOP Instance UID already exists in another worklist", "filename": filename}
        return {
            "study_id": existing_uid.study_id,
            "sop_instance_uid": sop_uid,
            "deduplicated": True,
        }

    # Load image and validate
    pixel_array = None
    rows, columns = None, None
    try:
        from PIL import Image
        import numpy as np
        with Image.open(io.BytesIO(data)) as img:
            # Convert to grayscale for validation
            gray = img.convert("L")
            rows, columns = gray.size[1], gray.size[0]
            pixel_array = np.array(gray, dtype=np.float32)
    except Exception as exc:
        logger.warning("Cannot decode image %s: %s", filename, exc)
        return {"error": f"Cannot decode image: {exc}", "filename": filename}

    # Build synthetic meta for validation
    meta = {
        "modality": "",
        "body_part": "",
        "view_position": "",
        "description": filename,
        "study_instance_uid": synthetic_uids["study_instance_uid"],
        "series_instance_uid": synthetic_uids["series_instance_uid"],
        "sop_instance_uid": sop_uid,
        "rows": rows,
        "columns": columns,
    }

    # Image-only: validate with image; default uncertain unless conservative evidence
    validation_state, validation_reason = validate_study(meta, pixel_array)
    # Image-only uploads default to uncertain (needs review) per requirements
    if validation_state == "chest":
        validation_state = "uncertain"
        validation_reason = "Image-only upload; manual review required before queuing inference"

    # Create Study
    study = Study(
        owner_id=owner_id,
        patient_id=None,
        modality=None,
        source=source,
        status="validating",
        validation_state=validation_state,
        validation_reason=validation_reason,
        study_instance_uid=synthetic_uids["study_instance_uid"],
    )
    session.add(study)
    session.flush()

    # Generate and store thumbnail
    if pixel_array is not None:
        try:
            thumb_bytes = generate_thumbnail(pixel_array)
        except Exception as exc:
            logger.warning("Thumbnail generation failed: %s", exc)
        else:
            thumb_key = f"thumbnails/{study.id}.png"
            store_bytes(thumb_key, thumb_bytes, "image/png", session=session, instance_id=None)
            study.thumbnail_url = f"/api/studies/{study.id}/thumbnail"

    # Store original
    ext = ".png" if "png" in content_type else ".jpg"
    object_key = f"originals/{study.id}/{sha256[:8]}{ext}"
    store_bytes(object_key, data, content_type, session=session, instance_id=None)

    # Create Instance
    instance = Instance(
        study_id=study.id,
        sop_instance_uid=sop_uid,
        series_instance_uid=synthetic_uids["series_instance_uid"],
        frame_count=1,
        rows=rows,
        columns=columns,
        object_key=object_key,
        sha256=sha256,
        file_size=len(data),
        content_type=content_type,
    )
    session.add(instance)
    session.flush()
    session.query(StoredObject).filter_by(object_key=object_key).update(
        {"instance_id": instance.id}
    )

    study, job = _finalize_study_status(study, validation_state, session)

    _audit(session, study.id, actor_id, "study_created", {
        "filename": filename,
        "sha256": sha256,
        "validation_state": validation_state,
        "source": source,
    })

    return {
        "study_id": study.id,
        "sop_instance_uid": sop_uid,
        "deduplicated": False,
    }


def _finalize_study_status(study: Study, validation_state: str, session: Session):
    """Set study status and optionally create a job based on validation state."""
    job = None
    if validation_state == "chest":
        study.status = "queued"
        job = AnalysisJob(study_id=study.id, status="queued")
        session.add(job)
    elif validation_state == "non_chest":
        study.status = "rejected"
    else:
        # uncertain -> needs_review
        study.status = "needs_review"
    session.flush()
    return study, job


def _audit(session: Session, study_id: Optional[str], actor_id: str, event_type: str, detail: dict):
    """Create an audit event."""
    event = AuditEvent(
        study_id=study_id,
        actor_id=actor_id,
        event_type=event_type,
        detail=detail,
    )
    session.add(event)
    session.flush()
