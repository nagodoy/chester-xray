# Chester AI radiology assistant

An authenticated research worklist for chest radiographs: FastAPI, React,
PostgreSQL, pydicom and the CHESTER classifier running locally through ONNX
Runtime.

## Safety boundary

**Use test or de-identified data only.** This is not a medical device, is not
represented as HIPAA-compliant, and must not be used for the diagnosis, treatment
or management of real patients. Model values are research outputs.
Operating-point normalized scores are not calibrated clinical probabilities.

See [`docs/production-architecture.md`](docs/production-architecture.md) before
considering any clinical deployment.

## What it does

- Authenticated worklist and study detail, with email one-time-code sign-in
- Manual DICOM, PNG and JPEG upload
- DICOMweb STOW-RS ingestion with a service token
- On-premises DICOM C-STORE gateway that forwards to STOW-RS
- Conservative chest-radiograph validation, holding anything uncertain for review
- Studies, jobs, results and audit trails in PostgreSQL
- Background inference in a separate worker process
- Raw scores, operating-point normalization, thresholds and recorded versions

## Layout

```
server/   FastAPI application, worker, tests
web/      React single-page application
models/   chester-all-224.onnx, the model the server runs
tools/    ONNX export and the parity check against the retired runtime
docs/     architecture, model parity, comparison notes
examples/ sample radiographs
```

## Running

The API and the worker are separate processes.

```bash
# Backend
cd server
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export DATABASE_URL=postgresql://user:pass@localhost:5432/chester
export SESSION_SECRET=... PSEUDONYM_SECRET=... DICOM_INGEST_TOKEN=...
export ADMIN_USERS=you@example.com
python -m chester.schema
uvicorn chester.main:app --port 5000     # in one shell
python -m chester.worker                 # in another

# Frontend
cd web && npm install && npm run dev
```

`npm start` at the repository root builds the frontend and serves everything from
the API process, which is what the deployment does.

## Maintenance

Thumbnails are written once, at ingestion. Studies filed before the generator
was fixed still carry a thumbnail that was stretched onto a square, and
re-running analysis will not replace it. Rebuild them from the instances
already in storage:

```bash
cd server
python -m chester.rethumbnail --dry-run   # report, change nothing
python -m chester.rethumbnail             # write
```

Each study is committed on its own, so the run is safe to interrupt, and one
already carrying the current thumbnail is skipped, so it is safe to repeat.
Studies whose bytes are gone are reported and passed over.

## Checks

```bash
cd server && ruff check . && ruff format --check . && pytest
cd web && npm run typecheck && npm run build
```

## Endpoints

| Endpoint | Purpose | Authentication |
|---|---|---|
| `GET /api/health` | Runtime, database and storage health | Public |
| `POST /api/auth/request-code` | Send a one-time code | Public |
| `POST /api/auth/verify-code` | Exchange a code for a session | Public |
| `GET /api/studies` | Worklist | Session token |
| `POST /api/uploads` | Manual multipart upload | Session token |
| `GET /api/studies/{id}` | Study, instances and results | Session token |
| `POST /api/studies/{id}/review` | Approve or reject a held study | Session token, reviewer role |
| `POST /api/studies/{id}/retry` | Requeue a failed or stuck study | Session token |
| `DELETE /api/studies/{id}` | Delete a study, its image and its analysis | Session token, administrator |
| `POST /api/studies/bulk-delete` | Delete several studies, reporting each | Session token, administrator |
| `GET /api/access-control/*` | Manage who may sign in | Session token, administrator |
| `POST /dicomweb/studies` | STOW-RS ingestion | Service token |
| `GET /dicomweb/studies` | Connectivity probe | Public |

A `GET` or `HEAD` on any upload path answers a probe describing the endpoint
rather than 405, so a modality can verify the node before it will send to it.
Present the ingest token with the probe and the reply also says whether that
token works, which is what separates a wrong password from an unreachable host.

The canonical STOW-RS URL is `/dicomweb/studies`. For OsiriX configurations that
use a WADO base path, `/wado/studies` also accepts uploads, including the
duplicated `/wado/studies/studies` path some of them emit. Posting to the server
root is not an upload path and returns 405.

OsiriX can authenticate with its HTTP username and password fields: any username,
with the configured `DICOM_INGEST_TOKEN` as the password, over HTTPS.

`DICOM_WADO_ANONYMOUS_INGEST=true` lets the WADO compatibility paths accept
uploads with no credential, for a controlled OsiriX setup that cannot send one.
`/dicomweb/studies` and the external gateway stay protected either way. Anonymous
ingestion means any host that can reach the endpoint can file studies into the
configured owner's worklist; use it only on a trusted network.
`DICOM_INGEST_OWNER_EMAIL` must name an authorized user who will own what arrives.

Deletion removes the stored bytes as well as the rows, and is restricted to
administrators within their own organization. The study's own audit events go
with it; a single `study_deleted` event, holding the study id rather than
anything identifying a patient, records the deletion permanently.

## Access model

Studies belong to a user and an organization. Visibility is: same organization,
and either your own study or a role that reads the whole organization
(administrator, radiologist, consultant, radiology validator). Sign-in is by email
one-time code; who may sign in comes from environment-configured administrators,
then explicit users, then domain rules.

## DICOM gateway

The listener answers C-ECHO as well as C-STORE, so a sender can verify it. A
calling AE outside `--allowed-calling-aes` is refused both.

The DIMSE listener is deliberately not exposed from the web deployment. Run it
inside the protected network:

```bash
pip install -e "server[gateway]"
python -m chester.gateway --stow-url https://your-host --token ... --owner you@example.com
```

## The model

`models/chester-all-224.onnx` is the torchxrayvision `densenet121-res224-all`
classifier. It reports 12 of the model's 18 outputs; the other six are suppressed,
carried over from the original CHESTER configuration.

Regenerate and re-verify it with:

```bash
python tools/export_onnx.py --out models/chester-all-224.onnx
python tools/parity_check.py --with-tfjs examples/*.png
```

[`docs/onnx-parity.md`](docs/onnx-parity.md) records the check that this
reproduces the previously deployed TensorFlow.js runtime to within float32 noise.
`models/xrv-all-45rot15trans15scale` and `scripts/chester_runtime.cjs` are kept
only so that comparison can be re-run.

## Background

Chester began as the browser-delivered research prototype described in
[Chester: A Web Delivered Locally Computed Chest X-Ray Disease Prediction
System](https://arxiv.org/abs/1901.11210).
