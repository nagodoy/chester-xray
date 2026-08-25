# Production Architecture

## ⚠️ Important Disclaimer

**This Replit MVP is intended for testing and de-identified data only.**

This application is a proof-of-concept research tool. It is **NOT suitable for clinical
use, NOT validated as a medical device, and NOT approved for diagnostic purposes.**

### PHI / Clinical Deployment Requirements

Deploying this application with Protected Health Information (PHI) requires:

1. **Business Associate Agreement (BAA)** with all hosting and infrastructure providers
2. **HIPAA-compliant infrastructure** — this Replit deployment does NOT meet HIPAA requirements
3. **Clinical-grade infrastructure** as described below
4. **Institutional review** (IRB, ethics board) for any patient data use
5. **Regulatory clearance** (FDA 510(k), CE Mark, etc.) for diagnostic claims

**The AI model outputs in this application are research scores only and must not be
used for clinical diagnosis, treatment decisions, or patient management.**

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Client (Browser)                         │
│  React SPA (web/)   ←→  Email one-time code + sessions      │
└─────────────────────┬───────────────────────────────────────┘
                      │ HTTPS
                      ▼
┌─────────────────────────────────────────────────────────────┐
│              FastAPI backend (server/chester/)                │
│                                                              │
│  /api/health              — Public health check              │
│  /api/auth/*              — Sign-in and session lifecycle    │
│  /api/studies/*           — Worklist (session token)         │
│  /api/uploads             — Multipart upload (session token) │
│  /api/access-control/*    — Access management (admin)        │
│  /dicomweb/studies*       — STOW-RS (service token)          │
└──────┬──────────────────────────┬───────────────────────────┘
       │                          │
       ▼                          ▼
┌──────────────┐         ┌────────────────────────┐
│  PostgreSQL   │         │  Object storage         │
│               │         │  (S3-compatible, or     │
│  organizations│         │   database fallback)    │
│  users        │         │  originals/{id}/*.dcm   │
│  studies      │         │  thumbnails/{id}.png    │
│  instances    │         └────────────────────────┘
│  analysis_*   │
│  audit_events │
└──────┬───────┘
       │
       ▼
Inference worker (separate process: python -m chester.worker):
  ┌─────────────────────────────────────────┐
  │  - Claims jobs with FOR UPDATE SKIP     │
  │    LOCKED, so several may run           │
  │  - Leases work, recovers expired leases │
  │  - ONNX Runtime, in-process             │
  └─────────────────────────────────────────┘

On-premises DICOM gateway (python -m chester.gateway):
  ┌─────────────────────────────────────────┐
  │  pynetdicom Storage SCP                  │
  │  - C-STORE receiver (port 11112)         │
  │  - Forwards to STOW-RS over HTTPS        │
  │  - Service token authentication          │
  └─────────────────────────────────────────┘
```

---

## Component Details

### 1. FastAPI Backend

- **Framework**: FastAPI 0.141+ with SQLAlchemy 2 sync ORM
- **Auth**: email OTP challenges and hashed database sessions, verified through
  the `X-Session-Token` request header
- **Database**: PostgreSQL via psycopg3 (or SQLite for local development/tests)
- **Storage**: any S3-compatible bucket, or database-backed bytes as a fallback
- **Schema**: created from the ORM by `python -m chester.schema` before the processes start; no migration tool and no startup DDL

### 2. Ingestion Pipeline

1. Client uploads DICOM or PNG/JPEG with `confirm_deidentified=true`
2. Backend computes SHA-256; deduplicates by SHA-256 and SOP Instance UID, scoped to the receiving organization
3. Parses DICOM with pydicom (all transfer syntaxes via pylibjpeg plugins)
4. Pseudonymizes patient ID with HMAC-SHA256 (`PSEUDONYM_SECRET`); never stores patient name
5. Validates study: `chest | uncertain | non_chest`
6. Generates PNG thumbnail with Pillow
7. Stores original file and thumbnail in object storage
8. Creates Study, Instance, and AnalysisJob records
9. Background worker picks up queued jobs

### 3. Validation States

| State | Condition | Next Action |
|---|---|---|
| `chest` | DX/CR/RG + CHEST/THORAX body part + frontal view | Auto-queue for inference |
| `uncertain` | Some chest evidence, or image-only upload | Route to `needs_review` |
| `non_chest` | Incompatible modality (CT/MR/US) or non-chest body part | Reject |

### 4. AI Inference Worker

- **Model**: `models/chester-all-224.onnx`, the torchxrayvision
  `densenet121-res224-all` classifier; 12 of its 18 outputs are reported
- **Runtime**: ONNX Runtime in the worker process. See `docs/onnx-parity.md` for
  the check that this reproduces the previously deployed TensorFlow.js runtime
- **Preprocessing**: grayscale/windowed pixels → resize shorter side to 224 →
  center crop → CHESTER-compatible `[-1024, 1024]` scaling
- **Output**: Raw sigmoid scores + op-normalized scores + above-threshold flags
- **⚠️ Note**: Op-normalized scores are NOT calibrated probabilities; they remap the
  operational threshold to 0.5 and apply the original CHESTER upper-range display
  emphasis; they are for research presentation only
- **Concurrency**: one job at a time per worker process; several workers may run
  against one database, since jobs are claimed with `FOR UPDATE SKIP LOCKED`
- **Failure**: sets job and study to `error`; `POST /api/studies/{id}/retry`
  requeues a failed study, or one stuck in `processing` behind a dead worker

### 5. STOW-RS Gateway

- Endpoint: `POST /dicomweb/studies` and `POST /dicomweb/studies/{study_uid}`
- OsiriX compatibility: `POST /wado/studies` and
  `POST /wado/studies/{study_uid}` are accepted aliases, including the
  duplicated `/wado/studies/studies` path; the server root is not a STOW
  endpoint
- Auth: `X-DICOM-Ingest-Key`, `Authorization: Bearer`, or Basic, all compared in constant time
- OsiriX authentication: HTTPS Basic auth, any username, with
  `DICOM_INGEST_TOKEN` as the password
- Optional anonymous OsiriX mode: `DICOM_WADO_ANONYMOUS_INGEST=true` removes
  authentication only from the WADO compatibility aliases; the canonical
  DICOMweb endpoint and external gateway remain protected
- Token: `DICOM_INGEST_TOKEN`, with no fallback
- Size: bodies are read in chunks and capped at `DICOM_MAX_UPLOAD_BYTES`
- Ownership: `X-Worklist-Owner` or `DICOM_INGEST_OWNER_EMAIL` must name an
  active user. An unrecognized owner is refused rather than guessed.
- Response: DICOM JSON-style success/failure sequences
- Status codes: 200 (all success), 202 (partial), 409 (all duplicate), 400 (all failure)

### 6. On-Premises DICOM SCP Gateway

- `python -m chester.gateway`; install with `pip install -e "server[gateway]"`
- Receives C-STORE from PACS or modalities and forwards to STOW-RS over HTTPS
- Refuses to start against a plaintext URL, since the token travels per request
- **Must be deployed on-premises behind a firewall**

---

## Production Clinical Infrastructure Requirements

If this system were to be used with real patient data, it would require:

### Infrastructure

- **Dedicated clinical-grade cloud or on-premises infrastructure** — not Replit
- **Encrypted storage at rest** (AES-256) for all DICOM files and database
- **TLS 1.2+ for all network communication** (ingestion, API, internal services)
- **VPN or dedicated network** for DICOM SCP ↔ STOW-RS communication
- **Isolated inference workers** (separate compute, no shared memory with web servers)
- **Redundant storage** with geographic backup

### Security & Compliance

- **BAA** with cloud provider, database vendor, and all subprocessors
- **RBAC**: roles are enforced per endpoint, and studies are scoped to an
  organization; extend rather than replace for clinical use
- **Full audit logging** to immutable, tamper-evident log store
- **Data retention policy** with automated purge per institutional policy
- **Access logging** for all PHI access (HIPAA §164.312(b))
- **Breach notification** procedures per HIPAA §164.400

### Authentication & Authorization

- **MFA required** for all clinical users
- **Session timeout** per clinical policy (typically 15 minutes idle)
- **IP allowlisting** or VPN for API access
- **Service account key rotation** for DICOM ingest token

### Model & Clinical Validation

- **Prospective clinical validation study** with representative patient population
- **IRB approval** for any research involving patient data
- **FDA 510(k) clearance or CE marking** if used for diagnostic support
- **Model drift monitoring** with periodic revalidation
- **Version-locked model weights** with cryptographic hash verification
- **Radiologist review required** before any AI output influences clinical decisions

### Operations

- **24/7 monitoring and alerting** for system availability
- **Backup and disaster recovery** with tested restore procedures
- **Change management process** for model/software updates
- **Incident response plan** for data breaches or system failures
- **Annual penetration testing** and vulnerability assessment

---

## Environment Variables

| Variable | Description | Required |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `SESSION_SECRET` | HMAC key for session tokens and one-time codes | Yes |
| `PSEUDONYM_SECRET` | HMAC key for patient pseudonyms; rotating it remaps every future pseudonym | Yes |
| `ADMIN_USERS` | Comma-separated environment-managed administrators | Yes |
| `DICOM_INGEST_TOKEN` | Service token for STOW-RS; no fallback | Yes |
| `DICOM_INGEST_OWNER_EMAIL` | Active user who owns STOW and C-STORE studies | Required for ingestion |
| `DICOM_WADO_ANONYMOUS_INGEST` | Drops authentication from the WADO aliases only | Optional |
| `DICOM_MAX_UPLOAD_BYTES` | Per-request upload cap (default 100 MB) | Optional |
| `STORAGE_BUCKET` | S3-compatible bucket; unset selects database storage | Optional |
| `SMTP_HOST`, `SMTP_FROM`, `SMTP_PASSWORD` | One-time-code delivery | Yes |
| `DEBUG` | Relax the production secret checks | Dev only |
| `TESTING` | Relax the production secret checks | Test only |

---

## Data Flow: Upload

```
Browser → POST /api/uploads (multipart, confirm_deidentified=true)
  → Internal session-token verification
  → SHA-256 dedup check
  → DICOM parse (pydicom + pylibjpeg)
  → Patient ID pseudonymization (HMAC-SHA256)
  → Chest/uncertain/non_chest validation
  → Thumbnail generation (Pillow)
  → Store original + thumbnail (object storage)
  → Create Study + Instance + AnalysisJob records
  → Return {studies, errors}

Worker process:
  → Claim a queued job (FOR UPDATE SKIP LOCKED) and take a lease
  → Retrieve the study's oldest instance from storage
  → Decode and preprocess (pydicom/Pillow, then resize, crop, scale)
  → Run ONNX Runtime inference outside any transaction
  → Store AnalysisResult (raw_scores, op_normalized_scores, thresholds, above_threshold)
  → Update Study.status → completed, and release the lease
```

---

*Last updated: 2026-08-25*
*Version: 2.0.0*
