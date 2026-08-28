"""Build the TORAX IA secondary capture from a source instance and a result.

A new series inside the same study, carrying the rendered sheet as its pixels
and the findings as private tags, so a viewer that reads neither the picture
nor the tags still files it under the right patient and study.
"""

from __future__ import annotations

import io
import logging

from chester.imaging.report_image import render_report
from chester.report import finding_rows

logger = logging.getLogger(__name__)

SECONDARY_CAPTURE_SOP_CLASS = "1.2.840.10008.5.1.4.1.1.7"
EXPLICIT_VR_LITTLE_ENDIAN = "1.2.840.10008.1.2.1"

SERIES_DESCRIPTION = "TORAX IA"
SERIES_NUMBER = 9901
PRODUCER = "TORAX AI"

# The private block, laid out as the AZMED/Rayvolve tags this was modelled on:
# a creator at (gggg,0010) and then five elements in its block. The creator
# string is ours rather than theirs, because that string is what says who
# produced the data -- borrowing another vendor's would make these findings
# indistinguishable from that vendor's own in any viewer downstream.
PRIVATE_GROUP = 0x270F
PRIVATE_BLOCK = 0x10
DEFAULT_PRIVATE_CREATOR = "TORAX AI"

# Tags that describe the source pixels or the geometry they were acquired in.
# Carried onto a rendered sheet they would be wrong, and two of them are worse
# than wrong: a viewer would happily measure distances on the report using the
# original's pixel spacing.
DROPPED_TAGS: tuple[tuple[int, int], ...] = (
    (0x0018, 0x1164),  # ImagerPixelSpacing
    (0x0028, 0x0030),  # PixelSpacing
    (0x0018, 0x1114),  # EstimatedRadiographicMagnificationFactor
    (0x0018, 0x1050),  # SpatialResolution
    (0x0028, 0x0106),  # SmallestImagePixelValue
    (0x0028, 0x0107),  # LargestImagePixelValue
    (0x0028, 0x1050),  # WindowCenter
    (0x0028, 0x1051),  # WindowWidth
    (0x0028, 0x1052),  # RescaleIntercept
    (0x0028, 0x1053),  # RescaleSlope
    (0x0028, 0x1054),  # RescaleType
    (0x0028, 0x1040),  # PixelIntensityRelationship
    (0x0028, 0x1041),  # PixelIntensityRelationshipSign
    (0x0028, 0x3000),  # ModalityLUTSequence
    (0x0028, 0x3010),  # VOILUTSequence
    (0x0018, 0x1600),  # ShutterShape
    (0x0018, 0x1620),  # VerticesOfThePolygonalShutter
    (0x0018, 0x1700),  # CollimatorShape
    (0x0018, 0x1702),  # CollimatorLeftVerticalEdge
    (0x0018, 0x1704),  # CollimatorRightVerticalEdge
    (0x0018, 0x1706),  # CollimatorUpperHorizontalEdge
    (0x0018, 0x1708),  # CollimatorLowerHorizontalEdge
    (0x0070, 0x005A),  # DisplayedAreaSelectionSequence
    (0x2050, 0x0020),  # PresentationLUTShape
)


def _uid():
    from pydicom.uid import generate_uid

    return generate_uid()


