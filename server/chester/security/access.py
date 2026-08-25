"""Who may sign in, as what, and what they may see.

Precedence is environment administrator, then an explicit user row, then a domain
rule. An inactive user row is a terminal denial and must never fall through to a
domain rule that would otherwise grant access.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from chester.config import settings
from chester.models import AccessControlAuditLog, AllowedDomain, Organization, User
from chester.security.roles import (
    ROLE_ADMIN,
    can_read_organization,
    can_review,
    email_domain,
    normalize_allowed_pages,
    normalize_email,
    pages_allow,
)

logger = logging.getLogger(__name__)

SOURCE_ENVIRONMENT = "environment"
SOURCE_USER = "user"
SOURCE_DOMAIN = "domain"


@dataclass(frozen=True)
class Grant:
    """An authorization decision that may not yet have a user row behind it.

    A domain rule authorizes an address before that address has ever signed in. The
    row is created when a code is verified, not when one is requested, so an
    unverified request cannot populate the user table.
    """

    email: str
    organization_id: uuid.UUID
    role: str
    allowed_pages: list[str] | None
    source: str
    user: User | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


@dataclass(frozen=True)
class AccessContext:
    """The authenticated caller, resolved for one request."""

    user_id: uuid.UUID
    email: str
    organization_id: uuid.UUID
    role: str
    allowed_pages: list[str] | None
    source: str = SOURCE_USER

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def reads_whole_organization(self) -> bool:
        return can_read_organization(self.role)

    @property
    def may_review(self) -> bool:
        return can_review(self.role)

    def can_access_page(self, page: str) -> bool:
        return self.is_admin or pages_allow(self.allowed_pages, page)

    @classmethod
    def from_user(cls, user: User, source: str = SOURCE_USER) -> AccessContext:
        if user.is_env_admin:
            # Configuration owns these accounts; the stored role and page list are
            # not authoritative for them.
            return cls(
                user_id=user.id,
                email=user.email,
                organization_id=user.organization_id,
                role=ROLE_ADMIN,
                allowed_pages=None,
                source=SOURCE_ENVIRONMENT,
            )
        return cls(
            user_id=user.id,
            email=user.email,
            organization_id=user.organization_id,
            role=user.role,
            allowed_pages=normalize_allowed_pages(user.allowed_pages),
            source=source,
        )


def default_organization(db: Session) -> Organization:
    """Fetch or create the organization new environment admins land in."""
    slug = settings.default_organization_slug
    organization = db.query(Organization).filter(Organization.slug == slug).first()
    if organization is None:
        organization = Organization(name=settings.default_organization_name, slug=slug)
        db.add(organization)
        db.flush()
    return organization


def resolve_grant(db: Session, email: str) -> Grant | None:
    """Decide whether an address may sign in, without creating anything."""
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        return None

    user = db.query(User).filter(User.email == normalized).first()
    configured_admin = normalized in settings.admin_emails

    if user is not None:
        if configured_admin:
            # Configuration outranks the stored row, which startup reconciliation
            # will bring back into line.
            return Grant(
                email=normalized,
                organization_id=user.organization_id,
                role=ROLE_ADMIN,
                allowed_pages=None,
                source=SOURCE_ENVIRONMENT,
                user=user,
            )
        if not user.active:
            return None
        return Grant(
            email=normalized,
            organization_id=user.organization_id,
            role=user.role,
            allowed_pages=normalize_allowed_pages(user.allowed_pages),
            source=SOURCE_USER,
            user=user,
        )

    if configured_admin:
        return Grant(
            email=normalized,
            organization_id=default_organization(db).id,
            role=ROLE_ADMIN,
            allowed_pages=None,
            source=SOURCE_ENVIRONMENT,
        )

    domain = email_domain(normalized)
    candidates = db.query(AllowedDomain).filter(AllowedDomain.active.is_(True)).all()
    matching = [
        rule for rule in candidates if domain == rule.domain or domain.endswith(f".{rule.domain}")
    ]
    if not matching:
        return None

    # The most specific rule wins, so a subdomain rule can narrow a parent one.
    selected = max(matching, key=lambda rule: len(rule.domain))
    return Grant(
        email=normalized,
        organization_id=selected.organization_id,
        role=selected.role,
        allowed_pages=normalize_allowed_pages(selected.allowed_pages),
        source=SOURCE_DOMAIN,
    )


def materialize_user(db: Session, grant: Grant) -> User:
    """Return the user row for a grant, creating it for a first domain sign-in."""
    if grant.user is not None:
        return grant.user

    user = db.query(User).filter(User.email == grant.email).first()
    if user is None:
        user = User(
            email=grant.email,
            organization_id=grant.organization_id,
            role=grant.role,
            allowed_pages=grant.allowed_pages,
            active=True,
            is_env_admin=grant.source == SOURCE_ENVIRONMENT,
            created_by=grant.source,
        )
        db.add(user)
        db.flush()
        logger.info("Created user %s from %s rule", grant.email, grant.source)
    return user


def bootstrap_env_admins(db: Session) -> None:
    """Reconcile ADMIN_USERS against the user table, in both directions.

    Configuration is the source of truth for these accounts: an address added there
    becomes an administrator, and one removed from it loses that access rather than
    silently keeping it.
    """
    configured = set(settings.admin_emails)

    if configured:
        organization = default_organization(db)
        for email in sorted(configured):
            user = db.query(User).filter(User.email == email).first()
            if user is None:
                db.add(
                    User(
                        email=email,
                        organization_id=organization.id,
                        role=ROLE_ADMIN,
                        allowed_pages=None,
                        active=True,
                        is_env_admin=True,
                        created_by=SOURCE_ENVIRONMENT,
                    )
                )
                _audit_environment(db, "bootstrap", email, ROLE_ADMIN)
            elif not user.is_env_admin or user.role != ROLE_ADMIN or not user.active:
                user.role = ROLE_ADMIN
                user.allowed_pages = None
                user.active = True
                user.is_env_admin = True
                _audit_environment(db, "environment_admin_synced", email, ROLE_ADMIN)

    for user in db.query(User).filter(User.is_env_admin.is_(True)).all():
        if user.email in configured:
            continue
        user.active = False
        user.is_env_admin = False
        _audit_environment(db, "environment_admin_revoked", user.email, user.role)

    db.flush()


def _audit_environment(db: Session, action: str, email: str, role: str | None) -> None:
    db.add(
        AccessControlAuditLog(
            actor_email=SOURCE_ENVIRONMENT,
            actor_role=ROLE_ADMIN,
            action=action,
            target_type="user",
            target_key=email,
            target_role=role,
            details={"source": "ADMIN_USERS"},
        )
    )


def visible_studies(query, access: AccessContext):
    """Restrict a Study query to what this caller may see.

    Two rules, in order: never cross an organization boundary, and within the
    caller's own organization show either everything or only their own studies
    depending on their role. The previous implementation had only the second half,
    keyed on an email string, so an administrator could manage who had access but
    could not see any study they had not uploaded themselves.
    """
    from chester.models import Study

    query = query.filter(Study.organization_id == access.organization_id)
    if access.reads_whole_organization:
        return query
    return query.filter(Study.owner_user_id == access.user_id)
