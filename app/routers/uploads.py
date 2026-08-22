"""File upload endpoint."""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.database import get_db
from app.ingestion import ingest_file
from app.models import Study
from app.routers.studies import _to_study_schema
from app.schemas import UploadError, UploadResponse
from app.worker import start_worker

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB per file
ALLOWED_CONTENT_TYPES = {
    "application/dicom",
    "application/octet-stream",
    "image/png",
    "image/jpeg",
    "image/jpg",
}


@router.post("/api/uploads", response_model=UploadResponse)
async def upload_files(
    request: Request,
    files: List[UploadFile],
    confirm_deidentified: bool = Form(...),
    actor_id: str = Depends(require_auth),
    session: Session = Depends(get_db),
):
    """
    Multipart file upload. Requires confirm_deidentified=true.
    Accepts DICOM (.dcm), PNG, JPEG.
    """
    if not confirm_deidentified:
        raise HTTPException(
            status_code=400,
            detail="confirm_deidentified must be true; only de-identified files may be uploaded",
        )

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    studies_out = []
    errors_out = []

    for upload_file in files:
        filename = upload_file.filename or "unknown"
        content_type = upload_file.content_type or "application/octet-stream"

        # Normalize content type
        if filename.lower().endswith(".dcm"):
            content_type = "application/dicom"
        elif filename.lower().endswith(".png"):
            content_type = "image/png"
        elif filename.lower().endswith((".jpg", ".jpeg")):
            content_type = "image/jpeg"

        # Read data
        try:
            data = await upload_file.read()
        except Exception as exc:
            errors_out.append(UploadError(filename=filename, error=f"Read error: {exc}"))
            continue

        if len(data) > MAX_FILE_SIZE:
            errors_out.append(UploadError(filename=filename, error="File too large (max 100 MB)"))
            continue

        if len(data) == 0:
            errors_out.append(UploadError(filename=filename, error="Empty file"))
            continue

        result = ingest_file(
            data=data,
            filename=filename,
            content_type=content_type,
            actor_id=actor_id,
            session=session,
            source="upload",
        )

        if "error" in result:
            errors_out.append(UploadError(filename=filename, error=result["error"]))
        else:
            study = session.query(Study).filter_by(
                id=result["study_id"],
                owner_id=actor_id,
            ).first()
            if study:
                studies_out.append(_to_study_schema(study))

    # Commit and start worker if needed
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Database commit failed: {exc}")

    if any(s.status == "queued" for s in studies_out):
        start_worker()

    return UploadResponse(studies=studies_out, errors=errors_out)
