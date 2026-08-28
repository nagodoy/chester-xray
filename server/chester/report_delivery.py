"""Send a study's TORAX IA report and record what happened.

Building the report and storing it on a viewer already live in
``chester.dicom_report`` and ``chester.dicom_send``. What was missing is the part
an operator can see: a delivery that was refused only ever reached the log of the
process that attempted it. Everything that sends goes through here so the answer
-- delivered, or refused and why -- is in the database either way.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from chester import network_log
from chester.config import settings
from chester.dicom_report import DEFAULT_PRIVATE_CREATOR, build_for_study
from chester.dicom_send import SendFailed, SendNotConfigured
from chester.models import AuditEvent, NetworkLog, Study

logger = logging.getLogger(__name__)

CHANNEL = "c-store"


def destination_label() -> str:
    """The destination as an operator reads it: AE title at host and port."""
    host = settings.dicom_send_host
    if not host:
        return ""
    return f"{settings.dicom_send_ae_title}@{host}:{settings.dicom_send_port}"


def deliver_report(
    db: Session,
    study: Study,
    *,
    actor: str,
    private_creator: str = DEFAULT_PRIVATE_CREATOR,
    dataset=None,
) -> NetworkLog:
    """Build the report for one study and store it on the configured node.

    ``dataset`` sends one that has already been built, so a caller that also
    writes the instance to disk does not render the sheet twice.

    Raises ``ValueError`` when the study has nothing to report on -- nothing was
    attempted, so nothing is logged. Every failed attempt is recorded and then
    raised as ``SendFailed`` or ``SendNotConfigured``.
    """
    from chester.dicom_send import send_dataset

    if dataset is None:
        dataset = build_for_study(db, study, private_creator=private_creator)
    reference = getattr(dataset, "SOPInstanceUID", None)
    peer = destination_label() or None

    try:
        send_dataset(dataset)
    except (SendFailed, SendNotConfigured) as exc:
        logger.warning("Report for study %s was not delivered: %s", study.id, exc)
        _record(db, study, actor, network_log.FAILURE, peer, reference, str(exc))
        raise
    except Exception as exc:
        # A refusal is only one of the ways a store fails. A host that does not
        # resolve, a closed port or an association dropped mid-transfer raise
        # whatever the socket layer raises, and a delivery that never left is
        # precisely what this log exists to show -- so nothing gets to escape
        # unrecorded. The caller sees one failure type either way.
        message = f"{peer or 'the destination'} could not be reached: {exc}"
        logger.warning("Report for study %s was not delivered: %s", study.id, message)
        _record(db, study, actor, network_log.FAILURE, peer, reference, message)
        raise SendFailed(message) from exc

    logger.info("Report for study %s delivered to %s", study.id, peer)
    return _record(db, study, actor, network_log.SUCCESS, peer, reference, None)


def _record(
    db: Session,
    study: Study,
    actor: str,
    status: str,
    peer: str | None,
    reference: str | None,
    message: str | None,
) -> NetworkLog:
    entry = network_log.record(
        db,
        organization_id=study.organization_id,
        direction=network_log.SENT,
        channel=CHANNEL,
        status=status,
        study_id=study.id,
        peer=peer,
        actor=actor,
        reference=reference,
        message=message,
        detail={"calling_ae_title": settings.dicom_send_calling_ae_title},
    )
    db.add(
        AuditEvent(
            study_id=study.id,
            actor=actor,
            event_type="report_sent" if status == network_log.SUCCESS else "report_send_failed",
            detail={"destination": peer, "sop_instance_uid": reference, "error": message},
        )
    )
    db.flush()
    return entry
