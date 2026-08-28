"""Browser file upload."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from chester.api.deps import client_ip, require_page
from chester.api.studies import _to_summary
from chester.config import settings
from chester.db import get_session
from chester.ingestion import ingest_file
from chester.models import Study, User
from chester.schemas import UploadError, UploadResponse
from chester.security.access import AccessContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/uploads", tags=["uploads"])

EXTENSION_CONTENT_TYPES = {
    ".dcm": "application/dicom",
    ".dicom": "application/dicom",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _normalized_content_type(filename: str, declared: str) -> str:
    """Trust the extension over the browser's guess, which is often wrong."""
    lowered = filename.lower()
    for extension, content_type in EXTENSION_CONTENT_TYPES.items():
        if lowered.endswith(extension):
            return content_type
    return declared or "application/octet-stream"


@router.post("", response_model=UploadResponse)
async def upload_files(
    request: Request,
    files: list[UploadFile],
    confirm_deidentified: bool = Form(...),
    access: AccessContext = Depends(require_page("upload")),
    db: Session = Depends(get_session),
):
    """Accept DICOM, PNG and JPEG uploads into the caller's worklist."""
    if not confirm_deidentified:
        raise HTTPException(
            status_code=400,
            detail="confirm_deidentified must be true; only de-identified files may be uploaded",
        )
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    owner = db.get(User, access.user_id)
    if owner is None:  # pragma: no cover - the session guarantees this
        raise HTTPException(status_code=403, detail="User is no longer authorized")

    accepted: list[Study] = []
    errors: list[UploadError] = []

    for upload in files:
        filename = upload.filename or "unknown"
        try:
            data = await upload.read()
        except Exception as exc:
            errors.append(UploadError(filename=filename, error=f"Read error: {exc}"))
            continue

        if not data:
            errors.append(UploadError(filename=filename, error="Empty file"))
            continue
        if len(data) > settings.dicom_max_upload_bytes:
            limit_mb = settings.dicom_max_upload_bytes // (1024 * 1024)
            errors.append(
                UploadError(filename=filename, error=f"File too large (max {limit_mb} MB)")
            )
            continue

        result = ingest_file(
            data=data,
            filename=filename,
            content_type=_normalized_content_type(filename, upload.content_type or ""),
            owner=owner,
            actor=access.email,
            db=db,
            source="upload",
            origin=client_ip(request),
        )

        if not result.ok:
            errors.append(UploadError(filename=filename, error=result.error or "Unknown error"))
            continue

        study = db.get(Study, result.study_id)
        if study is not None:
            accepted.append(study)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Upload commit failed")
        raise HTTPException(status_code=500, detail="Database error") from exc

    return UploadResponse(studies=[_to_summary(study) for study in accepted], errors=errors)
