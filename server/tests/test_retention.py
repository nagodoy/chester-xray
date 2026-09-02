"""Data retention: how long the network log is kept, and what applies the window."""

from __future__ import annotations

from datetime import timedelta

import pytest

from chester import retention
from chester.models import NetworkLog, Organization, utcnow
from chester.security.roles import ROLE_ADMIN, ROLE_TECHNICIAN


@pytest.fixture
def administrator(make_user):
    return make_user("keeper@example.com", ROLE_ADMIN)


@pytest.fixture
def admin_headers(signed_in, administrator):
    return signed_in("keeper@example.com")[0]


@pytest.fixture
def technician(make_user):
    return make_user("tech@example.com", ROLE_TECHNICIAN)


@pytest.fixture
def technician_headers(signed_in, technician):
    return signed_in("tech@example.com")[0]


@pytest.fixture
def make_entry(session, organization):
    """A network log entry recorded a given number of hours ago."""

    def _make(hours_ago: float, *, org=None) -> NetworkLog:
        entry = NetworkLog(
            organization_id=(org or organization).id,
            direction="received",
            channel="c-store",
            status="success",
            peer="PACS@10.0.0.1:11112",
            created_at=utcnow() - timedelta(hours=hours_ago),
        )
        session.add(entry)
        session.flush()
        return entry

    return _make


def remaining(session, org) -> int:
    return session.query(NetworkLog).filter(NetworkLog.organization_id == org.id).count()


class TestTheWindow:
    def test_the_offered_windows_are_twelve_twenty_four_and_thirty_six_hours(self):
        assert retention.WINDOW_HOURS == (12, 24, 36)

    def test_an_organization_that_never_chose_one_keeps_a_day(self, session, organization):
        hours, last_swept_at = retention.current(session, organization.id)
        assert hours == retention.DEFAULT_HOURS == 24
        assert last_swept_at is None

    def test_reading_the_window_does_not_create_a_row(self, session, organization):
        retention.current(session, organization.id)
        assert retention.stored_policy(session, organization.id) is None

    def test_a_window_outside_the_offered_set_is_refused(self, session, organization):
        with pytest.raises(ValueError):
            retention.set_window(session, organization.id, 1)
        with pytest.raises(ValueError):
            retention.set_window(session, organization.id, 26)


class TestPurging:
    def test_entries_older_than_the_window_go_and_newer_ones_stay(
        self, session, organization, make_entry
    ):
        make_entry(30)
        make_entry(25)
        kept = make_entry(23)

        assert retention.purge(session, organization.id) == 2
        assert [entry.id for entry in session.query(NetworkLog).all()] == [kept.id]

    def test_a_shorter_window_removes_more(self, session, organization, make_entry):
        make_entry(20)
        make_entry(13)
        make_entry(6)

        retention.set_window(session, organization.id, 12)
        assert retention.purge(session, organization.id) == 2
        assert remaining(session, organization) == 1

    def test_the_count_matches_what_the_purge_removes(self, session, organization, make_entry):
        for age in (40, 30, 25, 10, 1):
            make_entry(age)

        expected = retention.count_expired(session, organization.id, 24)
        assert expected == 3
        assert retention.purge(session, organization.id) == expected

    def test_purging_records_when_it_ran(self, session, organization, make_entry):
        make_entry(30)
        before = utcnow()

        retention.purge(session, organization.id)

        policy = retention.stored_policy(session, organization.id)
        assert policy is not None
        assert policy.last_swept_at is not None
        assert policy.last_swept_at >= before

    def test_one_organization_never_sweeps_another(self, session, organization, make_entry):
        other = Organization(name="Other", slug="other-org")
        session.add(other)
        session.flush()
        make_entry(30, org=other)
        make_entry(30)

        assert retention.purge(session, organization.id) == 1
        assert remaining(session, other) == 1


