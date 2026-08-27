"""On-premises DICOM Storage SCP that forwards to STOW-RS.

Run this inside the protected network. The DIMSE listener is deliberately not
exposed from the web deployment: C-STORE has no transport security of its own and
the port must not face the internet.

Usage:
    python -m chester.gateway --stow-url https://host --token ... --owner user@org

Environment variables mirror every flag: SCP_HOST, SCP_PORT, SCP_AE_TITLE,
SCP_ALLOWED_AES, STOW_URL, DICOM_INGEST_TOKEN, DICOM_INGEST_OWNER_EMAIL.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys

logger = logging.getLogger("chester.gateway")

STATUS_SUCCESS = 0x0000
STATUS_OUT_OF_RESOURCES = 0xA700
STATUS_SOP_CLASS_NOT_SUPPORTED = 0x0122

BOUNDARY = "chester-gateway-boundary"
RETRY_DELAYS = (1.0, 2.0, 4.0)


def build_multipart(dicom_bytes: bytes) -> tuple[bytes, str]:
    body = (
        f"--{BOUNDARY}\r\nContent-Type: application/dicom\r\n\r\n".encode()
        + dicom_bytes
        + f"\r\n--{BOUNDARY}--\r\n".encode()
    )
    content_type = f'multipart/related; type="application/dicom"; boundary={BOUNDARY}'
    return body, content_type


def build_handler(stow_url: str, token: str, owner: str, allowed_aes: list[str]):
    """Return a pynetdicom C-STORE handler that forwards to STOW-RS."""
    import time

    import requests

    endpoint = f"{stow_url.rstrip('/')}/dicomweb/studies"

    def handle_store(event):
        calling_ae = event.assoc.requestor.ae_title.strip()
        if allowed_aes and calling_ae not in allowed_aes:
            logger.warning("Refused calling AE %s: not in the allowed list", calling_ae)
            return STATUS_SOP_CLASS_NOT_SUPPORTED

        try:
            buffer = io.BytesIO()
            event.dataset.save_as(buffer, enforce_file_format=True)
            dicom_bytes = buffer.getvalue()
        except Exception:
            logger.exception("Could not serialize dataset from %s", calling_ae)
            return STATUS_OUT_OF_RESOURCES

        if not dicom_bytes:
            logger.error("Empty serialized dataset from %s", calling_ae)
            return STATUS_OUT_OF_RESOURCES

        body, content_type = build_multipart(dicom_bytes)
        headers = {
            "Content-Type": content_type,
            "X-DICOM-Ingest-Key": token,
            "X-Ingest-Source": "c-store",
            "X-Worklist-Owner": owner,
        }

        for attempt, delay in enumerate((*RETRY_DELAYS, None), start=1):
            try:
                response = requests.post(
                    endpoint, data=body, headers=headers, timeout=30, verify=True
                )
            except requests.RequestException as exc:
                logger.warning("Forward attempt %d failed: %s", attempt, exc)
            else:
                # 409 means already stored, which is a success from the sender's view.
                if response.status_code in (200, 202, 409):
                    logger.info(
                        "Forwarded instance from %s (HTTP %d)", calling_ae, response.status_code
                    )
                    return STATUS_SUCCESS
                logger.warning(
                    "Forward attempt %d rejected: HTTP %d", attempt, response.status_code
                )
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    # A client error will not fix itself on retry.
                    break
            if delay is not None:
                time.sleep(delay)

        return STATUS_OUT_OF_RESOURCES

    return handle_store


def build_echo_handler(allowed_aes: list[str]):
    """Return a pynetdicom C-ECHO handler.

    A sender verifies a node before it will store to it, so refusing
    verification means the operator never gets as far as an image. The calling
    AE is held to the same allowed list as C-STORE: a peer this gateway would
    not accept an image from should not be told the association is good.
    """

    def handle_echo(event):
        calling_ae = event.assoc.requestor.ae_title.strip()
        if allowed_aes and calling_ae not in allowed_aes:
            logger.warning("Refused C-ECHO from %s: not in the allowed list", calling_ae)
            return STATUS_SOP_CLASS_NOT_SUPPORTED
        logger.info("C-ECHO from %s", calling_ae)
        return STATUS_SUCCESS

    return handle_echo


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("SCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SCP_PORT", "11112")))
    parser.add_argument("--ae-title", default=os.environ.get("SCP_AE_TITLE", "WORKLIST_SCP"))
    parser.add_argument("--stow-url", default=os.environ.get("STOW_URL", ""))
    parser.add_argument("--token", default=os.environ.get("DICOM_INGEST_TOKEN", ""))
    parser.add_argument("--owner", default=os.environ.get("DICOM_INGEST_OWNER_EMAIL", ""))
    parser.add_argument(
        "--allowed-calling-aes",
        default=os.environ.get("SCP_ALLOWED_AES", ""),
        help="Comma-separated calling AE titles; empty accepts any",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    args = parse_args(argv)

    missing = [
        name
        for name, value in (
            ("--stow-url", args.stow_url),
            ("--token", args.token),
            ("--owner", args.owner),
        )
        if not value
    ]
    if missing:
        logger.error("Missing required configuration: %s", ", ".join(missing))
        return 2

    if not args.stow_url.startswith("https://"):
        # The token travels in a header on every forwarded instance.
        logger.error("--stow-url must use HTTPS; the ingest token is sent with each request")
        return 2

    from pynetdicom import AE, evt
    from pynetdicom.sop_class import (
        ComputedRadiographyImageStorage,
        DigitalXRayImageStorageForPresentation,
        SecondaryCaptureImageStorage,
        Verification,
        XRayAngiographicImageStorage,
    )

    allowed = [item.strip() for item in args.allowed_calling_aes.split(",") if item.strip()]
    application_entity = AE(ae_title=args.ae_title)
    for sop_class in (
        Verification,
        DigitalXRayImageStorageForPresentation,
        ComputedRadiographyImageStorage,
        SecondaryCaptureImageStorage,
        XRayAngiographicImageStorage,
    ):
        application_entity.add_supported_context(sop_class)

    handlers = [
        (evt.EVT_C_STORE, build_handler(args.stow_url, args.token, args.owner, allowed)),
        (evt.EVT_C_ECHO, build_echo_handler(allowed)),
    ]
    logger.info(
        "Listening on %s:%d as %s, forwarding to %s",
        args.host,
        args.port,
        args.ae_title,
        args.stow_url,
    )
    application_entity.start_server((args.host, args.port), evt_handlers=handlers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
