"""Access resolution, administrator reconciliation and study visibility."""

from __future__ import annotations

import pytest

from chester.models import AllowedDomain, Organization, Study, User
from chester.security.access import (
    SOURCE_DOMAIN,
    SOURCE_ENVIRONMENT,
    SOURCE_USER,
    AccessContext,
    bootstrap_env_admins,
    materialize_user,
    resolve_grant,
    visible_studies,
)
from chester.security.roles import (
    ROLE_ADMIN,
    ROLE_CONSULTANT,
    ROLE_RADIOLOGIST,
    ROLE_TECHNICIAN,
)


@pytest.fixture
def env_admins(monkeypatch):
    """Set ADMIN_USERS, which is a cached property on the settings object."""

    def _set(*emails: str):
        from chester.config import settings

        monkeypatch.setattr(settings, "admin_users", ",".join(emails), raising=False)
        monkeypatch.setattr(type(settings), "admin_emails", property(lambda _self: tuple(emails)))

    return _set


class TestResolution:
    def test_an_explicit_user_row_wins_over_a_domain_rule(self, session, organization, make_user):
        session.add(
            AllowedDomain(
                domain="example.com",
                organization_id=organization.id,
                role=ROLE_CONSULTANT,
                allowed_pages=["worklist"],
                active=True,
            )
        )
        make_user("reader@example.com", ROLE_TECHNICIAN, allowed_pages=[])
        session.flush()

        direct = resolve_grant(session, "  READER@Example.COM ")
        by_domain = resolve_grant(session, "other@example.com")

        assert direct.role == ROLE_TECHNICIAN
        assert direct.source == SOURCE_USER
        # An empty page list is not a restriction to nothing; it means "all pages".
        assert direct.allowed_pages is None
        assert by_domain.role == ROLE_CONSULTANT
        assert by_domain.source == SOURCE_DOMAIN
        assert by_domain.allowed_pages == ["worklist"]

    def test_an_inactive_user_is_denied_and_does_not_fall_through(
        self, session, organization, make_user
    ):
        """A deactivated account must not be rescued by a domain rule."""
        session.add(
            AllowedDomain(
                domain="example.com",
                organization_id=organization.id,
                role=ROLE_ADMIN,
                active=True,
            )
        )
        make_user("blocked@example.com", ROLE_ADMIN, active=False)
        session.flush()

        assert resolve_grant(session, "blocked@example.com") is None

    def test_an_inactive_domain_rule_grants_nothing(self, session, organization):
        session.add(
            AllowedDomain(
                domain="example.com",
                organization_id=organization.id,
                role=ROLE_TECHNICIAN,
                active=False,
            )
        )
        session.flush()

        assert resolve_grant(session, "someone@example.com") is None

    def test_the_most_specific_domain_rule_wins(self, session, organization):
        other = Organization(name="Sub", slug="sub")
        session.add(other)
        session.flush()
        session.add_all(
            [
                AllowedDomain(
                    domain="example.com",
                    organization_id=organization.id,
                    role=ROLE_TECHNICIAN,
                    active=True,
                ),
                AllowedDomain(
                    domain="research.example.com",
                    organization_id=other.id,
                    role=ROLE_RADIOLOGIST,
                    active=True,
                ),
            ]
        )
        session.flush()

        grant = resolve_grant(session, "someone@research.example.com")

        assert grant.role == ROLE_RADIOLOGIST
        assert grant.organization_id == other.id

    def test_unknown_and_malformed_addresses_are_denied(self, session):
        assert resolve_grant(session, "nobody@elsewhere.test") is None
        assert resolve_grant(session, "not-an-email") is None
        assert resolve_grant(session, "") is None
        assert resolve_grant(session, None) is None

    def test_a_domain_grant_creates_no_user_until_it_is_materialized(self, session, organization):
        session.add(
            AllowedDomain(
                domain="example.com",
                organization_id=organization.id,
                role=ROLE_TECHNICIAN,
                active=True,
            )
        )
        session.flush()

        grant = resolve_grant(session, "newcomer@example.com")
        assert grant.user is None
        assert session.query(User).filter_by(email="newcomer@example.com").count() == 0

        user = materialize_user(session, grant)
        assert user.organization_id == organization.id
        assert user.role == ROLE_TECHNICIAN
        assert user.created_by == SOURCE_DOMAIN


