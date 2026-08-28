"""Configured destinations, and the queue that delivers a finished report to them."""

from __future__ import annotations

import contextlib
import uuid

import pytest

from chester import worker
from chester.models import DeliveryJob, NetworkLog, SendDestination, Study, utcnow
from chester.security.roles import ROLE_ADMIN, ROLE_TECHNICIAN


@pytest.fixture
def admin(make_user):
    return make_user("admin@example.com", ROLE_ADMIN)


@pytest.fixture
def headers(signed_in, admin):
    return signed_in("admin@example.com")[0]


@pytest.fixture
def make_destination(session, organization):
    def _make(name: str, *, host: str = "pacs.example.org", active=True, auto_send=False):
        row = SendDestination(
            organization_id=organization.id,
            name=name,
            host=host,
            port=11112,
            ae_title="MEDFUSION",
            calling_ae_title="TORAX_AI",
            active=active,
            auto_send=auto_send,
        )
        session.add(row)
        session.flush()
        return row

    return _make


@pytest.fixture
def stub_report(monkeypatch):
    """Stand in for the built instance; building it is covered by test_report."""
    from pydicom.dataset import Dataset

    from chester import report_delivery

    dataset = Dataset()
    dataset.SOPInstanceUID = "1.2.826.0.1.3680043.10.1337.7"
    monkeypatch.setattr(report_delivery, "build_for_study", lambda *a, **k: dataset)
    return dataset


@pytest.fixture
def sent_to(monkeypatch):
    """Capture what send_dataset was asked to store, and where."""
    from chester import dicom_send

    calls: list[dict] = []
    monkeypatch.setattr(dicom_send, "send_dataset", lambda dataset, **kwargs: calls.append(kwargs))
    return calls


@pytest.fixture
def analysed_study(client, headers, make_dicom, session):
    """A study ingested through the API and carrying a completed analysis."""
    from chester.models import AnalysisResult

    response = client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("chest.dcm", make_dicom(), "application/dicom"))],
        headers=headers,
    )
    assert response.status_code == 200, response.text
    study = session.get(Study, uuid.UUID(response.json()["studies"][0]["id"]))
    study.status = "completed"
    session.add(
        AnalysisResult(
            study_id=study.id,
            model_version="chester-onnx:test",
            raw_scores={"Cardiomegaly": 0.9},
            op_normalized_scores={"Cardiomegaly": 0.8},
            thresholds={"Cardiomegaly": 0.5},
            above_threshold={"Cardiomegaly": True},
            above_threshold_findings=["Cardiomegaly"],
        )
    )
    session.flush()
    return study


