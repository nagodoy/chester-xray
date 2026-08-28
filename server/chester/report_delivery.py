"""Send a study's TORAX IA report to a destination and record what happened.

Building the report and storing it on a node already live in
``chester.dicom_report`` and ``chester.dicom_send``. What was missing is the part
an operator can see: a delivery that was refused only ever reached the log of the
process that attempted it. Everything that sends goes through here, so the answer
-- delivered, or refused and why -- is in the database either way, and so is the
destination it was meant for.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from chester import destinations, network_log
from chester.destinations import Destination
from chester.dicom_report import DEFAULT_PRIVATE_CREATOR, build_for_study
from chester.dicom_send import SendFailed, SendNotConfigured
from chester.models import AuditEvent, NetworkLog, Study

logger = logging.getLogger(__name__)

CHANNEL = "c-store"


def deliver_report(
    db: Session,
    study: Study,
    destination: Destination,
    *,
    actor: str,
    private_creator: str = DEFAULT_PRIVATE_CREATOR,
    dataset=None,
) -> NetworkLog:
    """Build the report for one study and store it on one destination.

    ``dataset`` sends one that has already been built, so a caller delivering to
    several destinations renders the sheet once.

    Raises ``ValueError`` when the study has nothing to report on -- nothing was
    attempted, so nothing is logged. Every failed attempt is recorded and then
    raised as ``SendFailed`` or ``SendNotConfigured``.
    """
    from chester.dicom_send import send_dataset

    if dataset is None:
        dataset = build_for_study(db, study, private_creator=private_creator)
    reference = getattr(dataset, "SOPInstanceUID", None)

    try:
        send_dataset(
            dataset,
            host=destination.host,
            port=destination.port,
            ae_title=destination.ae_title,
            calling_ae_title=destination.calling_ae_title,
        )
    except (SendFailed, SendNotConfigured) as exc:
        logger.warning("Report for study %s was not delivered: %s", study.id, exc)
        _record(db, study, destination, actor, network_log.FAILURE, reference, str(exc))
        raise
    except Exception as exc:
        # A refusal is only one of the ways a store fails. A host that does not
        # resolve, a closed port or an association dropped mid-transfer raise
        # whatever the socket layer raises, and a delivery that never left is
        # precisely what this log exists to show -- so nothing gets to escape
        # unrecorded. The caller sees one failure type either way.
        message = f"{destination.label} could not be reached: {exc}"
        logger.warning("Report for study %s was not delivered: %s", study.id, message)
        _record(db, study, destination, actor, network_log.FAILURE, reference, message)
        raise SendFailed(message) from exc

    logger.info("Report for study %s delivered to %s", study.id, destination.label)
    return _record(db, study, destination, actor, network_log.SUCCESS, reference, None)


def deliver_to_active(db: Session, study: Study, *, actor: str) -> list[tuple[Destination, str]]:
    """Deliver to every active destination, reporting each failure.

    The report is built once and stored on each node in turn. A node that refuses
    does not stop the next one: the caller gets the failures that happened, and
    every attempt is in the network log.
    """
    targets = destinations.active(db, study.organization_id)
    if not targets:
        raise SendNotConfigured("Nenhuma conexão de envio ativa está configurada.")

    dataset = build_for_study(db, study)
    failures: list[tuple[Destination, str]] = []
    for destination in targets:
        try:
            deliver_report(db, study, destination, actor=actor, dataset=dataset)
        except (SendFailed, SendNotConfigured) as exc:
            failures.append((destination, str(exc)))
    return failures


def _record(
    db: Session,
    study: Study,
    destination: Destination,
    actor: str,
    status: str,
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
        peer=destination.label,
        actor=actor,
        reference=reference,
        message=message,
        detail={
            "destination": destination.name,
            "calling_ae_title": destination.calling_ae_title,
        },
    )
    db.add(
        AuditEvent(
            study_id=study.id,
            actor=actor,
            event_type="report_sent" if status == network_log.SUCCESS else "report_send_failed",
            detail={
                "destination": destination.label,
                "sop_instance_uid": reference,
                "error": message,
            },
        )
    )
    db.flush()
    return entry