class TestTheRoutine:
    def test_the_sweep_covers_every_organization(self, session, organization, make_entry):
        other = Organization(name="Other", slug="other-org")
        session.add(other)
        session.flush()
        make_entry(30)
        make_entry(30, org=other)
        kept = make_entry(2, org=other)

        assert retention.sweep(session) == 2
        assert [entry.id for entry in session.query(NetworkLog).all()] == [kept.id]

    def test_the_sweep_reaches_an_organization_that_never_chose_a_window(
        self, session, organization, make_entry
    ):
        make_entry(30)
        assert retention.stored_policy(session, organization.id) is None

        assert retention.sweep(session) == 1
        assert remaining(session, organization) == 0

    def test_each_organization_keeps_its_own_window(self, session, organization, make_entry):
        other = Organization(name="Other", slug="other-org")
        session.add(other)
        session.flush()
        retention.set_window(session, organization.id, 12)
        retention.set_window(session, other.id, 36)

        make_entry(20)
        survivor = make_entry(20, org=other)

        assert retention.sweep(session) == 1
        assert [entry.id for entry in session.query(NetworkLog).all()] == [survivor.id]

    def test_the_worker_sweeps_on_its_first_pass_then_waits(self, monkeypatch):
        from chester import worker

        swept: list[int] = []
        monkeypatch.setattr(worker.retention, "sweep", lambda db, **kwargs: swept.append(1) or 1)

        last = worker.sweep_retention(float("-inf"))
        assert swept == [1]

        # A second call moments later is not due yet.
        worker.sweep_retention(last)
        assert swept == [1]

    def test_a_failing_sweep_does_not_stop_the_worker(self, monkeypatch):
        from chester import worker

        def explode(db, **kwargs):
            raise RuntimeError("database is away")

        monkeypatch.setattr(worker.retention, "sweep", explode)

        # Returns the attempt time rather than raising, so the loop carries on
        # and the next interval tries again.
        assert worker.sweep_retention(float("-inf")) > float("-inf")


class TestTheEndpoints:
    def test_the_page_reports_the_window_and_what_it_would_remove(
        self, client, technician_headers, make_entry
    ):
        make_entry(30)
        make_entry(2)

        response = client.get("/api/network-logs/retention", headers=technician_headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["hours"] == 24
        assert body["options"] == [12, 24, 36]
        assert body["expiring"] == 1
        assert body["last_swept_at"] is None

    def test_an_administrator_chooses_the_window(self, client, admin_headers, make_entry):
        make_entry(20)

        response = client.put(
            "/api/network-logs/retention", json={"hours": 12}, headers=admin_headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["hours"] == 12
        assert response.json()["expiring"] == 1

    def test_a_window_the_routine_does_not_offer_is_refused(self, client, admin_headers):
        response = client.put(
            "/api/network-logs/retention", json={"hours": 26}, headers=admin_headers
        )
        assert response.status_code == 400
        assert "12, 24, 36" in response.json()["detail"]

    def test_a_technician_cannot_change_the_window(self, client, technician_headers):
        response = client.put(
            "/api/network-logs/retention", json={"hours": 12}, headers=technician_headers
        )
        assert response.status_code == 403

    def test_a_technician_cannot_purge(self, client, technician_headers):
        response = client.post("/api/network-logs/retention/purge", headers=technician_headers)
        assert response.status_code == 403

    def test_purging_by_hand_reports_what_went(
        self, client, admin_headers, session, organization, make_entry
    ):
        make_entry(30)
        make_entry(28)
        make_entry(1)

        response = client.post("/api/network-logs/retention/purge", headers=admin_headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["deleted"] == 2
        assert body["retention"]["expiring"] == 0
        assert body["retention"]["last_swept_at"] is not None
        assert remaining(session, organization) == 1

    def test_a_purge_is_recorded_in_the_administration_trail(
        self, client, admin_headers, session, make_entry
    ):
        from chester.models import AccessControlAuditLog

        make_entry(30)
        client.post("/api/network-logs/retention/purge", headers=admin_headers)

        entry = (
            session.query(AccessControlAuditLog)
            .filter(AccessControlAuditLog.action == "retention_purge")
            .one()
        )
        assert entry.actor_email == "keeper@example.com"
        assert entry.details["deleted"] == 1
