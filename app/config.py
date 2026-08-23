"""Application configuration via pydantic-settings."""
from __future__ import annotations

import os
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./dev.db")

    # Auth
    clerk_secret_key: str = os.environ.get("CLERK_SECRET_KEY", "")
    session_secret: str = os.environ.get("SESSION_SECRET", "dev-session-secret-change-me")

    # DICOM ingestion token
    dicom_ingest_token: str = os.environ.get(
        "DICOM_INGEST_TOKEN",
        os.environ.get("SESSION_SECRET", "dev-session-secret-change-me"),
    )
    dicom_ingest_owner_id: str = os.environ.get("DICOM_INGEST_OWNER_ID", "")

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
