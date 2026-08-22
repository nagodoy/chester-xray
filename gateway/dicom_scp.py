"""
DICOM Storage SCP Gateway.

Listens for C-STORE requests and forwards received PS3.10 files
to the STOW-RS endpoint using HTTPS and a service token.

Usage:
    python gateway/dicom_scp.py [--host HOST] [--port PORT] [--ae-title AE_TITLE]
                                 [--stow-url STOW_URL] [--token TOKEN]
                                 [--owner-id CLERK_USER_ID]
                                 [--allowed-calling-aes AE1,AE2]

Environment variables:
    SCP_HOST            Listening host (default: 0.0.0.0)
    SCP_PORT            Listening port (default: 11112)
    SCP_AE_TITLE        Called AE title (default: WORKLIST_SCP)
    SCP_ALLOWED_AES     Comma-separated allowed calling AE titles (default: any)
    STOW_URL            STOW-RS base URL (default: http://localhost:5000)
    DICOM_INGEST_TOKEN  Service token for STOW-RS
    DICOM_INGEST_OWNER_ID Clerk user ID that owns forwarded studies
    SESSION_SECRET      Fallback token
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import tempfile
import time

import requests
from pynetdicom import AE, evt, StoragePresentationContexts  # type: ignore
from pynetdicom.sop_class import (  # type: ignore
    DigitalXRayImageStorageForPresentation,
    ComputedRadiographyImageStorage,
    SecondaryCaptureImageStorage,
    XRayAngiographicImageStorage,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("dicom_scp")

# Supported SOP classes for this gateway
SUPPORTED_SOP_CLASSES = [
    DigitalXRayImageStorageForPresentation,
    ComputedRadiographyImageStorage,
    SecondaryCaptureImageStorage,
    XRayAngiographicImageStorage,
]

# C-STORE status codes
STATUS_SUCCESS = 0x0000
STATUS_FAILURE = 0xA700  # Out of resources
STATUS_REFUSED = 0x0122  # SOP Class Not Supported


def build_handler(stow_url: str, token: str, owner_id: str, allowed_aes: list[str]):
    """Build pynetdicom event handler for C-STORE requests."""

    def handle_store(event: evt.Event):
        """Handle incoming C-STORE request."""
        assoc = event.assoc
        calling_ae = assoc.requestor.ae_title.strip()
        logger.info("C-STORE from AE: %s", calling_ae)

        if allowed_aes and calling_ae not in allowed_aes:
            logger.warning("Rejected calling AE: %s (not in allowed list)", calling_ae)
            return STATUS_REFUSED

        # Get DICOM dataset
        ds = event.dataset
        ds.is_implicit_VR = False
        ds.is_little_endian = True

        # Serialize to PS3.10 bytes
        buf = io.BytesIO()
        try:
            ds.save_as(buf, write_like_original=False)
            dicom_bytes = buf.getvalue()
        except Exception as exc:
            logger.error("Failed to serialize dataset: %s", exc)
            return STATUS_FAILURE

        if not dicom_bytes:
            logger.error("Empty serialized dataset")
            return STATUS_FAILURE

        # Forward to STOW-RS
        endpoint = f"{stow_url.rstrip('/')}/dicomweb/studies"
        boundary = "---DICOMSCP_BOUNDARY_001"
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/dicom\r\n"
            f"\r\n"
        ).encode() + dicom_bytes + f"\r\n--{boundary}--\r\n".encode()

        headers = {
            "Content-Type": f"multipart/related; type=application/dicom; boundary={boundary}",
            "X-DICOM-Ingest-Key": token,
            "X-Ingest-Source": "c-store",
            "X-Worklist-Owner": owner_id,
        }

        for attempt in range(3):
            try:
                resp = requests.post(
                    endpoint,
                    data=body,
                    headers=headers,
                    timeout=30,
                    verify=True,
                )
                logger.info("STOW-RS response: %d for AE %s", resp.status_code, calling_ae)
                if resp.status_code in (200, 202, 409):
                    return STATUS_SUCCESS
                else:
                    logger.error("STOW-RS error %d: %s", resp.status_code, resp.text[:200])
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                    continue
            except requests.exceptions.Timeout:
                logger.warning("STOW-RS timeout (attempt %d)", attempt + 1)
                if attempt < 2:
                    time.sleep(2)
            except Exception as exc:
                logger.error("STOW-RS request error: %s", exc)
                if attempt < 2:
                    time.sleep(2)

        return STATUS_FAILURE

    return handle_store


def main():
    parser = argparse.ArgumentParser(description="DICOM Storage SCP Gateway")
    parser.add_argument("--host", default=os.environ.get("SCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SCP_PORT", "11112")))
    parser.add_argument("--ae-title", default=os.environ.get("SCP_AE_TITLE", "WORKLIST_SCP"))
    parser.add_argument("--stow-url", default=os.environ.get("STOW_URL", "http://localhost:5000"))
    parser.add_argument(
        "--token",
        default=os.environ.get("DICOM_INGEST_TOKEN", os.environ.get("SESSION_SECRET", "")),
    )
    parser.add_argument(
        "--owner-id",
        default=os.environ.get("DICOM_INGEST_OWNER_ID", ""),
        help="Clerk user ID that owns studies forwarded by this gateway",
    )
    parser.add_argument(
        "--allowed-calling-aes",
        default=os.environ.get("SCP_ALLOWED_AES", ""),
        help="Comma-separated list of allowed calling AE titles; empty = any",
    )
    args = parser.parse_args()

    if not args.token:
        parser.error("--token or DICOM_INGEST_TOKEN is required")
    if not args.owner_id:
        parser.error("--owner-id or DICOM_INGEST_OWNER_ID is required")

    allowed_aes = [ae.strip() for ae in args.allowed_calling_aes.split(",") if ae.strip()]

    logger.info(
        "Starting DICOM SCP: AE=%s host=%s port=%d stow=%s",
        args.ae_title, args.host, args.port, args.stow_url,
    )
    if allowed_aes:
        logger.info("Allowed calling AEs: %s", allowed_aes)
    else:
        logger.info("Allowed calling AEs: any")

    ae = AE(ae_title=args.ae_title)

    # Add supported presentation contexts
    for sop_class in SUPPORTED_SOP_CLASSES:
        ae.add_supported_context(sop_class)

    # Also support all standard storage contexts
    for context in StoragePresentationContexts:
        ae.add_supported_context(context.abstract_syntax)

    handler = build_handler(args.stow_url, args.token, args.owner_id, allowed_aes)
    handlers = [(evt.EVT_C_STORE, handler)]

    server = ae.start_server(
        (args.host, args.port),
        block=True,
        evt_handlers=handlers,
    )


if __name__ == "__main__":
    main()
