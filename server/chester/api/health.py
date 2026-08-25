"""Health check."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from sqlalchemy import text

from chester.db import session_scope
from chester.schemas import HealthResponse
from chester.storage import active_backend

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("", response_model=HealthResponse)
def health() -> HealthResponse:
    """Public liveness probe."""
    db_ok = False
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        logger.exception("Health check database probe failed")

    from chester.inference import model_version

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        storage_backend=active_backend(),
        db_ok=db_ok,
        model_version=model_version(),
    )
