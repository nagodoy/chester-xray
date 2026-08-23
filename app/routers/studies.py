"""Studies CRUD endpoints."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.auth_deps import AccessContext, require_page
from app.database import get_db
from app.models import AnalysisJob, AuditEvent, Study
from app.schemas import ReviewRequest, StudyDetailSchema, StudyListResponse, StudySchema
from app.worker import start_worker

logger = logging.getLogger(__name__)
router = APIRouter()

STATUS_VALUES = [
    "received", "validating", "queued", "processing",
    "completed", "needs_review", "rejected", "error"
]


@router.get("/api/studies", response_model=StudyListResponse)
def list_studies(
    request: Request,
    search: Optional[str] = Query(None),
    study_status: Optional[str] = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    access: AccessContext = Depends(require_page("worklist")),
    session: Session = Depends(get_db),
):
    """List studies with optional search, status filter, pagination."""
    actor_id = access.actor_id
    q = session.query(Study).filter(Study.owner_id == actor_id)

    if search:
        like = f"%{search}%"
        q = q.filter(
            Study.description.ilike(like)
            | Study.patient_id.ilike(like)
            | Study.modality.ilike(like)
        )

    if study_status:
        if study_status not in STATUS_VALUES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {study_status}")
        if study_status == "needs_review" and not access.can_access_page("review"):
            raise HTTPException(status_code=403, detail="Review queue access denied")
        q = q.filter(Study.status == study_status)

    total = q.count()
    items = q.order_by(Study.created_at.desc()).offset(offset).limit(limit).all()

    # Compute counts per status
    counts_rows = (
        session.query(Study.status, func.count(Study.id))
        .filter(Study.owner_id == actor_id)
        .group_by(Study.status)
        .all()
    )
    counts = {row[0]: row[1] for row in counts_rows}

    return StudyListResponse(
        items=[_to_study_schema(s) for s in items],
        total=total,
        counts=counts,
    )


@router.get("/api/studies/{study_id}", response_model=StudyDetailSchema)
def get_study(
    study_id: str,
    request: Request,
    access: AccessContext = Depends(require_page("study-detail")),
    session: Session = Depends(get_db),
):
    """Get a single study with instances and results."""
    study = session.query(Study).filter_by(id=study_id, owner_id=access.actor_id).first()
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")
    return _to_study_detail_schema(study)


@router.post("/api/studies/{study_id}/retry", response_model=StudyDetailSchema)
def retry_study(
    study_id: str,
    request: Request,
    access: AccessContext = Depends(require_page("study-detail")),
    session: Session = Depends(get_db),
):
    """Retry a failed study by creating a new queued job."""
    actor_id = access.actor_id
    study = session.query(Study).filter_by(id=study_id, owner_id=actor_id).first()
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")

    if study.status not in ("error",):
        raise HTTPException(
            status_code=400,
            detail=f"Study in status '{study.status}' cannot be retried"
        )

    job = AnalysisJob(study_id=study.id, status="queued")
    session.add(job)
    study.status = "queued"
    study.error_message = None

    audit = AuditEvent(
        study_id=study.id,
        actor_id=actor_id,
        event_type="retry",
        detail={"previous_status": "error"},
    )
    session.add(audit)
    session.commit()

    start_worker()
    session.refresh(study)
    return _to_study_detail_schema(study)


@router.post("/api/studies/{study_id}/review", response_model=StudyDetailSchema)
def review_study(
    study_id: str,
    body: ReviewRequest,
    request: Request,
    access: AccessContext = Depends(require_page("review")),
    session: Session = Depends(get_db),
):
    """Review a study: approve (queues inference) or reject."""
    if access.role not in {"admin", "radiologist", "validador_radiologista"}:
        raise HTTPException(status_code=403, detail="Este papel não pode revisar estudos.")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'")

    actor_id = access.actor_id
    study = session.query(Study).filter_by(id=study_id, owner_id=actor_id).first()
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")

    if study.status not in ("needs_review",):
        raise HTTPException(
            status_code=400,
            detail=f"Study in status '{study.status}' cannot be reviewed"
        )

    audit = AuditEvent(
        study_id=study.id,
        actor_id=actor_id,
        event_type="review",
        detail={"decision": body.decision},
    )
    session.add(audit)

    if body.decision == "approve":
        study.status = "queued"
        job = AnalysisJob(study_id=study.id, status="queued")
        session.add(job)
    else:
        study.status = "rejected"

    session.commit()
    start_worker()
    session.refresh(study)
    return _to_study_detail_schema(study)


def _to_study_schema(study: Study) -> StudySchema:
    return StudySchema(
        id=study.id,
        patient_id=study.patient_id,
        patient_age=study.patient_age,
        patient_sex=study.patient_sex,
        study_date=study.study_date,
        modality=study.modality,
        view_position=study.view_position,
        description=study.description,
        source=study.source,
        status=study.status,
        validation_state=study.validation_state,
        validation_reason=study.validation_reason,
        thumbnail_url=study.thumbnail_url,
        created_at=study.created_at,
        updated_at=study.updated_at,
        top_findings=study.top_findings,
    )


def _to_study_detail_schema(study: Study) -> StudyDetailSchema:
    from app.schemas import AnalysisResultSchema, InstanceSchema

    instances = [
        InstanceSchema(
            id=inst.id,
            sop_instance_uid=inst.sop_instance_uid,
            sop_class_uid=inst.sop_class_uid,
            series_instance_uid=inst.series_instance_uid,
            transfer_syntax_uid=inst.transfer_syntax_uid,
            frame_count=inst.frame_count,
            rows=inst.rows,
            columns=inst.columns,
            bits_allocated=inst.bits_allocated,
            sha256=inst.sha256,
            file_size=inst.file_size,
            content_type=inst.content_type,
            audit_note=inst.audit_note,
            created_at=inst.created_at,
        )
        for inst in (study.instances or [])
    ]

    results = [
        AnalysisResultSchema(
            id=r.id,
            model_version=r.model_version,
            preprocessing_version=r.preprocessing_version,
            raw_scores=r.raw_scores,
            op_normalized_scores=r.op_normalized_scores,
            thresholds=r.thresholds,
            above_threshold=r.above_threshold,
            above_threshold_findings=r.above_threshold_findings,
            created_at=r.created_at,
        )
        for r in (study.results or [])
    ]

    return StudyDetailSchema(
        id=study.id,
        patient_id=study.patient_id,
        patient_age=study.patient_age,
        patient_sex=study.patient_sex,
        study_date=study.study_date,
        modality=study.modality,
        view_position=study.view_position,
        description=study.description,
        source=study.source,
        status=study.status,
        validation_state=study.validation_state,
        validation_reason=study.validation_reason,
        thumbnail_url=study.thumbnail_url,
        created_at=study.created_at,
        updated_at=study.updated_at,
        top_findings=study.top_findings,
        instances=instances,
        results=results,
        model_version=study.model_version,
        preprocessing_version=study.preprocessing_version,
        error_message=study.error_message,
        study_instance_uid=study.study_instance_uid,
    )
