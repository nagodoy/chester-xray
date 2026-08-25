# Chester server

FastAPI backend for the chest radiograph research worklist.

## Layout

```
chester/
  config.py        settings; SESSION_SECRET and PSEUDONYM_SECRET are separate
  db.py            engine, session factory, declarative base
  models.py        ORM models -- the single source of schema truth
  security/        roles, page permissions, normalization
migrations/        Alembic; every schema change ships as a revision
tests/             schema is built by running the migrations, not create_all
```

## Setup

```bash
cd server
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export DATABASE_URL=postgresql://user:pass@localhost:5432/chester
alembic upgrade head
```

SQLite works for local development and tests; PostgreSQL is the production target.

## Schema changes

The ORM is authoritative. After editing `chester/models.py`:

```bash
alembic revision --autogenerate -m "what changed"
alembic upgrade head
```

CI runs `alembic check`, which fails when the models and the migrations disagree.
There is no hand-maintained schema file to keep in sync.

## Checks

```bash
ruff check . && ruff format --check .
pytest
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
