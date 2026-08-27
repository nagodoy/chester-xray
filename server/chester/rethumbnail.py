"""Regenerate study thumbnails from the instances already stored.

Thumbnails were built by resizing every rendered frame onto a square, which
stretched any radiograph that was not square -- which is nearly all of them.
The generator was fixed, but a thumbnail is written once at ingestion and
never revisited, so every study filed before that fix still carries the
distorted picture. Re-running analysis does not help: the thumbnail is made
during ingestion, not by the worker.

This re-renders each study's thumbnail from the instance bytes still in
storage, through the same path ingestion uses, and writes it back over the
old key.

    python -m chester.rethumbnail --dry-run     # report, change nothing
    python -m chester.rethumbnail               # write

Safe to interrupt and safe to repeat. Each study is committed on its own, so
stopping half way leaves the studies already done in their new state and the
rest untouched; and a study whose thumbnail already matches what the current
generator produces is skipped, so a second run is close to a no-op.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys

from sqlalchemy.orm import Session

from chester.db import session_scope
from chester.imaging.dicom import generate_thumbnail, parse_dicom_bytes, render_frame
from chester.models import Instance, Study
from chester.storage import ObjectNotFound, retrieve_bytes, store_bytes

logger = logging.getLogger("chester.rethumbnail")

DICOM_CONTENT_TYPE = "application/dicom"


def _pixels_from(data: bytes, content_type: str | None):
    """Render a frame the way ingestion does, by how the bytes were filed.

    Two paths, because two kinds of file are accepted: a DICOM goes through
    the modality LUT and VOI windowing that render_frame applies, and a plain
    PNG or JPEG is simply read as grayscale.
    """
    import numpy as np
    from PIL import Image

    if (content_type or "").startswith(DICOM_CONTENT_TYPE):
        return render_frame(parse_dicom_bytes(data), frame_index=0)
    with Image.open(io.BytesIO(data)) as image:
        return np.array(image.convert("L"), dtype=np.float32)


def _source_instance(db: Session, study: Study) -> Instance | None:
    """The instance ingestion would have drawn the thumbnail from.

    The oldest one carrying bytes: ingestion writes the thumbnail from the
    first instance to arrive and leaves it alone thereafter, so picking the
    oldest reproduces that choice rather than quietly changing which image
    represents a multi-instance study.
    """
    return (
        db.query(Instance)
        .filter(Instance.study_id == study.id, Instance.object_key.isnot(None))
        .order_by(Instance.created_at.asc())
        .first()
    )


def regenerate(db: Session, study: Study, *, dry_run: bool) -> str:
    """Rebuild one study's thumbnail. Returns what happened, for the tally."""
    instance = _source_instance(db, study)
    if instance is None or not instance.object_key:
        return "no-source"

    try:
        data = retrieve_bytes(instance.object_key, session=db)
    except ObjectNotFound:
        logger.warning("Study %s: %s is gone from storage", study.id, instance.object_key)
        return "missing-bytes"

    try:
        thumbnail = generate_thumbnail(_pixels_from(data, instance.content_type))
    except Exception as exc:
        logger.warning("Study %s: could not render %s: %s", study.id, instance.object_key, exc)
        return "unreadable"

    key = f"thumbnails/{study.id}.png"
    try:
        if retrieve_bytes(key, session=db) == thumbnail:
            return "already-current"
    except ObjectNotFound:
        # No thumbnail at all: one whose generation failed at ingestion. Writing
        # one now is a repair, not a rewrite.
        pass

    if dry_run:
        return "would-write"

    store_bytes(key, thumbnail, "image/png", session=db)
    # A study that never got one is only reachable once this is set.
    study.thumbnail_url = f"/api/studies/{study.id}/thumbnail"
    return "written"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after this many studies; 0 processes all of them",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    tally: dict[str, int] = {}
    processed = 0
    with session_scope() as db:
        ids = [row[0] for row in db.query(Study.id).order_by(Study.created_at.asc()).all()]

    for study_id in ids:
        if args.limit and processed >= args.limit:
            break
        # A session per study, so an interrupted run leaves committed work
        # behind rather than losing all of it.
        with session_scope() as db:
            study = db.get(Study, study_id)
            if study is None:
                continue
            outcome = regenerate(db, study, dry_run=args.dry_run)
        tally[outcome] = tally.get(outcome, 0) + 1
        processed += 1
        if processed % 50 == 0:
            logger.info("%d studies processed", processed)

    logger.info(
        "%d studies: %s",
        processed,
        ", ".join(f"{count} {name}" for name, count in sorted(tally.items())) or "nothing to do",
    )
    if args.dry_run and tally.get("would-write"):
        logger.info("Dry run: re-run without --dry-run to write these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
