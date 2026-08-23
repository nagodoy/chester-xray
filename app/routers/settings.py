"""Authenticated, read-only application settings endpoints."""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends

from app.auth import require_auth
from app.config import settings
from app.schemas import (
    DicomScpSettingsSchema,
    DicomwebSettingsSchema,
    DicomwebStowSettingsSchema,
)

router = APIRouter()


def _safe_http_url(value: str) -> str:
    """Return a safe configured HTTP(S) URL without credentials or query data."""
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


def _hostname_and_port(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    port = str(parsed.port or (443 if parsed.scheme == "https" else 80))
    return hostname, port


@router.get("/api/settings/dicomweb", response_model=DicomwebSettingsSchema)
def get_dicomweb_settings(
    _actor_id: str = Depends(require_auth),
) -> DicomwebSettingsSchema:
    """Return safe DICOM connectivity metadata for the authenticated console."""
    stow_path = "/dicomweb/studies"
    public_base_url = _safe_http_url(settings.public_app_url)
    stow_url = f"{public_base_url}{stow_path}" if public_base_url else stow_path
    stow_host, stow_port = (
        _hostname_and_port(stow_url)
        if public_base_url
        else ("Não configurado", "—")
    )
    stow_https = public_base_url.startswith("https://")
    gateway_target = _safe_http_url(settings.dicom_stow_url) or stow_url
    scp_configured = bool(
        settings.dicom_scp_host
        and settings.dicom_ingest_owner_id
        and settings.dicom_ingest_token
    )

    return DicomwebSettingsSchema(
        scp=DicomScpSettingsSchema(
            status="configured" if scp_configured else "not_configured",
            status_label=(
                "Configuração declarada" if scp_configured else "Não configurado"
            ),
            host=settings.dicom_scp_host or "Não configurado",
            ae_title=settings.dicom_scp_ae_title,
            port=settings.dicom_scp_port,
            services=["C-STORE"],
            transport="DICOM TCP",
            gateway_target=gateway_target,
            owner_configured=bool(settings.dicom_ingest_owner_id),
        ),
        stow_rs=DicomwebStowSettingsSchema(
            status="configured" if public_base_url else "local_only",
            status_label=(
                "Configuração declarada"
                if public_base_url
                else "Somente local"
            ),
            url=stow_url,
            hostname=stow_host or "Não identificado",
            path=stow_path,
            port=stow_port,
            https=stow_https,
            ae_title="Não aplicável",
            services=["STOW-RS"],
            request_limit="Sem limite definido pelo aplicativo",
        ),
        service_token_configured=bool(settings.dicom_ingest_token),
    )