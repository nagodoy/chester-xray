"""Tests for worker and inference (model monkeypatched)."""
from __future__ import annotations

import os
import threading

import numpy as np
import pytest

from app.database import get_db_session
from app.models import AnalysisJob, AnalysisResult, Study


MOCK_PATHOLOGIES = [
    "Atelectasis", "Consolidation", "Infiltration", "Pneumothorax", "Edema",
    "Emphysema", "Fibrosis", "Effusion", "Pneumonia", "Pleural_Thickening",
    "Cardiomegaly", "Nodule", "Mass", "Hernia",
    "Lung Lesion", "Fracture", "Lung Opacity", "Enlarged Cardiomediastinum",
]

MOCK_OP_THRESHS = [
    0.07422872, 0.038290843, 0.09814756, 0.0098118475, 0.023601074,
    0.0022490358, 0.010060724, 0.103246614, 0.056810737, 0.026791653,
    0.050318155, 0.023985857, 0.01939503, 0.042889766, 0.053369623,
    0.035975814, 0.20204692, 0.05015312,
]


class MockModel:
    """Mock TorchXRayVision model."""
    pathologies = MOCK_PATHOLOGIES
    op_threshs = MOCK_OP_THRESHS

    def eval(self):
        return self

    def features2(self, tensor):
        return tensor

    def classifier(self, tensor):
        import torch
        batch_size = tensor.shape[0]
        # Return classifier logits. The real weighted XRV model's forward()
        # would normalize these, so production code must use classifier output.
        scores = torch.zeros(batch_size, len(self.pathologies))
        scores[0, 0] = 3.0   # Atelectasis: high
        scores[0, 4] = 2.5   # Edema: high
        return scores


def _make_mock_xrv(monkeypatch):
    """Monkeypatch torchxrayvision with mock model."""
    import sys
    import types

    # Create mock xrv module
    xrv_mock = types.ModuleType("torchxrayvision")

    class MockModels:
        @staticmethod
        def DenseNet(weights=None):
            return MockModel()

    class MockDatasets:
        @staticmethod
        def normalize(img, maxval):
            return img / maxval * 2 - 1

        @staticmethod
        def XRayCenterCrop():
            def apply(img):
                h, w = img.shape[-2:]
                s = min(h, w)
                sh, sw = (h - s) // 2, (w - s) // 2
                return img[..., sh:sh+s, sw:sw+s]
            return apply

        @staticmethod
        def XRayResizer(size):
            def apply(img):
                import numpy as _np
                from PIL import Image
                if img.ndim == 3:
                    pil = Image.fromarray((img[0] * 127.5 + 127.5).clip(0, 255).astype(_np.uint8), mode="L")
                    pil = pil.resize((size, size), Image.LANCZOS)
                    return _np.array(pil, dtype=_np.float32)[_np.newaxis, ...]
                pil = Image.fromarray((img * 127.5 + 127.5).clip(0, 255).astype(_np.uint8), mode="L")
                pil = pil.resize((size, size), Image.LANCZOS)
                return _np.array(pil, dtype=_np.float32)
            return apply

    xrv_mock.models = MockModels()
    xrv_mock.datasets = MockDatasets()

    monkeypatch.setitem(sys.modules, "torchxrayvision", xrv_mock)

    # Reset cached model
    import app.worker as worker_mod
    worker_mod._model = None
    worker_mod._model_version = None

    return xrv_mock


def _create_queued_study(dcm_bytes):
    """Create a study + job in queued state using get_db_session (same as worker)."""
    from app.ingestion import ingest_file

    with get_db_session() as session:
        result = ingest_file(
            data=dcm_bytes,
            filename="test.dcm",
            content_type="application/dicom",
            actor_id="test-user",
            session=session,
            source="test",
        )
        if "error" in result:
            raise RuntimeError(f"Ingest failed: {result['error']}")

        study_id = result["study_id"]
        study = session.query(Study).filter_by(id=study_id).first()

        # Force to queued
        study.status = "queued"
        job = session.query(AnalysisJob).filter_by(study_id=study_id).first()
        if job is None:
            job = AnalysisJob(study_id=study_id, status="queued")
            session.add(job)
        else:
            job.status = "queued"
        session.flush()
        job_id = job.id

    return study_id, job_id