class TestEnvironmentAdmins:
    def test_a_configured_admin_is_created_and_marked(self, session, env_admins):
        env_admins("boss@example.test")

        bootstrap_env_admins(session)

        user = session.query(User).filter_by(email="boss@example.test").one()
        assert user.is_env_admin and user.active and user.role == ROLE_ADMIN

    def test_an_admin_removed_from_configuration_loses_access(self, session, make_user, env_admins):
        """Configuration owns these accounts in both directions."""
        former = make_user("former@example.test", ROLE_ADMIN, is_env_admin=True)
        env_admins()

        bootstrap_env_admins(session)

        assert former.is_env_admin is False
        assert former.active is False

    def test_reconciliation_is_idempotent(self, session, env_admins):
        env_admins("boss@example.test")

        bootstrap_env_admins(session)
        bootstrap_env_admins(session)

        assert session.query(User).filter_by(email="boss@example.test").count() == 1

    def test_configuration_outranks_a_downgraded_row(self, session, make_user, env_admins):
        user = make_user("boss@example.test", ROLE_TECHNICIAN, is_env_admin=True)
        env_admins("boss@example.test")

        grant = resolve_grant(session, "boss@example.test")

        assert grant.role == ROLE_ADMIN
        assert grant.source == SOURCE_ENVIRONMENT
        assert AccessContext.from_user(user).is_admin


class TestStudyVisibility:
    @pytest.fixture
    def two_organizations(self, session, organization, make_user):
        rival = Organization(name="Rival", slug="rival")
        session.add(rival)
        session.flush()

        mine = make_user("owner@example.com", ROLE_TECHNICIAN)
        colleague = make_user("colleague@example.com", ROLE_TECHNICIAN)
        outsider = make_user("outsider@rival.test", ROLE_ADMIN, org=rival)

        session.add_all(
            [
                Study(owner_user_id=mine.id, organization_id=organization.id, status="completed"),
                Study(
                    owner_user_id=colleague.id,
                    organization_id=organization.id,
                    status="completed",
                ),
                Study(owner_user_id=outsider.id, organization_id=rival.id, status="completed"),
            ]
        )
        session.flush()
        return {
            "org": organization,
            "rival": rival,
            "mine": mine,
            "colleague": colleague,
            "outsider": outsider,
        }

    def _visible(self, session, user, role):
        access = AccessContext(
            user_id=user.id,
            email=user.email,
            organization_id=user.organization_id,
            role=role,
            allowed_pages=None,
        )
        return visible_studies(session.query(Study), access).all()

    def test_a_technician_sees_only_their_own_studies(self, session, two_organizations):
        visible = self._visible(session, two_organizations["mine"], ROLE_TECHNICIAN)

        assert len(visible) == 1
        assert visible[0].owner_user_id == two_organizations["mine"].id

    def test_an_administrator_sees_the_whole_organization(self, session, two_organizations):
        """The gap that motivated the rewrite: admins could manage access but see nothing."""
        visible = self._visible(session, two_organizations["mine"], ROLE_ADMIN)

        assert len(visible) == 2
        assert {study.organization_id for study in visible} == {two_organizations["org"].id}

    def test_an_organization_reader_never_crosses_the_boundary(self, session, two_organizations):
        visible = self._visible(session, two_organizations["outsider"], ROLE_ADMIN)

        assert len(visible) == 1
        assert visible[0].organization_id == two_organizations["rival"].id

    def test_a_radiologist_reads_the_organization_but_a_technician_does_not(
        self, session, two_organizations
    ):
        assert len(self._visible(session, two_organizations["mine"], ROLE_RADIOLOGIST)) == 2
        assert len(self._visible(session, two_organizations["mine"], ROLE_TECHNICIAN)) == 1
