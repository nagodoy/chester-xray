"""The nodes a generated report is stored on.

A destination used to be four environment variables, which made one address per
deployment and a redeploy to change it. Destinations are rows now; the
environment is what a site starts from, and is used only while nothing has been
configured, so an existing deployment keeps sending where it always did.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from chester.config import settings
from chester.models import SendDestination

# PS3.5: an AE title is at most sixteen characters, and the backslash is the
# value delimiter, so it cannot appear in one.
AE_TITLE_PATTERN = re.compile(r"^[^\\\s][^\\]{0,15}$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")

ENVIRONMENT_NAME = "Ambiente"


@dataclass(frozen=True)
class Destination:
    """One node to store on, from a row or from the environment."""

    name: str
    host: str
    port: int
    ae_title: str
    calling_ae_title: str
    id: uuid.UUID | None = None

    @property
    def label(self) -> str:
        """The destination as an operator reads it, and as the log records it."""
        return f"{self.ae_title}@{self.host}:{self.port}"


def from_row(row: SendDestination) -> Destination:
    return Destination(
        name=row.name,
        host=row.host,
        port=row.port,
        ae_title=row.ae_title,
        calling_ae_title=row.calling_ae_title,
        id=row.id,
    )


def from_settings() -> Destination | None:
    """The environment destination, when one is configured."""
    if not settings.dicom_send_host:
        return None
    return Destination(
        name=ENVIRONMENT_NAME,
        host=settings.dicom_send_host,
        port=settings.dicom_send_port,
        ae_title=settings.dicom_send_ae_title,
        calling_ae_title=settings.dicom_send_calling_ae_title,
    )


def configured(db: Session, organization_id: uuid.UUID) -> list[SendDestination]:
    """Every destination of an organization, newest last."""
    return (
        db.query(SendDestination)
        .filter(SendDestination.organization_id == organization_id)
        .order_by(SendDestination.created_at.asc())
        .all()
    )


def active(db: Session, organization_id: uuid.UUID) -> list[Destination]:
    """Where a report goes when someone asks for it to be sent.

    Falls back to the environment only while an organization has configured
    nothing at all -- once it has, a destination it deactivated stays silent.
    """
    rows = [row for row in configured(db, organization_id) if row.active]
    if rows:
        return [from_row(row) for row in rows]
    if configured(db, organization_id):
        return []
    fallback = from_settings()
    return [fallback] if fallback else []


def automatic(db: Session, organization_id: uuid.UUID) -> list[SendDestination]:
    """The destinations a completed analysis is delivered to on its own.

    Never the environment fallback: sending without being asked is a decision
    someone makes in the console, on a destination they can see.
    """
    return [row for row in configured(db, organization_id) if row.active and row.auto_send]


def normalize(
    *, name: str, host: str, port: int, ae_title: str, calling_ae_title: str
) -> dict[str, str | int]:
    """Validate one destination's fields, or say which one is wrong."""
    cleaned = {
        "name": (name or "").strip(),
        "host": (host or "").strip(),
        "ae_title": (ae_title or "").strip(),
        "calling_ae_title": (calling_ae_title or "").strip() or "TORAX_AI",
    }
    if not cleaned["name"]:
        raise ValueError("Informe um nome para a conexão.")
    if len(cleaned["name"]) > 64:
        raise ValueError("O nome tem no máximo 64 caracteres.")
    if not HOST_PATTERN.match(cleaned["host"]):
        raise ValueError("Endereço inválido.")
    if not 1 <= port <= 65535:
        raise ValueError("A porta deve estar entre 1 e 65535.")
    for field in ("ae_title", "calling_ae_title"):
        if not AE_TITLE_PATTERN.match(str(cleaned[field])):
            raise ValueError("AE title inválido: até 16 caracteres, sem barra invertida.")
    return {**cleaned, "port": port}
