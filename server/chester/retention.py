"""Data retention for the network log.

The network log grows with every exchange and nothing ever removed a row, so a
node that has been running for a while accumulates records long past the point
anyone would read them. Retention answers that with a window: entries older than
it are deleted, on a routine the worker runs and on demand from the interface.

The window is per organization and deliberately short -- this table records that
an exchange happened, not the study itself, and a day of it is what an operator
actually looks at. A missing policy row means the default, so an organization
that never chose one is still swept.

Only the network log is subject to this. Studies, audit events and the
access-control trail are not: they answer questions about care and about who did
what, which outlive a day.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from chester.models import NetworkLog, Organization, RetentionPolicy, utcnow

logger = logging.getLogger(__name__)

# The windows the interface offers, shortest first.
WINDOW_HOURS: tuple[int, ...] = (12, 24, 36)

DEFAULT_HOURS = 24

assert DEFAULT_HOURS in WINDOW_HOURS


def normalize_hours(hours: int) -> int:
    """Return a window the routine accepts, or raise ValueError.

    Restricting to the offered set is what keeps the sweep predictable: an
    arbitrary number from an API caller could set retention to a minute and empty
    the log the operator is reading.
    """
    if hours not in WINDOW_HOURS:
        offered = ", ".join(str(value) for value in WINDOW_HOURS)
        raise ValueError(f"Retention window must be one of {offered} hours, not {hours}")
    return hours


def cutoff(hours: int, *, now: datetime | None = None) -> datetime:
    """The instant before which entries have expired."""
    return (now or utcnow()) - timedelta(hours=hours)


def stored_policy(db: Session, organization_id: uuid.UUID) -> RetentionPolicy | None:
    """This organization's policy row, or None if it has never had one."""
    return (
        db.query(RetentionPolicy).filter(RetentionPolicy.organization_id == organization_id).first()
    )


def current(db: Session, organization_id: uuid.UUID) -> tuple[int, datetime | None]:
    """The window in force and when it was last applied, without writing anything.

    Reads must not create rows: an organization that has only ever looked at the
    page should leave no trace of having done so.
    """
    policy = stored_policy(db, organization_id)
    if policy is None:
        return DEFAULT_HOURS, None
    return policy.network_log_hours, policy.last_swept_at


def policy_for(db: Session, organization_id: uuid.UUID) -> RetentionPolicy:
    """This organization's policy, creating it with the default if it has none."""
    policy = stored_policy(db, organization_id)
    if policy is None:
        policy = RetentionPolicy(organization_id=organization_id, network_log_hours=DEFAULT_HOURS)
        db.add(policy)
        db.flush()
    return policy


def set_window(db: Session, organization_id: uuid.UUID, hours: int) -> RetentionPolicy:
    """Choose how long this organization keeps its network log."""
    policy = policy_for(db, organization_id)
    policy.network_log_hours = normalize_hours(hours)
    db.flush()
    return policy


def expired(db: Session, organization_id: uuid.UUID, hours: int, *, now: datetime | None = None):
    """The query behind both the count and the delete, so they cannot disagree."""
    return db.query(NetworkLog).filter(
        NetworkLog.organization_id == organization_id,
        NetworkLog.created_at < cutoff(hours, now=now),
    )


def count_expired(
    db: Session, organization_id: uuid.UUID, hours: int, *, now: datetime | None = None
) -> int:
    """How many entries the next sweep would remove."""
    return expired(db, organization_id, hours, now=now).count()


def purge(
    db: Session,
    organization_id: uuid.UUID,
    hours: int | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Delete this organization's expired entries and report how many went.

    ``hours`` defaults to the organization's own window. The caller owns the
    transaction, as it does for every other write in this codebase.
    """
    policy = policy_for(db, organization_id)
    window = normalize_hours(policy.network_log_hours if hours is None else hours)

    removed = expired(db, organization_id, window, now=now).delete(synchronize_session=False)
    policy.last_swept_at = now or utcnow()
    db.flush()
    return int(removed)


def sweep(db: Session, *, now: datetime | None = None) -> int:
    """Apply every organization's window. Returns the total entries removed.

    Every organization is swept, not only those with a policy row: retention that
    applied to nobody until someone opened a settings panel would be a surprise
    the first time a disk filled.
    """
    total = 0
    for (organization_id,) in db.query(Organization.id).all():
        removed = purge(db, organization_id, now=now)
        if removed:
            logger.info(
                "Retention removed %d network log entr%s for organization %s",
                removed,
                "y" if removed == 1 else "ies",
                organization_id,
            )
        total += removed
    return total
