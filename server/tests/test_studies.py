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


class TestDeletion:
    """Deleting a study, one at a time and in a batch."""

    def test_an_administrator_deletes_a_study_in_their_organization(
        self, client, signed_in, worklist, session
    ):
        headers, _ = signed_in("boss@example.com")
        study_id = str(worklist["mine"].id)

        response = client.delete(f"/api/studies/{study_id}", headers=headers)

        assert response.status_code == 204
        assert session.query(Study).filter(Study.id == worklist["mine"].id).first() is None

    def test_deletion_takes_the_instances_jobs_and_results_with_it(
        self, client, signed_in, worklist, session
    ):
        from chester.models import AnalysisResult, Instance

        study = worklist["mine"]
        session.add(Instance(study_id=study.id, organization_id=study.organization_id))
        job = AnalysisJob(study_id=study.id, status="completed")
        session.add(job)
        session.flush()
        session.add(AnalysisResult(study_id=study.id, job_id=job.id))
        session.flush()
        headers, _ = signed_in("boss@example.com")

        client.delete(f"/api/studies/{study.id}", headers=headers)

        assert session.query(Instance).filter_by(study_id=study.id).count() == 0
        assert session.query(AnalysisJob).filter_by(study_id=study.id).count() == 0
        assert session.query(AnalysisResult).filter_by(study_id=study.id).count() == 0

    def test_the_deletion_outlives_the_study_in_the_audit_trail(
        self, client, signed_in, worklist, session
    ):
        """The study's own events cascade away, so one study-less event replaces them."""
        from chester.models import AuditEvent

        study_id = str(worklist["mine"].id)
        headers, _ = signed_in("boss@example.com")

        client.delete(f"/api/studies/{study_id}", headers=headers)

        event = session.query(AuditEvent).filter_by(event_type="study_deleted").one()
        assert event.study_id is None
        assert event.actor == "boss@example.com"
        assert event.detail["study_id"] == study_id

    def test_a_technician_may_not_delete_even_their_own_study(
        self, client, signed_in, worklist, session
    ):
        headers, _ = signed_in("owner@example.com")

        response = client.delete(f"/api/studies/{worklist['mine'].id}", headers=headers)

        assert response.status_code == 403
        assert session.query(Study).filter(Study.id == worklist["mine"].id).first() is not None

    def test_an_administrator_cannot_reach_another_organizations_study(
        self, client, signed_in, worklist, session
    ):
        """404 rather than 403: its existence is not this caller's business."""
        headers, _ = signed_in("boss@example.com")

        response = client.delete(f"/api/studies/{worklist['elsewhere'].id}", headers=headers)

        assert response.status_code == 404
        assert session.query(Study).filter(Study.id == worklist["elsewhere"].id).first() is not None

    def test_a_batch_deletes_every_study_named(self, client, signed_in, worklist, session):
        headers, _ = signed_in("boss@example.com")
        ids = [str(worklist["mine"].id), str(worklist["theirs"].id)]

        body = client.post("/api/studies/bulk-delete", json={"ids": ids}, headers=headers).json()

        assert sorted(body["deleted"]) == sorted(ids)
        assert body["not_found"] == []
        assert body["errors"] == []
        assert session.query(Study).count() == 1  # only the rival organization's

    def test_a_batch_reports_what_it_could_not_reach_and_still_deletes_the_rest(
        self, client, signed_in, worklist, session
    ):
        """One unreachable id must not strand the others."""
        import uuid as uuid_module

        headers, _ = signed_in("boss@example.com")
        missing = str(uuid_module.uuid4())
        outside = str(worklist["elsewhere"].id)

        body = client.post(
            "/api/studies/bulk-delete",
            json={"ids": [str(worklist["mine"].id), missing, outside]},
            headers=headers,
        ).json()

        assert body["deleted"] == [str(worklist["mine"].id)]
        assert sorted(body["not_found"]) == sorted([missing, outside])
        assert session.query(Study).filter(Study.id == worklist["elsewhere"].id).first() is not None

    def test_a_batch_names_a_study_once_however_often_it_is_listed(
        self, client, signed_in, worklist
    ):
        headers, _ = signed_in("boss@example.com")
        study_id = str(worklist["mine"].id)

        body = client.post(
            "/api/studies/bulk-delete",
            json={"ids": [study_id, study_id]},
            headers=headers,
        ).json()

        assert body["deleted"] == [study_id]
        assert body["not_found"] == []

    def test_a_technician_may_not_delete_a_batch(self, client, signed_in, worklist, session):
        headers, _ = signed_in("owner@example.com")

        response = client.post(
            "/api/studies/bulk-delete",
            json={"ids": [str(worklist["mine"].id)]},
            headers=headers,
        )

        assert response.status_code == 403
        assert session.query(Study).filter(Study.id == worklist["mine"].id).first() is not None

    def test_an_empty_batch_is_refused_rather_than_silently_doing_nothing(
        self, client, signed_in, worklist
    ):
        headers, _ = signed_in("boss@example.com")

        response = client.post("/api/studies/bulk-delete", json={"ids": []}, headers=headers)

        assert response.status_code == 422

    def test_the_pixel_data_and_thumbnail_go_with_the_study(
        self, client, signed_in, worklist, session
    ):
        """The point of the delete: the bytes leave, not just the rows.

        Rows alone would leave the images on disk with nothing pointing at
        them -- gone from the interface, still there on the volume.
        """
        import pytest as pytest_module

        from chester.models import Instance
        from chester.storage import ObjectNotFound, retrieve_bytes, store_bytes

        study = worklist["mine"]
        pixels = b"\x00DICM-pixels"
        thumbnail = b"\x89PNG-thumbnail"
        store_bytes(f"instances/{study.id}.dcm", pixels, session=session)
        store_bytes(f"thumbnails/{study.id}.png", thumbnail, session=session)
        session.add(
            Instance(
                study_id=study.id,
                organization_id=study.organization_id,
                object_key=f"instances/{study.id}.dcm",
            )
        )
        session.flush()
        # Both are readable before the delete, or the assertions below prove nothing.
        assert retrieve_bytes(f"instances/{study.id}.dcm", session=session) == pixels
        assert retrieve_bytes(f"thumbnails/{study.id}.png", session=session) == thumbnail

        headers, _ = signed_in("boss@example.com")
        response = client.delete(f"/api/studies/{study.id}", headers=headers)

        assert response.status_code == 204
        with pytest_module.raises(ObjectNotFound):
            retrieve_bytes(f"instances/{study.id}.dcm", session=session)
        with pytest_module.raises(ObjectNotFound):
            retrieve_bytes(f"thumbnails/{study.id}.png", session=session)

    def test_a_study_with_no_thumbnail_yet_still_deletes(self, client, signed_in, worklist):
        """An object that was never written is not a failure."""
        headers, _ = signed_in("boss@example.com")

        response = client.delete(f"/api/studies/{worklist['theirs'].id}", headers=headers)

        assert response.status_code == 204
