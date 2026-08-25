"""Administrator-only access management."""

from __future__ import annotations

import pytest

from chester.models import AllowedDomain, User
from chester.security.roles import ROLE_ADMIN, ROLE_RADIOLOGIST, ROLE_TECHNICIAN


@pytest.fixture
def admin(client, signed_in, make_user):
    make_user("boss@example.com", ROLE_ADMIN)
    headers, _ = signed_in("boss@example.com")
    return headers


def test_only_administrators_may_manage_access(client, signed_in, make_user):
    make_user("tech@example.com", ROLE_TECHNICIAN)
    headers, _ = signed_in("tech@example.com")

    assert client.get("/api/access-control/users", headers=headers).status_code == 403


def test_managing_access_requires_a_session(client):
    assert client.get("/api/access-control/users").status_code == 401


class TestUsers:
    def test_creating_and_listing(self, client, admin):
        created = client.post(
            "/api/access-control/users",
            json={"email": " New.User@Example.COM ", "role": "radiologist"},
            headers=admin,
        )

        assert created.status_code == 201
        assert created.json()["email"] == "new.user@example.com"
        assert created.json()["role_label"] == "Radiologista"

        listed = client.get("/api/access-control/users", headers=admin).json()
        assert "new.user@example.com" in {item["email"] for item in listed}

    def test_a_duplicate_email_is_refused(self, client, admin, make_user):
        make_user("taken@example.com", ROLE_TECHNICIAN)

        response = client.post(
            "/api/access-control/users",
            json={"email": "taken@example.com", "role": "technician"},
            headers=admin,
        )
        assert response.status_code == 409

    @pytest.mark.parametrize(
        "payload",
        [
            {"email": "not-an-email", "role": "technician"},
            {"email": "ok@example.com", "role": "superuser"},
        ],
    )
    def test_invalid_input_is_refused(self, client, admin, payload):
        assert (
            client.post("/api/access-control/users", json=payload, headers=admin).status_code == 400
        )

    def test_updating_role_and_pages(self, client, admin, make_user, session):
        user = make_user("target@example.com", ROLE_TECHNICIAN)

        response = client.patch(
            f"/api/access-control/users/{user.id}",
            json={"role": "radiologist", "allowed_pages": ["worklist", "nope"]},
            headers=admin,
        )

        assert response.status_code == 200
        assert response.json()["role"] == ROLE_RADIOLOGIST
        assert response.json()["allowed_pages"] == ["worklist"]

    def test_an_environment_admin_cannot_be_edited(self, client, admin, make_user, session):
        """Configuration owns those accounts; the interface must not fight it."""
        managed = make_user("env@example.com", ROLE_ADMIN, is_env_admin=True)

        response = client.patch(
            f"/api/access-control/users/{managed.id}",
            json={"active": False},
            headers=admin,
        )
        assert response.status_code == 409

    def test_an_administrator_cannot_demote_themselves(self, client, signed_in, make_user, session):
        make_user("boss@example.com", ROLE_ADMIN)
        make_user("second@example.com", ROLE_ADMIN)
        headers, _ = signed_in("boss@example.com")
        me = session.query(User).filter_by(email="boss@example.com").one()

        response = client.patch(
            f"/api/access-control/users/{me.id}", json={"role": "technician"}, headers=headers
        )
        assert response.status_code == 409

    def test_the_last_administrator_cannot_be_removed(self, client, signed_in, make_user, session):
        """Otherwise the installation locks itself out of its own administration."""
        make_user("boss@example.com", ROLE_ADMIN)
        other = make_user("other@example.com", ROLE_ADMIN)
        headers, _ = signed_in("boss@example.com")

        # Removing one of two is fine.
        assert (
            client.delete(f"/api/access-control/users/{other.id}", headers=headers).status_code
            == 200
        )

        me = session.query(User).filter_by(email="boss@example.com").one()
        assert (
            client.delete(f"/api/access-control/users/{me.id}", headers=headers).status_code == 409
        )

    def test_removal_deactivates_rather_than_deletes(self, client, admin, make_user, session):
        """Studies reference their owner, so the row has to survive."""
        user = make_user("leaving@example.com", ROLE_TECHNICIAN)

        assert (
            client.delete(f"/api/access-control/users/{user.id}", headers=admin).status_code == 200
        )

        assert session.query(User).filter_by(email="leaving@example.com").one().active is False

    def test_another_organizations_user_is_not_found(self, client, admin, make_user, session):
        from chester.models import Organization

        rival = Organization(name="Rival", slug="rival")
        session.add(rival)
        session.flush()
        outsider = make_user("outsider@rival.test", ROLE_TECHNICIAN, org=rival)

        response = client.patch(
            f"/api/access-control/users/{outsider.id}", json={"active": False}, headers=admin
        )
        assert response.status_code == 404


class TestDomains:
    def test_creating_and_listing(self, client, admin):
        created = client.post(
            "/api/access-control/domains",
            json={"domain": "@Example.COM.", "role": "consultant"},
            headers=admin,
        )

        assert created.status_code == 201
        assert created.json()["domain"] == "example.com"

        listed = client.get("/api/access-control/domains", headers=admin).json()
        assert listed[0]["domain"] == "example.com"

    def test_an_invalid_domain_is_refused(self, client, admin):
        response = client.post(
            "/api/access-control/domains", json={"domain": "nodot"}, headers=admin
        )
        assert response.status_code == 400

    def test_a_duplicate_domain_is_refused(self, client, admin, session, organization):
        session.add(
            AllowedDomain(
                domain="example.com", organization_id=organization.id, role=ROLE_TECHNICIAN
            )
        )
        session.flush()

        response = client.post(
            "/api/access-control/domains", json={"domain": "example.com"}, headers=admin
        )
        assert response.status_code == 409

    def test_deleting_a_domain(self, client, admin, session, organization):
        rule = AllowedDomain(
            domain="example.com", organization_id=organization.id, role=ROLE_TECHNICIAN
        )
        session.add(rule)
        session.flush()

        assert (
            client.delete(f"/api/access-control/domains/{rule.id}", headers=admin).status_code
            == 200
        )
        assert session.query(AllowedDomain).count() == 0


def test_every_change_is_audited(client, admin, make_user):
    client.post(
        "/api/access-control/users",
        json={"email": "audited@example.com", "role": "technician"},
        headers=admin,
    )

    audit = client.get("/api/access-control/audit", headers=admin).json()

    entry = next(item for item in audit if item["target_key"] == "audited@example.com")
    assert entry["action"] == "create"
    assert entry["actor_email"] == "boss@example.com"


def test_metadata_lists_roles_and_pages(client, admin):
    body = client.get("/api/access-control/metadata", headers=admin).json()

    assert {"value": "admin", "label": "Administrador"} in body["roles"]
    assert any(page["value"] == "worklist" for page in body["pages"])
