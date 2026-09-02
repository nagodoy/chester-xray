"""What this node received and what it sent.

Two reads over one table: exams that arrived and where they came from, and
reports that went out and whether the destination took them. Scoped to the
caller's organization, like every other worklist read.

The same table is the one retention applies to, so the window an administrator
chooses and the purge they can run by hand live here too. The routine that
enforces the window without being asked runs in the worker.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from chester import retention
from chester.api.deps import require_admin, require_page
from chester.db import get_session
from chester.models import AccessControlAuditLog, NetworkLog
from chester.network_log import DIRECTIONS, STATUSES
from chester.schemas import (
    NetworkLogListResponse,
    NetworkLogSchema,
    RetentionPurgeResponse,
    RetentionSchema,
    RetentionUpdate,
)
from chester.security.access import AccessContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/network-logs", tags=["network-logs"])


@router.get("", response_model=NetworkLogListResponse)
def list_network_logs(
    direction: str = Query(..., description="received or sent"),
    log_status: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    access: AccessContext = Depends(require_page("network-logs")),
    db: Session = Depends(get_session),
):
    if direction not in DIRECTIONS:
        raise HTTPException(status_code=400, detail=f"Invalid direction: {direction}")
    if log_status is not None and log_status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {log_status}")

    query = db.query(NetworkLog).filter(
        NetworkLog.organization_id == access.organization_id,
        NetworkLog.direction == direction,
    )
    if log_status is not None:
        query = query.filter(NetworkLog.status == log_status)

    total = query.count()
    rows = query.order_by(desc(NetworkLog.created_at)).offset(offset).limit(limit).all()
    return NetworkLogListResponse(
        items=[NetworkLogSchema.model_validate(row) for row in rows], total=total
    )


def _retention_state(db: Session, access: AccessContext) -> RetentionSchema:
    hours, last_swept_at = retention.current(db, access.organization_id)
    return RetentionSchema(
        hours=hours,
        options=list(retention.WINDOW_HOURS),
        expiring=retention.count_expired(db, access.organization_id, hours),
        last_swept_at=last_swept_at,
    )


@router.get("/retention", response_model=RetentionSchema)
def read_retention(
    access: AccessContext = Depends(require_page("network-logs")),
    db: Session = Depends(get_session),
):
    """The current window and how many entries it has already expired.

    Readable by anyone who can see the page: the count is what makes the window
    mean something, and hiding it from a technician would leave them wondering
    where yesterday's rows went. Changing it takes an administrator.
    """
    return _retention_state(db, access)


@router.put("/retention", response_model=RetentionSchema)
def update_retention(
    body: RetentionUpdate,
    access: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    try:
        retention.set_window(db, access.organization_id, body.hours)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(
        AccessControlAuditLog(
            actor_email=access.email,
            actor_role=access.role,
            action="retention_window_set",
            target_type="network_log",
            target_key=str(access.organization_id),
            details={"hours": body.hours},
        )
    )
    state = _retention_state(db, access)
    db.commit()
    return state


@router.post("/retention/purge", response_model=RetentionPurgeResponse)
def purge_retention(
    access: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """Apply the window now instead of waiting for the routine's next pass."""
    deleted = retention.purge(db, access.organization_id)
    db.add(
        AccessControlAuditLog(
            actor_email=access.email,
            actor_role=access.role,
            action="retention_purge",
            target_type="network_log",
            target_key=str(access.organization_id),
            details={"deleted": deleted},
        )
    )
    state = _retention_state(db, access)
    db.commit()
    logger.info("%s purged %d network log entr(ies) by hand", access.email, deleted)
    return RetentionPurgeResponse(deleted=deleted, retention=state)
