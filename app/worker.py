"""Background worker for AI inference jobs."""
from __future__ import annotations

import io
import logging
import os
import socket
import threading
import time
import uuid
from datetime import datetime
from datetime import timedelta
from typing import Optional

import numpy as np
from sqlalchemy import and_, or_

from app.config import settings
from app.database import get_db_session
from app.models import AnalysisJob, AnalysisResult, Study

logger = logging.getLogger(__name__)

PREPROCESSING_VERSION = "1.0.0"
JOB_LEASE_MINUTES = 30
_WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"

# Lazy-loaded model globals
_model = None
_model_version: Optional[str] = None
_model_lock = threading.Lock()

# Worker thread
_worker_thread: Optional[threading.Thread] = None
_worker_stop = threading.Event()


def get_model_version() -> Optional[str]:
    return _model_version


def _load_model():
    """Lazy-load TorchXRayVision model. Returns (model, version) or raises."""
    global _model, _model_version
    with _model_lock:
        if _model is not None:
            return _model, _model_version

        import torch
        import torchxrayvision as xrv  # type: ignore

        model_name = settings.model_name
        logger.info("Loading TorchXRayVision model: %s", model_name)
        model = xrv.models.DenseNet(weights=model_name)
        model.eval()

        package_version = getattr(xrv, "__version__", "unknown")
        version = f"torchxrayvision-{package_version}:{model_name}"
        _model = model
        _model_version = version
        logger.info("Model loaded: %s", version)
        return model, version


def run_inference(pixel_array: np.ndarray) -> dict:
    """
    Run model inference on a 2D float32 numpy array.

    Returns dict with:
      - raw_scores: {pathology: float}
      - op_normalized_scores: {pathology: float}
      - thresholds: {pathology: float}
      - above_threshold: {pathology: bool}
      - above_threshold_findings: [pathology names above threshold]
      - model_version: str
    """
    import torch
    model, version = _load_model()

    import torchxrayvision as xrv  # type: ignore
    from torchvision import transforms

    # The renderer supplies arbitrary-value grayscale arrays. Normalize once to
    # TorchXRayVision's expected [-1024, 1024] range.
    from app.dicom_utils import normalize_for_model
    arr = normalize_for_model(pixel_array)

    # Center crop and resize to 224x224
    transform = transforms.Compose([
        xrv.datasets.XRayCenterCrop(),
        xrv.datasets.XRayResizer(224),
    ])
    arr = transform(arr[None, ...])[0]  # Add/remove channel dim

    # Build tensor: [1, 1, 224, 224]
    tensor = torch.from_numpy(arr).unsqueeze(0).unsqueeze(0).float()

    # TorchXRayVision's weighted DenseNet.forward() already applies sigmoid
    # and operating-point normalization. Bypass that presentation transform so
    # we can persist both the true sigmoid output and the normalized score once.
    if not hasattr(model, "features2") or not hasattr(model, "classifier"):
        raise RuntimeError("Loaded XRV model does not expose classifier features")
    with torch.no_grad():
        logits = model.classifier(model.features2(tensor))
        sigmoid_scores = torch.sigmoid(logits).squeeze(0).cpu().numpy()

    pathologies = model.pathologies
    op_threshs = getattr(model, "op_threshs", None)

    raw_scores = {}
    op_normalized_scores = {}
    thresholds = {}
    above_threshold = {}
    above_threshold_findings = []

    for i, pathology in enumerate(pathologies):
        if not pathology:
            continue
        raw = float(sigmoid_scores[i])
        raw_scores[pathology] = raw

        if op_threshs is not None and i < len(op_threshs) and op_threshs[i] > 0:
            thresh = float(op_threshs[i])
            # TorchXRayVision operating-point normalization: threshold -> 0.5,
            # preserving both the below- and above-threshold ranges.
            if raw < thresh:
                normalized = 0.5 * raw / (thresh + 1e-8)
            else:
                normalized = 0.5 + 0.5 * (raw - thresh) / (1.0 - thresh + 1e-8)
            normalized = min(1.0, max(0.0, normalized))
        else:
            thresh = 0.5
            normalized = raw

        thresholds[pathology] = thresh
        op_normalized_scores[pathology] = normalized
        is_above = raw >= thresh
        above_threshold[pathology] = is_above
        if is_above:
            above_threshold_findings.append(pathology)

    return {
        "raw_scores": raw_scores,
        "op_normalized_scores": op_normalized_scores,
        "thresholds": thresholds,
        "above_threshold": above_threshold,
        "above_threshold_findings": above_threshold_findings,
        "model_version": version,
    }


