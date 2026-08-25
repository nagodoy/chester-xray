"""Study thumbnails."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from chester.api.deps import require_page
from chester.db import get_session
from chester.models import Study
from chester.security.access import AccessContext, visible_studies
from chester.storage import ObjectNotFound, retrieve_bytes

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/studies", tags=["studies"])


@router.get("/{study_id}/thumbnail")
def get_thumbnail(
    study_id: uuid.UUID,
    access: AccessContext = Depends(require_page("study-detail")),
    db: Session = Depends(get_session),
):
    """Return the study's thumbnail, subject to the same visibility rules."""
    study = visible_studies(db.query(Study), access).filter(Study.id == study_id).first()
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")

    try:
        data = retrieve_bytes(f"thumbnails/{study_id}.png", session=db)
    except ObjectNotFound:
        raise HTTPException(status_code=404, detail="Thumbnail not available") from None
    except Exception:
        logger.exception("Thumbnail retrieval failed for study %s", study_id)
        raise HTTPException(status_code=500, detail="Thumbnail retrieval failed") from None

    return Response(content=data, media_type="image/png")
