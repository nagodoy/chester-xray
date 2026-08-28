"""Recording what this node exchanged with another system.

Ingestion and delivery both write here so the interface can answer two questions
without reading logs off a container: what arrived and from where, and what was
sent and whether it landed. The helper only adds and flushes -- the caller owns
the transaction, as it does for audit events.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from chester.models import NetworkLog

RECEIVED = "received"
SENT = "sent"

SUCCESS = "success"
FAILURE = "failure"
DUPLICATE = "duplicate"

DIRECTIONS: frozenset[str] = frozenset({RECEIVED, SENT})
STATUSES: frozenset[str] = frozenset({SUCCESS, FAILURE, DUPLICATE})


def record(
    db: Session,
    *,
    organization_id: uuid.UUID,
    direction: str,
    channel: str,
    status: str,
    study_id: uuid.UUID | None = None,
    peer: str | None = None,
    actor: str | None = None,
    reference: str | None = None,
    message: str | None = None,
    detail: dict | None = None,
) -> NetworkLog:
    """Write one exchange. Returns the row, still uncommitted."""
    entry = NetworkLog(
        organization_id=organization_id,
        study_id=study_id,
        direction=direction,
        channel=channel,
        status=status,
        peer=peer,
        actor=actor,
        reference=reference,
        message=message,
        detail=detail,
    )
    db.add(entry)
    db.flush()
    return entry
