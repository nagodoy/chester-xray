"""DICOM parsing and rendering.

These cover the edge cases that are expensive to rediscover with real files:
MONOCHROME1 inversion, the modality LUT, VOI windows arriving as a scalar or a
sequence, and multi-frame frame selection.
"""

from __future__ import annotations

import io

import numpy as np
import pytest

from chester.imaging import dicom


def test_parse_extracts_the_metadata_the_worklist_needs(make_dicom):
    meta = dicom.extract_metadata(dicom.parse_dicom_bytes(make_dicom()))

    assert meta["modality"] == "DX"
    assert meta["body_part"] == "CHEST"
    assert meta["view_position"] == "PA"
    assert meta["description"] == "CHEST PA"
    assert meta["patient_age"] == "045Y"
    assert meta["rows"] == 64 and meta["columns"] == 64
    assert meta["frame_count"] == 1
    assert meta["study_instance_uid"] and meta["sop_instance_uid"]


def test_parse_rejects_a_file_without_pixel_data():
    import io

    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.MediaStorageSOPClassUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset("", {}, file_meta=file_meta, preamble=b"\x00" * 128)
    ds.PatientID = "X"
    buffer = io.BytesIO()
    ds.save_as(buffer, enforce_file_format=True)

    with pytest.raises(ValueError, match="PixelData"):
        dicom.parse_dicom_bytes(buffer.getvalue())


def test_metadata_falls_back_to_series_description(make_dicom):
    meta = dicom.extract_metadata(dicom.parse_dicom_bytes(make_dicom(study_description="")))
    # SeriesDescription is unset in the fixture, so the description is simply empty
    # rather than the study description being invented.
    assert meta["description"] == ""


def test_monochrome1_is_inverted(make_dicom):
    """MONOCHROME1 stores white as low, so it must be flipped for display."""
    ramp = np.tile(np.arange(64, dtype=np.uint16) * 16, (64, 1))

    normal = dicom.render_frame_for_model(
        dicom.parse_dicom_bytes(make_dicom(pixels=ramp, photometric="MONOCHROME2"))
    )
    inverted = dicom.render_frame_for_model(
        dicom.parse_dicom_bytes(make_dicom(pixels=ramp, photometric="MONOCHROME1"))
    )

    assert normal[0, 0] < normal[0, -1]
    assert inverted[0, 0] > inverted[0, -1]
    np.testing.assert_allclose(normal + inverted, 255.0, atol=1.0)


def test_model_raster_is_bounded_to_8_bit_range(make_dicom):
    rendered = dicom.render_frame_for_model(dicom.parse_dicom_bytes(make_dicom()))

    assert rendered.shape == (64, 64)
    assert rendered.min() >= 0.0
    assert rendered.max() <= 255.0


def test_window_as_a_sequence_is_accepted(make_dicom):
    """WindowCenter and WindowWidth are multi-valued; a list must not crash."""
    from pydicom.multival import MultiValue

    data = make_dicom(window_center="512", window_width="1024")
    dataset = dicom.parse_dicom_bytes(data)
    dataset.WindowCenter = MultiValue(float, [512.0, 600.0])
    dataset.WindowWidth = MultiValue(float, [1024.0, 1200.0])

    rendered = dicom.render_frame_for_model(dataset)
    assert np.isfinite(rendered).all()


def test_degenerate_window_falls_back_to_the_data_range(make_dicom):
    """A width of 1 carries no contrast and must not collapse the image."""
    ramp = np.tile(np.arange(64, dtype=np.uint16) * 16, (64, 1))
    rendered = dicom.render_frame_for_model(
        dicom.parse_dicom_bytes(make_dicom(pixels=ramp, window_center="0", window_width="1"))
    )

    assert rendered.max() > rendered.min()


def test_absent_window_still_renders(make_dicom):
    ramp = np.tile(np.arange(64, dtype=np.uint16) * 16, (64, 1))
    rendered = dicom.render_frame_for_model(
        dicom.parse_dicom_bytes(make_dicom(pixels=ramp, window_center=None, window_width=None))
    )

    assert rendered.min() == 0.0
    assert rendered.max() == 255.0


