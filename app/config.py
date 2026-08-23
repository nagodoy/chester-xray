"""Application configuration via pydantic-settings."""
from __future__ import annotations

import os
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./dev.db")

    # Auth
    session_secret: str = os.environ.get("SESSION_SECRET", "dev-session-secret-change-me")
    admin_users: str = os.environ.get(
        "ADMIN_USERS",
        os.environ.get("ADMIN_EMAILS", os.environ.get("AUTHORIZED_EMAIL", "nelsonagodoy@gmail.com")),
    )
    auth_session_hours: int = int(os.environ.get("AUTH_SESSION_HOURS", "12"))
    auth_otp_minutes: int = int(os.environ.get("AUTH_OTP_MINUTES", "10"))
    auth_otp_attempts: int = int(os.environ.get("AUTH_OTP_ATTEMPTS", "5"))
    auth_otp_cooldown_seconds: int = int(os.environ.get("AUTH_OTP_COOLDOWN_SECONDS", "60"))

    # SMTP email delivery
    smtp_from: str = os.environ.get("SMTP_FROM", "")
    smtp_host: str = os.environ.get("SMTP_HOST", "")
    smtp_password: str = os.environ.get("SMTP_PASSWORD", "")
    smtp_port: int = int(os.environ.get("SMTP_PORT", "587"))

    # DICOM ingestion token
    dicom_ingest_token: str = os.environ.get(
        "DICOM_INGEST_TOKEN",
        os.environ.get("STOW_API_KEY", ""),
    )
    dicom_ingest_owner_id: str = os.environ.get("DICOM_INGEST_OWNER_ID", "")

    # External DICOM SCP gateway (read-only settings display)
    dicom_scp_host: str = os.environ.get("SCP_HOST", "")
    dicom_scp_port: int = int(os.environ.get("SCP_PORT", "11112"))
    dicom_scp_ae_title: str = os.environ.get("SCP_AE_TITLE", "WORKLIST_SCP")
    dicom_stow_url: str = os.environ.get("STOW_URL", "")
    public_app_url: str = os.environ.get("PUBLIC_APP_URL", "")

    # Object storage
    replit_object_storage_bucket: Optional[str] = os.environ.get(
        "REPLIT_OBJECT_STORAGE_BUCKET_ID", None
    )

    # Worker
    worker_concurrency: int = 1

    # CHESTER model
    chester_model_directory: str = "models/xrv-all-45rot15trans15scale"
    chester_inference_timeout_seconds: float = 90.0

    # App
    debug: bool = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")

    @property
    def admin_emails(self) -> list[str]:
        return [
            email.strip().casefold()
            for email in self.admin_users.split(",")
            if email.strip()
        ]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
