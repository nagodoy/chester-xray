"""Response models for the HTTP API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InstanceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sop_instance_uid: str | None
    sop_class_uid: str | None
    series_instance_uid: str | None
    transfer_syntax_uid: str | None
    frame_count: int
    rows: int | None
    columns: int | None
    bits_allocated: int | None
    sha256: str | None
    file_size: int | None
    content_type: str | None
    audit_note: str | None
    created_at: datetime


class AnalysisResultSchema(BaseModel):
    """One scored run, carrying only the outputs this deployment reports.

    A result recorded before an output was suppressed still holds it in the
    stored document. The interface builds its table from whatever keys arrive,
    so the filtering happens here, once, rather than in each reader.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    model_version: str | None
    preprocessing_version: str | None
    raw_scores: dict[str, float] | None
    op_normalized_scores: dict[str, float] | None
    thresholds: dict[str, float] | None
    above_threshold: dict[str, bool] | None
    above_threshold_findings: list[str] | None
    created_at: datetime

    @model_validator(mode="after")
    def _drop_suppressed(self):
        from chester.inference import is_reported

        for field in ("raw_scores", "op_normalized_scores", "thresholds", "above_threshold"):
            stored = getattr(self, field)
            if stored:
                setattr(self, field, {k: v for k, v in stored.items() if is_reported(k)})
        if self.above_threshold_findings:
            self.above_threshold_findings = [
                name for name in self.above_threshold_findings if is_reported(name)
            ]
        return self


class StudySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: str | None
    patient_age: str | None
    patient_sex: str | None
    study_date: str | None
    modality: str | None
    body_part: str | None
    view_position: str | None
    description: str | None
    source: str | None
    status: str
    validation_state: str | None
    validation_reason_code: str | None
    validation_reason: str | None
    thumbnail_url: str | None
    created_at: datetime
    updated_at: datetime
    owner_email: str | None = None
    top_findings: list[Any] = []


class StudyDetailSchema(StudySchema):
    instances: list[InstanceSchema] = []
    results: list[AnalysisResultSchema] = []
    model_version: str | None = None
    preprocessing_version: str | None = None
    error_message: str | None = None
    study_instance_uid: str | None = None


class StudyListResponse(BaseModel):
    items: list[StudySchema]
    total: int
    counts: dict[str, int]


class UploadError(BaseModel):
    filename: str
    error: str


class UploadResponse(BaseModel):
    studies: list[StudySchema]
    errors: list[UploadError]


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]


class BulkDeleteRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class BulkDeleteResponse(BaseModel):
    """Per-id outcomes: one unreachable study must not sink the whole batch."""

    deleted: list[uuid.UUID]
    not_found: list[uuid.UUID]
    errors: list[dict]


class HealthResponse(BaseModel):
    status: str
    storage_backend: str
    db_ok: bool
    model_version: str | None


class NetworkLogSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    study_id: uuid.UUID | None
    direction: str
    channel: str
    peer: str | None
    status: str
    actor: str | None
    reference: str | None
    message: str | None
    detail: dict[str, Any] | None
    created_at: datetime


class NetworkLogListResponse(BaseModel):
    items: list[NetworkLogSchema]
    total: int


class RetentionSchema(BaseModel):
    """The network log retention window, and what applying it would remove now."""

    hours: int
    # The windows the interface may offer, so it does not hard-code the set.
    options: list[int]
    expiring: int
    last_swept_at: datetime | None


class RetentionUpdate(BaseModel):
    hours: int


class RetentionPurgeResponse(BaseModel):
    deleted: int
    retention: RetentionSchema