def build_report_dataset(
    source_bytes: bytes,
    pixels,
    result,
    *,
    private_creator: str = DEFAULT_PRIVATE_CREATOR,
    series_uid: str | None = None,
):
    """Return a Secondary Capture dataset carrying the report sheet.

    Every tag of the source instance is kept except those that describe its
    pixels, so the new series lands under the same patient, study, accession
    and referring physician without any of that being retyped.
    """
    from PIL import Image
    from pydicom import dcmread
    from pydicom.dataset import Dataset

    source = dcmread(io.BytesIO(source_bytes), force=True)
    rows = finding_rows(result)

    sheet = Image.open(
        io.BytesIO(
            render_report(
                pixels,
                patient_name=str(source.get("PatientName", "") or ""),
                accession_number=str(source.get("AccessionNumber", "") or ""),
                study_date=_format_date(str(source.get("StudyDate", "") or "")),
                rows=rows,
            )
        )
    ).convert("RGB")

    dataset = Dataset()
    for element in source:
        if element.tag.group == 0x0002 or element.tag == 0x7FE00010:
            # File meta is rebuilt below; pixel data is replaced wholesale.
            continue
        dataset.add(element)
    for group, elem in DROPPED_TAGS:
        if (group, elem) in dataset:
            del dataset[(group, elem)]

    dataset.SOPClassUID = SECONDARY_CAPTURE_SOP_CLASS
    dataset.SOPInstanceUID = _uid()
    dataset.SeriesInstanceUID = series_uid or _uid()
    dataset.SeriesDescription = SERIES_DESCRIPTION
    dataset.SeriesNumber = SERIES_NUMBER
    dataset.InstanceNumber = 1
    dataset.ImageType = ["DERIVED", "SECONDARY"]
    dataset.ConversionType = "WSD"
    dataset.Modality = source.get("Modality", "OT") or "OT"

    # Who made *this* instance, which is not who made the source. Copying the
    # tags wholesale left the sheet claiming the source's manufacturer -- the
    # same misattribution the private creator avoids, and unlike
    # SendingApplicationEntityTitle these travel: file meta is a file-format
    # construct that C-STORE does not carry, so a receiver reads the producer
    # from here and from the calling AE of the association.
    dataset.Manufacturer = PRODUCER
    dataset.ManufacturerModelName = SERIES_DESCRIPTION
    dataset.SecondaryCaptureDeviceManufacturer = PRODUCER
    dataset.SecondaryCaptureDeviceManufacturerModelName = SERIES_DESCRIPTION
    model_version = getattr(result, "model_version", None)
    if model_version:
        dataset.SoftwareVersions = str(model_version)

    dataset.SamplesPerPixel = 3
    dataset.PhotometricInterpretation = "RGB"
    dataset.PlanarConfiguration = 0
    dataset.Rows = sheet.height
    dataset.Columns = sheet.width
    dataset.BitsAllocated = 8
    dataset.BitsStored = 8
    dataset.HighBit = 7
    dataset.PixelRepresentation = 0
    dataset.PixelData = sheet.tobytes()

    _apply_private_block(dataset, rows, private_creator)

    dataset.file_meta = _file_meta(dataset)
    return dataset


def _apply_private_block(dataset, rows: list[dict], private_creator: str) -> None:
    """Write the findings into the private block, one item per pathology."""
    from pydicom.dataset import Dataset

    block = dataset.private_block(PRIVATE_GROUP, private_creator, create=True)
    positives = [row for row in rows if row["confidence"] != "ABSENT"]
    block.add_new(0x01, "SH", "true")
    block.add_new(0x02, "SH", "true" if positives else "false")

    items = []
    for row in rows:
        item = Dataset()
        item.CodeMeaning = row["code_meaning"]
        item.TextValue = row["confidence"]
        items.append(item)
    block.add_new(0x03, "SQ", items)
    block.add_new(0x04, "SH", "ORIGINAL")
    block.add_new(0x05, "SH", "CHEST")


def _file_meta(dataset):
    """File meta for the new instance, naming the application that made it.

    SendingApplicationEntityTitle is what a receiver reads to say where an
    instance came from -- the source exam carries AZMED there, because AZMED
    produced it. This one is ours, and it is taken from the same setting the
    C-STORE association uses as its calling AE, so the tag and the association
    cannot drift apart and claim different senders.
    """
    from pydicom.dataset import FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian

    from chester.config import settings

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = dataset.SOPClassUID
    meta.MediaStorageSOPInstanceUID = dataset.SOPInstanceUID
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.SendingApplicationEntityTitle = settings.dicom_send_calling_ae_title
    return meta


def _format_date(value: str) -> str:
    """DICOM DA to something a reader recognises, or the raw value."""
    if len(value) == 8 and value.isdigit():
        return f"{value[6:8]}/{value[4:6]}/{value[0:4]}"
    return value


