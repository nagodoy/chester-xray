"""The confidence rule, and the sheet and DICOM instance built from it."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image
from pydicom import dcmread
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, generate_uid

from chester import report
from chester.dicom_report import (
    DEFAULT_PRIVATE_CREATOR,
    PRIVATE_GROUP,
    SERIES_DESCRIPTION,
    build_report_dataset,
    dataset_to_bytes,
)


class TestConfidence:
    """ABSENT under the operating point, CONFIDENT over, DOUBT either side."""

    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0.10, report.CONFIDENCE_ABSENT),
            (0.40, report.CONFIDENCE_ABSENT),
            (0.46, report.CONFIDENCE_DOUBT),
            (0.50, report.CONFIDENCE_DOUBT),
            (0.54, report.CONFIDENCE_DOUBT),
            (0.70, report.CONFIDENCE_CONFIDENT),
        ],
    )
    def test_the_bands_around_an_operating_point_of_one_half(self, score, expected):
        assert report.classify_confidence(score, 0.5) == expected

    def test_the_band_straddles_the_threshold_rather_than_sitting_above_it(self):
        """A score just under is no more decidable than one just over."""
        assert report.classify_confidence(0.48, 0.5) == report.CONFIDENCE_DOUBT
        assert report.classify_confidence(0.52, 0.5) == report.CONFIDENCE_DOUBT

    def test_the_band_is_proportional_to_the_operating_point(self):
        """A tenth of 0.04 is a much narrower band than a tenth of 0.5."""
        assert report.classify_confidence(0.045, 0.04) == report.CONFIDENCE_CONFIDENT
        assert report.classify_confidence(0.045, 0.5) == report.CONFIDENCE_ABSENT

    def test_an_operating_point_of_zero_falls_back_to_over_or_under(self):
        """There is no band to be near, so the only honest split is the sign."""
        assert report.classify_confidence(0.0, 0.0) == report.CONFIDENCE_ABSENT
        assert report.classify_confidence(0.1, 0.0) == report.CONFIDENCE_CONFIDENT

    def test_finding_names_take_the_shape_the_private_tags_use(self):
        assert report.dicom_code_meaning("Pleural Thickening") == "PLEURALTHICKENING"
        assert report.dicom_code_meaning("Enlarged Cardiomediastinum") == (
            "ENLARGEDCARDIOMEDIASTINUM"
        )


class _Result:
    def __init__(self, raw, thresholds):
        self.raw_scores = raw
        self.thresholds = thresholds
        self.op_normalized_scores = dict.fromkeys(raw, 0.5)


@pytest.fixture
def result() -> _Result:
    """Three reported findings: one over, one on the band, one well under.

    Every name here must be one the deployment reports -- a suppressed output is
    filtered out of the sheet and the tags, which is its own test.
    """
    return _Result(
        {"Cardiomegaly": 0.9, "Effusion": 0.5, "Mass": 0.01},
        {"Cardiomegaly": 0.5, "Effusion": 0.5, "Mass": 0.5},
    )


@pytest.fixture
def pixels() -> np.ndarray:
    """A portrait frame, the shape a chest radiograph actually is."""
    return np.tile(np.linspace(0, 255, 600, dtype=np.float32)[:, None], (1, 400))


@pytest.fixture
def source_dicom(pixels) -> bytes:
    """A DX instance carrying the identity a report has to inherit."""
    dataset = Dataset()
    dataset.PatientName = "MARIA DE LOS ANGELES SOTO BARRIOS"
    dataset.PatientID = "531.7264811"
    dataset.PatientSex = "F"
    dataset.AccessionNumber = "9000000254296647"
    dataset.StudyDate = "20260826"
    dataset.StudyInstanceUID = "1.3.840.20260826.1531.29000000254296647"
    dataset.SeriesInstanceUID = generate_uid()
    dataset.SOPInstanceUID = generate_uid()
    dataset.SOPClassUID = "1.2.840.10008.5.1.4.1.1.1.1"
    dataset.Modality = "DX"
    dataset.BodyPartExamined = "CHEST"
    dataset.ReferringPhysicianName = "WILLIAM MARASINI DE REZENDE"
    dataset.StudyID = "17691"
    # The tags that must not survive onto a rendered sheet.
    dataset.PixelSpacing = ["0.148", "0.148"]
    dataset.ImagerPixelSpacing = ["0.148", "0.148"]
    dataset.WindowCenter = "127.0"
    dataset.WindowWidth = "255.0"

    array = pixels.astype("uint8")
    dataset.Rows, dataset.Columns = array.shape
    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = "MONOCHROME2"
    dataset.BitsAllocated = 8
    dataset.BitsStored = 8
    dataset.HighBit = 7
    dataset.PixelRepresentation = 0
    dataset.PixelData = array.tobytes()

    dataset.file_meta = FileMetaDataset()
    dataset.file_meta.MediaStorageSOPClassUID = dataset.SOPClassUID
    dataset.file_meta.MediaStorageSOPInstanceUID = dataset.SOPInstanceUID
    dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    buffer = io.BytesIO()
    dataset.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


class TestSheet:
    def test_the_sheet_renders_as_a_png(self, pixels, result):
        from chester.imaging.report_image import render_report

        data = render_report(
            pixels,
            patient_name="A",
            accession_number="1",
            study_date="26/08/2026",
            rows=report.finding_rows(result),
        )

        assert data.startswith(b"\x89PNG\r\n\x1a\n")

    def test_the_picture_keeps_seven_parts_to_the_table_s_three(self, pixels, result):
        from chester.imaging import report_image

        data = report_image.render_report(
            pixels,
            patient_name="A",
            accession_number="1",
            study_date="26/08/2026",
            rows=report.finding_rows(result),
        )

        _, height = Image.open(io.BytesIO(data)).size
        body = height - report_image.HEADER_HEIGHT
        image_part = body / (1 + 1 / report_image.IMAGE_SHARE)
        assert abs((image_part / body) - 0.7) < 0.01

    def test_a_long_name_is_shortened_rather_than_run_over_its_neighbour(self):
        from PIL import ImageDraw

        from chester.imaging.report_image import _fit_text, _font

        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        font = _font(21, bold=True)
        name = "MARIA DE LOS ANGELES SOTO BARRIOS"

        fitted = _fit_text(draw, name, font, 200)

        assert fitted != name
        assert fitted.endswith("…")
        assert draw.textlength(fitted, font=font) <= 200

    def test_a_name_that_already_fits_is_left_alone(self):
        from PIL import ImageDraw

        from chester.imaging.report_image import _fit_text, _font

        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))

        assert _fit_text(draw, "AB", _font(21), 500) == "AB"


class TestSecondaryCapture:
    def test_the_patient_study_and_accession_come_across_untouched(
        self, source_dicom, pixels, result
    ):
        dataset = build_report_dataset(source_dicom, pixels, result)

        source = dcmread(io.BytesIO(source_dicom))
        assert dataset.PatientName == source.PatientName
        assert dataset.PatientID == source.PatientID
        assert dataset.AccessionNumber == source.AccessionNumber
        assert dataset.StudyInstanceUID == source.StudyInstanceUID
        assert dataset.ReferringPhysicianName == source.ReferringPhysicianName
        assert dataset.StudyID == source.StudyID

    def test_it_is_a_new_series_in_the_same_study(self, source_dicom, pixels, result):
        source = dcmread(io.BytesIO(source_dicom))

        dataset = build_report_dataset(source_dicom, pixels, result)

        assert dataset.SeriesDescription == SERIES_DESCRIPTION
        assert dataset.SeriesInstanceUID != source.SeriesInstanceUID
        assert dataset.SOPInstanceUID != source.SOPInstanceUID
        assert dataset.StudyInstanceUID == source.StudyInstanceUID
        assert dataset.SOPClassUID == "1.2.840.10008.5.1.4.1.1.7"
        assert list(dataset.ImageType) == ["DERIVED", "SECONDARY"]

    def test_the_sheet_is_the_pixel_data(self, source_dicom, pixels, result):
        dataset = build_report_dataset(source_dicom, pixels, result)

        assert dataset.PhotometricInterpretation == "RGB"
        assert dataset.SamplesPerPixel == 3
        assert len(dataset.PixelData) == dataset.Rows * dataset.Columns * 3

    def test_the_source_pixel_geometry_does_not_come_with_it(self, source_dicom, pixels, result):
        """A viewer would otherwise measure distances on the report sheet."""
        dataset = build_report_dataset(source_dicom, pixels, result)

        assert "PixelSpacing" not in dataset
        assert "ImagerPixelSpacing" not in dataset
        assert "WindowCenter" not in dataset
        assert "WindowWidth" not in dataset

    def test_every_finding_is_in_the_private_block_with_its_confidence(
        self, source_dicom, pixels, result
    ):
        dataset = build_report_dataset(source_dicom, pixels, result)

        block = dataset.private_block(PRIVATE_GROUP, DEFAULT_PRIVATE_CREATOR)
        items = {item.CodeMeaning: item.TextValue for item in block[0x03].value}
        assert items == {
            "CARDIOMEGALY": "CONFIDENT",
            "EFFUSION": "DOUBT",
            "MASS": "ABSENT",
        }
        assert block[0x02].value == "true"
        assert block[0x05].value == "CHEST"

    def test_a_study_with_nothing_over_its_operating_points_says_so(self, source_dicom, pixels):
        quiet = _Result({"Mass": 0.01}, {"Mass": 0.5})

        dataset = build_report_dataset(source_dicom, pixels, quiet)

        block = dataset.private_block(PRIVATE_GROUP, DEFAULT_PRIVATE_CREATOR)
        assert block[0x02].value == "false"

    def test_the_creator_is_ours_unless_asked_otherwise(self, source_dicom, pixels, result):
        """The creator string is what attributes the findings to a producer."""
        dataset = build_report_dataset(source_dicom, pixels, result)
        assert dataset[(PRIVATE_GROUP, 0x0010)].value == DEFAULT_PRIVATE_CREATOR

        other = build_report_dataset(source_dicom, pixels, result, private_creator="AZMED")
        assert other[(PRIVATE_GROUP, 0x0010)].value == "AZMED"

    def test_it_survives_a_round_trip_through_the_file_format(self, source_dicom, pixels, result):
        dataset = build_report_dataset(source_dicom, pixels, result)

        parsed = dcmread(io.BytesIO(dataset_to_bytes(dataset)))

        block = parsed.private_block(PRIVATE_GROUP, DEFAULT_PRIVATE_CREATOR)
        assert len(block[0x03].value) == 3


class TestPrivateSequenceOnTheWire:
    """Why the sender proposes Explicit VR and nothing else."""

    def _encode(self, dataset, transfer_syntax) -> bytes:
        from pydicom.filewriter import dcmwrite

        buffer = io.BytesIO()
        dataset.file_meta.TransferSyntaxUID = transfer_syntax
        dcmwrite(buffer, dataset, enforce_file_format=True)
        return buffer.getvalue()

    def test_explicit_vr_keeps_the_findings_readable(self, source_dicom, pixels, result):
        dataset = build_report_dataset(source_dicom, pixels, result)

        parsed = dcmread(io.BytesIO(self._encode(dataset, ExplicitVRLittleEndian)))

        element = parsed[(PRIVATE_GROUP, 0x1003)]
        assert element.VR == "SQ"
        assert len(element.value) == 3

    def test_implicit_vr_reduces_them_to_bytes(self, source_dicom, pixels, result):
        """A private tag is in no receiver's dictionary, so Implicit VR loses
        the structure entirely: the image arrives and the findings do not."""
        dataset = build_report_dataset(source_dicom, pixels, result)

        parsed = dcmread(io.BytesIO(self._encode(dataset, ImplicitVRLittleEndian)))

        element = parsed[(PRIVATE_GROUP, 0x1003)]
        assert element.VR == "UN"
        assert isinstance(element.value, bytes)

    def test_the_sender_proposes_explicit_vr_only(self):
        """Offering Implicit as a fallback would let the acceptor pick it."""
        import inspect

        from chester import dicom_send

        body = inspect.getsource(dicom_send.send_dataset)
        assert "transfer_syntax=[ExplicitVRLittleEndian]" in body


class TestSending:
    def test_sending_with_no_destination_is_refused_rather_than_silently_skipped(
        self, monkeypatch, source_dicom, pixels, result
    ):
        from chester import dicom_send
        from chester.config import settings

        monkeypatch.setattr(settings, "dicom_send_host", "")
        dataset = build_report_dataset(source_dicom, pixels, result)

        with pytest.raises(dicom_send.SendNotConfigured):
            dicom_send.send_dataset(dataset)


class TestProvenance:
    """Who produced the instance, and how a receiver can tell."""

    def test_the_sending_application_names_us_in_the_file_meta(self, source_dicom, pixels, result):
        dataset = build_report_dataset(source_dicom, pixels, result)

        parsed = dcmread(io.BytesIO(dataset_to_bytes(dataset)))

        assert parsed.file_meta.SendingApplicationEntityTitle == "TORAX_AI"

    def test_the_file_meta_title_follows_the_calling_ae_of_the_association(
        self, monkeypatch, source_dicom, pixels, result
    ):
        """The tag and the AE that carries it must not claim different senders."""
        from chester.config import settings

        monkeypatch.setattr(settings, "dicom_send_calling_ae_title", "OTHER_AE")

        dataset = build_report_dataset(source_dicom, pixels, result)

        assert dataset.file_meta.SendingApplicationEntityTitle == "OTHER_AE"

    def test_the_producer_is_us_rather_than_whoever_made_the_source(
        self, source_dicom, pixels, result
    ):
        """Copying every tag would leave the sheet claiming AZMED made it.

        Unlike the file meta, these travel: C-STORE carries the dataset, not
        the file-format header, so this is what a receiver actually reads.
        """
        source = dcmread(io.BytesIO(source_dicom))
        source.Manufacturer = "AZMED"
        source.ManufacturerModelName = "Rayvolve"
        buffer = io.BytesIO()
        source.save_as(buffer, enforce_file_format=True)

        dataset = build_report_dataset(buffer.getvalue(), pixels, result)

        assert dataset.Manufacturer == "TORAX AI"
        assert dataset.ManufacturerModelName == SERIES_DESCRIPTION
        assert dataset.SecondaryCaptureDeviceManufacturer == "TORAX AI"

    def test_the_model_version_is_recorded_when_the_result_carries_one(
        self, source_dicom, pixels, result
    ):
        result.model_version = "chester-onnx:densenet121-res224-all"

        dataset = build_report_dataset(source_dicom, pixels, result)

        assert dataset.SoftwareVersions == "chester-onnx:densenet121-res224-all"


def test_the_destination_defaults_to_the_configured_viewer():
    """Nothing is sent without --send, so these are defaults, not a trigger."""
    from chester.config import settings

    assert settings.dicom_send_host == "superpaccs.com.br"
    assert settings.dicom_send_port == 11112
    assert settings.dicom_send_ae_title == "medfusion"
    assert settings.dicom_send_calling_ae_title == "TORAX_AI"
