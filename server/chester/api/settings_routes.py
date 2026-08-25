"""Read-only connectivity settings shown in the console."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from chester.api.deps import require_page
from chester.config import settings
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
