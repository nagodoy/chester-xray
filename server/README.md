# Chester server

FastAPI backend for the chest radiograph research worklist.

## Layout

```
chester/
  config.py        settings; SESSION_SECRET and PSEUDONYM_SECRET are separate
  db.py            engine, session factory, declarative base
  models.py        ORM models -- the single source of schema truth
  security/        roles, page permissions, normalization
  schema.py        creates the schema from the models, and reports drift
tests/             schema is built the same way the application builds it
```

## Setup

```bash
cd server
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export DATABASE_URL=postgresql://user:pass@localhost:5432/chester
python -m chester.schema
```

SQLite works for local development and tests; PostgreSQL is the production target.

## Schema changes

The ORM is authoritative. After editing `chester/models.py`:

```bash
python -m chester.schema
```

There is no migration tool. `create_all` creates whole tables only -- it never adds
a column to a table that already exists -- so changing a model on a live database
means dropping the affected tables and recreating them. `chester.schema.drift()`
reports the mismatch instead of leaving it to surface as a query error: the command
exits non-zero, CI runs it, and the API logs it at startup.

## Running

The API and the worker are separate processes:

```bash
uvicorn chester.main:app --host 0.0.0.0 --port 5000
python -m chester.worker
```

The worker is not started by the web application. Running inference inside uvicorn
loaded the model into every web process and made analysis compete with request
handling; as its own process the queue scales independently, and several workers
can run against one database because jobs are claimed with SKIP LOCKED.

## Checks

```bash
ruff check . && ruff format --check .
pytest

# Also exercise the concurrency tests, which need a real database:
CHESTER_TEST_POSTGRES_URL=postgresql+psycopg://user:pass@localhost/chester_test pytest
```

## Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (`postgresql://` is rewritten to psycopg 3) |
| `SESSION_SECRET` | HMAC key for session tokens and OTP hashes |
| `PSEUDONYM_SECRET` | HMAC key for patient pseudonyms; see below |
| `ADMIN_USERS` | Comma-separated environment-managed administrators |
| `DICOM_INGEST_TOKEN` | Service token for STOW-RS ingestion |
| `DICOM_INGEST_OWNER_EMAIL` | Authorized address that receives ingested studies |
| `DICOM_WADO_ANONYMOUS_INGEST` | Allows unauthenticated POST to the WADO aliases |
| `STORAGE_BUCKET` | S3-compatible bucket; unset falls back to database-backed storage |

`PSEUDONYM_SECRET` is deliberately separate from `SESSION_SECRET`. Deriving pseudonyms
from the session secret means rotating that secret — which you must be able to do —
silently changes every future pseudonym, so the same patient stops mapping to the same
identifier. Rotating `PSEUDONYM_SECRET` has that effect by design, so treat it as
long-lived.

Outside development the process refuses to start on default secrets.
