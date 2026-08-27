"""DICOM parsing, metadata extraction and pixel rendering.

Ported with intent from the previous implementation. The edge cases handled here --
MONOCHROME1 inversion, rescale slope/intercept, window centre/width arriving as
either a scalar or a sequence, multi-frame selection -- are the expensive part of
working with real DICOM files, so the behaviour is preserved deliberately rather
than reimplemented.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_TRANSFER_SYNTAX = "1.2.840.10008.1.2.1"


def _tag_str(dataset: Any, tag: str, default: str = "") -> str:
    """Read a string tag without letting a malformed value abort ingestion."""
    try:
        value = dataset.get(tag)
    except Exception:  # pragma: no cover - defensive against odd datasets
        return default
    if value is None:
        return default
    try:
        return str(value).strip()
    except Exception:  # pragma: no cover
        return default


def _tag_int(dataset: Any, tag: str, default: int | None = None) -> int | None:
    try:
        value = dataset.get(tag)
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _window(dataset: Any) -> tuple[float, float] | None:
    """Return (centre, width) when a usable VOI window is present.

    WindowCenter and WindowWidth are multi-valued in DICOM, so they arrive as either
    a scalar or a sequence. A string is iterable but not a sequence of values, hence
    the explicit exclusion.
    """
    centre = getattr(dataset, "WindowCenter", None)
    width = getattr(dataset, "WindowWidth", None)
    if centre is None or width is None:
        return None
    try:
        centre_value = (
            float(centre[0])
            if hasattr(centre, "__iter__") and not isinstance(centre, str)
            else float(centre)
        )
        width_value = (
            float(width[0])
            if hasattr(width, "__iter__") and not isinstance(width, str)
            else float(width)
        )
    except (ValueError, TypeError, IndexError):
        return None
    return centre_value, width_value


def _rescale(dataset: Any) -> tuple[float, float]:
    slope = float(getattr(dataset, "RescaleSlope", 1.0) or 1.0)
    intercept = float(getattr(dataset, "RescaleIntercept", 0.0) or 0.0)
    return slope, intercept


def _is_monochrome1(dataset: Any) -> bool:
    photometric = str(getattr(dataset, "PhotometricInterpretation", "MONOCHROME2")).upper()
    return "MONOCHROME1" in photometric


def _select_frame(pixel_array: np.ndarray, frame_index: int) -> np.ndarray:
    if pixel_array.ndim == 3:
        pixel_array = pixel_array[frame_index]
    if pixel_array.ndim != 2:
        raise ValueError(f"Unexpected pixel array shape: {pixel_array.shape}")
    return pixel_array


def parse_dicom_bytes(data: bytes) -> Any:
    """Parse PS3.10 bytes, requiring the pixel data the pipeline depends on."""
    import pydicom
    from pydicom.filebase import DicomBytesIO

    with contextlib.suppress(ImportError):
        # Registers handlers for the compressed transfer syntaxes.
        import pylibjpeg  # noqa: F401
    try:
        import pydicom.config

        pydicom.config.convert_wrong_length_to_UN = True
    except Exception:  # pragma: no cover
        pass

    dataset = pydicom.dcmread(DicomBytesIO(data), force=True)
    if "PixelData" not in dataset:
        raise ValueError("DICOM instance does not contain PixelData")
    if not getattr(dataset, "Rows", None) or not getattr(dataset, "Columns", None):
        raise ValueError("DICOM instance is missing Rows or Columns")
    return dataset


def extract_metadata(dataset: Any) -> dict:
    """Pull the fields the worklist needs. The patient name is never read."""
    transfer_syntax = getattr(getattr(dataset, "file_meta", None), "TransferSyntaxUID", None)

    frame_count_raw = _tag_str(dataset, "NumberOfFrames", "1")
    try:
        frame_count = max(1, int(frame_count_raw))
    except (ValueError, TypeError):
        frame_count = 1

    study_description = _tag_str(dataset, "StudyDescription", "")
    series_description = _tag_str(dataset, "SeriesDescription", "")

    return {
        "modality": _tag_str(dataset, "Modality", "").upper(),
        "body_part": _tag_str(dataset, "BodyPartExamined", "").upper(),
        "view_position": _tag_str(dataset, "ViewPosition", "").upper(),
        "description": study_description or series_description,
        "study_instance_uid": _tag_str(dataset, "StudyInstanceUID", ""),
        "series_instance_uid": _tag_str(dataset, "SeriesInstanceUID", ""),
        "sop_instance_uid": _tag_str(dataset, "SOPInstanceUID", ""),
        "sop_class_uid": _tag_str(dataset, "SOPClassUID", ""),
        "transfer_syntax_uid": (
            str(transfer_syntax) if transfer_syntax else DEFAULT_TRANSFER_SYNTAX
        ),
        "raw_patient_id": _tag_str(dataset, "PatientID", ""),
        "patient_age": _tag_str(dataset, "PatientAge", ""),
        "patient_sex": _tag_str(dataset, "PatientSex", ""),
        "study_date": _tag_str(dataset, "StudyDate", ""),
        "rows": _tag_int(dataset, "Rows"),
        "columns": _tag_int(dataset, "Columns"),
        "bits_allocated": _tag_int(dataset, "BitsAllocated"),
        "frame_count": frame_count,
    }


def render_frame(dataset: Any, frame_index: int = 0) -> np.ndarray:
    """Render a frame in modality units, for validation and thumbnails.

    Applies the modality LUT and clips to the VOI window when one is present, but
    keeps the native intensity scale.
    """
    try:
        pixel_array = dataset.pixel_array
    except Exception as exc:
        raise ValueError(f"Cannot decode pixel data: {exc}") from exc

    values = _select_frame(pixel_array, frame_index).astype(np.float32)
    slope, intercept = _rescale(dataset)
    values = values * slope + intercept

    window = _window(dataset)
    if window is not None:
        centre, width = window
        if width > 0:
            values = np.clip(values, centre - width / 2.0, centre + width / 2.0)

    if _is_monochrome1(dataset):
        values = values.max() - values

    return values


def render_frame_for_model(dataset: Any, frame_index: int = 0) -> np.ndarray:
    """Render a frame as the 0..255 grayscale raster the model expects.

    Distinct from render_frame: the model needs a display-normalized raster, so the
    window (or the full data range when no window is given) is mapped onto 0..255.
    """
    try:
        pixel_array = dataset.pixel_array
    except Exception as exc:
        raise ValueError(f"Cannot decode pixel data: {exc}") from exc

    raw = _select_frame(pixel_array, frame_index).astype(np.float32)
    slope, intercept = _rescale(dataset)
    values = raw * slope + intercept

    low = float(raw.min()) * slope + intercept
    high = float(raw.max()) * slope + intercept

    window = _window(dataset)
    if window is not None:
        centre, width = window
        # A width of 1 or less carries no usable contrast; fall back to the range.
        if width > 1:
            low = centre - width / 2.0
            high = centre + width / 2.0

    if not high > low:
        high = low + 1.0

    normalized = np.clip((values - low) / (high - low), 0.0, 1.0)
    if _is_monochrome1(dataset):
        normalized = 1.0 - normalized

    return np.rint(normalized * 255.0).astype(np.float32)


def to_pil_image(values: np.ndarray):
    """Convert a 2D float array to an 8-bit grayscale PIL image."""
    from PIL import Image

    minimum, maximum = float(values.min()), float(values.max())
    if maximum > minimum:
        normalized = (values - minimum) / (maximum - minimum) * 255.0
    else:
        normalized = np.zeros_like(values)
    return Image.fromarray(np.clip(normalized, 0, 255).astype(np.uint8), mode="L")


def generate_thumbnail(values: np.ndarray, size: tuple[int, int] = (512, 512)) -> bytes:
    """Render a PNG thumbnail from a 2D float array.

    The image is fitted inside `size` rather than resized onto it. A chest
    radiograph is almost never square, and the previous unconditional resize
    stretched every one of them onto a square: the ribcage came out wider or
    taller than it is, in the only picture of the study the interface shows.
    Image.thumbnail keeps the ratio, and only ever shrinks, so a small source
    is left alone rather than upscaled into a sharper-looking lie.
    """
    from PIL import Image

    image = to_pil_image(values)
    image.thumbnail(size, Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generate_synthetic_uid(seed: str, prefix: str = "2.25") -> str:
    """Derive a stable UID from a seed. 2.25 is the UUID-derived OID arc."""
    digest = hashlib.sha256(seed.encode()).hexdigest()
    return f"{prefix}.{int(digest[:20], 16) % (10**20)}"


def make_synthetic_uids(seed_bytes: bytes) -> dict[str, str]:
    """Derive Study/Series/SOP UIDs for an upload that carries no DICOM identity.

    Derived from content, so re-uploading the same file yields the same UIDs and
    deduplication still works.
    """
    seed = hashlib.sha256(seed_bytes).hexdigest()
    return {
        "study_instance_uid": generate_synthetic_uid(seed + "study"),
        "series_instance_uid": generate_synthetic_uid(seed + "series"),
        "sop_instance_uid": generate_synthetic_uid(seed + "sop"),
    }


def looks_like_dicom(data: bytes, filename: str, content_type: str) -> bool:
    """Detect DICOM by preamble magic, filename or declared content type."""
    if filename.lower().endswith((".dcm", ".dicom")):
        return True
    has_magic = len(data) > 132 and data[128:132] == b"DICM"
    if content_type in ("application/dicom", "application/octet-stream"):
        return has_magic
    return has_magic
