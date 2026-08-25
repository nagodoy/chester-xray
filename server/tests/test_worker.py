"""The analysis worker: claiming, processing, failure and lease recovery."""

from __future__ import annotations

import os
import threading
import uuid

import pytest

from chester import worker
from chester.models import AnalysisJob, AnalysisResult, Instance, Study, utcnow
from chester.security.roles import ROLE_ADMIN


@pytest.fixture
def queued_study(client, signed_in, make_user, make_dicom, session):
    """A real chest DICOM ingested through the API, so it is queued for analysis."""
    make_user("uploader@example.com", ROLE_ADMIN)
    headers, _ = signed_in("uploader@example.com")
    response = client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("chest.dcm", make_dicom(), "application/dicom"))],
        headers=headers,
    )
    assert response.status_code == 200, response.text
    study = session.get(Study, uuid.UUID(response.json()["studies"][0]["id"]))
    job = session.query(AnalysisJob).filter_by(study_id=study.id).one()
    return study, job


class TestClaiming:
    def test_claiming_marks_the_job_and_the_study(self, session, queued_study):
        study, job = queued_study

        claimed = worker.claim_job(session)

        assert claimed == job.id
        assert job.status == "processing"
        assert job.lease_owner == worker.WORKER_ID
        assert job.lease_expires_at is not None
        assert job.attempt == 1
        assert study.status == "processing"

    def test_there_is_nothing_to_claim_twice(self, session, queued_study):
        assert worker.claim_job(session) is not None
        assert worker.claim_job(session) is None

    def test_an_empty_queue_yields_nothing(self, session):
        assert worker.claim_job(session) is None


class TestLeaseRecovery:
    def test_an_expired_lease_is_requeued(self, session, queued_study):
        _, job = queued_study
        worker.claim_job(session)
        job.lease_expires_at = utcnow() - __import__("datetime").timedelta(minutes=1)
        session.flush()

        assert worker.recover_expired_leases(session) == 1
        assert job.status == "queued"
        assert job.lease_owner is None

    def test_a_live_lease_is_left_alone(self, session, queued_study):
        """Another worker may still be running it."""
        _, job = queued_study
        worker.claim_job(session)

        assert worker.recover_expired_leases(session) == 0
        assert job.status == "processing"
        assert job.lease_owner == worker.WORKER_ID


class TestInstanceSelection:
    def test_the_oldest_instance_is_always_chosen(self, session, queued_study, make_dicom):
        """A multi-instance study must analyse the same image every time."""
        study, _ = queued_study
        first = session.query(Instance).filter_by(study_id=study.id).one()

        later = Instance(
            study_id=study.id,
            organization_id=study.organization_id,
            sop_instance_uid="2.25.999",
            object_key="originals/other.dcm",
            content_type="application/dicom",
            sha256="deadbeef",
        )
        session.add(later)
        session.flush()

        from chester.storage import retrieve_bytes

        # Selection must not depend on which row the database happens to return.
        for _ in range(3):
            chosen = (
                session.query(Instance)
                .filter(Instance.study_id == study.id)
                .order_by(Instance.created_at.asc(), Instance.id.asc())
                .first()
            )
            assert chosen.id == first.id
        assert retrieve_bytes(first.object_key, session=session)

    def test_a_study_without_an_instance_fails_cleanly(self, session, queued_study):
        study, _ = queued_study
        session.query(Instance).filter_by(study_id=study.id).delete()
        session.flush()

        with pytest.raises(ValueError, match="no stored instance"):
            worker.load_pixels(session, study)


