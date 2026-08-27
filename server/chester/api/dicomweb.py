"""DICOMweb STOW-RS ingestion.

Keeps /dicomweb/studies as the canonical endpoint and retains the WADO-style
aliases OsiriX needs, including the duplicated path some configurations emit.
"""

from __future__ import annotations

import base64
import email.parser
import email.policy
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from chester.config import settings
from chester.db import get_session
from chester.ingestion import ingest_file
from chester.models import Study, User
from chester.security.roles import normalize_email
from chester.security.tokens import tokens_equal

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dicomweb"])

# DICOM PS3.18 status codes carried in the FailedSOPSequence.
STATUS_SUCCESS = "0000"
STATUS_DUPLICATE = "B000"
STATUS_FAILURE = "C122"

# DICOM JSON tags used in the STOW-RS response.
TAG_RETRIEVE_URL = "00081190"
TAG_FAILED_SOP_SEQUENCE = "00081198"
TAG_REFERENCED_SOP_SEQUENCE = "00081199"
TAG_SOP_CLASS_UID = "00081150"
TAG_SOP_INSTANCE_UID = "00081155"
TAG_FAILURE_REASON = "00081197"

OWNER_HEADER = "X-Worklist-Owner"
INGEST_KEY_HEADER = "X-DICOM-Ingest-Key"
OWNER_PATTERN = re.compile(r"[A-Za-z0-9_.:@+-]+")
MAX_OWNER_LENGTH = 320


def verify_service_token(request: Request) -> bool:
    """Accept the ingest token as a custom header, a Bearer token or a Basic password.

    OsiriX supplies HTTP Basic credentials rather than a custom header, so the
    password field carries the token. Every comparison is constant time; the
    previous implementation short-circuited the Bearer branch with a plain `==`
    before its constant-time compare.
    """
    expected = settings.dicom_ingest_token
    if not expected:
        return False

    ingest_key = request.headers.get(INGEST_KEY_HEADER, "")
    if ingest_key:
        return tokens_equal(ingest_key, expected)

    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return tokens_equal(authorization[7:].strip(), expected)

    if authorization.startswith("Basic "):
        try:
            decoded = base64.b64decode(authorization[6:].strip(), validate=True).decode()
        except (ValueError, UnicodeDecodeError):
            return False
        username, separator, password = decoded.partition(":")
        if not username or not separator:
            return False
        return tokens_equal(password, expected)

    return False


def credentials_presented(request: Request) -> bool:
    """Whether the caller sent anything that could carry the ingest token."""
    if request.headers.get(INGEST_KEY_HEADER):
        return True
    authorization = request.headers.get("Authorization", "")
    return authorization.startswith(("Bearer ", "Basic "))


def echo_document(request: Request) -> dict:
    """What a connectivity probe gets back.

    Modality workstations verify a node before they will send to it, and a
    plain GET is how they do it over HTTP -- OsiriX included. Answering 405
    reads as a broken endpoint, so the same paths that accept an upload also
    answer a probe describing what they accept.

    The document is deliberately static: it names the endpoint and what it
    speaks, and nothing about the deployment behind it. The one conditional
    field is `authenticated`, which appears only when the caller actually
    presented a credential -- that turns the probe into a way to tell a wrong
    token apart from an unreachable host, which is the failure operators
    actually hit, without telling an anonymous prober anything the upload
    endpoint would not already tell them.
    """
    document = {
        "service": "Torax AI DICOMweb",
        "status": "active",
        "endpoint": "/dicomweb/studies",
        "aeTitle": settings.dicom_scp_ae_title,
        "capabilities": ["STOW-RS"],
        "supportedContentTypes": [
            'multipart/related; type="application/dicom"',
            "application/dicom",
        ],
    }
    if credentials_presented(request):
        document["authenticated"] = verify_service_token(request)
    return document


