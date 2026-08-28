"""Connectivity settings: how devices reach this node, and where it sends.

The ingestion half is read-only -- it describes what the deployment already is.
The destinations half is not: where a report goes used to be four environment
variables, so a site could reach exactly one node and moving it meant a
redeploy.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from chester import destinations as destinations_module
from chester.api.deps import require_admin, require_page
from chester.config import settings
from chester.db import get_session
from chester.models import SendDestination
from chester.security.access import AccessContext

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ScpSettings(BaseModel):
    status: str
    status_label: str
    host: str
    ae_title: str
    port: int
    services: list[str]
    transport: str
    gateway_target: str
    owner_configured: bool


class StowSettings(BaseModel):
    status: str
    status_label: str
    url: str
    hostname: str
    path: str
    port: str
    https: bool
    ae_title: str
    services: list[str]
    request_limit: str


class DicomwebSettings(BaseModel):
    scp: ScpSettings
    stow_rs: StowSettings
    service_token_configured: bool
    wado_anonymous: bool


def safe_http_url(value: str) -> str:
    """Return a configured URL stripped of credentials and query data.

    Never echo back something that could carry a secret from configuration into
    a browser.
    """
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    try:
        _ = parsed.port
    except ValueError:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


@router.get("/dicomweb", response_model=DicomwebSettings)
def dicomweb_settings(
    _access: AccessContext = Depends(require_page("settings")),
) -> DicomwebSettings:
    """Describe how devices should connect. Secrets are never included."""
    stow_path = "/wado/studies" if settings.dicom_wado_anonymous_ingest else "/dicomweb/studies"
    base_url = safe_http_url(settings.public_app_url)
    stow_url = f"{base_url}{stow_path}" if base_url else stow_path

    if base_url:
        parsed = urlsplit(stow_url)
        hostname = parsed.hostname or "Não identificado"
        port = str(parsed.port or (443 if parsed.scheme == "https" else 80))
    else:
        hostname, port = "Não configurado", "—"

    limit_mb = settings.dicom_max_upload_bytes // (1024 * 1024)
    scp_configured = bool(
        settings.dicom_scp_host
        and settings.dicom_ingest_owner_email
        and settings.dicom_ingest_token
    )

    return DicomwebSettings(
        scp=ScpSettings(
            status="configured" if scp_configured else "not_configured",
            status_label="Configuração declarada" if scp_configured else "Não configurado",
            host=settings.dicom_scp_host or "Não configurado",
            ae_title=settings.dicom_scp_ae_title,
            port=settings.dicom_scp_port,
            services=["C-STORE"],
            transport="DICOM TCP",
            gateway_target=safe_http_url(settings.dicom_stow_url) or stow_url,
            owner_configured=bool(settings.dicom_ingest_owner_email),
        ),
        stow_rs=StowSettings(
            status="configured" if base_url else "local_only",
            status_label="Configuração declarada" if base_url else "Somente local",
            url=stow_url,
            hostname=hostname,
            path=stow_path,
            port=port,
            https=base_url.startswith("https://"),
            ae_title="Não aplicável",
            services=["STOW-RS"],
            request_limit=f"Limite de {limit_mb} MB por requisição",
        ),
        service_token_configured=bool(settings.dicom_ingest_token),
        wado_anonymous=settings.dicom_wado_anonymous_ingest,
    )


class DestinationSchema(BaseModel):
    id: str
    name: str
    host: str
    port: int
    ae_title: str
    calling_ae_title: str
    active: bool
    auto_send: bool
    created_by: str | None


class EnvironmentDestination(BaseModel):
    """The address a deployment starts from, used while nothing is configured."""

    name: str
    host: str
    port: int
    ae_title: str
    calling_ae_title: str


class DestinationList(BaseModel):
    items: list[DestinationSchema]
    # Present only while the list is empty, which is when it is still in use.
    environment: EnvironmentDestination | None
    editable: bool


class DestinationBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=11112, ge=1, le=65535)
    ae_title: str = Field(min_length=1, max_length=16)
    calling_ae_title: str = Field(default="TORAX_AI", max_length=16)
    active: bool = True
    auto_send: bool = False


class DestinationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    ae_title: str | None = Field(default=None, min_length=1, max_length=16)
    calling_ae_title: str | None = Field(default=None, max_length=16)
    active: bool | None = None
    auto_send: bool | None = None


def _payload(row: SendDestination) -> DestinationSchema:
    return DestinationSchema(
        id=str(row.id),
        name=row.name,
        host=row.host,
        port=row.port,
        ae_title=row.ae_title,
        calling_ae_title=row.calling_ae_title,
        active=row.active,
        auto_send=row.auto_send,
        created_by=row.created_by,
    )


def _load(db: Session, actor: AccessContext, destination_id: uuid.UUID) -> SendDestination:
    row = (
        db.query(SendDestination)
        .filter(
            SendDestination.id == destination_id,
            SendDestination.organization_id == actor.organization_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Conexão de envio não encontrada.")
    return row


def _validated(**fields) -> dict:
    try:
        return destinations_module.normalize(**fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/destinations", response_model=DestinationList)
def list_destinations(
    access: AccessContext = Depends(require_page("settings")),
    db: Session = Depends(get_session),
):
    """Every destination of the caller's organization, and the fallback in use."""
    rows = destinations_module.configured(db, access.organization_id)
    fallback = None if rows else destinations_module.from_settings()
    return DestinationList(
        items=[_payload(row) for row in rows],
        environment=(
            EnvironmentDestination(
                name=fallback.name,
                host=fallback.host,
                port=fallback.port,
                ae_title=fallback.ae_title,
                calling_ae_title=fallback.calling_ae_title,
            )
            if fallback
            else None
        ),
        editable=access.is_admin,
    )


