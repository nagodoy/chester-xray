"""Health check endpoint."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.database import get_db_session
from app.schemas import HealthResponse
from app.storage import active_backend
from app.worker import get_model_version

router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Public health check."""
    db_ok = False
    try:
        with get_db_session() as session:
            session.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        storage_backend=active_backend(),
        db_ok=db_ok,
        model_version=get_model_version(),
    )