def dataset_to_bytes(dataset) -> bytes:
    from pydicom.filewriter import dcmwrite

    buffer = io.BytesIO()
    dcmwrite(buffer, dataset, enforce_file_format=True)
    return buffer.getvalue()


def latest_result(study):
    """The most recent analysis, which is what the report describes."""
    completed = [result for result in study.results if result.raw_scores]
    if not completed:
        return None
    return max(completed, key=lambda result: result.created_at)


def source_instance(db, study):
    """The instance the report draws its picture from.

    The same one the thumbnail and the analysis were built from -- the frontal
    projection where the study holds one, the oldest carrying bytes otherwise --
    so the sheet shows the image that was actually scored rather than the
    lateral filed beside it.
    """
    from chester.instances import representative_instance

    return representative_instance(db, study)


def build_for_study(db, study, *, private_creator: str = DEFAULT_PRIVATE_CREATOR):
    """Assemble the secondary capture for one study, or explain why not."""
    from chester.imaging.source import pixels_from_stored
    from chester.storage import ObjectNotFound, retrieve_bytes

    result = latest_result(study)
    if result is None:
        raise ValueError("study has no completed analysis to report")

    instance = source_instance(db, study)
    if instance is None:
        raise ValueError("study has no stored instance to draw from")

    try:
        data = retrieve_bytes(instance.object_key, session=db)
    except ObjectNotFound as exc:
        raise ValueError(f"instance bytes are gone from storage: {instance.object_key}") from exc

    pixels = pixels_from_stored(data, instance.content_type)
    return build_report_dataset(data, pixels, result, private_creator=private_creator)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import logging
    import sys
    import uuid as uuid_module

    from chester.db import session_scope
    from chester.models import Study

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", required=True, help="Study id to report on")
    parser.add_argument("--out", default="", help="Also write the instance to this path")
    parser.add_argument(
        "--send",
        action="store_true",
        help="Store the instance on the configured DICOM destination",
    )
    parser.add_argument(
        "--destination",
        default="",
        help="Send only to the configured destination with this name",
    )
    parser.add_argument(
        "--private-creator",
        default=DEFAULT_PRIVATE_CREATOR,
        help=(
            "Private creator for the findings block. Change it only to match a "
            "viewer that reads another vendor's block, knowing that the string "
            "is what attributes the findings."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    try:
        study_id = uuid_module.UUID(args.study)
    except ValueError:
        logger.error("Not a study id: %s", args.study)
        return 2

    with session_scope() as db:
        study = db.get(Study, study_id)
        if study is None:
            logger.error("No study %s", study_id)
            return 1
        try:
            dataset = build_for_study(db, study, private_creator=args.private_creator)
        except ValueError as exc:
            logger.error("Cannot report on %s: %s", study_id, exc)
            return 1
        data = dataset_to_bytes(dataset)

        logger.info(
            "Built %s (%s), series %s",
            dataset.SOPInstanceUID,
            SERIES_DESCRIPTION,
            dataset.SeriesInstanceUID,
        )

        if args.out:
            with open(args.out, "wb") as handle:
                handle.write(data)
            logger.info("Wrote %s (%d bytes)", args.out, len(data))

        # The send happens inside the session so the attempt is recorded in the
        # network log with everything else this node exchanged, whether the
        # destination took the instance or refused it.
        if args.send:
            from chester import destinations
            from chester.dicom_send import SendFailed, SendNotConfigured
            from chester.report_delivery import deliver_report

            targets = destinations.active(db, study.organization_id)
            if args.destination:
                targets = [item for item in targets if item.name == args.destination]
                if not targets:
                    logger.error("No active destination named %s", args.destination)
                    return 1
            if not targets:
                logger.error("No active destination is configured")
                return 1

            failed = False
            for destination in targets:
                try:
                    deliver_report(db, study, destination, actor="cli", dataset=dataset)
                except (SendFailed, SendNotConfigured) as exc:
                    logger.error("Not delivered to %s: %s", destination.name, exc)
                    failed = True
            if failed:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