@router.post("/destinations", response_model=DestinationSchema, status_code=201)
def create_destination(
    body: DestinationBody,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    fields = _validated(
        name=body.name,
        host=body.host,
        port=body.port,
        ae_title=body.ae_title,
        calling_ae_title=body.calling_ae_title,
    )
    taken = (
        db.query(SendDestination)
        .filter(
            SendDestination.organization_id == actor.organization_id,
            SendDestination.name == fields["name"],
        )
        .first()
    )
    if taken is not None:
        raise HTTPException(status_code=409, detail="Já existe uma conexão com esse nome.")

    row = SendDestination(
        organization_id=actor.organization_id,
        active=body.active,
        auto_send=body.auto_send,
        created_by=actor.email,
        **fields,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _payload(row)


@router.patch("/destinations/{destination_id}", response_model=DestinationSchema)
def update_destination(
    destination_id: uuid.UUID,
    body: DestinationPatch,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    row = _load(db, actor, destination_id)
    fields = _validated(
        name=body.name if body.name is not None else row.name,
        host=body.host if body.host is not None else row.host,
        port=body.port if body.port is not None else row.port,
        ae_title=body.ae_title if body.ae_title is not None else row.ae_title,
        calling_ae_title=(
            body.calling_ae_title if body.calling_ae_title is not None else row.calling_ae_title
        ),
    )
    for key, value in fields.items():
        setattr(row, key, value)
    if body.active is not None:
        row.active = body.active
    if body.auto_send is not None:
        row.auto_send = body.auto_send

    db.commit()
    db.refresh(row)
    return _payload(row)


@router.delete("/destinations/{destination_id}", status_code=204)
def delete_destination(
    destination_id: uuid.UUID,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """Remove a destination, and with it any delivery still queued for it."""
    db.delete(_load(db, actor, destination_id))
    db.commit()


@router.post("/destinations/{destination_id}/test")
def test_destination(
    destination_id: uuid.UUID,
    actor: AccessContext = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """Ask the node to answer a C-ECHO, and say what it answered."""
    from chester.dicom_send import SendFailed, echo

    row = _load(db, actor, destination_id)
    try:
        echo(
            host=row.host,
            port=row.port,
            ae_title=row.ae_title,
            calling_ae_title=row.calling_ae_title,
        )
    except SendFailed as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "message": f"{row.ae_title}@{row.host}:{row.port}: {exc}"}
    return {"ok": True, "message": f"{row.ae_title}@{row.host}:{row.port}"}