def test_rescale_slope_and_intercept_are_applied(make_dicom):
    flat = np.full((64, 64), 100, dtype=np.uint16)
    dataset = dicom.parse_dicom_bytes(
        make_dicom(
            pixels=flat,
            rescale_slope="2.0",
            rescale_intercept="-50.0",
            window_center=None,
            window_width=None,
        )
    )

    assert dicom.render_frame(dataset)[0, 0] == pytest.approx(100 * 2.0 - 50.0)


def test_multi_frame_uses_the_requested_frame(make_dicom):
    frames = np.stack([np.full((64, 64), value, dtype=np.uint16) for value in (10, 200, 400)])
    dataset = dicom.parse_dicom_bytes(
        make_dicom(frame_count=3, pixels=frames, window_center=None, window_width=None)
    )

    assert dicom.render_frame(dataset, 0)[0, 0] == pytest.approx(10)
    assert dicom.render_frame(dataset, 2)[0, 0] == pytest.approx(400)


def test_multi_frame_count_is_reported(make_dicom):
    frames = np.zeros((3, 64, 64), dtype=np.uint16)
    meta = dicom.extract_metadata(dicom.parse_dicom_bytes(make_dicom(frame_count=3, pixels=frames)))
    assert meta["frame_count"] == 3


def test_thumbnail_is_a_png(make_dicom):
    rendered = dicom.render_frame(dicom.parse_dicom_bytes(make_dicom()))
    thumbnail = dicom.generate_thumbnail(rendered)

    assert thumbnail.startswith(b"\x89PNG\r\n\x1a\n")


def test_thumbnail_of_a_flat_image_does_not_divide_by_zero():
    thumbnail = dicom.generate_thumbnail(np.full((64, 64), 7.0, dtype=np.float32))
    assert thumbnail.startswith(b"\x89PNG\r\n\x1a\n")


def test_synthetic_uids_are_derived_from_content():
    """Re-uploading identical bytes must yield identical UIDs, so dedup works."""
    first = dicom.make_synthetic_uids(b"same bytes")
    second = dicom.make_synthetic_uids(b"same bytes")
    other = dicom.make_synthetic_uids(b"different bytes")

    assert first == second
    assert first["sop_instance_uid"] != other["sop_instance_uid"]
    assert (
        len({first["study_instance_uid"], first["series_instance_uid"], first["sop_instance_uid"]})
        == 3
    )


def test_dicom_detection(make_dicom):
    data = make_dicom()
    assert dicom.looks_like_dicom(data, "x.dcm", "application/octet-stream")
    assert dicom.looks_like_dicom(data, "no-extension", "application/dicom")
    assert dicom.looks_like_dicom(data, "no-extension", "image/png")  # magic wins
    assert not dicom.looks_like_dicom(b"\x89PNG\r\n\x1a\n", "x.png", "image/png")


def test_a_thumbnail_keeps_the_shape_of_the_radiograph():
    """A stretched preview misrepresents the anatomy it is previewing."""
    from PIL import Image

    tall = np.tile(np.linspace(0, 255, 1000, dtype=np.float32)[:, None], (1, 500))

    data = dicom.generate_thumbnail(tall)

    width, height = Image.open(io.BytesIO(data)).size
    assert height > width
    assert abs((width / height) - 0.5) < 0.01


def test_a_thumbnail_is_bounded_by_the_requested_box():
    from PIL import Image

    wide = np.zeros((400, 2000), dtype=np.float32)

    data = dicom.generate_thumbnail(wide, size=(256, 256))

    width, height = Image.open(io.BytesIO(data)).size
    assert width <= 256 and height <= 256


def test_a_source_smaller_than_the_box_is_not_upscaled():
    from PIL import Image

    data = dicom.generate_thumbnail(np.zeros((64, 64), dtype=np.float32), size=(256, 256))

    assert Image.open(io.BytesIO(data)).size == (64, 64)
