"""Background analysis worker.

Runs as its own process (``python -m chester.worker``), not as a thread inside the
web application. Previously a daemon thread started inside uvicorn, which meant
the model was loaded in every web process, inference competed with request
handling, and the queue could not be scaled independently of the API.

Jobs are claimed with SELECT ... FOR UPDATE SKIP LOCKED so several workers can run
against one database without processing the same job twice.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import uuid
from datetime import timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from chester.config import settings
from chester.db import session_scope
from chester.models import (
    AnalysisJob,
    AnalysisResult,
    AuditEvent,
    DeliveryJob,
    Instance,
    Study,
    utcnow,
)

logger = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"


def _lease_expiry():
    return utcnow() + timedelta(minutes=settings.job_lease_minutes)


def claim_job(db: Session, job_id: uuid.UUID | None = None) -> uuid.UUID | None:
    """Take ownership of one queued job, or return None if there is nothing to do.

    SKIP LOCKED is what makes concurrent workers safe: a row another transaction
    already holds is passed over instead of blocking.
    """
    query = db.query(AnalysisJob).filter(AnalysisJob.status == STATUS_QUEUED)
    if job_id is not None:
        query = query.filter(AnalysisJob.id == job_id)

    job = query.order_by(AnalysisJob.created_at.asc()).with_for_update(skip_locked=True).first()
    if job is None:
        return None

    job.status = STATUS_PROCESSING
    job.started_at = utcnow()
    job.attempt = (job.attempt or 0) + 1
    job.lease_owner = WORKER_ID
    job.lease_expires_at = _lease_expiry()

    study = db.get(Study, job.study_id)
    if study is not None:
        study.status = STATUS_PROCESSING
    db.flush()
    return job.id


def load_pixels(db: Session, study: Study):
    """Decode the study's first instance into a 0..255 grayscale raster.

    Ordered by creation so a multi-instance study always analyses the same image.
    The previous implementation took an arbitrary row, so which image was scored
    was not determined.
    """
    import io

    import numpy as np
    from PIL import Image

    from chester.imaging.dicom import render_frame_for_model
    from chester.storage import retrieve_bytes

    instance = (
        db.query(Instance)
        .filter(Instance.study_id == study.id)
        .order_by(Instance.created_at.asc(), Instance.id.asc())
        .first()
    )
    if instance is None or not instance.object_key:
        raise ValueError("study has no stored instance to analyse")

    raw = retrieve_bytes(instance.object_key, session=db)
    content_type = instance.content_type or ""

    if "dicom" in content_type or content_type == "application/octet-stream":
        import pydicom
        from pydicom.filebase import DicomBytesIO

        dataset = pydicom.dcmread(DicomBytesIO(raw), force=True)
        return render_frame_for_model(dataset, frame_index=0)

    with Image.open(io.BytesIO(raw)) as image:
        return np.array(image.convert("RGB"), dtype=np.float32).mean(axis=2)


def process_job(job_id: uuid.UUID) -> None:
    """Run one claimed job to completion, recording either a result or the error."""
    from chester.inference import infer

    error: str | None = None
    outcome: dict | None = None

    with session_scope() as db:
        job = db.get(AnalysisJob, job_id)
        if job is None or job.lease_owner != WORKER_ID:
            logger.warning("Job %s is no longer held by this worker", job_id)
            return
        study = db.get(Study, job.study_id)
        if study is None:
            _fail(db, job, None, "Study not found")
            return
        try:
            pixels = load_pixels(db, study)
        except Exception as exc:
            logger.exception("Could not load pixels for job %s", job_id)
            _fail(db, job, study, str(exc))
            return

    # Inference happens outside a transaction so a slow model does not hold a
    # database connection or row locks for its duration.
    try:
        outcome = infer(pixels)
    except Exception as exc:
        logger.exception("Inference failed for job %s", job_id)
        error = str(exc)

    with session_scope() as db:
        job = db.get(AnalysisJob, job_id)
        if job is None or job.lease_owner != WORKER_ID:
            logger.warning("Discarding result for job %s after losing its lease", job_id)
            return
        study = db.get(Study, job.study_id)

        if error is not None or outcome is None:
            _fail(db, job, study, error or "Unknown inference error")
            return

        existing = db.query(AnalysisResult).filter_by(job_id=job.id).first()
        if existing is not None:
            logger.warning("Job %s already has a result; not inserting another", job_id)
            _complete(db, job, study, existing.created_at)
            return

        db.add(
            AnalysisResult(
                study_id=job.study_id,
                job_id=job.id,
                model_version=outcome["model_version"],
                preprocessing_version=outcome["preprocessing_version"],
                raw_scores=outcome["raw_scores"],
                op_normalized_scores=outcome["op_normalized_scores"],
                thresholds=outcome["thresholds"],
                above_threshold=outcome["above_threshold"],
                above_threshold_findings=outcome["above_threshold_findings"],
            )
        )
        if study is not None:
            study.model_version = outcome["model_version"]
            study.preprocessing_version = outcome["preprocessing_version"]
            study.error_message = None
        db.add(
            AuditEvent(
                study_id=job.study_id,
                actor=f"worker:{WORKER_ID}",
                event_type="analysis_completed",
                detail={"findings": outcome["above_threshold_findings"]},
            )
        )
        _complete(db, job, study, utcnow())
        if study is not None:
            queue_deliveries(db, study)


def _complete(db: Session, job: AnalysisJob, study: Study | None, completed_at) -> None:
    job.status = STATUS_COMPLETED
    job.completed_at = completed_at
    job.lease_owner = None
    job.lease_expires_at = None
    if study is not None:
        study.status = STATUS_COMPLETED
    db.flush()


def _fail(db: Session, job: AnalysisJob, study: Study | None, message: str) -> None:
    job.status = STATUS_ERROR
    job.error_message = message
    job.completed_at = utcnow()
    job.lease_owner = None
    job.lease_expires_at = None
    if study is not None:
        study.status = STATUS_ERROR
        study.error_message = message
    db.flush()


def queue_deliveries(db: Session, study: Study) -> int:
    """Queue the finished report for every destination that sends on its own.

    Queued rather than sent here: a node that is down must not fail the analysis
    that produced the report, and the attempt belongs where it can be retried.
    """
    from chester import destinations

    queued = 0
    for destination in destinations.automatic(db, study.organization_id):
        db.add(DeliveryJob(study_id=study.id, destination_id=destination.id))
        queued += 1
    if queued:
        logger.info("Queued %d automatic deliver(ies) for study %s", queued, study.id)
        db.flush()
    return queued


def claim_delivery(db: Session) -> uuid.UUID | None:
    """Take ownership of one delivery that is due, or return None."""
    job = (
        db.query(DeliveryJob)
        .filter(
            DeliveryJob.status == STATUS_QUEUED,
            DeliveryJob.next_attempt_at <= utcnow(),
        )
        .order_by(DeliveryJob.next_attempt_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if job is None:
        return None

    job.status = STATUS_PROCESSING
    job.started_at = utcnow()
    job.attempt = (job.attempt or 0) + 1
    job.lease_owner = WORKER_ID
    job.lease_expires_at = _lease_expiry()
    db.flush()
    return job.id


def process_delivery(job_id: uuid.UUID) -> None:
    """Store one report on one destination, recording the attempt either way."""
    from chester.destinations import from_row
    from chester.report_delivery import deliver_report

    with session_scope() as db:
        job = db.get(DeliveryJob, job_id)
        if job is None or job.lease_owner != WORKER_ID:
            logger.warning("Delivery %s is no longer held by this worker", job_id)
            return

        study = db.get(Study, job.study_id)
        destination = job.destination
        if study is None or destination is None or not destination.active:
            _fail_delivery(db, job, "Study or destination is gone", retry=False)
            return

        try:
            deliver_report(db, study, from_row(destination), actor=f"worker:{WORKER_ID}")
        except ValueError as exc:
            # Nothing to report on. Another attempt would find the same, so this
            # one is finished rather than retried.
            _fail_delivery(db, job, str(exc), retry=False)
            return
        except Exception as exc:
            _fail_delivery(db, job, str(exc), retry=True)
            return

        job.status = STATUS_COMPLETED
        job.completed_at = utcnow()
        job.error_message = None
        job.lease_owner = None
        job.lease_expires_at = None
        db.flush()


def _fail_delivery(db: Session, job: DeliveryJob, message: str, *, retry: bool) -> None:
    """Requeue the attempt if any are left, otherwise give up on it."""
    job.error_message = message
    job.lease_owner = None
    job.lease_expires_at = None

    if retry and job.attempt < settings.delivery_max_attempts:
        job.status = STATUS_QUEUED
        job.next_attempt_at = utcnow() + timedelta(minutes=settings.delivery_retry_minutes)
        logger.info(
            "Delivery %s failed on attempt %d; retrying after %g minute(s)",
            job.id,
            job.attempt,
            settings.delivery_retry_minutes,
        )
    else:
        job.status = STATUS_ERROR
        job.completed_at = utcnow()
        logger.warning("Delivery %s failed for good: %s", job.id, message)
    db.flush()


def recover_expired_leases(db: Session) -> int:
    """Requeue jobs whose lease has run out.

    Only expired leases. A job another live worker still holds must be left alone,
    which is why the lease has an expiry rather than being a plain owner flag.
    """
    now = utcnow()
    cutoff = now - timedelta(minutes=settings.job_lease_minutes)
    stale = (
        db.query(AnalysisJob)
        .filter(
            AnalysisJob.status == STATUS_PROCESSING,
            or_(
                AnalysisJob.lease_expires_at < now,
                and_(
                    AnalysisJob.lease_expires_at.is_(None),
                    AnalysisJob.started_at < cutoff,
                ),
            ),
        )
        .all()
    )
    deliveries = (
        db.query(DeliveryJob)
        .filter(
            DeliveryJob.status == STATUS_PROCESSING,
            or_(
                DeliveryJob.lease_expires_at < now,
                and_(
                    DeliveryJob.lease_expires_at.is_(None),
                    DeliveryJob.started_at < cutoff,
                ),
            ),
        )
        .all()
    )
    for job in [*stale, *deliveries]:
        logger.info("Recovering job %s from an expired lease", job.id)
        job.status = STATUS_QUEUED
        job.lease_owner = None
        job.lease_expires_at = None
    db.flush()
    return len(stale) + len(deliveries)


def run(stop: threading.Event | None = None) -> None:
    """Poll for work until asked to stop."""
    stop = stop or threading.Event()
    logger.info("Worker %s started", WORKER_ID)

    with session_scope() as db:
        recovered = recover_expired_leases(db)
    if recovered:
        logger.info("Requeued %d job(s) with expired leases", recovered)

    while not stop.is_set():
        try:
            with session_scope() as db:
                job_id = claim_job(db)
            if job_id is not None:
                logger.info("Processing job %s", job_id)
                process_job(job_id)
                continue

            # Analysis first: a delivery is worth nothing until the report it
            # carries exists.
            with session_scope() as db:
                delivery_id = claim_delivery(db)
            if delivery_id is None:
                stop.wait(timeout=settings.worker_poll_seconds)
                continue
            logger.info("Processing delivery %s", delivery_id)
            process_delivery(delivery_id)
        except Exception:
            logger.exception("Worker loop error")
            stop.wait(timeout=settings.worker_poll_seconds * 2)

    logger.info("Worker %s stopped", WORKER_ID)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings.require_production_secrets()

    stop = threading.Event()

    def _handle_signal(signum, _frame):
        logger.info("Received signal %s; finishing the current job", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    run(stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
