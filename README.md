# Chester AI Radiology Assistant

An authenticated research worklist for chest radiographs, built with FastAPI,
React, PostgreSQL, pydicom and the local CHESTER TensorFlow.js model.

## Safety boundary

**Use test or de-identified data only.** This Replit MVP is not a medical
device, is not represented as HIPAA-compliant, and must not be used for
diagnosis, treatment or management of real patients. Model values are research
outputs. Operating-point normalized scores are not calibrated clinical
probabilities.

See [`docs/production-architecture.md`](docs/production-architecture.md) before
considering any clinical deployment.

## What it does

- Authenticated worklist and study detail views with Clerk
- Manual DICOM, PNG and JPEG upload
- DICOMweb STOW-RS ingestion with a service token
- External/on-premises DICOM C-STORE gateway using pynetdicom
- Conservative chest-radiograph validation with manual review for uncertain data
- Persistent PostgreSQL studies, jobs, results and audit events
- Replit App Storage support with an explicit database-backed development fallback
- Background inference with the local CHESTER `xrv-all-45rot15trans15scale` GraphModel
- Raw scores, operating-point normalization, thresholds and model/preprocessing versions

## Run

```bash
npm start
```

The command builds the Vite frontend and starts FastAPI on port 5000. The
configured Replit workflow already uses this command.

## Validate

```bash
npm run check
```

This compiles the Python modules, builds the frontend and runs the backend test
suite.

## Main endpoints

| Endpoint | Purpose | Authentication |
|---|---|---|
| `GET /api/health` | Runtime/database/storage health | Public |
| `GET /api/studies` | Worklist | Clerk session |
| `POST /api/uploads` | Manual multipart upload | Clerk session |
| `GET /api/studies/{id}` | Study, instances and results | Clerk session |
| `POST /api/studies/{id}/review` | Approve/reject uncertain study | Clerk session |
| `POST /api/studies/{id}/retry` | Retry failed inference | Clerk session |
| `POST /dicomweb/studies` | STOW-RS ingestion | Service token |

Configure a dedicated `DICOM_INGEST_TOKEN` before connecting the external
gateway. The MVP can fall back to `SESSION_SECRET`, but token separation and
rotation are required for a production design.

STOW-RS also requires `X-Worklist-Owner` (or `DICOM_INGEST_OWNER_ID`) containing
the Clerk user ID that may view and manage the received studies. Browser
uploads and every study/thumbnail action are isolated to the authenticated
Clerk subject.

## DICOM gateway

The DIMSE listener is intentionally not exposed from Replit. Run
[`gateway/dicom_scp.py`](gateway/dicom_scp.py) inside the protected network and
follow [`gateway/README.md`](gateway/README.md).

## Background

Chester originated as the browser-delivered research prototype described in
[Chester: A Web Delivered Locally Computed Chest X-Ray Disease Prediction
System](https://arxiv.org/abs/1901.11210). This repository now runs the original
local GraphModel through a persistent server-side TensorFlow.js runtime. The
legacy static interface remains historical reference and is not served by the
active application.
