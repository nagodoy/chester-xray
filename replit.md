# Chester on Replit

## Active architecture

The active application is a React/Vite single-page app served by FastAPI. The
backend uses PostgreSQL for worklist metadata, jobs, results and audit events,
and runs TorchXRayVision inference in a single background worker.

`npm start` builds the frontend and starts Uvicorn on port 5000. Do not switch
the workflow back to the legacy `server.js` static server.

## Safety

Only test or de-identified data may be uploaded. Do not use PHI or present this
MVP as HIPAA-compliant or clinically validated. Research scores, including
operating-point normalized values, are not calibrated clinical probabilities.

## Ingestion

- Browser uploads: DICOM, PNG and JPEG through authenticated `/api/uploads`
- DICOMweb: STOW-RS at `/dicomweb/studies` using `DICOM_INGEST_TOKEN`
- DIMSE: run the external `gateway/dicom_scp.py`; never expose C-STORE directly
  from the Replit web workflow

Uncertain studies enter `needs_review`. Non-chest studies are rejected rather
than silently discarded.

## Schema and validation

The development schema is in `db/schema.sql`. Replit Publish handles the
development-to-production schema diff; production startup must not create or
migrate tables.

Run the complete local validation with:

```bash
npm run check
```

Clinical infrastructure and compliance requirements are documented in
`docs/production-architecture.md`.