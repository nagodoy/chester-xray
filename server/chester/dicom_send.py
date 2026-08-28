"""Send a generated instance to a DICOM node over C-STORE.

The gateway in `chester.gateway` receives images; this sends one. They are
separate because they face opposite ways: the gateway listens inside the
protected network for a modality, and this reaches out to a workstation --
an OsiriX listener, typically -- to hand it a result.
"""

from __future__ import annotations

import logging

from chester.config import settings

logger = logging.getLogger(__name__)

SECONDARY_CAPTURE_SOP_CLASS = "1.2.840.10008.5.1.4.1.1.7"


class SendNotConfigured(RuntimeError):
    """Raised when no destination node has been configured."""


class SendFailed(RuntimeError):
    """Raised when the association or the store itself was refused."""


def destination_configured() -> bool:
    return bool(settings.dicom_send_host)


def send_dataset(
    dataset,
    *,
    host: str | None = None,
    port: int | None = None,
    ae_title: str | None = None,
    calling_ae_title: str | None = None,
) -> None:
    """Store one dataset on the destination node.

    Raises rather than returning a status: a report the operator believes was
    delivered but was not is worse than an error they can see.
    """
    host = host or settings.dicom_send_host
    if not host:
        raise SendNotConfigured("No DICOM destination configured (DICOM_SEND_HOST)")

    port = port or settings.dicom_send_port
    ae_title = ae_title or settings.dicom_send_ae_title
    calling_ae_title = calling_ae_title or settings.dicom_send_calling_ae_title

    from pydicom.uid import ExplicitVRLittleEndian
    from pynetdicom import AE
    from pynetdicom.sop_class import SecondaryCaptureImageStorage

    application_entity = AE(ae_title=calling_ae_title)
    # Explicit VR only, which is a deliberate restriction rather than a
    # preference. The findings ride in a private sequence, and a private tag
    # is in no receiver's data dictionary: under Implicit VR the wire carries
    # no VR either, so the far end cannot tell the sequence from a blob and
    # decodes it as raw bytes -- the image arrives and the findings do not.
    #
    # Offering Implicit as a fallback would not help: the acceptor picks from
    # its own list, not ours, and most pick Implicit first. So the only way to
    # be sure the findings survive is to propose nothing else, and to fail
    # loudly if the destination cannot speak it.
    application_entity.add_requested_context(
        SecondaryCaptureImageStorage,
        transfer_syntax=[ExplicitVRLittleEndian],
    )

    association = application_entity.associate(host, port, ae_title=ae_title)
    if not association.is_established:
        raise SendFailed(
            f"{ae_title} at {host}:{port} refused the association. This sends "
            "Secondary Capture as Explicit VR Little Endian only, because the "
            "findings are in a private sequence that Implicit VR would strip "
            "of its structure."
        )

    try:
        status = association.send_c_store(dataset)
    finally:
        association.release()

    if not status:
        raise SendFailed(f"{ae_title} returned no status for the store")
    code = getattr(status, "Status", None)
    if code != 0x0000:
        raise SendFailed(f"{ae_title} rejected the store with status 0x{code:04X}")
    logger.info("Stored %s on %s at %s:%d", dataset.SOPInstanceUID, ae_title, host, port)


def echo(
    *,
    host: str,
    port: int,
    ae_title: str,
    calling_ae_title: str,
) -> None:
    """Verify a destination with C-ECHO, raising when it does not answer.

    What this proves is that the node is reachable and accepts an association
    from this AE title -- which is what an operator is asking when they save a
    connection. It says nothing about whether the node will accept a Secondary
    Capture, because Verification is a different presentation context.
    """
    from pynetdicom import AE
    from pynetdicom.sop_class import Verification

    application_entity = AE(ae_title=calling_ae_title)
    application_entity.add_requested_context(Verification)

    association = application_entity.associate(host, port, ae_title=ae_title)
    if not association.is_established:
        raise SendFailed(f"{ae_title} at {host}:{port} refused the association")

    try:
        status = association.send_c_echo()
    finally:
        association.release()

    if not status:
        raise SendFailed(f"{ae_title} returned no status for the echo")
    code = getattr(status, "Status", None)
    if code != 0x0000:
        raise SendFailed(f"{ae_title} answered the echo with status 0x{code:04X}")
