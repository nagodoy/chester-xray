# DICOM Storage SCP Gateway

This gateway implements a DICOM Storage SCP (Service Class Provider) using pynetdicom.
It accepts C-STORE requests and forwards received PS3.10 files to the Radiology Worklist STOW-RS endpoint.

## Features

- Accepts C-STORE requests for DX, CR, SC, and XA SOP classes (plus all standard storage contexts)
- Forwards received DICOM files to `/dicomweb/studies` as `multipart/related; type=application/dicom`
- Authenticates via `X-DICOM-Ingest-Key` header (DICOM_INGEST_TOKEN or SESSION_SECRET)
- Assigns forwarded studies to one authorized email via `X-Worklist-Owner`
- Configurable AE title, host, port, and allowed calling AE titles
- Automatic retry with exponential backoff on STOW-RS failures

## Quick Start

```bash
# Install dependencies
pip install pynetdicom requests

# Run with defaults
python gateway/dicom_scp.py

# Custom configuration
python gateway/dicom_scp.py \
  --host 0.0.0.0 \
  --port 11112 \
  --ae-title MY_SCP \
  --stow-url https://my-worklist.example.com \
  --token my-ingest-token \
  --owner-id reader@example.com \
  --allowed-calling-aes PACS_1,MODALITY_1
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SCP_HOST` | `0.0.0.0` | Listening host |
| `SCP_PORT` | `11112` | Listening port |
| `SCP_AE_TITLE` | `WORKLIST_SCP` | Called AE title |
| `SCP_ALLOWED_AES` | *(any)* | Comma-separated allowed calling AE titles |
| `STOW_URL` | `http://localhost:5000` | STOW-RS base URL |
| `DICOM_INGEST_TOKEN` | `SESSION_SECRET` | Service authentication token |
| `DICOM_INGEST_OWNER_ID` | *(required)* | Authorized email that owns received studies |

## Published Chester deployment

For the current published Chester deployment, configure the gateway service
with this base URL:

```text
STOW_URL=https://rx.nelsongodoy.com.br
```

The gateway appends `/dicomweb/studies` when forwarding each C-STORE, so the
resulting STOW-RS endpoint is:

```text
https://rx.nelsongodoy.com.br/dicomweb/studies
```

The alternate published hostname `https://torax.replit.app` remains valid but
is not the canonical gateway target.

## Security Notes

- Use TLS termination (e.g., nginx) in front of the STOW-RS endpoint in production.
- Restrict `SCP_ALLOWED_AES` to known PACS/modality AE titles.
- The DICOM SCP port (11112) should not be exposed to the public internet.
- See DICOM Conformance Statement for full security guidance.
