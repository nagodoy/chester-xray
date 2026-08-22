"""STOW-RS DICOM web endpoints."""
from __future__ import annotations

import email
import email.parser
import hashlib
import hmac
import io
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.ingestion import ingest_file
from app.models import Study

logger = logging.getLogger(__name__)
router = APIRouter()

# DICOM STOW-RS SOP Class UID for storage commitment
STOW_SUCCESS_STATUS = "0000"  # Success
STOW_DUPLICATE_STATUS = "B000"  # Duplicate SOP Instance
STOW_FAILURE_STATUS = "C122"  # Failure: referenced SOP class not supported


def _verify_service_token(request: Request) -> bool:
    """
    Verify service token from X-DICOM-Ingest-Key header or Bearer token.
    Constant-time comparison to DICOM_INGEST_TOKEN, falling back to SESSION_SECRET.
    """
    token = settings.dicom_ingest_token or settings.session_secret

    # Check X-DICOM-Ingest-Key header
    ingest_key = request.headers.get("X-DICOM-Ingest-Key", "")
    if ingest_key:
        return hmac.compare_digest(ingest_key, token)

    # Check Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer = auth_header[7:].strip()
        # Try Clerk auth fallback (service accounts)
        if bearer == token:
            return True
        return hmac.compare_digest(bearer, token)

    return False


