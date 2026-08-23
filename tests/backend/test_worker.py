"""Tests for worker and inference (model monkeypatched)."""
from __future__ import annotations

import numpy as np
import pytest

from app.database import get_db_session
from app.models import AnalysisJob, AnalysisResult, Study


MOCK_PATHOLOGIES = [
    "Atelectasis", "Consolidation", "", "", "Edema", "Emphysema", "Fibrosis",
    "Effusion", "", "Pleural Thickening", "Cardiomegaly", "", "Mass", "Hernia",
    "", "", "Lung Opacity", "Enlarged Cardiomedia.",
]

MOCK_OP_THRESHS = [
    0.07422872, 0.038290843, 0.09814756, 0.0098118475, 0.023601074,
    0.0022490358, 0.010060724, 0.103246614, 0.056810737, 0.026791653,
    0.050318155, 0.023985857, 0.01939503, 0.042889766, 0.053369623,
    0.035975814, 0.20204692, 0.05015312,
]


class MockModel:
    """Mock CHESTER model with already-sigmoid output."""
    pathologies = MOCK_PATHOLOGIES
    op_threshs = MOCK_OP_THRESHS
    scale_upper = 1.3
    version = "chester-tfjs:test"

    def infer(self, pixel_array):
        scores = np.full(len(self.pathologies), 0.01, dtype=np.float32)
        scores[0] = 0.95
        scores[4] = 0.90
        return scores

    def close(self):
        pass


def _make_mock_chester():
    """Install a mock CHESTER runtime in the worker cache."""
    import app.worker as worker_mod
    worker_mod._model = MockModel()
    worker_mod._model_version = worker_mod._model.version

    return worker_mod._model


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


def test_run_inference_mock(db_session):
    """Test inference with monkeypatched model."""
    _make_mock_chester()

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
    # CHESTER's output node is already sigmoid; no second sigmoid is allowed.
    assert result["raw_scores"]["Atelectasis"] == pytest.approx(0.95)
    # The legacy CHESTER upper-range emphasis is preserved.
    expected = 1.0 - ((1.0 - 0.95) / ((1.0 - MOCK_OP_THRESHS[0]) * 2.0))
    assert result["op_normalized_scores"]["Atelectasis"] == pytest.approx(
        min(1.0, expected * 1.3)
    )


def test_chester_normalization_boundaries():
    class BoundaryModel(MockModel):
        def infer(self, pixel_array):
            scores = np.full(len(self.pathologies), 0.01, dtype=np.float32)
            scores[0] = self.op_threshs[0] / 2.0
            scores[1] = self.op_threshs[1]
            # Choose a raw score whose pre-emphasis normalized value is 0.7.
            scores[4] = 1.0 - 0.6 * (1.0 - self.op_threshs[4])
            return scores

    import app.worker as worker_mod
    worker_mod._model = BoundaryModel()
    worker_mod._model_version = worker_mod._model.version

    result = worker_mod.run_inference(np.zeros((224, 224), dtype=np.float32))

    assert result["op_normalized_scores"]["Atelectasis"] == pytest.approx(0.25)
    assert result["op_normalized_scores"]["Consolidation"] == pytest.approx(0.5)
    assert result["op_normalized_scores"]["Edema"] == pytest.approx(0.7 * 1.3)


def test_process_job_mock(setup_test_db):
    """Test job processing end-to-end with monkeypatched model."""
    from tests.backend.conftest import make_minimal_dicom
    _make_mock_chester()

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


def test_inference_failure_sets_error(setup_test_db):
    """Inference failure should set job and study to error state."""
    from tests.backend.conftest import make_minimal_dicom

    class BadModel(MockModel):
        def infer(self, pixel_array):
            raise RuntimeError("CHESTER runtime unavailable")

    import app.worker as worker_mod
    worker_mod._model = BadModel()
    worker_mod._model_version = worker_mod._model.version

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


def test_scores_not_called_probabilities(setup_test_db):
    """
    Ensure op_normalized_scores are not mislabeled as calibrated probabilities.
    The schema uses 'op_normalized_scores', not 'calibrated_probabilities'.
    """
    _make_mock_chester()
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
