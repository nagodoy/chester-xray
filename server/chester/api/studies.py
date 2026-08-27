"""Worklist and study detail."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from chester.api.deps import require_admin, require_page
from chester.db import get_session
from chester.models import AnalysisJob, AuditEvent, Study
from chester.schemas import (
    AnalysisResultSchema,
    BulkDeleteRequest,
    BulkDeleteResponse,
    InstanceSchema,
    ReviewRequest,
    StudyDetailSchema,
    StudyListResponse,
    StudySchema,
)
from chester.security.access import AccessContext, visible_studies
from chester.storage import ObjectNotFound, delete_object

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/studies", tags=["studies"])

STATUS_VALUES = frozenset(
    {
        "received",
        "validating",
        "queued",
        "processing",
        "completed",
        "needs_review",
        "rejected",
        "error",
    }
)
RETRYABLE_STATUSES = frozenset({"error", "processing"})


def top_findings(study: Study, limit: int = 5) -> list[dict]:
    """Summarize the most recent completed result."""
    completed = [result for result in study.results if result.above_threshold_findings]
    if not completed:
        return []
    latest = max(completed, key=lambda result: result.created_at)
    return [
        {
            "pathology": pathology,
            "raw_score": (latest.raw_scores or {}).get(pathology),
            "normalized_score": (latest.op_normalized_scores or {}).get(pathology),
            "threshold": (latest.thresholds or {}).get(pathology),
            "above_threshold": (latest.above_threshold or {}).get(pathology, False),
        }
        for pathology in (latest.above_threshold_findings or [])[:limit]
    ]


def _to_summary(study: Study) -> StudySchema:
    schema = StudySchema.model_validate(study)
    schema.top_findings = top_findings(study)
    schema.owner_email = study.owner.email if study.owner else None
    return schema


def _to_detail(study: Study) -> StudyDetailSchema:
    detail = StudyDetailSchema.model_validate(study)
    detail.top_findings = top_findings(study)
    detail.owner_email = study.owner.email if study.owner else None
    detail.instances = [InstanceSchema.model_validate(item) for item in study.instances]
    detail.results = [AnalysisResultSchema.model_validate(item) for item in study.results]
    return detail


def _load(db: Session, access: AccessContext, study_id: uuid.UUID) -> Study:
    study = visible_studies(db.query(Study), access).filter(Study.id == study_id).first()
    if study is None:
        # Deliberately 404 rather than 403: whether a study exists in another
        # organization is not this caller's business.
        raise HTTPException(status_code=404, detail="Study not found")
    return study


@router.get("", response_model=StudyListResponse)
def list_studies(
    search: str | None = Query(None),
    study_status: str | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    access: AccessContext = Depends(require_page("worklist")),
    db: Session = Depends(get_session),
):
    """List studies this caller may see."""
    query = visible_studies(db.query(Study), access)

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            Study.description.ilike(pattern)
            | Study.patient_id.ilike(pattern)
            | Study.modality.ilike(pattern)
        )

    if study_status:
        if study_status not in STATUS_VALUES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {study_status}")
        if study_status == "needs_review" and not access.can_access_page("review"):
            raise HTTPException(status_code=403, detail="Review queue access denied")
        query = query.filter(Study.status == study_status)

    total = query.count()
    items = query.order_by(Study.created_at.desc()).offset(offset).limit(limit).all()

    counts = dict(
        visible_studies(db.query(Study.status, func.count(Study.id)), access)
        .group_by(Study.status)
        .all()
    )

    return StudyListResponse(
        items=[_to_summary(study) for study in items], total=total, counts=counts
    )


@router.get("/{study_id}", response_model=StudyDetailSchema)
def get_study(
    study_id: uuid.UUID,
    access: AccessContext = Depends(require_page("study-detail")),
    db: Session = Depends(get_session),
):
    return _to_detail(_load(db, access, study_id))


@router.post("/{study_id}/retry", response_model=StudyDetailSchema)
def retry_study(
    study_id: uuid.UUID,
    access: AccessContext = Depends(require_page("study-detail")),
    db: Session = Depends(get_session),
):
    """Queue a fresh analysis for a study that failed or is stuck.

    'processing' is retryable because a worker can die holding a lease; the
    previous implementation accepted only 'error', so such a study had no way out
    through the interface.
    """
    study = _load(db, access, study_id)
    if study.status not in RETRYABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Study in status '{study.status}' cannot be retried",
        )

    previous_status = study.status
    db.add(AnalysisJob(study_id=study.id, status="queued"))
    study.status = "queued"
    study.error_message = None
    db.add(
        AuditEvent(
            study_id=study.id,
            actor=access.email,
            event_type="retry",
            detail={"previous_status": previous_status},
        )
    )
    db.commit()
    db.refresh(study)
    return _to_detail(study)


@router.post("/{study_id}/review", response_model=StudyDetailSchema)
def review_study(
    study_id: uuid.UUID,
    body: ReviewRequest,
    access: AccessContext = Depends(require_page("review")),
    db: Session = Depends(get_session),
):
    """Approve a held study for analysis, or reject it."""
    if not access.may_review:
        raise HTTPException(status_code=403, detail="Este papel não pode revisar estudos.")

    study = _load(db, access, study_id)
    if study.status != "needs_review":
        raise HTTPException(
            status_code=400,
            detail=f"Study in status '{study.status}' cannot be reviewed",
        )

    db.add(
        AuditEvent(
            study_id=study.id,
            actor=access.email,
            event_type="review",
            detail={"decision": body.decision},
        )
    )
    if body.decision == "approve":
        study.status = "queued"
        db.add(AnalysisJob(study_id=study.id, status="queued"))
    else:
        study.status = "rejected"

    db.commit()
    db.refresh(study)
    return _to_detail(study)


def _purge_objects(db: Session, study: Study) -> None:
    """Remove the stored bytes for a study, before its rows go.

    Deleting the rows first would leave the pixel data behind with nothing left
    pointing at it -- unreachable through the interface but still on disk, which
    is the opposite of what a delete is for here. An object that is already gone
    is not a failure; anything else is raised so the caller can roll the
    transaction back and the study stays deletable rather than half-deleted.
    """
    keys = [instance.object_key for instance in study.instances if instance.object_key]
    # Derived from the study rather than an instance, so it has no row of its own.
    keys.append(f"thumbnails/{study.id}.png")
    for key in keys:
        try:
            delete_object(key, session=db)
        except ObjectNotFound:
            logger.info("Object %s was already gone while deleting study %s", key, study.id)


def _delete_study(db: Session, access: AccessContext, study: Study) -> None:
    """Delete one study, its instances, jobs and results, and its bytes.

    The study's own audit events go with it -- the schema cascades them -- so a
    single study-less event is written in their place. It records who deleted
    what and when, by study id rather than by anything identifying a patient,
    and it outlives the study it describes.
    """
    _purge_objects(db, study)
    db.add(
        AuditEvent(
            study_id=None,
            actor=access.email,
            event_type="study_deleted",
            detail={
                "study_id": str(study.id),
                "status": study.status,
                "instances": len(study.instances),
                "organization_id": str(study.organization_id),
            },
        )
    )
    db.delete(study)


@router.delete("/{study_id}", status_code=204)
def delete_study(
    study_id: uuid.UUID,
    access: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
) -> None:
    """Delete a study and everything it carries. Administrators only."""
    study = _load(db, access, study_id)
    _delete_study(db, access, study)
    db.commit()


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
def bulk_delete_studies(
    body: BulkDeleteRequest,
    access: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
) -> BulkDeleteResponse:
    """Delete several studies, reporting each one's outcome.

    Each study is committed on its own. A batch that failed as a unit would give
    the operator no way to tell which of a hundred selected studies actually
    went, and one unreadable object would strand the other ninety-nine.
    """
    deleted: list[uuid.UUID] = []
    not_found: list[uuid.UUID] = []
    errors: list[dict] = []

    for study_id in dict.fromkeys(body.ids):
        study = visible_studies(db.query(Study), access).filter(Study.id == study_id).first()
        if study is None:
            not_found.append(study_id)
            continue
        try:
            _delete_study(db, access, study)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception("Could not delete study %s", study_id)
            errors.append({"id": str(study_id), "error": str(exc)})
        else:
            deleted.append(study_id)

    return BulkDeleteResponse(deleted=deleted, not_found=not_found, errors=errors)