class TestConfiguring:
    def test_the_environment_address_is_shown_only_while_nothing_is_configured(
        self, client, headers
    ):
        response = client.get("/api/settings/destinations", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items"] == []
        assert body["environment"]["host"]  # the deployment default still in use
        assert body["editable"] is True

        created = client.post(
            "/api/settings/destinations",
            json={"name": "PACS", "host": "pacs.example.org", "ae_title": "MEDFUSION"},
            headers=headers,
        )
        assert created.status_code == 201, created.text

        body = client.get("/api/settings/destinations", headers=headers).json()
        assert [item["name"] for item in body["items"]] == ["PACS"]
        assert body["environment"] is None

    def test_two_connections_cannot_share_a_name(self, client, headers):
        body = {"name": "PACS", "host": "pacs.example.org", "ae_title": "MEDFUSION"}
        assert client.post("/api/settings/destinations", json=body, headers=headers).status_code
        again = client.post("/api/settings/destinations", json=body, headers=headers)
        assert again.status_code == 409

    def test_an_ae_title_the_protocol_cannot_carry_is_refused(self, client, headers):
        response = client.post(
            "/api/settings/destinations",
            json={"name": "PACS", "host": "pacs.example.org", "ae_title": "BAD\\TITLE"},
            headers=headers,
        )
        assert response.status_code == 400

    def test_automatic_sending_is_switched_on_per_connection(
        self, client, headers, make_destination, session
    ):
        row = make_destination("PACS")
        response = client.patch(
            f"/api/settings/destinations/{row.id}",
            json={"auto_send": True, "port": 4242},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["auto_send"] is True
        assert row.auto_send is True
        assert row.port == 4242

    def test_reading_needs_the_page_and_writing_needs_an_administrator(
        self, client, signed_in, make_user
    ):
        make_user("tech@example.com", ROLE_TECHNICIAN)
        tech_headers, _ = signed_in("tech@example.com")

        read = client.get("/api/settings/destinations", headers=tech_headers)
        assert read.status_code == 200
        assert read.json()["editable"] is False

        written = client.post(
            "/api/settings/destinations",
            json={"name": "PACS", "host": "pacs.example.org", "ae_title": "MEDFUSION"},
            headers=tech_headers,
        )
        assert written.status_code == 403


class TestSendingByHand:
    def test_the_report_goes_to_every_active_connection(
        self, client, headers, analysed_study, make_destination, stub_report, sent_to, session
    ):
        make_destination("PACS", host="pacs.example.org")
        make_destination("Estação", host="osirix.example.org")
        make_destination("Antigo", host="old.example.org", active=False)

        response = client.post(f"/api/studies/{analysed_study.id}/send-report", headers=headers)
        assert response.status_code == 200, response.text

        assert [call["host"] for call in sent_to] == ["pacs.example.org", "osirix.example.org"]
        peers = {
            entry.peer for entry in session.query(NetworkLog).filter(NetworkLog.direction == "sent")
        }
        assert peers == {
            "MEDFUSION@pacs.example.org:11112",
            "MEDFUSION@osirix.example.org:11112",
        }

    def test_one_node_refusing_does_not_hide_the_other_landing(
        self, client, headers, analysed_study, make_destination, stub_report, monkeypatch, session
    ):
        from chester import dicom_send

        make_destination("PACS", host="pacs.example.org")
        make_destination("Estação", host="osirix.example.org")

        def send(dataset, **kwargs):
            if kwargs["host"] == "pacs.example.org":
                raise dicom_send.SendFailed("refused the association")

        monkeypatch.setattr(dicom_send, "send_dataset", send)

        response = client.post(f"/api/studies/{analysed_study.id}/send-report", headers=headers)
        assert response.status_code == 502
        assert "PACS" in response.json()["detail"]

        outcomes = {
            entry.peer: entry.status
            for entry in session.query(NetworkLog).filter(NetworkLog.direction == "sent")
        }
        assert outcomes == {
            "MEDFUSION@pacs.example.org:11112": "failure",
            "MEDFUSION@osirix.example.org:11112": "success",
        }

    def test_with_every_connection_off_nothing_is_attempted(
        self, client, headers, analysed_study, make_destination, sent_to
    ):
        make_destination("Antigo", active=False)

        response = client.post(f"/api/studies/{analysed_study.id}/send-report", headers=headers)
        assert response.status_code == 400
        assert sent_to == []


def _bind_worker_sessions(monkeypatch, session):
    """Make the worker's own session_scope reuse the test's transaction."""

    @contextlib.contextmanager
    def _scope():
        yield session
        session.flush()

    monkeypatch.setattr(worker, "session_scope", _scope)


class TestAutomaticDelivery:
    def test_a_completed_analysis_queues_only_the_automatic_connections(
        self, session, analysed_study, make_destination
    ):
        automatic = make_destination("PACS", auto_send=True)
        make_destination("Manual", host="manual.example.org")
        make_destination("Desligado", host="off.example.org", active=False, auto_send=True)

        assert worker.queue_deliveries(session, analysed_study) == 1

        job = session.query(DeliveryJob).one()
        assert job.destination_id == automatic.id
        assert job.status == "queued"

    def test_a_queued_delivery_is_claimed_sent_and_closed(
        self, session, analysed_study, make_destination, stub_report, sent_to, monkeypatch
    ):
        make_destination("PACS", auto_send=True)
        worker.queue_deliveries(session, analysed_study)
        _bind_worker_sessions(monkeypatch, session)

        job_id = worker.claim_delivery(session)
        assert job_id is not None
        worker.process_delivery(job_id)

        job = session.get(DeliveryJob, job_id)
        assert job.status == "completed"
        assert job.lease_owner is None
        assert [call["host"] for call in sent_to] == ["pacs.example.org"]

        entry = session.query(NetworkLog).filter(NetworkLog.direction == "sent").one()
        assert entry.status == "success"
        assert entry.actor.startswith("worker:")

    def test_a_node_that_is_down_is_tried_again_and_then_given_up_on(
        self, session, analysed_study, make_destination, stub_report, monkeypatch
    ):
        from chester import dicom_send
        from chester.config import settings

        make_destination("PACS", auto_send=True)
        worker.queue_deliveries(session, analysed_study)
        _bind_worker_sessions(monkeypatch, session)
        monkeypatch.setattr(
            dicom_send,
            "send_dataset",
            lambda dataset, **kwargs: (_ for _ in ()).throw(dicom_send.SendFailed("node is down")),
        )

        for attempt in range(1, settings.delivery_max_attempts + 1):
            job_id = worker.claim_delivery(session)
            assert job_id is not None, f"attempt {attempt} was not offered"
            worker.process_delivery(job_id)
            job = session.get(DeliveryJob, job_id)
            assert job.attempt == attempt
            if attempt < settings.delivery_max_attempts:
                assert job.status == "queued"
                assert job.next_attempt_at > utcnow()
                # Due only later, so the loop does not spin on a node that is down.
                assert worker.claim_delivery(session) is None
                job.next_attempt_at = utcnow()
                session.flush()

        assert job.status == "error"
        assert "node is down" in job.error_message
        assert (
            session.query(NetworkLog)
            .filter(NetworkLog.direction == "sent", NetworkLog.status == "failure")
            .count()
            == settings.delivery_max_attempts
        )

    def test_a_study_with_nothing_to_report_is_not_retried(
        self, client, headers, make_dicom, session, make_destination, monkeypatch
    ):
        response = client.post(
            "/api/uploads",
            data={"confirm_deidentified": "true"},
            files=[("files", ("chest.dcm", make_dicom(), "application/dicom"))],
            headers=headers,
        )
        study = session.get(Study, uuid.UUID(response.json()["studies"][0]["id"]))
        make_destination("PACS", auto_send=True)
        worker.queue_deliveries(session, study)
        _bind_worker_sessions(monkeypatch, session)

        job_id = worker.claim_delivery(session)
        worker.process_delivery(job_id)

        job = session.get(DeliveryJob, job_id)
        assert job.status == "error"
        assert "no completed analysis" in job.error_message
        assert session.query(NetworkLog).filter(NetworkLog.direction == "sent").count() == 0

    def test_an_expired_delivery_lease_is_recovered(
        self, session, analysed_study, make_destination
    ):
        from datetime import timedelta

        make_destination("PACS", auto_send=True)
        worker.queue_deliveries(session, analysed_study)
        job_id = worker.claim_delivery(session)
        job = session.get(DeliveryJob, job_id)
        job.lease_expires_at = utcnow() - timedelta(minutes=1)
        session.flush()

        assert worker.recover_expired_leases(session) == 1
        assert job.status == "queued"
        assert job.lease_owner is None
