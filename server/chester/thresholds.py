"""What operating point an organization actually runs each output against.

`chester.inference.OPERATING_POINTS` holds the defaults, and this decides when a
deployment departs from them. The two are deliberately different kinds of thing:
the tuple is a clinical decision that goes through code review with the evidence
written beside it, and an override is a local adjustment an administrator makes
without one.

That difference is why the bounds below exist. A threshold is the whole of the
decision an output makes -- setting it to 0 reports every finding on every exam
and setting it to 1 reports none, and both are one typo away in a text field.
`MIN_FACTOR` and `MAX_FACTOR` keep an override within a factor of the point the
model was fitted at, so a mistake is a bad threshold rather than a silent switch
to reporting everything or nothing. They are not a claim that anything inside the
range is safe: nothing here measures that. tools/calibrate_thresholds.py does,
over exams a radiologist has read, and an override made without it is a guess.

Overrides never touch a stored result. `AnalysisResult.thresholds` records the
points that were in force when the study ran, and every report and DICOM tag is
built from that record, so changing a threshold today cannot alter what a
radiologist was shown last week.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from chester.inference import OPERATING_POINTS, PATHOLOGIES, REPORTED_PATHOLOGIES
from chester.models import AuditEvent, ThresholdOverride

logger = logging.getLogger(__name__)

# The built-in point for every output this deployment reports.
DEFAULTS: dict[str, float] = {
    name: float(OPERATING_POINTS[index])
    for index, name in enumerate(PATHOLOGIES)
    if name in REPORTED_PATHOLOGIES
}

# How far an override may sit from the default, as a multiple of it.
MIN_FACTOR = 0.25
MAX_FACTOR = 4.0

# What the interface calls this change in the audit trail.
AUDIT_EVENT = "threshold_override_changed"


def is_adjustable(pathology: str) -> bool:
    """Whether this output is one an organization may move.

    Suppressed outputs are not: they are never scored into a result, so a
    threshold for one would be a number with nothing to apply it to.
    """
    return pathology in DEFAULTS


def bounds(pathology: str) -> tuple[float, float]:
    """The range an override may take for this output."""
    default = DEFAULTS[pathology]
    return default * MIN_FACTOR, default * MAX_FACTOR


def normalize(pathology: str, value: float) -> float:
    """Return a threshold this deployment accepts, or raise ValueError."""
    if not is_adjustable(pathology):
        raise ValueError(f"{pathology} is not an output this deployment reports.")
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        raise ValueError("Threshold must be a number.") from None
    if threshold != threshold or threshold in (float("inf"), float("-inf")):
        raise ValueError("Threshold must be a finite number.")
    low, high = bounds(pathology)
    if not low <= threshold <= high:
        raise ValueError(
            f"Threshold for {pathology} must be between {low:.6g} and {high:.6g}, "
            f"which is {MIN_FACTOR:g}x to {MAX_FACTOR:g}x its default of "
            f"{DEFAULTS[pathology]:.6g}."
        )
    return threshold


def stored(db: Session, organization_id: uuid.UUID) -> dict[str, ThresholdOverride]:
    """Every override this organization has, keyed by pathology.

    Rows for outputs this deployment no longer reports are left out rather than
    deleted: suppression can be revisited, and dropping the value an operator
    chose would lose it silently the first time an output was withdrawn.
    """
    rows = (
        db.query(ThresholdOverride)
        .filter(ThresholdOverride.organization_id == organization_id)
        .all()
    )
    return {row.pathology: row for row in rows if is_adjustable(row.pathology)}


def in_force(db: Session, organization_id: uuid.UUID) -> dict[str, float]:
    """The point each reported output is judged against for this organization.

    Defaults with the organization's overrides applied on top. This is what the
    worker hands to `chester.inference.infer`.
    """
    effective = dict(DEFAULTS)
    for pathology, row in stored(db, organization_id).items():
        effective[pathology] = float(row.threshold)
    return effective


def set_override(
    db: Session,
    organization_id: uuid.UUID,
    pathology: str,
    value: float,
    *,
    actor: str,
) -> ThresholdOverride:
    """Move one output's operating point, and record who moved it.

    Does not commit: the caller owns the transaction, so the audit row and the
    value land together or not at all.
    """
    threshold = normalize(pathology, value)
    row = (
        db.query(ThresholdOverride)
        .filter(
            ThresholdOverride.organization_id == organization_id,
            ThresholdOverride.pathology == pathology,
        )
        .first()
    )
    previous = float(row.threshold) if row is not None else None

    if row is None:
        row = ThresholdOverride(
            organization_id=organization_id,
            pathology=pathology,
            threshold=threshold,
            updated_by=actor,
        )
        db.add(row)
        # Flushed here so a second call in the same session finds this row
        # instead of adding another and tripping the unique constraint. The
        # session this runs in does not autoflush.
        db.flush()
    else:
        row.threshold = threshold
        row.updated_by = actor

    _record(
        db,
        actor=actor,
        pathology=pathology,
        previous=previous,
        current=threshold,
    )
    return row


def clear_override(
    db: Session,
    organization_id: uuid.UUID,
    pathology: str,
    *,
    actor: str,
) -> bool:
    """Return this output to its built-in default. True if a row was removed."""
    if not is_adjustable(pathology):
        raise ValueError(f"{pathology} is not an output this deployment reports.")
    row = (
        db.query(ThresholdOverride)
        .filter(
            ThresholdOverride.organization_id == organization_id,
            ThresholdOverride.pathology == pathology,
        )
        .first()
    )
    if row is None:
        return False
    _record(
        db,
        actor=actor,
        pathology=pathology,
        previous=float(row.threshold),
        current=DEFAULTS[pathology],
        reset=True,
    )
    db.delete(row)
    return True


def _record(
    db: Session,
    *,
    actor: str,
    pathology: str,
    previous: float | None,
    current: float,
    reset: bool = False,
) -> None:
    """Append the change to the trail.

    `AuditEvent` rather than a table of its own: its `study_id` is nullable for
    exactly this, an event about the deployment rather than about one exam, and
    a new table would need a schema run before the first override could be saved.
    """
    db.add(
        AuditEvent(
            study_id=None,
            actor=actor,
            event_type=AUDIT_EVENT,
            detail={
                "pathology": pathology,
                "previous": previous,
                "current": current,
                "default": DEFAULTS[pathology],
                "reset_to_default": reset,
            },
        )
    )
    logger.info(
        "Threshold for %s set to %r by %s (was %r, default %r)",
        pathology,
        current,
        actor,
        previous,
        DEFAULTS[pathology],
    )