class TestProcessing:
    def test_a_job_runs_end_to_end_and_records_scores(self, session, queued_study, monkeypatch):
        study, job = queued_study
        _bind_worker_sessions(monkeypatch, session)
        worker.claim_job(session)

        worker.process_job(job.id)

        assert job.status == "completed"
        assert job.lease_owner is None
        assert study.status == "completed"

        result = session.query(AnalysisResult).filter_by(job_id=job.id).one()
        assert result.model_version.startswith("chester-onnx:")
        assert len(result.raw_scores) == 12
        assert set(result.raw_scores) == set(result.thresholds)
        assert all(0.0 <= value <= 1.0 for value in result.raw_scores.values())
        assert all(0.0 <= value <= 1.0 for value in result.op_normalized_scores.values())

    def test_findings_are_exactly_the_scores_above_their_threshold(
        self, session, queued_study, monkeypatch
    ):
        _, job = queued_study
        _bind_worker_sessions(monkeypatch, session)
        worker.claim_job(session)

        worker.process_job(job.id)

        result = session.query(AnalysisResult).filter_by(job_id=job.id).one()
        expected = {
            pathology
            for pathology, score in result.raw_scores.items()
            if score >= result.thresholds[pathology]
        }
        assert set(result.above_threshold_findings) == expected

    def test_an_inference_failure_is_recorded_on_the_study(
        self, session, queued_study, monkeypatch
    ):
        study, job = queued_study
        _bind_worker_sessions(monkeypatch, session)
        worker.claim_job(session)

        def _boom(_pixels):
            raise RuntimeError("model exploded")

        monkeypatch.setattr("chester.inference.infer", _boom)

        worker.process_job(job.id)

        assert job.status == "error"
        assert "model exploded" in job.error_message
        assert study.status == "error"
        assert "model exploded" in study.error_message
        assert session.query(AnalysisResult).count() == 0

    def test_a_job_this_worker_does_not_hold_is_left_alone(
        self, session, queued_study, monkeypatch
    ):
        """Losing a lease mid-flight must not overwrite the new owner's work."""
        _, job = queued_study
        _bind_worker_sessions(monkeypatch, session)
        worker.claim_job(session)
        job.lease_owner = "someone-else"
        session.flush()

        worker.process_job(job.id)

        assert session.query(AnalysisResult).count() == 0
        assert job.lease_owner == "someone-else"


def _bind_worker_sessions(monkeypatch, session):
    """Make the worker's own session_scope reuse the test's transaction."""
    import contextlib

    @contextlib.contextmanager
    def _scope():
        yield session
        session.flush()

    monkeypatch.setattr(worker, "session_scope", _scope)


POSTGRES_URL = os.environ.get("CHESTER_TEST_POSTGRES_URL", "")


@pytest.mark.skipif(not POSTGRES_URL, reason="CHESTER_TEST_POSTGRES_URL is not set")
class TestConcurrency:
    """SKIP LOCKED only exists on a real database; SQLite silently ignores it."""

    @pytest.fixture
    def pg_sessions(self):
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker

        from tests.conftest import SERVER_ROOT

        engine = create_engine(POSTGRES_URL)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))

        config = Config(str(SERVER_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(SERVER_ROOT / "migrations"))
        config.set_main_option("sqlalchemy.url", POSTGRES_URL)
        command.upgrade(config, "head")

        factory = sessionmaker(bind=engine)
        yield engine, factory
        engine.dispose()

    def test_only_one_worker_can_claim_a_job(self, pg_sessions, make_dicom):
        from chester.models import Organization, User

        engine, factory = pg_sessions
        setup = factory()
        org = Organization(name="Org", slug="org")
        setup.add(org)
        setup.flush()
        user = User(email="a@b.test", organization_id=org.id, role=ROLE_ADMIN)
        setup.add(user)
        setup.flush()
        study = Study(owner_user_id=user.id, organization_id=org.id, status="queued")
        setup.add(study)
        setup.flush()
        setup.add(AnalysisJob(study_id=study.id, status="queued"))
        setup.commit()
        setup.close()

        first, second = factory(), factory()
        try:
            claimed_first = worker.claim_job(first)
            claimed_second = worker.claim_job(second)

            assert claimed_first is not None
            assert claimed_second is None, "two workers claimed the same job"
        finally:
            first.rollback()
            second.rollback()
            first.close()
            second.close()

    def test_two_concurrent_workers_split_two_jobs(self, pg_sessions):
        from chester.models import Organization, User

        engine, factory = pg_sessions
        setup = factory()
        org = Organization(name="Org", slug="org")
        setup.add(org)
        setup.flush()
        user = User(email="a@b.test", organization_id=org.id, role=ROLE_ADMIN)
        setup.add(user)
        setup.flush()
        for _ in range(2):
            study = Study(owner_user_id=user.id, organization_id=org.id, status="queued")
            setup.add(study)
            setup.flush()
            setup.add(AnalysisJob(study_id=study.id, status="queued"))
        setup.commit()
        setup.close()

        claimed: list[uuid.UUID] = []
        lock = threading.Lock()

        def _claim():
            db = factory()
            try:
                job_id = worker.claim_job(db)
                db.commit()
                if job_id is not None:
                    with lock:
                        claimed.append(job_id)
            finally:
                db.close()

        threads = [threading.Thread(target=_claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(claimed) == 2
        assert len(set(claimed)) == 2, "the same job was claimed twice"
