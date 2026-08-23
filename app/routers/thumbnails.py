"""Thumbnail endpoint."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.auth_deps import AccessContext, require_page
from app.database import get_db
from app.models import Study
from app.storage import retrieve_bytes

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/studies/{study_id}/thumbnail")
def get_thumbnail(
    study_id: str,
    request: Request,
    access: AccessContext = Depends(require_page("study-detail")),
    session: Session = Depends(get_db),
):
    """Return the thumbnail PNG for a study."""
    study = session.query(Study).filter_by(id=study_id, owner_id=access.actor_id).first()
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")

    thumb_key = f"thumbnails/{study_id}.png"

    try:
        data = retrieve_bytes(thumb_key, session=session)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Thumbnail not available")
    except Exception as exc:
        logger.error("Thumbnail retrieval error: %s", exc)
        raise HTTPException(status_code=500, detail="Thumbnail retrieval failed")

    return Response(content=data, media_type="image/png")
