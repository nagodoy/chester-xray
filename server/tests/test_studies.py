"""Worklist endpoints: visibility, review and retry."""

from __future__ import annotations

import pytest

from chester.models import AnalysisJob, Organization, Study
from chester.security.roles import (
    ROLE_ADMIN,
    ROLE_RADIOLOGIST,
    ROLE_TECHNICIAN,
)


@pytest.fixture
def worklist(session, organization, make_user):
    """Two organizations, three users, three studies."""
    rival = Organization(name="Rival", slug="rival")
    session.add(rival)
    session.flush()

    owner = make_user("owner@example.com", ROLE_TECHNICIAN)
    colleague = make_user("colleague@example.com", ROLE_TECHNICIAN)
    boss = make_user("boss@example.com", ROLE_ADMIN)
    outsider = make_user("outsider@rival.test", ROLE_ADMIN, org=rival)

    mine = Study(
        owner_user_id=owner.id,
        organization_id=organization.id,
        status="completed",
        description="Mine",
    )
    theirs = Study(
        owner_user_id=colleague.id,
        organization_id=organization.id,
        status="needs_review",
        description="Theirs",
    )
    elsewhere = Study(
        owner_user_id=outsider.id,
        organization_id=rival.id,
        status="completed",
        description="Elsewhere",
    )
    session.add_all([mine, theirs, elsewhere])
    session.flush()
    return {
        "owner": owner,
        "colleague": colleague,
        "boss": boss,
        "outsider": outsider,
        "mine": mine,
        "theirs": theirs,
        "elsewhere": elsewhere,
    }


def test_a_technician_sees_only_their_own_studies(client, signed_in, worklist):
    headers, _ = signed_in("owner@example.com")

    body = client.get("/api/studies", headers=headers).json()

    assert [item["description"] for item in body["items"]] == ["Mine"]
    assert body["total"] == 1


def test_an_administrator_sees_the_whole_organization(client, signed_in, worklist):
    headers, _ = signed_in("boss@example.com")

    body = client.get("/api/studies", headers=headers).json()

    assert {item["description"] for item in body["items"]} == {"Mine", "Theirs"}
    assert "Elsewhere" not in {item["description"] for item in body["items"]}


def test_counts_respect_the_same_visibility_rule(client, signed_in, worklist):
    owner_headers, _ = signed_in("owner@example.com")
    owner_counts = client.get("/api/studies", headers=owner_headers).json()["counts"]

    assert owner_counts == {"completed": 1}


def test_another_organizations_study_is_not_found(client, signed_in, worklist):
    """404, not 403: whether it exists elsewhere is not this caller's business."""
    headers, _ = signed_in("boss@example.com")

    response = client.get(f"/api/studies/{worklist['elsewhere'].id}", headers=headers)

    assert response.status_code == 404


def test_a_colleagues_study_is_reachable_by_an_administrator(client, signed_in, worklist):
    headers, _ = signed_in("boss@example.com")

    response = client.get(f"/api/studies/{worklist['theirs'].id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["description"] == "Theirs"


def test_a_technician_cannot_reach_a_colleagues_study(client, signed_in, worklist):
    headers, _ = signed_in("owner@example.com")

    response = client.get(f"/api/studies/{worklist['theirs'].id}", headers=headers)

    assert response.status_code == 404


def test_an_invalid_status_filter_is_rejected(client, signed_in, worklist):
    headers, _ = signed_in("boss@example.com")

    response = client.get("/api/studies?status=nonsense", headers=headers)

    assert response.status_code == 400


class TestReview:
    def test_a_radiologist_can_approve_and_it_queues_analysis(
        self, client, signed_in, worklist, session, make_user
    ):
        make_user("radiologist@example.com", ROLE_RADIOLOGIST)
        headers, _ = signed_in("radiologist@example.com")

        response = client.post(
            f"/api/studies/{worklist['theirs'].id}/review",
            json={"decision": "approve"},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "queued"
        assert session.query(AnalysisJob).filter_by(study_id=worklist["theirs"].id).count() == 1

    def test_rejecting_does_not_queue_analysis(
        self, client, signed_in, worklist, session, make_user
    ):
        make_user("radiologist@example.com", ROLE_RADIOLOGIST)
        headers, _ = signed_in("radiologist@example.com")

        response = client.post(
            f"/api/studies/{worklist['theirs'].id}/review",
            json={"decision": "reject"},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert session.query(AnalysisJob).count() == 0

    def test_a_technician_cannot_review(self, client, signed_in, worklist, make_user):
        """Roles are enforced, not just page permissions.

        The technician can reach the study through the review page permission but
        must not be able to act on it.
        """
        reviewer = worklist["colleague"]
        assert reviewer.role == ROLE_TECHNICIAN
        headers, _ = signed_in("colleague@example.com")

        response = client.post(
            f"/api/studies/{worklist['theirs'].id}/review",
            json={"decision": "approve"},
            headers=headers,
        )

        assert response.status_code == 403

    def test_a_study_not_awaiting_review_cannot_be_reviewed(
        self, client, signed_in, worklist, make_user
    ):
        make_user("radiologist@example.com", ROLE_RADIOLOGIST)
        headers, _ = signed_in("radiologist@example.com")

        response = client.post(
            f"/api/studies/{worklist['mine'].id}/review",
            json={"decision": "approve"},
            headers=headers,
        )

        assert response.status_code == 400

    def test_an_unknown_decision_is_rejected(self, client, signed_in, worklist, make_user):
        make_user("radiologist@example.com", ROLE_RADIOLOGIST)
        headers, _ = signed_in("radiologist@example.com")

        response = client.post(
            f"/api/studies/{worklist['theirs'].id}/review",
            json={"decision": "maybe"},
            headers=headers,
        )

        assert response.status_code == 422


class TestRetry:
    def test_a_failed_study_can_be_retried(self, client, signed_in, worklist, session):
        worklist["mine"].status = "error"
        worklist["mine"].error_message = "boom"
        session.flush()
        headers, _ = signed_in("owner@example.com")

        response = client.post(f"/api/studies/{worklist['mine'].id}/retry", headers=headers)

        assert response.status_code == 200
        assert response.json()["status"] == "queued"
        assert response.json()["error_message"] is None

    def test_a_study_stuck_in_processing_can_be_retried(self, client, signed_in, worklist, session):
        """A worker can die holding a lease; the interface must offer a way out."""
        worklist["mine"].status = "processing"
        session.flush()
        headers, _ = signed_in("owner@example.com")

        response = client.post(f"/api/studies/{worklist['mine'].id}/retry", headers=headers)

        assert response.status_code == 200
        assert response.json()["status"] == "queued"

    def test_a_completed_study_cannot_be_retried(self, client, signed_in, worklist):
        headers, _ = signed_in("owner@example.com")

        response = client.post(f"/api/studies/{worklist['mine'].id}/retry", headers=headers)

        assert response.status_code == 400


def test_the_worklist_requires_a_session(client):
    assert client.get("/api/studies").status_code == 401
