"""ORM models.

The identity model is the substantive change from the previous schema. Studies used
to be owned by ``owner_id``, a raw email string compared by equality in every query,
with no user table at all. That made a study's owner and a person two different
things: changing someone's address orphaned their studies, administrators could not
see anything they did not upload themselves, and a one-off identifier migration
needed a dedicated alias table to avoid guessing identities.

Studies now belong to a user row and an organization, both by UUID. Visibility is
"same organization, and either yours or your role reads the whole organization".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chester.db import Base, JsonDocument, UtcDateTime
from chester.security.roles import ROLE_TECHNICIAN


def utcnow() -> datetime:
    """Timezone-aware UTC. datetime.utcnow() is naive and deprecated."""
    return datetime.now(UTC)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    users: Mapped[list[User]] = relationship(back_populates="organization")


class User(TimestampMixin, Base):
    """A person authorized to use the application.

    This table is also the allowlist: ``active`` false denies access. Emails are
    stored already normalized (stripped, casefolded) so a plain unique index is
    enough and no database extension is required.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False, default=ROLE_TECHNICIAN)
    # None means every page; a list restricts to its members.
    allowed_pages: Mapped[list | None] = mapped_column(JsonDocument, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Managed by ADMIN_USERS; immutable through the API and reconciled at startup.
    is_env_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="users")
    studies: Mapped[list[Study]] = relationship(back_populates="owner")


class AllowedDomain(TimestampMixin, Base):
    """Grants membership of an organization to every address in a domain.

    A matching address gets a user row on its first successful sign-in, not when a
    code is requested, so an unverified request cannot populate the table.
    """

    __tablename__ = "allowed_domains"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    domain: Mapped[str] = mapped_column(String(253), nullable=False, unique=True, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False, default=ROLE_TECHNICIAN)
    allowed_pages: Mapped[list | None] = mapped_column(JsonDocument, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)

    organization: Mapped[Organization] = relationship()


class AuthChallenge(Base):
    """A pending one-time code.

    Keyed by email rather than by user, because a domain-authorized address has no
    user row until it verifies a code for the first time.
    """

    __tablename__ = "auth_challenges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    consumed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, index=True
    )
    request_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_auth_challenges_email_requested", "email", "requested_at"),)


