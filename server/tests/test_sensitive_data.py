"""Who may reveal identifying fields on screen, and who decides."""

from __future__ import annotations

import pytest

from chester import sensitive_data
from chester.models import Organization, SensitiveDataPolicy
from chester.security.roles import (
    ROLE_ADMIN,
    ROLE_CONSULTANT,
    ROLE_RADIOLOGIST,
    ROLE_TECHNICIAN,
)


@pytest.fixture
def administrator(make_user):
    return make_user("chief@example.com", ROLE_ADMIN)


@pytest.fixture
def admin_headers(signed_in, administrator):
    return signed_in("chief@example.com")[0]


@pytest.fixture
def radiologist(make_user):
    return make_user("rad@example.com", ROLE_RADIOLOGIST)


@pytest.fixture
def technician(make_user):
    return make_user("tech@example.com", ROLE_TECHNICIAN)


def test_default_applies_without_a_row(session, organization):
    """An organization that never chose is still governed by a policy."""
    assert sensitive_data.roles_in_force(session, organization.id) == sensitive_data.DEFAULT_ROLES
    assert sensitive_data.may_reveal(session, organization.id, ROLE_RADIOLOGIST)
    assert not sensitive_data.may_reveal(session, organization.id, ROLE_TECHNICIAN)


def test_reading_the_policy_creates_nothing(session, organization):
    """Looking at the page must leave no trace of having looked."""
    sensitive_data.roles_in_force(session, organization.id)
    sensitive_data.may_reveal(session, organization.id, ROLE_TECHNICIAN)
    assert session.query(SensitiveDataPolicy).count() == 0


def test_normalize_drops_admin_and_duplicates(session):
    assert sensitive_data.normalize_roles([ROLE_ADMIN]) == []
    assert sensitive_data.normalize_roles([ROLE_CONSULTANT, ROLE_CONSULTANT, " RADIOLOGIST "]) == [
        ROLE_CONSULTANT,
        ROLE_RADIOLOGIST,
    ]
    assert sensitive_data.normalize_roles(None) == []


def test_normalize_rejects_an_unknown_role(session):
    """A typo must fail loudly rather than quietly narrowing who may reveal."""
    with pytest.raises(ValueError, match="Perfil desconhecido"):
        sensitive_data.normalize_roles(["radiolgist"])


def test_an_administrator_always_reveals(session, organization):
    """Even out of an empty policy: nobody may edit themselves out of this page."""
    sensitive_data.set_roles(session, organization.id, [], actor="chief@example.com")
    assert sensitive_data.roles_in_force(session, organization.id) == frozenset()
    assert sensitive_data.may_reveal(session, organization.id, ROLE_ADMIN)
    assert not sensitive_data.may_reveal(session, organization.id, ROLE_RADIOLOGIST)


def test_set_roles_round_trip(session, organization):
    sensitive_data.set_roles(session, organization.id, [ROLE_TECHNICIAN], actor="chief@example.com")
    assert sensitive_data.roles_in_force(session, organization.id) == frozenset({ROLE_TECHNICIAN})

    policy = sensitive_data.stored_policy(session, organization.id)
    assert policy is not None
    assert policy.updated_by == "chief@example.com"

    # A second call updates the row rather than adding another.
    sensitive_data.set_roles(session, organization.id, [ROLE_CONSULTANT], actor="other@example.com")
    assert session.query(SensitiveDataPolicy).count() == 1
    assert sensitive_data.roles_in_force(session, organization.id) == frozenset({ROLE_CONSULTANT})


def test_policies_do_not_leak_between_organizations(session, organization):
    other = Organization(name="Other Org", slug="other-org")
    session.add(other)
    session.flush()

    sensitive_data.set_roles(session, organization.id, [ROLE_TECHNICIAN], actor="chief@e.com")

    assert sensitive_data.roles_in_force(session, other.id) == sensitive_data.DEFAULT_ROLES
    assert not sensitive_data.may_reveal(session, other.id, ROLE_TECHNICIAN)


def test_get_lists_every_role_with_the_administrator_fixed(client, admin_headers):
    response = client.get("/api/settings/sensitive-data", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["editable"] is True
    assert body["updated_by"] is None

    by_value = {row["value"]: row for row in body["roles"]}
    assert by_value[ROLE_ADMIN] == {
        "value": ROLE_ADMIN,
        "label": "Administrador",
        "allowed": True,
        "selectable": False,
    }
    assert by_value[ROLE_RADIOLOGIST]["allowed"] is True
    assert by_value[ROLE_TECHNICIAN]["allowed"] is False
    assert by_value[ROLE_TECHNICIAN]["selectable"] is True


def test_put_returns_the_whole_policy(client, admin_headers):
    response = client.put(
        "/api/settings/sensitive-data",
        json={"roles": [ROLE_TECHNICIAN]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    by_value = {row["value"]: row for row in response.json()["roles"]}
    assert by_value[ROLE_TECHNICIAN]["allowed"] is True
    assert by_value[ROLE_RADIOLOGIST]["allowed"] is False
    assert by_value[ROLE_ADMIN]["allowed"] is True
    assert response.json()["updated_by"] == "chief@example.com"


def test_put_rejects_an_unknown_role(client, admin_headers):
    response = client.put(
        "/api/settings/sensitive-data",
        json={"roles": ["radiolgist"]},
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert "Perfil desconhecido" in response.json()["detail"]


def test_only_an_administrator_may_change_the_policy(client, signed_in, radiologist):
    headers = signed_in("rad@example.com")[0]

    assert client.get("/api/settings/sensitive-data", headers=headers).status_code == 200

    forbidden = client.put(
        "/api/settings/sensitive-data",
        json={"roles": [ROLE_TECHNICIAN]},
        headers=headers,
    )
    assert forbidden.status_code == 403


def test_the_page_permission_gates_reading(client, signed_in, make_user):
    """A role without the settings page cannot read the policy either."""
    make_user("limited@example.com", ROLE_TECHNICIAN, allowed_pages=["worklist"])
    headers = signed_in("limited@example.com")[0]

    assert client.get("/api/settings/sensitive-data", headers=headers).status_code == 403


def test_sign_in_says_whether_the_caller_may_reveal(client, signed_in, radiologist, technician):
    """The interface must not decide this for itself."""
    _, rad_access = signed_in("rad@example.com")
    assert rad_access["may_reveal_sensitive"] is True

    tech_headers, tech_access = signed_in("tech@example.com")
    assert tech_access["may_reveal_sensitive"] is False

    validated = client.get("/api/auth/validate-session", headers=tech_headers)
    assert validated.json()["access"]["may_reveal_sensitive"] is False


def test_the_flag_follows_the_policy(client, signed_in, admin_headers, technician):
    """Granting a role mid-session is visible on the next validate-session."""
    tech_headers, access = signed_in("tech@example.com")
    assert access["may_reveal_sensitive"] is False

    granted = client.put(
        "/api/settings/sensitive-data",
        json={"roles": [ROLE_TECHNICIAN]},
        headers=admin_headers,
    )
    assert granted.status_code == 200, granted.text

    refreshed = client.get("/api/auth/validate-session", headers=tech_headers)
    assert refreshed.json()["access"]["may_reveal_sensitive"] is True
