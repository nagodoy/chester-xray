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
│  React/Vite SPA     ←→  Email OTP + internal sessions      │
└─────────────────────┬───────────────────────────────────────┘
                      │ HTTPS
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI Backend (app/)                      │
│                                                              │
│  /api/health          — Public health check                  │
│  /api/studies/*       — Protected CRUD (session token)       │
│  /api/uploads         — Protected multipart upload           │
│  /dicomweb/studies*   — STOW-RS (service token)              │
└──────┬──────────────────────────┬───────────────────────────┘
       │                          │
       ▼                          ▼
┌──────────────┐         ┌────────────────────────┐
│  PostgreSQL   │         │  Object Storage         │
│  (Replit DB)  │         │  (Replit Object Store   │
│               │         │   or DB-backed fallback)│
│  studies      │         │  originals/{id}/*.dcm   │
│  instances    │         │  thumbnails/{id}.png    │
│  analysis_*   │         └────────────────────────┘
│  audit_events │
└──────────────┘

Background Worker Thread:
  ┌─────────────────────────────────────────┐
  │  Single-threaded inference worker        │
  │  - Polls analysis_jobs (queued)          │
  │  - Uses persistent local CHESTER runtime │
  │  - Stores AnalysisResult in DB          │
  └─────────────────────────────────────────┘

On-Premises DICOM Gateway (gateway/):
  ┌─────────────────────────────────────────┐
  │  pynetdicom Storage SCP                  │
  │  - C-STORE receiver (port 11112)         │
  │  - Forwards to STOW-RS via HTTPS         │
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
- **Storage**: Replit Object Storage (primary) or database-backed bytes (fallback)
- **Schema**: `db/schema.sql` — no startup DDL in production

### 2. Ingestion Pipeline

1. Client uploads DICOM or PNG/JPEG with `confirm_deidentified=true`
2. Backend computes SHA-256; deduplicates by SHA-256 and SOP Instance UID
3. Parses DICOM with pydicom (all transfer syntaxes via pylibjpeg plugins)
4. Pseudonymizes patient ID with HMAC-SHA256 (SESSION_SECRET); never stores patient name
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

- **Model**: local CHESTER TensorFlow.js GraphModel `xrv-all-45rot15trans15scale`
- **Runtime**: persistent local Node process; model and seven weight shards load
  once, with no external model download and no TorchXRayVision fallback
- **Preprocessing**: grayscale/windowed pixels → resize shorter side to 224 →
  center crop → CHESTER-compatible `[-1024, 1024]` scaling
- **Output**: Raw sigmoid scores + op-normalized scores + above-threshold flags
- **⚠️ Note**: Op-normalized scores are NOT calibrated probabilities; they remap the
  operational threshold to 0.5 and apply the original CHESTER upper-range display
  emphasis; they are for research presentation only
- **Concurrency**: 1 worker thread; no parallel inference
- **Failure**: Sets job + study to `error`; retry supported via `POST /api/studies/{id}/retry`

### 5. STOW-RS Gateway

- Endpoint: `POST /dicomweb/studies` and `POST /dicomweb/studies/{study_uid}`
- Auth: `X-DICOM-Ingest-Key` header or `Authorization: Bearer` compared in constant time
- Token: `DICOM_INGEST_TOKEN` (falls back to `SESSION_SECRET`)
- Ownership: `X-Worklist-Owner` or `DICOM_INGEST_OWNER_ID` must identify an
  authorized email; all study API queries enforce the authenticated email as
  owner. Historical non-email owners require an explicit audited alias.
- Response: DICOM JSON-style success/failure sequences
- Status codes: 200 (all success), 202 (partial), 409 (all duplicate), 400 (all failure)

### 6. On-Premises DICOM SCP Gateway

- See `gateway/README.md` and `gateway/DICOM_CONFORMANCE.md`
- Receives C-STORE from PACS/modalities; forwards to STOW-RS via HTTPS
- **Must be deployed on-premises behind a firewall — not on Replit**

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
- **RBAC** (Role-Based Access Control): radiologist, technologist, admin roles
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
| `SESSION_SECRET` | HMAC key for patient ID pseudonymization; fallback service token | Yes |
| `DICOM_INGEST_TOKEN` | Service token for STOW-RS; defaults to SESSION_SECRET | Recommended |
| `DICOM_INGEST_OWNER_ID` | Authorized email that owns STOW/C-STORE studies | Required for gateway |
| `REPLIT_OBJECT_STORAGE_BUCKET_ID` | Replit Object Storage bucket ID | Optional |
| `DEBUG` | Enable debug mode (dev-user passthrough) | Dev only |
| `TESTING` | Suppress worker startup during tests | Test only |

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

Background worker (separate thread):
  → Poll analysis_jobs WHERE status='queued'
  → Retrieve original file from storage
  → Decode + preprocess (pydicom/PIL + CHESTER transforms)
  → Run local CHESTER GraphModel inference (persistent TensorFlow.js runtime)
  → Store AnalysisResult (raw_scores, op_normalized_scores, thresholds, above_threshold)
  → Update Study.status → completed
```

---

*Last updated: 2026-08-23*
*Version: 1.1.0 MVP*