def require_service_token(request: Request) -> None:
    if not verify_service_token(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing DICOM ingest token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def read_limited_body(request: Request, limit: int) -> bytes:
    """Read the request body, refusing anything past the cap.

    Read in chunks rather than with request.body() so an oversized upload is
    rejected as it arrives instead of being buffered in full first. This path
    accepts unauthenticated uploads when anonymous WADO is enabled, so an
    unbounded read is a denial-of-service primitive.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="Payload too large")

    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise HTTPException(status_code=413, detail="Payload too large")
        chunks.append(chunk)
    return b"".join(chunks)


def parse_multipart_related(content_type: str, body: bytes) -> list[bytes]:
    """Split a multipart/related body into its parts.

    Uses the standard library's RFC-conformant parser. The previous implementation
    split the raw body on the boundary bytes wherever they appeared, so a DICOM
    payload that happened to contain the boundary sequence was silently torn in
    half. A conformant parser only honours a delimiter at the start of a line.
    """
    headers = b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n"
    message = email.parser.BytesParser(policy=email.policy.default).parsebytes(headers + body)

    if not message.is_multipart():
        raise ValueError("body is not multipart")

    parts: list[bytes] = []
    for part in message.iter_parts():
        payload = part.get_payload(decode=True)
        if payload:
            parts.append(payload)
    return parts


def resolve_owner(db: Session, request: Request) -> User:
    """Find the user who will own everything in this request."""
    raw_owner = (
        request.headers.get(OWNER_HEADER, "").strip() or settings.dicom_ingest_owner_email.strip()
    )
    if not raw_owner:
        raise HTTPException(
            status_code=400,
            detail=f"{OWNER_HEADER} or DICOM_INGEST_OWNER_EMAIL is required",
        )
    if len(raw_owner) > MAX_OWNER_LENGTH or not OWNER_PATTERN.fullmatch(raw_owner):
        raise HTTPException(status_code=400, detail="Invalid worklist owner identifier")

    owner = (
        db.query(User)
        .filter(User.email == normalize_email(raw_owner), User.active.is_(True))
        .first()
    )
    if owner is None:
        # Never invent an owner. An unrecognized identifier is a configuration
        # error, and guessing one would file a study into someone else's worklist.
        raise HTTPException(status_code=400, detail="Worklist owner is not an authorized user")
    return owner


def build_response(successes: list[dict], failures: list[dict], duplicates: list[dict]) -> dict:
    """Assemble the DICOM JSON response body."""
    body: dict = {}

    if successes:
        body[TAG_RETRIEVE_URL] = {"vr": "UR", "Value": ["/dicomweb/studies"]}
        body[TAG_REFERENCED_SOP_SEQUENCE] = {
            "vr": "SQ",
            "Value": [
                {
                    TAG_SOP_CLASS_UID: {"vr": "UI", "Value": [item.get("sop_class_uid", "")]},
                    TAG_SOP_INSTANCE_UID: {
                        "vr": "UI",
                        "Value": [item.get("sop_instance_uid", "")],
                    },
                    TAG_RETRIEVE_URL: {"vr": "UR", "Value": [item.get("retrieve_url", "")]},
                }
                for item in successes
            ],
        }

    all_failures = failures + [{**item, "failure_reason": STATUS_DUPLICATE} for item in duplicates]
    if all_failures:
        body[TAG_FAILED_SOP_SEQUENCE] = {
            "vr": "SQ",
            "Value": [
                {
                    TAG_SOP_CLASS_UID: {"vr": "UI", "Value": [item.get("sop_class_uid", "")]},
                    TAG_SOP_INSTANCE_UID: {
                        "vr": "UI",
                        "Value": [item.get("sop_instance_uid", "")],
                    },
                    TAG_FAILURE_REASON: {
                        "vr": "US",
                        "Value": [item.get("failure_reason", STATUS_FAILURE)],
                    },
                }
                for item in all_failures
            ],
        }

    return body


def response_status(successes: list, failures: list, duplicates: list) -> int:
    if successes and (failures or duplicates):
        return 202  # Accepted: partial success
    if successes:
        return 200
    if duplicates and not failures:
        return 409  # Conflict: everything was already stored
    return 400


async def handle_stow(
    request: Request,
    db: Session,
    *,
    study_uid: str | None = None,
    require_auth: bool = True,
) -> JSONResponse:
    """Shared STOW-RS handler for the canonical and compatibility routes."""
    if require_auth:
        require_service_token(request)

    owner = resolve_owner(db, request)

    content_type = request.headers.get("content-type", "")
    if "multipart/related" not in content_type.lower():
        raise HTTPException(
            status_code=400,
            detail="Content-Type must be multipart/related; type=application/dicom",
        )

    body = await read_limited_body(request, settings.dicom_max_upload_bytes)
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        parts = parse_multipart_related(content_type, body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Multipart parse error: {exc}") from exc

    if not parts:
        raise HTTPException(status_code=400, detail="No DICOM parts found in multipart body")

    ingest_source = (
        "c-store" if request.headers.get("X-Ingest-Source", "").lower() == "c-store" else "stow-rs"
    )

    successes: list[dict] = []
    failures: list[dict] = []
    duplicates: list[dict] = []

    for payload in parts:
        if study_uid and not _matches_study(payload, study_uid, failures):
            continue

        result = ingest_file(
            data=payload,
            filename="stow.dcm",
            content_type="application/dicom",
            owner=owner,
            actor=f"dicomweb:{owner.email}",
            db=db,
            source=ingest_source,
        )

        if not result.ok:
            failures.append(
                {"sop_instance_uid": "", "sop_class_uid": "", "failure_reason": STATUS_FAILURE}
            )
        elif result.deduplicated:
            duplicates.append(
                {
                    "sop_instance_uid": result.sop_instance_uid,
                    "sop_class_uid": "",
                    "failure_reason": STATUS_DUPLICATE,
                }
            )
        else:
            study = db.get(Study, result.study_id)
            retrieve = f"/dicomweb/studies/{study.study_instance_uid or study.id}" if study else ""
            successes.append(
                {
                    "sop_instance_uid": result.sop_instance_uid,
                    "sop_class_uid": "",
                    "retrieve_url": retrieve,
                }
            )

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("STOW-RS commit failed")
        raise HTTPException(status_code=500, detail="Database error") from None

    return JSONResponse(
        content=build_response(successes, failures, duplicates),
        status_code=response_status(successes, failures, duplicates),
    )


def _matches_study(payload: bytes, study_uid: str, failures: list[dict]) -> bool:
    """Reject an instance filed under a StudyInstanceUID that is not its own."""
    from chester.imaging.dicom import extract_metadata, parse_dicom_bytes

    try:
        incoming = extract_metadata(parse_dicom_bytes(payload)).get("study_instance_uid")
    except Exception:
        failures.append(
            {"sop_instance_uid": "", "sop_class_uid": "", "failure_reason": STATUS_FAILURE}
        )
        return False
    if incoming != study_uid:
        failures.append(
            {"sop_instance_uid": "", "sop_class_uid": "", "failure_reason": STATUS_FAILURE}
        )
        return False
    return True


@router.post("/dicomweb/studies")
async def stow_studies(request: Request, db: Session = Depends(get_session)):
    """Canonical STOW-RS endpoint."""
    return await handle_stow(request, db)


@router.post("/dicomweb/studies/{study_uid}")
async def stow_study(study_uid: str, request: Request, db: Session = Depends(get_session)):
    """STOW-RS scoped to one StudyInstanceUID."""
    return await handle_stow(request, db, study_uid=study_uid)


@router.post("/wado/studies")
async def stow_wado(request: Request, db: Session = Depends(get_session)):
    """Compatibility alias for OsiriX configurations using a WADO base path."""
    return await handle_stow(request, db, require_auth=not settings.dicom_wado_anonymous_ingest)


@router.post("/wado/studies/{study_uid}")
async def stow_wado_study(study_uid: str, request: Request, db: Session = Depends(get_session)):
    """Same alias, including the duplicated /wado/studies/studies path OsiriX emits."""
    return await handle_stow(
        request,
        db,
        study_uid=None if study_uid == "studies" else study_uid,
        require_auth=not settings.dicom_wado_anonymous_ingest,
    )


# A probe is a GET on the very path the sender is configured to POST to, so
# every upload path answers one, including the duplicated /wado/studies/studies
# that some OsiriX configurations emit.
# HEAD as well as GET: a probe that only wants to know the node is there sends
# one, and Starlette does not answer HEAD from a GET route on its own.
PROBE_METHODS = ["GET", "HEAD"]


@router.api_route("/dicomweb/studies", methods=PROBE_METHODS)
def echo_dicomweb(request: Request) -> dict:
    """Connectivity probe for the canonical endpoint."""
    return echo_document(request)


@router.api_route("/wado/studies", methods=PROBE_METHODS)
def echo_wado(request: Request) -> dict:
    """Connectivity probe for the WADO-style alias."""
    return echo_document(request)


@router.api_route("/wado/studies/{study_uid}", methods=PROBE_METHODS)
def echo_wado_study(study_uid: str, request: Request) -> dict:
    """Probe on the scoped alias, including the duplicated path."""
    return echo_document(request)
