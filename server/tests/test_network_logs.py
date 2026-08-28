"""The network log: what arrived and from where, what was sent and whether it landed."""

from __future__ import annotations

import pytest

from chester.models import NetworkLog
from chester.security.roles import ROLE_ADMIN, ROLE_TECHNICIAN

TOKEN = "test-dicom-ingest-token"


@pytest.fixture
def operator(make_user):
    return make_user("operator@example.com", ROLE_ADMIN)


@pytest.fixture
def headers(signed_in, operator):
    return signed_in("operator@example.com")[0]


@pytest.fixture
def upload(client, headers):
    def _upload(files):
        return client.post(
            "/api/uploads",
            data={"confirm_deidentified": "true"},
            files=files,
            headers=headers,
        )

    return _upload


def logs(session, direction: str) -> list[NetworkLog]:
    return (
        session.query(NetworkLog)
        .filter(NetworkLog.direction == direction)
        .order_by(NetworkLog.created_at)
        .all()
    )


class TestReceiving:
    def test_an_upload_records_where_it_came_from(self, upload, make_dicom, session):
        response = upload([("files", ("chest.dcm", make_dicom(), "application/dicom"))])
        assert response.status_code == 200, response.text

        entry = logs(session, "received")[-1]
        assert entry.channel == "upload"
        assert entry.status == "success"
        assert entry.peer  # the client address, whatever the transport reports
        assert entry.actor == "operator@example.com"
        assert str(entry.study_id) == response.json()["studies"][0]["id"]
        assert entry.detail["filename"] == "chest.dcm"

    def test_the_same_bytes_twice_are_recorded_as_a_duplicate(self, upload, make_dicom, session):
        data = make_dicom()
        upload([("files", ("chest.dcm", data, "application/dicom"))])
        upload([("files", ("again.dcm", data, "application/dicom"))])

        assert [entry.status for entry in logs(session, "received")] == ["success", "duplicate"]

    def test_a_refused_file_is_recorded_with_its_reason(self, upload, session):
        response = upload([("files", ("broken.png", b"not an image", "image/png"))])
        assert response.status_code == 200, response.text

        entry = logs(session, "received")[-1]
        assert entry.status == "failure"
        assert entry.message

    def test_stow_and_a_forwarded_c_store_are_told_apart(
        self, client, make_stow_body, make_dicom, operator, session
    ):
        def post(payload, extra=None):
            body, content_type = make_stow_body([payload])
            request_headers = {
                "Content-Type": content_type,
                "X-DICOM-Ingest-Key": TOKEN,
                "X-Worklist-Owner": operator.email,
            }
            request_headers.update(extra or {})
            return client.post("/dicomweb/studies", content=body, headers=request_headers)

        assert post(make_dicom()).status_code in (200, 202)
        assert post(make_dicom(), {"X-Ingest-Source": "c-store"}).status_code in (200, 202)

        assert [entry.channel for entry in logs(session, "received")] == ["stow-rs", "c-store"]


class TestReading:
    def test_the_page_lists_the_organizations_own_traffic_only(
        self, client, headers, upload, make_dicom, session, operator
    ):
        from chester.models import Organization

        other = Organization(name="Other", slug="other-org")
        session.add(other)
        session.flush()
        session.add(
            NetworkLog(
                organization_id=other.id,
                direction="received",
                channel="upload",
                status="success",
                peer="10.0.0.9",
            )
        )
        session.flush()

        upload([("files", ("chest.dcm", make_dicom(), "application/dicom"))])

        response = client.get("/api/network-logs?direction=received", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["channel"] == "upload"
        assert body["items"][0]["peer"] != "10.0.0.9"

    def test_a_direction_the_log_does_not_have_is_refused(self, client, headers):
        response = client.get("/api/network-logs?direction=sideways", headers=headers)
        assert response.status_code == 400

    def test_the_page_permission_gates_the_read(self, client, signed_in, make_user):
        make_user("narrow@example.com", ROLE_TECHNICIAN, allowed_pages=["worklist"])
        narrow_headers, _ = signed_in("narrow@example.com")

        response = client.get("/api/network-logs?direction=received", headers=narrow_headers)
        assert response.status_code == 403


@pytest.fixture
def sendable(upload, make_dicom):
    """A study to send a report for. The report itself is stubbed per test."""
    response = upload([("files", ("chest.dcm", make_dicom(), "application/dicom"))])
    assert response.status_code == 200, response.text
    return response.json()["studies"][0]["id"]


@pytest.fixture
def stub_report(monkeypatch):
    """Stand in for the built instance; building it is covered by test_report."""
    from pydicom.dataset import Dataset

    from chester import report_delivery

    dataset = Dataset()
    dataset.SOPInstanceUID = "1.2.826.0.1.3680043.10.1337.1"
    monkeypatch.setattr(report_delivery, "build_for_study", lambda *a, **k: dataset)
    return dataset


class TestSending:
    def test_a_study_with_no_analysis_is_refused_and_logs_nothing(
        self, client, headers, sendable, session
    ):
        response = client.post(f"/api/studies/{sendable}/send-report", headers=headers)

        assert response.status_code == 400
        assert logs(session, "sent") == []

    def test_a_refused_destination_answers_502_and_is_recorded(
        self, client, headers, sendable, session, stub_report, monkeypatch
    ):
        from chester import dicom_send

        def refuse(dataset, **kwargs):
            raise dicom_send.SendFailed("medfusion refused the association")

        monkeypatch.setattr(dicom_send, "send_dataset", refuse)

        response = client.post(f"/api/studies/{sendable}/send-report", headers=headers)
        assert response.status_code == 502
        assert "refused" in response.json()["detail"]

        entry = logs(session, "sent")[-1]
        assert entry.status == "failure"
        assert entry.channel == "c-store"
        assert entry.message == "medfusion refused the association"
        assert str(entry.study_id) == sendable
        assert entry.peer  # the configured destination, as an operator reads it

    def test_a_destination_that_cannot_be_reached_is_recorded_too(
        self, client, headers, sendable, session, stub_report, monkeypatch
    ):
        """The store fails below pynetdicom -- an unresolvable host, a closed port.

        Nothing in that path raises SendFailed, and a delivery that never left is
        exactly what the log is for, so it must not escape as an unhandled error.
        """
        import socket

        from chester import dicom_send

        def unreachable(dataset, **kwargs):
            raise socket.gaierror(-2, "Name or service not known")

        monkeypatch.setattr(dicom_send, "send_dataset", unreachable)

        response = client.post(f"/api/studies/{sendable}/send-report", headers=headers)
        assert response.status_code == 502

        entry = logs(session, "sent")[-1]
        assert entry.status == "failure"
        assert "Name or service not known" in entry.message

    def test_a_delivered_report_is_recorded_as_a_success(
        self, client, headers, sendable, session, stub_report, monkeypatch
    ):
        from chester import dicom_send

        monkeypatch.setattr(dicom_send, "send_dataset", lambda dataset, **kwargs: None)

        response = client.post(f"/api/studies/{sendable}/send-report", headers=headers)
        assert response.status_code == 200, response.text

        entry = logs(session, "sent")[-1]
        assert entry.status == "success"
        assert entry.actor == "operator@example.com"
        assert entry.reference == stub_report.SOPInstanceUID