class AuthSession(Base):
    """A browser session. Only the HMAC of the token is stored."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship()


class Study(TimestampMixin, Base):
    __tablename__ = "studies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    patient_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    patient_age: Mapped[str | None] = mapped_column(String(16), nullable=True)
    patient_sex: Mapped[str | None] = mapped_column(String(8), nullable=True)
    study_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    modality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    body_part: Mapped[str | None] = mapped_column(String(64), nullable=True)
    view_position: Mapped[str | None] = mapped_column(String(16), nullable=True)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")

    # received | validating | queued | processing | completed | needs_review
    # | rejected | error
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received", index=True)
    # chest | uncertain | non_chest
    validation_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # A stable identifier the interface translates. The prose beside it is the
    # English rendering, kept for logs and for consumers without a translation
    # table; it is derived from the code, so the two cannot disagree.
    validation_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    thumbnail_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preprocessing_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    study_instance_uid: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    owner: Mapped[User] = relationship(back_populates="studies")
    organization: Mapped[Organization] = relationship()
    instances: Mapped[list[Instance]] = relationship(
        back_populates="study", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[AnalysisJob]] = relationship(
        back_populates="study", cascade="all, delete-orphan"
    )
    results: Mapped[list[AnalysisResult]] = relationship(
        back_populates="study", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="study", cascade="all, delete-orphan"
    )
    delivery_jobs: Mapped[list[DeliveryJob]] = relationship(
        back_populates="study", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_studies_org_created", "organization_id", "created_at"),
        Index("ix_studies_owner_status", "owner_user_id", "status"),
    )


class Instance(Base):
    """One stored image.

    organization_id is denormalized from the parent study so identity and content
    uniqueness can be scoped per organization. A globally unique sop_instance_uid
    would let one tenant's upload refuse another's, which breaks a legitimate case
    -- two organizations may hold the same instance -- and answers whether a given
    UID exists somewhere else in the system.
    """

    __tablename__ = "instances"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    study_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("studies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    sop_instance_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sop_class_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    series_instance_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transfer_syntax_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    frame_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    columns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bits_allocated: Mapped[int | None] = mapped_column(Integer, nullable=True)

    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audit_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    study: Mapped[Study] = relationship(back_populates="instances")
    organization: Mapped[Organization] = relationship()
    stored_objects: Mapped[list[StoredObject]] = relationship(
        back_populates="instance", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "sop_instance_uid", name="uq_instances_org_sop_uid"),
        Index("ix_instances_org_sha256", "organization_id", "sha256"),
    )


class StoredObject(Base):
    """Object bytes when the database-backed storage fallback is in use."""

    __tablename__ = "stored_objects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("instances.id", ondelete="CASCADE"), nullable=True, index=True
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="database")
    content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inline_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    instance: Mapped[Instance | None] = relationship(back_populates="stored_objects")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    study_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("studies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # queued | processing | completed | error
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    study: Mapped[Study] = relationship(back_populates="jobs")
    result: Mapped[AnalysisResult | None] = relationship(back_populates="job", uselist=False)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    study_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("studies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=True, unique=True
    )

    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preprocessing_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    raw_scores: Mapped[dict | None] = mapped_column(JsonDocument, nullable=True)
    op_normalized_scores: Mapped[dict | None] = mapped_column(JsonDocument, nullable=True)
    thresholds: Mapped[dict | None] = mapped_column(JsonDocument, nullable=True)
    above_threshold: Mapped[dict | None] = mapped_column(JsonDocument, nullable=True)
    above_threshold_findings: Mapped[list | None] = mapped_column(JsonDocument, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    study: Mapped[Study] = relationship(back_populates="results")
    job: Mapped[AnalysisJob | None] = relationship(back_populates="result")


class AuditEvent(Base):
    """Per-study activity trail."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    study_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("studies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Free text: a user email, or a service identifier for machine ingestion.
    actor: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    detail: Mapped[dict | None] = mapped_column(JsonDocument, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    study: Mapped[Study | None] = relationship(back_populates="audit_events")


class AccessControlAuditLog(Base):
    """Append-only trail for access-control administration."""

    __tablename__ = "access_control_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    actor_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    actor_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_key: Mapped[str] = mapped_column(String(320), nullable=False)
    target_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(JsonDocument, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, index=True
    )


class NetworkLog(Base):
    """One exchange with another system: an exam that arrived, or a report sent.

    ``study_id`` is a plain identifier rather than a foreign key, deliberately.
    A network log answers "what did this node exchange, and with whom", which is a
    question about the connection and not about the study: deleting the study must
    not erase the record that something was received from a modality or delivered
    to a viewer. It is the same reasoning that keeps a ``study_deleted`` audit event
    after its study is gone.
    """

    __tablename__ = "network_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    study_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    # received | sent
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # upload | stow-rs | c-store | wado on the way in; c-store on the way out.
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    # Where it came from or went to: an address on the way in, AE@host:port out.
    peer: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # success | failure | duplicate
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    actor: Mapped[str | None] = mapped_column(String(320), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JsonDocument, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, index=True
    )

    __table_args__ = (
        Index("ix_network_logs_org_created", "organization_id", "created_at"),
        Index("ix_network_logs_direction_created", "direction", "created_at"),
    )


class SendDestination(TimestampMixin, Base):
    """A node this organization stores generated reports on.

    The destination used to be one address in the environment, which meant a site
    with a PACS and a reading workstation could reach only one of them, and moving
    either was a redeploy. It is a row now, so the console configures it.
    """

    __tablename__ = "send_destinations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=11112)
    # DICOM caps an AE title at sixteen characters.
    ae_title: Mapped[str] = mapped_column(String(16), nullable=False)
    calling_ae_title: Mapped[str] = mapped_column(String(16), nullable=False, default="TORAX_AI")

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Whether a completed analysis is delivered here without anyone asking.
    auto_send: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)

    organization: Mapped[Organization] = relationship()
    delivery_jobs: Mapped[list[DeliveryJob]] = relationship(
        back_populates="destination", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_send_destinations_org_name"),
    )


class DeliveryJob(Base):
    """One report to store on one destination.

    Delivery is queued rather than done inline at the end of an analysis: a node
    that is down must not fail the analysis that produced the report, and an
    attempt that failed for a reason that passes deserves another one.
    """

    __tablename__ = "delivery_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    study_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("studies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("send_destinations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # queued | processing | completed | error
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True, index=True
    )
    # A failed attempt waits before the next one, so a node that is down is not
    # hammered by the poll loop.
    next_attempt_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, index=True
    )

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    study: Mapped[Study] = relationship(back_populates="delivery_jobs")
    destination: Mapped[SendDestination] = relationship(back_populates="delivery_jobs")
