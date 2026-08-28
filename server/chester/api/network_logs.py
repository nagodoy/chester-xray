"""What this node received and what it sent.

Two reads over one table: exams that arrived and where they came from, and
reports that went out and whether the destination took them. Scoped to the
caller's organization, like every other worklist read.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from chester.api.deps import require_page
from chester.db import get_session
from chester.models import NetworkLog
from chester.network_log import DIRECTIONS, STATUSES
from chester.schemas import NetworkLogListResponse, NetworkLogSchema
from chester.security.access import AccessContext

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
