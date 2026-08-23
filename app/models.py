"""SQLAlchemy ORM models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    return datetime.utcnow()


def _uuid() -> str:
    return str(uuid.uuid4())


class Study(Base):
    __tablename__ = "studies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    patient_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    patient_age: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    patient_sex: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    study_date: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    modality: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    view_position: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="upload")

    # Status: received|validating|queued|processing|completed|needs_review|rejected|error
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")

    # Validation state: chest|uncertain|non_chest
    validation_state: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    validation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Model/preprocessing versions
    model_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    preprocessing_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # DICOM UIDs
    study_instance_uid: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    # Relationships
    instances: Mapped[list["Instance"]] = relationship(
        "Instance", back_populates="study", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["AnalysisJob"]] = relationship(
        "AnalysisJob", back_populates="study", cascade="all, delete-orphan"
    )
    results: Mapped[list["AnalysisResult"]] = relationship(
        "AnalysisResult", back_populates="study", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        "AuditEvent", back_populates="study", cascade="all, delete-orphan"
    )

    @property
    def top_findings(self) -> list[dict]:
        """Return top findings from most recent completed result."""
        if not self.results:
            return []
        completed = [r for r in self.results if r.above_threshold_findings]
        if not completed:
            return []
        # Most recent
        latest = max(completed, key=lambda r: r.created_at)
        findings = latest.above_threshold_findings or []
        return [
            {
                "pathology": pathology,
                "raw_score": (latest.raw_scores or {}).get(pathology),
                "normalized_score": (latest.op_normalized_scores or {}).get(pathology),
                "threshold": (latest.thresholds or {}).get(pathology),
                "above_threshold": (latest.above_threshold or {}).get(pathology, False),
            }
            for pathology in findings[:5]
        ]


class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(ForeignKey("studies.id"), nullable=False, index=True)

    sop_instance_uid: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    sop_class_uid: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    series_instance_uid: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    transfer_syntax_uid: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    frame_count: Mapped[int] = mapped_column(Integer, default=1)
    rows: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    columns: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bits_allocated: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Storage
    object_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Audit note (e.g. multi-frame selection)
    audit_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    study: Mapped["Study"] = relationship("Study", back_populates="instances")
    stored_objects: Mapped[list["StoredObject"]] = relationship(
        "StoredObject", back_populates="instance", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("sop_instance_uid", name="uq_instances_sop_uid"),
    )


class StoredObject(Base):
    __tablename__ = "stored_objects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    instance_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("instances.id"), nullable=True, index=True
    )

    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="db")
    content_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # For DB-backed storage: inline bytes as hex or base64
    inline_data: Mapped[Optional[bytes]] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    instance: Mapped["Instance"] = relationship("Instance", back_populates="stored_objects")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(ForeignKey("studies.id"), nullable=False, index=True)

    # Status: queued|processing|completed|error
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    study: Mapped["Study"] = relationship("Study", back_populates="jobs")
    result: Mapped[Optional["AnalysisResult"]] = relationship(
        "AnalysisResult", back_populates="job", uselist=False
    )


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    study_id: Mapped[str] = mapped_column(ForeignKey("studies.id"), nullable=False, index=True)
    job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=True, unique=True
    )

    model_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    preprocessing_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Raw sigmoid scores per pathology
    raw_scores: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Op-normalized scores
    op_normalized_scores: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Thresholds used
    thresholds: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Above threshold flags
    above_threshold: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Above threshold pathology list for quick access
    above_threshold_findings: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    study: Mapped["Study"] = relationship("Study", back_populates="results")
    job: Mapped[Optional["AnalysisJob"]] = relationship(
        "AnalysisJob", back_populates="result"
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    study_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("studies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    study: Mapped[Optional["Study"]] = relationship(
        "Study", back_populates="audit_events"
    )


class AllowedEmail(Base):
    __tablename__ = "allowed_emails"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="technician")
    allowed_pages: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_env_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class LegacyOwnerAlias(Base):
    """Explicit, audited translation from a pre-OTP study owner to an email owner."""
    __tablename__ = "legacy_owner_aliases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    legacy_owner_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AllowedDomain(Base):
    __tablename__ = "allowed_domains"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    domain: Mapped[str] = mapped_column(String(253), nullable=False, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="technician")
    allowed_pages: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AccessControlAuditLog(Base):
    __tablename__ = "access_control_audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    actor_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    actor_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_key: Mapped[str] = mapped_column(String(320), nullable=False)
    target_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    request_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    request_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