def _claim_job(job_id: Optional[str] = None) -> Optional[tuple[str, str]]:
    """Atomically claim one queued job and return (job_id, lease_owner)."""
    now = datetime.utcnow()
    with get_db_session() as session:
        query = session.query(AnalysisJob).filter(AnalysisJob.status == "queued")
        if job_id is not None:
            query = query.filter(AnalysisJob.id == job_id)
        job = (
            query.order_by(AnalysisJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if job is None:
            return None

        job.status = "processing"
        job.started_at = now
        job.attempt = (job.attempt or 0) + 1
        job.lease_owner = _WORKER_ID
        job.lease_expires_at = now + timedelta(minutes=JOB_LEASE_MINUTES)
        study = session.query(Study).filter_by(id=job.study_id).first()
        if study is not None:
            study.status = "processing"
        session.flush()
        return job.id, _WORKER_ID


def _process_job(job_id: str) -> None:
    """Claim and process a single queued job (used by tests and manual repair)."""
    claim = _claim_job(job_id)
    if claim is None:
        logger.info("Job %s is not queued or is already leased", job_id)
        return
    _process_claimed_job(*claim)


def _process_claimed_job(job_id: str, lease_owner: str) -> None:
    """Process a job that was atomically claimed by this worker."""
    from app.dicom_utils import render_dicom_frame, normalize_for_model
    from app.storage import retrieve_bytes

    with get_db_session() as session:
        job = session.query(AnalysisJob).filter_by(
            id=job_id,
            status="processing",
            lease_owner=lease_owner,
        ).first()
        if job is None:
            logger.warning("Job %s is no longer leased by %s", job_id, lease_owner)
            return

        study = session.query(Study).filter_by(id=job.study_id).first()
        if study is None:
            job.status = "error"
            job.error_message = "Study not found"
            job.lease_owner = None
            job.lease_expires_at = None
            session.flush()
            return

    # Do inference outside the session to avoid long-held locks
    error_msg = None
    inference_result = None
    pixel_array = None

    try:
        with get_db_session() as session:
            job = session.query(AnalysisJob).filter_by(id=job_id).first()
            study = session.query(Study).filter_by(id=job.study_id).first()
            instance = session.query(
                __import__("app.models", fromlist=["Instance"]).Instance
            ).filter_by(study_id=study.id).first()

            if instance is None or not instance.object_key:
                raise ValueError("No instance/object_key found for study")

            raw_bytes = retrieve_bytes(instance.object_key, session=session)
            ct = instance.content_type or ""

        # Decode image
        if "dicom" in ct or ct == "application/octet-stream":
            import pydicom
            from pydicom.filebase import DicomBytesIO
            ds = pydicom.dcmread(DicomBytesIO(raw_bytes), force=True)
            arr = render_dicom_frame(ds, frame_index=0)
        else:
            from PIL import Image
            img = Image.open(io.BytesIO(raw_bytes)).convert("L")
            arr = np.array(img, dtype=np.float32)

        inference_result = run_inference(arr)

    except Exception as exc:
        logger.exception("Inference error for job %s: %s", job_id, exc)
        error_msg = str(exc)

    # Save result
    with get_db_session() as session:
        job = session.query(AnalysisJob).filter_by(
            id=job_id,
            status="processing",
            lease_owner=lease_owner,
        ).first()
        if job is None:
            logger.warning("Discarding result for job %s after lease loss", job_id)
            return
        study = session.query(Study).filter_by(id=job.study_id).first()

        if error_msg:
            job.status = "error"
            job.error_message = error_msg
            job.completed_at = datetime.utcnow()
            if study:
                study.status = "error"
                study.error_message = error_msg
        else:
            existing_result = session.query(AnalysisResult).filter_by(job_id=job_id).first()
            if existing_result is not None:
                logger.warning("Job %s already has a result; not inserting another", job_id)
                job.status = "completed"
                job.completed_at = existing_result.created_at
                if study:
                    study.status = "completed"
                job.lease_owner = None
                job.lease_expires_at = None
                session.flush()
                return
            job.status = "completed"
            job.completed_at = datetime.utcnow()

            mv = inference_result["model_version"]
            result = AnalysisResult(
                study_id=job.study_id,
                job_id=job_id,
                model_version=mv,
                preprocessing_version=PREPROCESSING_VERSION,
                raw_scores=inference_result["raw_scores"],
                op_normalized_scores=inference_result["op_normalized_scores"],
                thresholds=inference_result["thresholds"],
                above_threshold=inference_result["above_threshold"],
                above_threshold_findings=inference_result["above_threshold_findings"],
            )
            session.add(result)

            if study:
                study.status = "completed"
                study.model_version = mv
                study.preprocessing_version = PREPROCESSING_VERSION
                study.error_message = None

        job.lease_owner = None
        job.lease_expires_at = None
        session.flush()


def _worker_loop() -> None:
    """Main worker loop: poll for queued jobs."""
    logger.info("Worker thread started")

    # Recover only expired leases. A different live process may still own any
    # other processing job.
    with get_db_session() as session:
        now = datetime.utcnow()
        legacy_cutoff = now - timedelta(minutes=JOB_LEASE_MINUTES)
        stale = session.query(AnalysisJob).filter(
            AnalysisJob.status == "processing",
            or_(
                AnalysisJob.lease_expires_at < now,
                and_(
                    AnalysisJob.lease_expires_at.is_(None),
                    AnalysisJob.started_at < legacy_cutoff,
                ),
            ),
        ).all()
        for job in stale:
            logger.info("Recovering stale job %s", job.id)
            job.status = "queued"
            job.lease_owner = None
            job.lease_expires_at = None
        session.flush()

    while not _worker_stop.is_set():
        try:
            claim = _claim_job()
            if claim:
                logger.info("Processing job %s", claim[0])
                _process_claimed_job(*claim)
            else:
                _worker_stop.wait(timeout=5.0)
        except Exception as exc:
            logger.exception("Worker loop error: %s", exc)
            _worker_stop.wait(timeout=10.0)

    logger.info("Worker thread stopped")


def start_worker() -> None:
    """Start the background worker thread."""
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="inference-worker")
    _worker_thread.start()


def stop_worker() -> None:
    """Stop the background worker thread."""
    _worker_stop.set()
    if _worker_thread:
        _worker_thread.join(timeout=5.0)
