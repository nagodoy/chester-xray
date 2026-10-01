"""Recompute the topography of results recorded before the lungs were segmented.

The first version of chester.topography divided the whole scored square into
thirds, so evidence on the neck read as "upper" and on the abdomen as "lower".
Those entries are no longer shown (`topography.is_current`), which leaves every
study analysed before the fix with no topography at all. This writes the current
version onto them, from the instance bytes still in storage.

    python -m chester.retopography --dry-run     # report, change nothing
    python -m chester.retopography               # write

Only `AnalysisResult.topography` is written. The scores, the thresholds and the
findings stay what they were when the study ran -- the rule chester.thresholds
keeps for stored results -- because the topography is a reading of where the
evidence for those scores sits, not part of the scoring. Each change is recorded
in the audit trail.

Safe to interrupt and safe to repeat: each study is committed on its own, and a
result already carrying current entries is skipped.
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy.orm import Session

from chester import segmentation, topography
from chester.db import session_scope
from chester.models import AnalysisResult, AuditEvent, Study

logger = logging.getLogger("chester.retopography")


def _latest_result(db: Session, study: Study) -> AnalysisResult | None:
    return (
        db.query(AnalysisResult)
        .filter(AnalysisResult.study_id == study.id)
        .order_by(AnalysisResult.created_at.desc())
        .first()
    )


def _is_current(stored: dict | None) -> bool:
    """A result whose every entry is from the current version (or that has none to redo)."""
    return bool(stored) and all(topography.is_current(entry) for entry in stored.values())


def recompute(db: Session, study: Study, *, dry_run: bool) -> str:
    """Rewrite one study's latest topography. Returns what happened, for the tally."""
    from chester.worker import load_pixels

    result = _latest_result(db, study)
    if result is None:
        return "no-result"
    if _is_current(result.topography):
        return "already-current"

    try:
        pixels = load_pixels(db, study)
    except Exception as exc:
        logger.warning("Study %s: could not load its pixels: %s", study.id, exc)
        return "unreadable"

    try:
        located = topography.locate_all(pixels)
    except Exception as exc:
        logger.warning("Study %s: could not locate its findings: %s", study.id, exc)
        return "failed"
    if located is None:
        return "no-lungs"

    if dry_run:
        return "would-write"

    result.topography = located
    db.add(
        AuditEvent(
            study_id=study.id,
            actor="maintenance:retopography",
            event_type="topography_recomputed",
            detail={"result_id": str(result.id), "version": topography.VERSION},
        )
    )
    return "written"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument(
        "--limit", type=int, default=0, help="Stop after this many studies; 0 processes all"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    if not segmentation.available():
        logger.error("No segmentation model is loaded; nothing can be located.")
        return 1

    tally: dict[str, int] = {}
    processed = 0
    with session_scope() as db:
        ids = [row[0] for row in db.query(Study.id).order_by(Study.created_at.asc()).all()]

    for study_id in ids:
        if args.limit and processed >= args.limit:
            break
        with session_scope() as db:
            study = db.get(Study, study_id)
            if study is None:
                continue
            outcome = recompute(db, study, dry_run=args.dry_run)
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
