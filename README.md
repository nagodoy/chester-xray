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
- Frontal (PA/AP) chest films only: a lateral is refused, and an exam holding
  both is analysed from its frontal image
- Send connections configured in the console, with automatic delivery of a
  finished report
- Network log of every exam received and every report sent, with its outcome
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
already in storage -- which also redraws a two-film exam from its frontal
image, where the thumbnail was taken from the lateral that arrived first:

```bash
cd server
python -m chester.rethumbnail --dry-run   # report, change nothing
python -m chester.rethumbnail             # write
```

Each study is committed on its own, so the run is safe to interrupt, and one
already carrying the current thumbnail is skipped, so it is safe to repeat.
Studies whose bytes are gone are reported and passed over.

## ANALISADA report series

Turns a completed analysis into a Secondary Capture instance and, optionally,
stores it on a viewer:

```bash
cd server
python -m chester.dicom_report --study <id> --out report.dcm
python -m chester.dicom_report --study <id> --send      # to DICOM_SEND_HOST
```

The instance is a new series, `ANALISADA`, inside the source study. It carries
a rendered sheet -- the radiograph over an identification cell and a table of
every reported finding -- and repeats the same findings in a private block:
a creator at `(270F,0010)` and a sequence of `CodeMeaning` / `TextValue`
pairs, modelled on the AZMED/Rayvolve tags.

Each finding is called `ABAIXO` under its operating point, `ACIMA` over
it, and `DUVIDOSO` within ten per cent of it either way -- the band straddles
the threshold, because a score just under is no more decidable than one just
over.

Every tag of the source instance is copied except those describing its pixels
and geometry. Pixel spacing in particular is dropped: left on a rendered
sheet, it would let a viewer measure distances on the report.

`--private-creator` changes the creator string. It defaults to `TORAX AI`
rather than `AZMED`, because that string is what attributes the findings to a
producer; set it to another vendor's only to satisfy a viewer that reads
their block, knowing what it claims.

Sending proposes Explicit VR Little Endian and nothing else. The findings are
in a private sequence, and a private tag is in no receiver's data dictionary,
so under Implicit VR the wire carries no VR either and the far end decodes
the sequence as raw bytes -- the image arrives and the findings do not.

Where it goes is configured in **Ajustes**, not in the environment: a connection
is a row with a name, an address, an AE title and a calling AE title, and an
organization can have several -- a PACS and a reading workstation are two nodes.
`--destination NAME` sends to one of them; without it the report goes to every
active connection, and so does the button on the study.

A connection marked automatic receives the report on its own: when the worker
finishes an analysis it queues one delivery per automatic connection, and the
same worker stores it. A node that is down does not fail the analysis -- the
delivery is retried `DELIVERY_MAX_ATTEMPTS` times, `DELIVERY_RETRY_MINUTES`
apart, and every attempt is a row in the network log.

`DICOM_SEND_HOST` and its companions are the fallback a deployment starts from.
They are used only while an organization has configured nothing at all; once it
has, the console is the only thing that decides where reports go.

Every attempt is recorded, from the command line and from the interface alike:
the **Network logs** page lists what this node received and where it came from,
and what it sent and whether the destination took it. A refused delivery is a row
with the reason the far end gave, not a line in the log of whichever process
happened to run the send.

That fallback defaults to `superpaccs.com.br:11112`, AE title `medfusion`,
calling as `TORAX_AI`; override with `DICOM_SEND_HOST`, `DICOM_SEND_PORT`,
`DICOM_SEND_AE_TITLE` and `DICOM_SEND_CALLING_AE_TITLE`. Nothing leaves the
process without `--send`.

The instance names itself: `SendingApplicationEntityTitle (0002,0017)` carries
the calling AE, so the tag and the association cannot claim different senders.
That tag is file meta, which C-STORE does not transmit -- a receiver writes
its own -- so the producer is also recorded where it does travel, in
`Manufacturer`, `ManufacturerModelName` and the Secondary Capture device tags.
Those would otherwise still name whoever made the source exam.

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
| `POST /api/studies/{id}/send-report` | Build the ANALISADA report and store it on every active destination | Session token |
| `GET /api/settings/destinations` | Configured send connections | Session token, `settings` page |
| `POST/PATCH/DELETE /api/settings/destinations` | Manage send connections | Session token, administrator |
| `POST /api/settings/destinations/{id}/test` | C-ECHO a connection | Session token, administrator |
| `GET /api/network-logs` | Exams received and reports sent | Session token, `network-logs` page |
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
classifier. It reports 15 of the model's 18 outputs. The three withheld are
Fracture, carried over from the original CHESTER configuration, which blanked its
label, and Fibrosis and Nodule, each withdrawn here because it fired on 7 of 7
reference images whose known label is not that finding.

That configuration blanked four other labels -- Infiltration, Pneumothorax,
Pneumonia and Lung Lesion -- and all four are reported again, each on its
published operating point rather than a locally calibrated one. The note in
`server/chester/inference.py` records what that does and does not establish.

`tools/calibrate_thresholds.py` runs that measurement over exams a radiologist
has read, and proposes a threshold per output:

```bash
python tools/calibrate_thresholds.py --manifest exams.csv
python tools/calibrate_thresholds.py --from-filenames examples/0000000[123]*.png
```

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
