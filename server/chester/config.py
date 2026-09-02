"""Application configuration.

Secrets are deliberately separate. The previous implementation derived patient
pseudonyms from SESSION_SECRET, which meant rotating the session secret -- a thing
you must be able to do -- silently changed every future pseudonym, so the same
patient stopped mapping to the same identifier. PSEUDONYM_SECRET is independent and
must not be rotated without accepting that consequence.
"""

from __future__ import annotations

from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "sqlite+pysqlite:///./dev.db"

    # Secrets
    session_secret: str = "dev-session-secret-change-me"
    pseudonym_secret: str = "dev-pseudonym-secret-change-me"

    # Environment-managed administrators, comma separated.
    admin_users: str = ""

    # The organization environment admins are bootstrapped into.
    default_organization_slug: str = "default"
    default_organization_name: str = "Default"

    # Authentication
    auth_session_hours: int = 12
    auth_otp_minutes: int = 10
    auth_otp_attempts: int = 5
    auth_otp_cooldown_seconds: int = 60

    # SMTP
    smtp_from: str = ""
    smtp_host: str = ""
    smtp_password: str = ""
    smtp_port: int = 587

    # DICOM ingestion
    dicom_ingest_token: str = ""
    dicom_ingest_owner_email: str = ""
    dicom_wado_anonymous_ingest: bool = False
    dicom_max_upload_bytes: int = 100 * 1024 * 1024

    # External DICOM SCP gateway (read-only display)
    dicom_scp_host: str = ""
    dicom_scp_port: int = 11112
    dicom_scp_ae_title: str = "WORKLIST_SCP"
    dicom_stow_url: str = ""

    # Where a generated ANALISADA series is sent when no destination has been
    # configured in the console, e.g. an OsiriX listener. Configured destinations
    # take precedence; this is the fallback a deployment starts from.
    dicom_send_host: str = "superpaccs.com.br"
    dicom_send_port: int = 11112
    dicom_send_ae_title: str = "medfusion"
    # Also written into SendingApplicationEntityTitle, so the tag on the
    # instance and the AE that carried it cannot say different things.
    dicom_send_calling_ae_title: str = "TORAX_AI"
    public_app_url: str = ""

    # Object storage
    storage_bucket: str = ""
    storage_endpoint_url: str = ""
    storage_region: str = ""

    # Model
    model_path: str = "models/chester-all-224.onnx"
    inference_timeout_seconds: float = 90.0

    # Worker
    job_lease_minutes: int = 30
    worker_poll_seconds: float = 5.0
    # Automatic delivery: how many times an attempt is repeated, and how long a
    # failed one waits before the next.
    delivery_max_attempts: int = 3
    delivery_retry_minutes: float = 5.0
    # Data retention: how often the worker applies each organization's network
    # log window. Well below the shortest window, so an entry never outlives it
    # by much, and far above the cost of one bulk delete.
    retention_sweep_minutes: float = 15.0

    debug: bool = False
    testing: bool = False

    @cached_property
    def admin_emails(self) -> tuple[str, ...]:
        return tuple(
            entry.strip().casefold() for entry in self.admin_users.split(",") if entry.strip()
        )

    def require_production_secrets(self) -> None:
        """Fail fast rather than run production on development defaults."""
        if self.debug or self.testing:
            return
        if self.session_secret == "dev-session-secret-change-me":
            raise RuntimeError("SESSION_SECRET must be configured outside development.")
        if self.pseudonym_secret == "dev-pseudonym-secret-change-me":
            raise RuntimeError("PSEUDONYM_SECRET must be configured outside development.")
        if not self.dicom_ingest_token:
            raise RuntimeError("DICOM_INGEST_TOKEN must be configured outside development.")


settings = Settings()
