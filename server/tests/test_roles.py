from __future__ import annotations

import pytest

from chester.security import roles


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  Foo@Example.COM ", "foo@example.com"), (None, ""), ("", "")],
)
def test_normalize_email(raw, expected):
    assert roles.normalize_email(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("@Example.com.", "example.com"), (" SUB.example.com ", "sub.example.com")],
)
def test_normalize_domain(raw, expected):
    assert roles.normalize_domain(raw) == expected


def test_normalize_role_rejects_unknown():
    assert roles.normalize_role("ADMIN") == "admin"
    with pytest.raises(ValueError):
        roles.normalize_role("superuser")


def test_normalize_allowed_pages_drops_unknown_and_deduplicates():
    assert roles.normalize_allowed_pages(["worklist", "worklist", "nope"]) == ["worklist"]


def test_normalize_allowed_pages_distinguishes_none_from_empty():
    """None means every page; an all-unknown list must not silently mean 'all'."""
    assert roles.normalize_allowed_pages(None) is None
    assert roles.normalize_allowed_pages([]) is None
    assert roles.normalize_allowed_pages(["nope"]) is None


def test_pages_allow():
    assert roles.pages_allow(None, "settings") is True
    assert roles.pages_allow(["worklist"], "worklist") is True
    assert roles.pages_allow(["worklist"], "settings") is False


def test_only_reviewer_roles_may_review():
    assert roles.can_review(roles.ROLE_RADIOLOGIST)
    assert roles.can_review(roles.ROLE_ADMIN)
    assert not roles.can_review(roles.ROLE_TECHNICIAN)


def test_organization_readers_exclude_technicians():
    assert roles.can_read_organization(roles.ROLE_ADMIN)
    assert not roles.can_read_organization(roles.ROLE_TECHNICIAN)
