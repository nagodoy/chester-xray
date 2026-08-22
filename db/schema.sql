-- Radiology Worklist MVP — Production Schema
-- Apply with: psql $DATABASE_URL -f db/schema.sql
-- No startup DDL in production; tables are managed here.

BEGIN;

-- Studies table
CREATE TABLE IF NOT EXISTS studies (
    id                   VARCHAR(36)  PRIMARY KEY,
    owner_id             VARCHAR(128) NOT NULL,
    patient_id           VARCHAR(128),
    patient_age          VARCHAR(16),
    patient_sex          VARCHAR(8),
    study_date           VARCHAR(16),
    modality             VARCHAR(16),
    view_position        VARCHAR(16),
    description          VARCHAR(256),
    source               VARCHAR(32)  NOT NULL DEFAULT 'upload',
    status               VARCHAR(32)  NOT NULL DEFAULT 'received',
    validation_state     VARCHAR(32),
    validation_reason    TEXT,
    thumbnail_url        VARCHAR(512),
    model_version        VARCHAR(64),
    preprocessing_version VARCHAR(64),
    error_message        TEXT,
    study_instance_uid   VARCHAR(128),
    created_at           TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMP    NOT NULL DEFAULT NOW()
);

ALTER TABLE studies ADD COLUMN IF NOT EXISTS owner_id VARCHAR(128);
UPDATE studies SET owner_id = 'legacy-unassigned' WHERE owner_id IS NULL;
ALTER TABLE studies ALTER COLUMN owner_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_studies_status       ON studies (status);
CREATE INDEX IF NOT EXISTS idx_studies_owner_id     ON studies (owner_id);
CREATE INDEX IF NOT EXISTS idx_studies_created_at   ON studies (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_studies_study_uid    ON studies (study_instance_uid);

-- Instances table
CREATE TABLE IF NOT EXISTS instances (
    id                   VARCHAR(36)  PRIMARY KEY,
    study_id             VARCHAR(36)  NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    sop_instance_uid     VARCHAR(128) UNIQUE,
    sop_class_uid        VARCHAR(128),
    series_instance_uid  VARCHAR(128),
    transfer_syntax_uid  VARCHAR(128),
    frame_count          INTEGER      NOT NULL DEFAULT 1,
    rows                 INTEGER,
    columns              INTEGER,
    bits_allocated       INTEGER,
    object_key           VARCHAR(512),
    sha256               VARCHAR(64),
    file_size            INTEGER,
    content_type         VARCHAR(64),
    audit_note           TEXT,
    created_at           TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_instances_study_id   ON instances (study_id);
CREATE INDEX IF NOT EXISTS idx_instances_sha256     ON instances (sha256);

-- Stored objects table (DB-backed storage fallback)
CREATE TABLE IF NOT EXISTS stored_objects (
    id               VARCHAR(36)  PRIMARY KEY,
    instance_id      VARCHAR(36)  REFERENCES instances(id) ON DELETE CASCADE,
    object_key       VARCHAR(512) NOT NULL,
    storage_backend  VARCHAR(32)  NOT NULL DEFAULT 'database',
    content_type     VARCHAR(64),
    sha256           VARCHAR(64),
    file_size        INTEGER,
    inline_data      BYTEA,
    created_at       TIMESTAMP    NOT NULL DEFAULT NOW()
);

ALTER TABLE stored_objects ALTER COLUMN instance_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_stored_objects_instance  ON stored_objects (instance_id);
CREATE INDEX IF NOT EXISTS idx_stored_objects_key       ON stored_objects (object_key);

-- Analysis jobs table
CREATE TABLE IF NOT EXISTS analysis_jobs (
    id            VARCHAR(36)  PRIMARY KEY,
    study_id      VARCHAR(36)  NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    status        VARCHAR(32)  NOT NULL DEFAULT 'queued',
    attempt       INTEGER      NOT NULL DEFAULT 0,
    error_message TEXT,
    lease_owner   VARCHAR(128),
    lease_expires_at TIMESTAMP,
    created_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    started_at    TIMESTAMP,
    completed_at  TIMESTAMP
);

ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS lease_owner VARCHAR(128);
ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_jobs_study_id  ON analysis_jobs (study_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status    ON analysis_jobs (status);

-- Analysis results table
CREATE TABLE IF NOT EXISTS analysis_results (
    id                    VARCHAR(36)  PRIMARY KEY,
    study_id              VARCHAR(36)  NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    job_id                VARCHAR(36)  UNIQUE REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    model_version         VARCHAR(64),
    preprocessing_version VARCHAR(64),
    raw_scores            JSONB,
    op_normalized_scores  JSONB,
    thresholds            JSONB,
    above_threshold       JSONB,
    above_threshold_findings JSONB,
    created_at            TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_results_study_id  ON analysis_results (study_id);
ALTER TABLE analysis_results
    DROP CONSTRAINT IF EXISTS analysis_results_job_id_fkey;
ALTER TABLE analysis_results
    ADD CONSTRAINT analysis_results_job_id_fkey
    FOREIGN KEY (job_id) REFERENCES analysis_jobs(id) ON DELETE CASCADE;
CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_results_job_id
    ON analysis_results (job_id) WHERE job_id IS NOT NULL;

-- Audit events table
CREATE TABLE IF NOT EXISTS audit_events (
    id          VARCHAR(36)  PRIMARY KEY,
    study_id    VARCHAR(36)  REFERENCES studies(id) ON DELETE CASCADE,
    actor_id    VARCHAR(128),
    event_type  VARCHAR(64)  NOT NULL,
    detail      JSONB,
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_study_id  ON audit_events (study_id);
ALTER TABLE audit_events
    DROP CONSTRAINT IF EXISTS audit_events_study_id_fkey;
ALTER TABLE audit_events
    ADD CONSTRAINT audit_events_study_id_fkey
    FOREIGN KEY (study_id) REFERENCES studies(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_audit_actor_id  ON audit_events (actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_type      ON audit_events (event_type);

COMMIT;