def test_run_inference_mock(monkeypatch, db_session):
    """Test inference with monkeypatched model."""
    from tests.backend.conftest import make_minimal_dicom
    _make_mock_xrv(monkeypatch)

    from app.worker import run_inference

    arr = np.random.rand(256, 256).astype(np.float32) * 2048 - 1024
    result = run_inference(arr)

    assert "raw_scores" in result
    assert "op_normalized_scores" in result
    assert "thresholds" in result
    assert "above_threshold" in result
    assert "above_threshold_findings" in result
    assert "model_version" in result

    # Atelectasis and Edema should be above threshold
    assert "Atelectasis" in result["above_threshold_findings"]
    assert "Edema" in result["above_threshold_findings"]

    # Raw scores should not be equal to normalized scores (they differ)
    assert result["raw_scores"]["Atelectasis"] != result["op_normalized_scores"]["Atelectasis"]
    # This catches accidental use of weighted DenseNet.forward() plus a second
    # sigmoid: the raw score must be sigmoid(3), not sigmoid(op_norm(...)).
    assert result["raw_scores"]["Atelectasis"] == pytest.approx(
        1.0 / (1.0 + np.exp(-3.0)), rel=1e-5
    )


def test_process_job_mock(monkeypatch, setup_test_db):
    """Test job processing end-to-end with monkeypatched model."""
    from tests.backend.conftest import make_minimal_dicom
    _make_mock_xrv(monkeypatch)

    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    study_id, job_id = _create_queued_study(dcm)

    from app.worker import _process_job
    _process_job(job_id)

    # Check results using same session mechanism as worker
    with get_db_session() as session:
        result = session.query(AnalysisResult).filter_by(study_id=study_id).first()
        assert result is not None
        assert result.raw_scores is not None
        assert len(result.raw_scores) > 0

        study = session.query(Study).filter_by(id=study_id).first()
        assert study.status == "completed"


def test_inference_failure_sets_error(monkeypatch, setup_test_db):
    """Inference failure should set job and study to error state."""
    from tests.backend.conftest import make_minimal_dicom

    # Monkeypatch to raise an exception
    import sys, types
    xrv_bad = types.ModuleType("torchxrayvision")

    class BadModels:
        @staticmethod
        def DenseNet(weights=None):
            raise RuntimeError("Model download failed")

    xrv_bad.models = BadModels()
    xrv_bad.datasets = type("DS", (), {
        "normalize": staticmethod(lambda img, maxval: img),
        "Compose": staticmethod(lambda t: lambda x: x),
        "XRayCenterCrop": staticmethod(lambda: lambda x: x),
        "XRayResizer": staticmethod(lambda s: lambda x: x),
    })()
    monkeypatch.setitem(sys.modules, "torchxrayvision", xrv_bad)

    import app.worker as worker_mod
    worker_mod._model = None
    worker_mod._model_version = None

    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    study_id, job_id = _create_queued_study(dcm)

    from app.worker import _process_job
    _process_job(job_id)

    with get_db_session() as session:
        job = session.query(AnalysisJob).filter_by(id=job_id).first()
        study = session.query(Study).filter_by(id=study_id).first()

        assert job.status == "error"
        assert study.status == "error"
        assert job.error_message is not None


def test_scores_not_called_probabilities(monkeypatch, setup_test_db):
    """
    Ensure op_normalized_scores are not mislabeled as calibrated probabilities.
    The schema uses 'op_normalized_scores', not 'calibrated_probabilities'.
    """
    _make_mock_xrv(monkeypatch)
    from tests.backend.conftest import make_minimal_dicom

    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    study_id, job_id = _create_queued_study(dcm)

    from app.worker import _process_job
    _process_job(job_id)

    with get_db_session() as session:
        result = session.query(AnalysisResult).filter_by(study_id=study_id).first()
        assert result is not None
        # Has op_normalized_scores, not calibrated_probabilities
        assert result.op_normalized_scores is not None
        # The result model must not have a "calibrated_probabilities" field
        assert not hasattr(result, "calibrated_probabilities")


def test_job_claim_is_exclusive(setup_test_db):
    """Only one worker lease can claim a queued job."""
    from tests.backend.conftest import make_minimal_dicom
    from app.worker import _claim_job

    dcm = make_minimal_dicom(modality="DX", body_part="CHEST", view_position="PA")
    _, job_id = _create_queued_study(dcm)

    first_claim = _claim_job(job_id)
    assert first_claim is not None
    assert first_claim[0] == job_id
    assert _claim_job(job_id) is None

    with get_db_session() as session:
        job = session.query(AnalysisJob).filter_by(id=job_id).first()
        assert job.status == "processing"
        assert job.lease_owner
        assert job.lease_expires_at
        job.status = "completed"
        job.lease_owner = None
        job.lease_expires_at = None
