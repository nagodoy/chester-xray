"""Who may reveal identifying fields on screen.

The worklist sits on a screen in a reading room, in a demonstration and in a
shared window, and until now there was no way to hide the identifiers on it
short of signing out. The interface masks them by default and offers a toggle to
reveal them; this decides whose toggle exists.

Two things this is deliberately *not*.

It is not access control. The values are already in the JSON the browser
fetched -- masking happens after the response arrives, so anyone with developer
tools reads them whether the toggle is on or off. Real redaction would mean the
serializer in ``chester.api.studies`` omitting fields per role, which would also
break the search the worklist filters with. What this protects against is a
person looking at someone else's screen.

It is not an audit trail either. Revealing sends no request, so there is nothing
to record; only *changing the policy* touches the server.

Shaped after ``chester.retention``: a row per organization, a missing row meaning
the default, and reads that never write.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from chester.models import SensitiveDataPolicy
from chester.security.roles import REVIEWER_ROLES, ROLE_ADMIN, VALID_ROLES

# The roles that reveal when an organization has never chosen.
#
# The reviewers rather than every role that reads the whole organization: these
# are the roles that already approve or reject a study, so they are the ones with
# a reason to know which person an exam belongs to. A consultant reading the same
# worklist has no such reason by default. One constant to change if a deployment
# disagrees.
DEFAULT_ROLES: frozenset[str] = REVIEWER_ROLES - {ROLE_ADMIN}

# Roles an organization may actually choose between. Administrators are left out
# on purpose -- see `may_reveal`.
SELECTABLE_ROLES: tuple[str, ...] = tuple(role for role in VALID_ROLES if role != ROLE_ADMIN)


def normalize_roles(values: Iterable[str] | None) -> list[str]:
    """Return a de-duplicated list of selectable roles, or raise ValueError.

    An empty list is accepted and means "only administrators". Unknown roles are
    rejected rather than dropped: a typo in an API call should fail loudly, not
    quietly narrow who can reveal.
    """
    roles: list[str] = []
    for value in values or ():
        role = str(value).strip().casefold()
        if role == ROLE_ADMIN:
            # Implicit rather than stored. Accepting it silently would make the
            # panel show a checkbox whose state could not be turned off.
            continue
        if role not in SELECTABLE_ROLES:
            offered = ", ".join(SELECTABLE_ROLES)
            raise ValueError(f"Perfil desconhecido: {value!r}. Escolha entre: {offered}.")
        if role not in roles:
            roles.append(role)
    return roles


def stored_policy(db: Session, organization_id: uuid.UUID) -> SensitiveDataPolicy | None:
    """This organization's row, or None if it has never had one.

    Reads must not create rows, for the reason ``chester.retention.current`` gives:
    an organization that has only ever looked at the page should leave no trace of
    having looked.
    """
    return (
        db.query(SensitiveDataPolicy)
        .filter(SensitiveDataPolicy.organization_id == organization_id)
        .first()
    )


def roles_in_force(db: Session, organization_id: uuid.UUID) -> frozenset[str]:
    """The roles that may reveal for this organization, administrators aside."""
    policy = stored_policy(db, organization_id)
    if policy is None:
        return DEFAULT_ROLES
    return frozenset(policy.roles or ())


def may_reveal(db: Session, organization_id: uuid.UUID, role: str) -> bool:
    """Whether this role may reveal.

    An administrator always may, and that is not stored: the policy is edited from
    the settings page, and a configuration an administrator can edit themselves out
    of is one nobody can put back.
    """
    if role == ROLE_ADMIN:
        return True
    return role in roles_in_force(db, organization_id)


def policy_for(db: Session, organization_id: uuid.UUID) -> SensitiveDataPolicy:
    """This organization's row, creating it with the default if it has none."""
    policy = stored_policy(db, organization_id)
    if policy is None:
        policy = SensitiveDataPolicy(
            organization_id=organization_id,
            roles=sorted(DEFAULT_ROLES),
        )
        db.add(policy)
        db.flush()
    return policy


def set_roles(
    db: Session,
    organization_id: uuid.UUID,
    values: Iterable[str] | None,
    *,
    actor: str,
) -> SensitiveDataPolicy:
    """Choose which roles reveal. Does not commit; the caller owns the transaction."""
    roles = normalize_roles(values)
    policy = policy_for(db, organization_id)
    policy.roles = roles
    policy.updated_by = actor
    db.flush()
    return policy