async def _require_dicom_auth(request: Request) -> None:
    """Raise 401 if service token verification fails."""
    if not _verify_service_token(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing DICOM ingest token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _parse_multipart_dicom(content_type: str, body: bytes) -> list[bytes]:
    """
    Parse multipart/related; type=application/dicom body.
    Returns list of DICOM file bytes.
    """
    # Extract boundary
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            boundary = part[9:].strip().strip('"')
            break

    if not boundary:
        raise ValueError("No boundary in Content-Type")

    # Split on boundary
    delimiter = f"--{boundary}".encode()
    end_delimiter = f"--{boundary}--".encode()

    parts = []
    chunks = body.split(delimiter)

    for chunk in chunks[1:]:  # Skip preamble
        if chunk.startswith(b"--") or chunk.strip() == b"":
            continue
        # Remove leading CRLF
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        elif chunk.startswith(b"\n"):
            chunk = chunk[1:]

        # Find header/body separator
        sep = b"\r\n\r\n"
        idx = chunk.find(sep)
        if idx == -1:
            sep = b"\n\n"
            idx = chunk.find(sep)

        if idx == -1:
            # No headers, treat whole chunk as body
            content = chunk
        else:
            content = chunk[idx + len(sep):]

        # Remove trailing boundary/CRLF
        content = content.rstrip(b"\r\n")
        if content.endswith(b"--"):
            content = content[:-2].rstrip(b"\r\n")

        if content:
            parts.append(content)

    return parts


def _build_stow_response(
    successes: list[dict],
    failures: list[dict],
    duplicates: list[dict],
) -> dict:
    """Build DICOM JSON-style STOW-RS response."""
    resp = {}

    if successes:
        resp["00081190"] = {  # RetrieveURL (placeholder)
            "vr": "UR",
            "Value": ["/dicomweb/studies"],
        }
        resp["00081199"] = {  # ReferencedSOPSequence
            "vr": "SQ",
            "Value": [
                {
                    "00081150": {"vr": "UI", "Value": [s.get("sop_class_uid", "")]},
                    "00081155": {"vr": "UI", "Value": [s.get("sop_instance_uid", "")]},
                    "00081190": {"vr": "UR", "Value": [s.get("retrieve_url", "")]},
                }
                for s in successes
            ],
        }

    all_failures = failures + [
        {**d, "failure_reason": STOW_DUPLICATE_STATUS} for d in duplicates
    ]
    if all_failures:
        resp["00081198"] = {  # FailedSOPSequence
            "vr": "SQ",
            "Value": [
                {
                    "00081150": {"vr": "UI", "Value": [f.get("sop_class_uid", "")]},
                    "00081155": {"vr": "UI", "Value": [f.get("sop_instance_uid", "")]},
                    "00081197": {"vr": "US", "Value": [f.get("failure_reason", STOW_FAILURE_STATUS)]},
                }
                for f in all_failures
            ],
        }

    return resp


async def _handle_stow(
    request: Request,
    session: Session,
    study_uid: Optional[str] = None,
) -> JSONResponse:
    """Common STOW-RS handler."""
    await _require_dicom_auth(request)
    owner_id = (
        request.headers.get("X-Worklist-Owner", "").strip()
        or settings.dicom_ingest_owner_id.strip()
    )
    if not owner_id:
        raise HTTPException(
            status_code=400,
            detail="X-Worklist-Owner or DICOM_INGEST_OWNER_ID is required",
        )
    if len(owner_id) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:@-]+", owner_id):
        raise HTTPException(status_code=400, detail="Invalid worklist owner identifier")

    content_type = request.headers.get("content-type", "")

    if "multipart/related" not in content_type.lower():
        raise HTTPException(
            status_code=400,
            detail="Content-Type must be multipart/related; type=application/dicom",
        )

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        dicom_parts = _parse_multipart_dicom(content_type, body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Multipart parse error: {exc}")

    if not dicom_parts:
        raise HTTPException(status_code=400, detail="No DICOM parts found in multipart body")

    successes = []
    failures = []
    duplicates = []

    for dicom_bytes in dicom_parts:
        if not dicom_bytes:
            continue

        if study_uid:
            try:
                from app.dicom_utils import extract_dicom_metadata, parse_dicom_bytes
                incoming_uid = extract_dicom_metadata(parse_dicom_bytes(dicom_bytes)).get(
                    "study_instance_uid"
                )
                if incoming_uid != study_uid:
                    failures.append({
                        "sop_instance_uid": "",
                        "sop_class_uid": "",
                        "failure_reason": STOW_FAILURE_STATUS,
                        "error": "StudyInstanceUID does not match request path",
                    })
                    continue
            except Exception as exc:
                failures.append({
                    "sop_instance_uid": "",
                    "sop_class_uid": "",
                    "failure_reason": STOW_FAILURE_STATUS,
                    "error": str(exc),
                })
                continue

        ingest_source = (
            "c-store"
            if request.headers.get("X-Ingest-Source", "").lower() == "c-store"
            else "stow-rs"
        )
        result = ingest_file(
            data=dicom_bytes,
            filename="stow.dcm",
            content_type="application/dicom",
            actor_id="dicomweb-service",
            session=session,
            source=ingest_source,
            owner_id=owner_id,
        )

        if "error" in result:
            failures.append({
                "sop_instance_uid": "",
                "sop_class_uid": "",
                "failure_reason": STOW_FAILURE_STATUS,
                "error": result["error"],
            })
        elif result.get("deduplicated"):
            duplicates.append({
                "sop_instance_uid": result.get("sop_instance_uid", ""),
                "sop_class_uid": "",
                "failure_reason": STOW_DUPLICATE_STATUS,
            })
        else:
            study_id = result["study_id"]
            study = session.query(Study).filter_by(
                id=study_id,
                owner_id=owner_id,
            ).first()
            successes.append({
                "sop_instance_uid": result.get("sop_instance_uid", ""),
                "sop_class_uid": "",
                "retrieve_url": f"/dicomweb/studies/{study.study_instance_uid or study_id}" if study else "",
            })

    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("STOW-RS commit error: %s", exc)
        raise HTTPException(status_code=500, detail="Database error")

    from app.worker import start_worker
    if successes:
        start_worker()

    response_body = _build_stow_response(successes, failures, duplicates)

    # Determine HTTP status code
    if not successes and not duplicates and failures:
        http_status = 400
    elif not successes and duplicates and not failures:
        http_status = 409
    elif successes and (failures or duplicates):
        http_status = 202  # Partial success
    elif successes:
        http_status = 200
    else:
        http_status = 400

    return JSONResponse(content=response_body, status_code=http_status)


@router.post("/dicomweb/studies")
async def stow_studies(
    request: Request,
    session: Session = Depends(get_db),
):
    """STOW-RS: Store DICOM instances."""
    return await _handle_stow(request, session, study_uid=None)


@router.post("/dicomweb/studies/{study_uid}")
async def stow_study(
    study_uid: str,
    request: Request,
    session: Session = Depends(get_db),
):
    """STOW-RS: Store DICOM instances for a specific study."""
    return await _handle_stow(request, session, study_uid=study_uid)
