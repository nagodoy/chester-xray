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
silently discarded, and so are lateral films: only the frontal (PA/AP) projection
is analysed. An exam sent as two films shares one Study Instance UID, so both
become instances of one study, which is then scored, illustrated and reported
from its frontal instance whichever arrived first.

## Delivery

Where a finished report is stored is a row in `send_destinations`, edited in the
console, not an environment variable. `DICOM_SEND_HOST` and its companions remain
as the fallback used while an organization has configured nothing.

A destination marked `auto_send` gets the report without anyone asking: the
worker queues a `delivery_jobs` row when an analysis completes and stores it on
the next pass. It is a queue rather than a call at the end of the analysis
because a node that is down must not fail the analysis that produced the report;
attempts are retried and every one of them is recorded in `network_logs`.

Sending needs `pynetdicom`, which is a runtime dependency for that reason -- the
gateway extra now only carries `requests`.

## Schema

The ORM in `server/chester/models.py` is the schema. There is no migration tool:
`python -m chester.schema` creates every table from the models, and both the
deployment and the dev workflow run it once before starting the API and worker.

```bash
cd server && python -m chester.schema
```

**`create_all` only creates whole tables. It never adds a column to a table that
already exists.** So a model change made against a live database applies to
nothing, and there is no in-place upgrade path -- the fix is to drop the affected
tables and let the schema step recreate them. Because that gap is invisible on its
own, `chester.schema.drift()` reports it: the schema command exits non-zero, CI
runs it, and the API logs an error at startup rather than serving a stale database
quietly.

The database is expected to start empty. `bootstrap_env_admins` creates the default
organization and the `ADMIN_USERS` accounts on first start.

### The Publish database step stays off

Publish offers to migrate the database by diffing this schema against production
and generating DDL. **Leave that step disabled.** It rewrites tables in place with
casts it does not emit, and truncates tables to add `NOT NULL` columns. This
project has no migration story by choice: production starts from an empty database
and the schema step builds it.

## Validation

```bash
cd server && ruff check . && pytest
cd web && npm run typecheck && npm run build
```

Clinical infrastructure and compliance requirements are in
`docs/production-architecture.md`.
