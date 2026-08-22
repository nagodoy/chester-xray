# DICOM Conformance Statement

**Application Name:** Radiology Worklist MVP — DICOM SCP Gateway  
**Version:** 1.0.0  
**Date:** 2026

## 1. Introduction

This conformance statement describes the DICOM capabilities of the SCP gateway component
of the Radiology Worklist MVP. This application is intended for **research and de-identified
data only**. It is NOT validated for clinical diagnostic use and does NOT constitute a
medical device.

## 2. Implementation

| Property | Value |
|---|---|
| Implementation Class UID | 2.25.12345678901234567890 |
| Implementation Version Name | WORKLIST_SCP_1_0 |
| Default AE Title | WORKLIST_SCP |
| Port | 11112 (configurable) |

## 3. Networking

### 3.1 Supported Roles

| Role | Support |
|---|---|
| SCU | No |
| SCP | Yes |

### 3.2 Association Policies

- Maximum number of simultaneous associations: 10 (pynetdicom default)
- Maximum PDU size: 65536 bytes
- Asynchronous operations: Not supported
- Implementation identification negotiation: Not supported

## 4. Storage SCP Conformance

### 4.1 Supported SOP Classes

| SOP Class | SOP Class UID |
|---|---|
| Digital X-Ray Image Storage – For Presentation | 1.2.840.10008.5.1.4.1.1.1.1 |
| Computed Radiography Image Storage | 1.2.840.10008.5.1.4.1.1.1 |
| Secondary Capture Image Storage | 1.2.840.10008.5.1.4.1.1.7 |
| X-Ray Angiographic Image Storage | 1.2.840.10008.5.1.4.1.1.12.1 |
| All Standard Storage SOP Classes | (see pynetdicom StoragePresentationContexts) |

### 4.2 Supported Transfer Syntaxes

| Transfer Syntax | UID |
|---|---|
| Implicit VR Little Endian | 1.2.840.10008.1.2 |
| Explicit VR Little Endian | 1.2.840.10008.1.2.1 |
| JPEG Baseline | 1.2.840.10008.1.2.4.50 |
| JPEG 2000 Lossless | 1.2.840.10008.1.2.4.90 |
| JPEG 2000 | 1.2.840.10008.1.2.4.91 |
| RLE Lossless | 1.2.840.10008.1.2.5 |

### 4.3 C-STORE Response Status Codes

| Status | Code | Condition |
|---|---|---|
| Success | 0x0000 | File forwarded to STOW-RS successfully |
| Failure – Out of Resources | 0xA700 | STOW-RS unreachable or returned error |
| Refused – SOP Class Not Supported | 0x0122 | Calling AE not in allowed list |

## 5. Data Handling

- Received PS3.10 files are forwarded verbatim to the STOW-RS endpoint
- No DICOM attributes are modified by the gateway
- Patient name is never logged; only SOP Instance UID is logged
- Files are not stored locally; they are forwarded in memory

## 6. Security

**IMPORTANT:** This gateway and the associated worklist application are designed for
**de-identified research data only**. Do not send Protected Health Information (PHI)
to this system without:

1. A formal HIPAA Business Associate Agreement (BAA) with all hosting providers
2. Appropriate technical safeguards (TLS 1.2+, access controls, audit logging)
3. A Data Use Agreement with relevant institutional review
4. Network isolation for the DICOM port (11112)

### 6.1 Access Control

- The gateway authenticates to the STOW-RS endpoint using a shared service token
- Calling AE titles can be restricted via `SCP_ALLOWED_AES`
- No encryption is applied at the DICOM level (DICOM TLS is not implemented)
- Use network-level TLS (VPN or TLS proxy) for production deployments

### 6.2 Audit

- Association accept/reject events are logged to stdout
- C-STORE success/failure is logged with SOP Instance UID
- Detailed audit events are recorded in the backend audit_events table

## 7. Limitations

- No DICOM TLS (DICOM Secure Transport)
- No Query/Retrieve SCP (C-FIND, C-MOVE, C-GET)
- No Worklist SCP (C-FIND for Modality Worklist)
- No Storage Commitment (N-ACTION, N-EVENT-REPORT)
- No MPPS (N-CREATE, N-SET)
- Multi-frame images: first frame only is used for AI analysis
- This is NOT a regulatory-cleared medical device

## 8. References

- DICOM Standard: https://www.dicomstandard.org/
- pynetdicom: https://pydicom.github.io/pynetdicom/
- DICOM PS3.4 Storage SOP Classes: https://dicom.nema.org/dicom/2013/output/chtml/part04/chapter_B.html
