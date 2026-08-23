"""Pydantic response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class InstanceSchema(BaseModel):
    id: str
    sop_instance_uid: Optional[str]
    sop_class_uid: Optional[str]
    series_instance_uid: Optional[str]
    transfer_syntax_uid: Optional[str]
    frame_count: int
    rows: Optional[int]
    columns: Optional[int]
    bits_allocated: Optional[int]
    sha256: Optional[str]
    file_size: Optional[int]
    content_type: Optional[str]
    audit_note: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class AnalysisResultSchema(BaseModel):
    id: str
    model_version: Optional[str]
    preprocessing_version: Optional[str]
    raw_scores: Optional[Dict[str, float]]
    op_normalized_scores: Optional[Dict[str, float]]
    thresholds: Optional[Dict[str, float]]
    above_threshold: Optional[Dict[str, bool]]
    above_threshold_findings: Optional[List[str]]
    created_at: datetime

    model_config = {"from_attributes": True}


class StudySchema(BaseModel):
    id: str
    patient_id: Optional[str]
    patient_age: Optional[str]
    patient_sex: Optional[str]
    study_date: Optional[str]
    modality: Optional[str]
    view_position: Optional[str]
    description: Optional[str]
    source: Optional[str]
    status: str
    validation_state: Optional[str]
    validation_reason: Optional[str]
    thumbnail_url: Optional[str]
    created_at: datetime
    updated_at: datetime
    top_findings: List[Any]

    model_config = {"from_attributes": True}


class StudyDetailSchema(StudySchema):
    instances: List[InstanceSchema]
    results: List[AnalysisResultSchema]
    model_version: Optional[str]
    preprocessing_version: Optional[str]
    error_message: Optional[str]
    study_instance_uid: Optional[str]


class StudyListResponse(BaseModel):
    items: List[StudySchema]
    total: int
    counts: Dict[str, int]


class UploadError(BaseModel):
    filename: str
    error: str


class UploadResponse(BaseModel):
    studies: List[StudySchema]
    errors: List[UploadError]


class ReviewRequest(BaseModel):
    decision: str  # approve | reject


class HealthResponse(BaseModel):
    status: str
    storage_backend: str
    db_ok: bool
    model_version: Optional[str]


class DicomScpSettingsSchema(BaseModel):
    status: Literal["configured", "not_configured"]
    status_label: str
    host: str
    ae_title: str
    port: int
    services: List[str]
    transport: str
    gateway_target: str
    owner_configured: bool


class DicomwebStowSettingsSchema(BaseModel):
    status: Literal["configured", "local_only"]
    status_label: str
    url: str
    hostname: str
    path: str
    port: str
    https: bool
    ae_title: str
    services: List[str]
    request_limit: str


class DicomwebSettingsSchema(BaseModel):
    scp: DicomScpSettingsSchema
    stow_rs: DicomwebStowSettingsSchema
    service_token_configured: bool
    wado_anonymous: bool = False
