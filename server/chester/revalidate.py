"""Look again at every film of studies refused as lateral.

A chest exam is usually sent as two films under one Study Instance UID, the
frontal and the lateral. When the lateral arrived first the study was refused,
and the frontal that followed only reopened it if it was confirmed as chest on
its own. A frontal whose metadata spoke Portuguese -- body part "TORAX", the
projection named only by its series, "PA" or "FRENTE" -- was not, so the whole
exam stayed refused as if it held nothing but the lateral.

Ingestion now reads those films correctly, but it decides a study once, as
each instance arrives. This re-reads every stored instance of the studies
still refused as lateral, through the same validation and the same reopening
rule ingestion uses: a frontal confirmed as chest queues the study for
analysis, and a film that is not lateral but cannot be confirmed hands the
study to a human. The lateral is never scored; the worker picks the frontal.

    python -m chester.revalidate --dry-run     # report, change nothing
    python -m chester.revalidate               # write

Safe to interrupt and safe to repeat: each study is committed on its own, and a
study reopened by a previous run is no longer refused, so it is not revisited.
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy.orm import Session

from chester.db import session_scope
from chester.imaging.dicom import extract_metadata, parse_dicom_bytes, render_frame
from chester.imaging.validation import CHEST, CODE_LATERAL_VIEW, UNCERTAIN, validate_study
from chester.ingestion import STATUS_REJECTED, reopen_with
from chester.instances import DICOM_CONTENT_TYPES, stored_instances
from chester.models import AuditEvent, Study
from chester.storage import ObjectNotFound, retrieve_bytes

logger = logging.getLogger("chester.revalidate")

ACTOR = "system:revalidate"


def _read(db: Session, instance):
    """The instance's metadata, pixels and validation, or None when unreadable."""
    content_type = instance.content_type or ""
    if not content_type.startswith(DICOM_CONTENT_TYPES):
        return None
    try:
        dataset = parse_dicom_bytes(retrieve_bytes(instance.object_key, session=db))
    except ObjectNotFound:
        logger.warning("Instance %s: %s is gone from storage", instance.id, instance.object_key)
        return None
    except Exception as exc:
        logger.warning("Instance %s: could not parse: %s", instance.id, exc)
        return None

    meta = extract_metadata(dataset)
    pixels = None
    try:
        pixels = render_frame(dataset, frame_index=0)
    except Exception as exc:
        logger.warning("Instance %s: could not render: %s", instance.id, exc)
    return meta, pixels, validate_study(meta, pixels)


def revalidate(db: Session, study: Study, *, dry_run: bool) -> str:
    """Re-read one study's films. Returns what happened, for the tally."""
    readings = [reading for i in stored_instances(db, study) if (reading := _read(db, i))]

    # The confirmed frontal wins; failing that, a film that is at least not lateral.
    for wanted in (CHEST, UNCERTAIN):
        for meta, pixels, validation in readings:
            if validation.state != wanted:
                continue
            if dry_run:
                return "would-queue" if wanted == CHEST else "would-review"
            reopen_with(db, study, validation, meta, pixels)
            db.add(
                AuditEvent(
                    study_id=study.id,
                    actor=ACTOR,
                    event_type="study_revalidated",
                    detail={
                        "validation_state": validation.state,
                        "validation_reason_code": validation.code,
                        "study_status": study.status,
                    },
                )
            )
            return "queued" if wanted == CHEST else "held-for-review"

    return "only-lateral"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    with session_scope() as db:
        ids = [
            row[0]
            for row in db.query(Study.id)
            .filter(
                Study.status == STATUS_REJECTED,
                Study.validation_reason_code == CODE_LATERAL_VIEW,
            )
            .order_by(Study.created_at.asc())
            .all()
        ]

    tally: dict[str, int] = {}
    for study_id in ids:
        with session_scope() as db:
            study = db.get(Study, study_id)
            if study is None:
                continue
            outcome = revalidate(db, study, dry_run=args.dry_run)
        tally[outcome] = tally.get(outcome, 0) + 1
        logger.info("Study %s: %s", study_id, outcome)

    logger.info(
        "%d studies: %s",
        len(ids),
        ", ".join(f"{count} {name}" for name, count in sorted(tally.items())) or "nothing to do",
    )
    if args.dry_run and (tally.get("would-queue") or tally.get("would-review")):
        logger.info("Dry run: re-run without --dry-run to write these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
