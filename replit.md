# Chester on Replit

## Architecture

A React single-page application served by FastAPI, with PostgreSQL for worklist
metadata, jobs, results and audit trails, and a separate worker process running
ONNX inference.

Two processes. `npm start` builds the frontend and serves the API; the worker runs
as its own workflow. Do not move inference back into the web process: it loads the
model into every web worker and makes analysis compete with request handling.

## Safety

Only test or de-identified data. Do not upload PHI, and do not present this as
HIPAA-compliant or clinically validated. Research scores, including
operating-point normalized values, are not calibrated clinical probabilities.

## Ingestion

- Browser uploads: DICOM, PNG and JPEG through authenticated `/api/uploads`
- DICOMweb: STOW-RS at `/dicomweb/studies` using `DICOM_INGEST_TOKEN`
- DIMSE: run `python -m chester.gateway` inside the protected network; never
  expose C-STORE from the web workflow

`DICOM_WADO_ANONYMOUS_INGEST` is enabled for OsiriX. It is a deliberate choice:
any host that can reach `/wado/studies` can file studies into the configured
owner's worklist. Requests are size-capped and read in chunks, but the endpoint
itself is unauthenticated.

Uncertain studies enter `needs_review`. Non-chest studies are rejected rather than
silently discarded.

## Schema

Alembic owns the schema, with the ORM in `server/chester/models.py` as the single
source of truth. Deployment runs `alembic upgrade head` before starting. There is
no startup DDL and no hand-maintained SQL file.

```bash
cd server
alembic revision --autogenerate -m "what changed"
alembic upgrade head
```

CI runs `alembic check`, which fails when the models and migrations disagree.

## Validation

```bash
cd server && ruff check . && pytest
cd web && npm run typecheck && npm run build
```

Clinical infrastructure and compliance requirements are in
`docs/production-architecture.md`.
