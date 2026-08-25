/** Shapes returned by the server. Kept in one place so a contract change surfaces here. */

export type StudyStatus =
  | "received"
  | "validating"
  | "queued"
  | "processing"
  | "completed"
  | "needs_review"
  | "rejected"
  | "error";

export type ValidationState = "chest" | "uncertain" | "non_chest";

export type Page =
  | "worklist"
  | "review"
  | "study-detail"
  | "upload"
  | "settings"
  | "access-control";

export interface Access {
  email: string;
  role: string;
  is_admin: boolean;
  /** null means every page; a list restricts to its members. */
  allowed_pages: Page[] | null;
  organization_id: string;
  source: string;
}

export interface Finding {
  pathology: string;
  raw_score: number | null;
  normalized_score: number | null;
  threshold: number | null;
  above_threshold: boolean;
}

export interface Study {
  id: string;
  patient_id: string | null;
  patient_age: string | null;
  patient_sex: string | null;
  study_date: string | null;
  modality: string | null;
  body_part: string | null;
  view_position: string | null;
  description: string | null;
  source: string | null;
  status: StudyStatus;
  validation_reason_code: string | null;
  validation_state: ValidationState | null;
  /** English prose from the server, used only when a code has no translation. */
  validation_reason: string | null;
  thumbnail_url: string | null;
  created_at: string;
  updated_at: string;
  owner_email: string | null;
  top_findings: Finding[];
}

export interface AnalysisResult {
  id: string;
  model_version: string | null;
  preprocessing_version: string | null;
  raw_scores: Record<string, number> | null;
  op_normalized_scores: Record<string, number> | null;
  thresholds: Record<string, number> | null;
  above_threshold: Record<string, boolean> | null;
  above_threshold_findings: string[] | null;
  created_at: string;
}

export interface Instance {
  id: string;
  sop_instance_uid: string | null;
  frame_count: number;
  rows: number | null;
  columns: number | null;
  file_size: number | null;
  content_type: string | null;
  audit_note: string | null;
  created_at: string;
}

export interface StudyDetail extends Study {
  instances: Instance[];
  results: AnalysisResult[];
  model_version: string | null;
  preprocessing_version: string | null;
  error_message: string | null;
  study_instance_uid: string | null;
}

export interface StudyList {
  items: Study[];
  total: number;
  counts: Partial<Record<StudyStatus, number>>;
}

export interface UploadOutcome {
  studies: Study[];
  errors: { filename: string; error: string }[];
}

export interface DicomwebSettings {
  scp: {
    status: "configured" | "not_configured";
    status_label: string;
    host: string;
    ae_title: string;
    port: number;
    services: string[];
    transport: string;
    gateway_target: string;
    owner_configured: boolean;
  };
  stow_rs: {
    status: "configured" | "local_only";
    status_label: string;
    url: string;
    hostname: string;
    path: string;
    port: string;
    https: boolean;
    ae_title: string;
    services: string[];
    request_limit: string;
  };
  service_token_configured: boolean;
  wado_anonymous: boolean;
}

export interface ManagedUser {
  id: string;
  email: string;
  role: string;
  role_label: string;
  allowed_pages: Page[] | null;
  active: boolean;
  is_env_admin: boolean;
  created_at: string;
  updated_at: string;
}

export interface ManagedDomain {
  id: string;
  domain: string;
  role: string;
  role_label: string;
  allowed_pages: Page[] | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AuditEntry {
  id: string;
  actor_email: string;
  actor_role: string | null;
  action: string;
  target_type: string;
  target_key: string;
  target_role: string | null;
  created_at: string;
}

export interface AccessMetadata {
  roles: { value: string; label: string }[];
  pages: { value: Page; label: string }[];
}
