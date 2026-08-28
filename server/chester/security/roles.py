"""Roles, page permissions and the normalization rules they depend on."""

from __future__ import annotations

from collections.abc import Iterable

ROLE_ADMIN = "admin"
ROLE_TECHNICIAN = "technician"
ROLE_RADIOLOGIST = "radiologist"
ROLE_CONSULTANT = "consultant"
ROLE_TECHNICAL_VALIDATOR = "technical_validator"
ROLE_RADIOLOGY_VALIDATOR = "radiology_validator"

ROLE_LABELS: dict[str, str] = {
    ROLE_ADMIN: "Administrador",
    ROLE_TECHNICIAN: "Técnico",
    ROLE_RADIOLOGIST: "Radiologista",
    ROLE_CONSULTANT: "Consultor",
    ROLE_TECHNICAL_VALIDATOR: "Validador técnico",
    ROLE_RADIOLOGY_VALIDATOR: "Validador radiologista",
}

VALID_ROLES: tuple[str, ...] = tuple(ROLE_LABELS)

# Roles permitted to approve or reject a study held for review.
REVIEWER_ROLES: frozenset[str] = frozenset({ROLE_ADMIN, ROLE_RADIOLOGIST, ROLE_RADIOLOGY_VALIDATOR})

# Roles that may read every study in their own organization rather than only their
# own. This is what lets an administrator actually administer.
ORG_READER_ROLES: frozenset[str] = frozenset(
    {ROLE_ADMIN, ROLE_RADIOLOGIST, ROLE_RADIOLOGY_VALIDATOR, ROLE_CONSULTANT}
)

PAGE_LABELS: dict[str, str] = {
    "worklist": "Todos os estudos",
    "review": "Requer revisão",
    "study-detail": "Detalhe do estudo",
    "upload": "Envio de estudos",
    "settings": "Ajustes DICOMweb",
    "network-logs": "Logs de rede",
    "access-control": "Controle de acesso",
}

VALID_PAGES: tuple[str, ...] = tuple(PAGE_LABELS)


def normalize_email(value: str | None) -> str:
    return (value or "").strip().casefold()


def normalize_domain(value: str | None) -> str:
    return (value or "").strip().casefold().lstrip("@").rstrip(".")


def normalize_role(value: str | None) -> str:
    role = (value or "").strip().casefold()
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {value!r}")
    return role


def normalize_allowed_pages(value: Iterable[str] | None) -> list[str] | None:
    """Return a de-duplicated list of known pages, or None meaning 'all pages'."""
    if value is None:
        return None
    known: list[str] = []
    for page in value:
        candidate = str(page).strip().casefold()
        if candidate in PAGE_LABELS and candidate not in known:
            known.append(candidate)
    return known or None


def email_domain(email: str) -> str:
    return normalize_email(email).rsplit("@", 1)[-1]


def pages_allow(allowed_pages: list[str] | None, page: str) -> bool:
    """None means every page; a list restricts to its members."""
    return allowed_pages is None or page in allowed_pages


def can_read_organization(role: str) -> bool:
    return role in ORG_READER_ROLES


def can_review(role: str) -> bool:
    return role in REVIEWER_ROLES
