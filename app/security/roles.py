"""Roles and page permission normalization for the application."""
from __future__ import annotations

from typing import Iterable, Optional

VALID_ROLES = (
    "admin",
    "technician",
    "radiologist",
    "consultor",
    "validador_tecnico",
    "validador_radiologista",
)

ROLE_LABELS = {
    "admin": "Administrador",
    "technician": "Técnico",
    "radiologist": "Radiologista",
    "consultor": "Consultor",
    "validador_tecnico": "Validador técnico",
    "validador_radiologista": "Validador radiologista",
}

PAGE_LABELS = {
    "worklist": "Todos os estudos",
    "review": "Requer revisão",
    "study-detail": "Detalhe do estudo",
    "upload": "Envio de estudos",
    "settings": "Ajustes DICOMweb",
    "access-control": "Controle de acesso",
}


def normalize_email(value: str | None) -> str:
    return (value or "").strip().casefold()


def normalize_domain(value: str | None) -> str:
    domain = (value or "").strip().casefold().lstrip("@").rstrip(".")
    return domain


def normalize_role(value: str | None) -> str:
    role = (value or "").strip().casefold()
    if role not in VALID_ROLES:
        raise ValueError("Invalid role")
    return role


def normalize_allowed_pages(value: Iterable[str] | None) -> Optional[list[str]]:
    if value is None:
        return None
    known = []
    for page in value:
        page = str(page).strip().casefold()
        if page in PAGE_LABELS and page not in known:
            known.append(page)
    return known or None


def email_domain(email: str) -> str:
    return normalize_email(email).rsplit("@", 1)[-1]


def pages_allow(allowed_pages: Optional[list[str]], page: str) -> bool:
    return allowed_pages is None or page in (allowed_pages or [])