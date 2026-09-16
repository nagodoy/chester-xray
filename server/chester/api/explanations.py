"""Where a finding's score came from, as a picture.

One endpoint, deliberately cheap to reason about: it recomputes the map from the
study's stored pixels rather than keeping a rendered copy anywhere. A derived
image that lived in object storage would need its own line in `_purge_objects`,
its own retention answer and its own migration for studies that predate it, all
to avoid 85 milliseconds of work. The small cache below covers the case that
actually repeats -- the worklist asking for the same square while it polls.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from chester import inference, saliency
from chester.api.deps import require_page
from chester.db import get_session
from chester.models import Study
from chester.security.access import AccessContext, visible_studies

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/studies", tags=["studies"])

# How many rendered squares to keep. Each is a PNG of a few hundred kilobytes, so
# this is a few megabytes at worst, and it is a cache: losing it on restart costs
# one forward pass per entry and nothing else.
CACHE_ENTRIES = 48

_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_cache_lock = threading.Lock()


def _cached(key: tuple[str, str]) -> bytes | None:
    with _cache_lock:
        data = _cache.get(key)
        if data is not None:
            _cache.move_to_end(key)
        return data


def _remember(key: tuple[str, str], data: bytes) -> None:
    with _cache_lock:
        _cache[key] = data
        _cache.move_to_end(key)
        while len(_cache) > CACHE_ENTRIES:
            _cache.popitem(last=False)


def reset_cache() -> None:
    """Drop everything rendered so far. For tests."""
    with _cache_lock:
        _cache.clear()


@router.get("/{study_id}/explain/{pathology}")
def explain_finding(
    study_id: uuid.UUID,
    pathology: str,
    access: AccessContext = Depends(require_page("study-detail")),
    db: Session = Depends(get_session),
):
    """The square the model scored, tinted where this finding's evidence is.

    Subject to the same visibility rules as the study itself: an explanation is a
    rendering of the pixels, so seeing one must require being allowed to see them.
    """
    if not saliency.explainable(pathology):
        # 404 rather than 400: a suppressed output is not an explanation this
        # deployment has, and the interface should not offer it either.
        raise HTTPException(status_code=404, detail="Esta saída não é explicada.")

    study = visible_studies(db.query(Study), access).filter(Study.id == study_id).first()
    if study is None:
        raise HTTPException(status_code=404, detail="Study not found")

    key = (str(study_id), pathology)
    data = _cached(key)

    if data is None:
        if not inference.activation_available():
            raise HTTPException(
                status_code=503,
                detail="O modelo carregado não expõe as ativações necessárias.",
            )
        from chester.worker import load_pixels

        try:
            pixels = load_pixels(db, study)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception:
            logger.exception("Could not load pixels to explain study %s", study_id)
            raise HTTPException(status_code=500, detail="Explanation failed") from None

        try:
            data = saliency.overlay_png(pixels, pathology)
        except Exception:
            logger.exception("Could not explain %s for study %s", pathology, study_id)
            raise HTTPException(status_code=500, detail="Explanation failed") from None

        _remember(key, data)

    return Response(
        content=data,
        media_type="image/png",
        # Private, because it is a picture of a patient's chest. Short, because the
        # map is a function of the model and the pixels, and a model change should
        # not be waited out.
        headers={"Cache-Control": "private, max-age=300"},
    )
