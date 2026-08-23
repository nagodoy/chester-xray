"""DICOM parsing, validation, and image extraction utilities."""
from __future__ import annotations

import hashlib
import io
import logging
import struct
import uuid
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

PREPROCESSING_VERSION = "2.0.0"

# Chest-suggestive modalities
CHEST_MODALITIES = {"DX", "CR", "RG"}
# Chest-related body part values
CHEST_BODY_PARTS = {
    "CHEST", "THORAX", "LUNG", "HEART", "RIBCAGE", "STERNUM",
    "MEDIASTINUM", "TRACHEA", "BRONCHUS"
}
# Non-chest body parts
NON_CHEST_BODY_PARTS = {
    "HEAD", "SKULL", "BRAIN", "NECK", "ABDOMEN", "PELVIS", "SPINE",
    "CSPINE", "TSPINE", "LSPINE", "EXTREMITY", "ARM", "LEG", "KNEE",
    "HAND", "FOOT", "HIP", "SHOULDER", "ELBOW", "WRIST", "ANKLE",
    "MANDIBLE", "ORBIT", "SINUSES"
}
# Non-chest modalities
NON_CHEST_MODALITIES = {"CT", "MR", "US", "NM", "PT", "MG", "OT", "XA", "RF", "SC"}
# Frontal view position indicators
FRONTAL_VIEWS = {"PA", "AP", "PA/AP"}


def _tag_str(ds, tag: str, default: str = "") -> str:
    """Safely get a DICOM string tag value."""
    try:
        val = ds.get(tag)
        if val is None:
            return default
        return str(val).strip()
    except Exception:
        return default


def _tag_int(ds, tag: str, default: Optional[int] = None) -> Optional[int]:
    try:
        val = ds.get(tag)
        if val is None:
            return default
        return int(val)
    except Exception:
        return default


def generate_synthetic_uid(seed: str, prefix: str = "2.25") -> str:
    """Generate a synthetic DICOM UID based on a seed value."""
    digest = hashlib.sha256(seed.encode()).hexdigest()
    int_val = int(digest[:20], 16) % (10 ** 20)
    return f"{prefix}.{int_val}"


def extract_dicom_metadata(ds) -> dict:
    """Extract relevant metadata from a pydicom dataset."""
    import pydicom

    modality = _tag_str(ds, "Modality", "").upper()
    body_part = _tag_str(ds, "BodyPartExamined", "").upper()
    view_position = _tag_str(ds, "ViewPosition", "").upper()
    study_description = _tag_str(ds, "StudyDescription", "")
    series_description = _tag_str(ds, "SeriesDescription", "")

    # UIDs
    study_uid = _tag_str(ds, "StudyInstanceUID", "")
    series_uid = _tag_str(ds, "SeriesInstanceUID", "")
    sop_uid = _tag_str(ds, "SOPInstanceUID", "")
    sop_class_uid = _tag_str(ds, "SOPClassUID", "")
    transfer_syntax = getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", None)
    transfer_syntax = str(transfer_syntax) if transfer_syntax else "1.2.840.10008.1.2.1"

    # Patient fields (we pseudonymize later — never store name)
    raw_patient_id = _tag_str(ds, "PatientID", "")
    patient_age = _tag_str(ds, "PatientAge", "")
    patient_sex = _tag_str(ds, "PatientSex", "")
    study_date = _tag_str(ds, "StudyDate", "")

    # Image geometry
    rows = _tag_int(ds, "Rows")
    columns = _tag_int(ds, "Columns")
    bits_allocated = _tag_int(ds, "BitsAllocated")
    frame_count_raw = _tag_str(ds, "NumberOfFrames", "1")
    try:
        frame_count = max(1, int(frame_count_raw))
    except (ValueError, TypeError):
        frame_count = 1

    description = study_description or series_description

    return {
        "modality": modality,
        "body_part": body_part,
        "view_position": view_position,
        "description": description,
        "study_instance_uid": study_uid,
        "series_instance_uid": series_uid,
        "sop_instance_uid": sop_uid,
        "sop_class_uid": sop_class_uid,
        "transfer_syntax_uid": transfer_syntax,
        "raw_patient_id": raw_patient_id,
        "patient_age": patient_age,
        "patient_sex": patient_sex,
        "study_date": study_date,
        "rows": rows,
        "columns": columns,
        "bits_allocated": bits_allocated,
        "frame_count": frame_count,
    }


def validate_study(meta: dict, image_array: Optional[np.ndarray] = None) -> tuple[str, str]:
    """
    Validate whether a study is chest-related.

    Returns (validation_state, reason) where state is one of:
      chest | uncertain | non_chest
    """
    modality = meta.get("modality", "").upper()
    body_part = meta.get("body_part", "").upper()
    view_position = meta.get("view_position", "").upper()
    description = (meta.get("description") or "").upper()

    # Hard non_chest: incompatible modality
    if modality in NON_CHEST_MODALITIES:
        return "non_chest", f"Modality {modality} is not compatible with chest X-ray analysis"

    # Hard non_chest: known non-chest body part
    if body_part and body_part in NON_CHEST_BODY_PARTS:
        return "non_chest", f"Body part {body_part} is not the chest"

    # Strong chest evidence: chest modality + chest body part + frontal view
    is_chest_modality = modality in CHEST_MODALITIES
    is_chest_body = body_part in CHEST_BODY_PARTS
    is_frontal = view_position in FRONTAL_VIEWS

    # Text hints
    desc_has_chest = any(
        kw in description
        for kw in ["CHEST", "THORAX", "CXR", "LUNG", "PA", "AP VIEW"]
    )

    if is_chest_modality and (is_chest_body or is_frontal or desc_has_chest):
        return "chest", "Chest modality with supporting metadata"

    if is_chest_body and is_frontal:
        return "chest", "Chest body part with frontal view"

    # Image-based validation if array provided
    if image_array is not None:
        state, reason = _image_validation(image_array, modality, body_part, view_position)
        if state != "uncertain":
            return state, reason

    # Uncertain: some suggestive evidence but not conclusive
    if is_chest_modality or is_chest_body or is_frontal or desc_has_chest:
        return "uncertain", "Some chest indicators present but not conclusive"

    # No chest evidence at all but not explicitly non-chest
    if not modality and not body_part:
        return "uncertain", "No metadata available; manual review required"

    return "uncertain", "Insufficient metadata to confirm chest X-ray"


def _image_validation(
    arr: np.ndarray, modality: str, body_part: str, view_position: str
) -> tuple[str, str]:
    """
    Heuristic image-based validation.
    Returns (state, reason).
    """
    try:
        from skimage.measure import shannon_entropy
        h, w = arr.shape[:2]

        # Aspect ratio check: chest X-rays are roughly square to 4:3 portrait/landscape
        ratio = h / max(w, 1)
        if ratio < 0.5 or ratio > 2.5:
            return "uncertain", "Unusual aspect ratio for chest X-ray"

        # Size check: minimum reasonable CXR resolution
        if h < 64 or w < 64:
            return "non_chest", "Image too small to be a diagnostic chest X-ray"

        # Entropy check: grayscale medical images have moderate entropy
        arr_norm = arr.astype(np.float32)
        arr_norm = (arr_norm - arr_norm.min()) / (arr_norm.max() - arr_norm.min() + 1e-8)
        entropy = shannon_entropy(arr_norm)

        if entropy < 0.5:
            return "uncertain", "Low image entropy; may be blank or artifact"
        if entropy > 7.5:
            return "uncertain", "High entropy; may not be a medical image"

        return "uncertain", "Image passes basic quality checks; metadata insufficient"
    except Exception as exc:
        logger.debug("Image validation error: %s", exc)
        return "uncertain", "Image validation error"


def render_dicom_frame(ds, frame_index: int = 0) -> np.ndarray:
    """
    Render a DICOM frame to a normalized float32 numpy array in range [-1024, 1024].
    Handles MONOCHROME1/2, rescale slope/intercept, windowing, multi-frame (first frame).
    """
    import pydicom
    from pydicom.pixel_data_handlers.util import apply_voi_lut

    # Get pixel array — pydicom handles compressed syntax via plugins
    try:
        pixel_array = ds.pixel_array
    except Exception as exc:
        raise ValueError(f"Cannot decode pixel data: {exc}") from exc

    # Handle multi-frame
    if pixel_array.ndim == 3:
        pixel_array = pixel_array[frame_index]

    if pixel_array.ndim != 2:
        raise ValueError(f"Unexpected pixel array shape: {pixel_array.shape}")

    arr = pixel_array.astype(np.float32)

    # Apply rescale slope/intercept (Modality LUT)
    slope = float(getattr(ds, "RescaleSlope", 1.0) or 1.0)
    intercept = float(getattr(ds, "RescaleIntercept", 0.0) or 0.0)
    arr = arr * slope + intercept

    # Apply windowing if available
    wc = getattr(ds, "WindowCenter", None)
    ww = getattr(ds, "WindowWidth", None)
    if wc is not None and ww is not None:
        try:
            wc_val = float(wc[0]) if hasattr(wc, "__iter__") and not isinstance(wc, str) else float(wc)
            ww_val = float(ww[0]) if hasattr(ww, "__iter__") and not isinstance(ww, str) else float(ww)
            if ww_val > 0:
                low = wc_val - ww_val / 2
                high = wc_val + ww_val / 2
                arr = np.clip(arr, low, high)
        except (ValueError, TypeError):
            pass

    # Handle MONOCHROME1 (invert)
    photometric = str(getattr(ds, "PhotometricInterpretation", "MONOCHROME2")).upper()
    if "MONOCHROME1" in photometric:
        arr = arr.max() - arr

    return arr


def render_dicom_frame_for_chester(ds, frame_index: int = 0) -> np.ndarray:
    """Render a DICOM frame as the 8-bit grayscale raster used by CHESTER."""
    try:
        pixel_array = ds.pixel_array
    except Exception as exc:
        raise ValueError(f"Cannot decode pixel data: {exc}") from exc

    if pixel_array.ndim == 3:
        pixel_array = pixel_array[frame_index]
    if pixel_array.ndim != 2:
        raise ValueError(f"Unexpected pixel array shape: {pixel_array.shape}")

    raw = pixel_array.astype(np.float32)
    slope = float(getattr(ds, "RescaleSlope", 1.0) or 1.0)
    intercept = float(getattr(ds, "RescaleIntercept", 0.0) or 0.0)
    values = raw * slope + intercept
    low = float(raw.min()) * slope + intercept
    high = float(raw.max()) * slope + intercept

    wc = getattr(ds, "WindowCenter", None)
    ww = getattr(ds, "WindowWidth", None)
    if wc is not None and ww is not None:
        try:
            wc_value = (
                float(wc[0])
                if hasattr(wc, "__iter__") and not isinstance(wc, str)
                else float(wc)
            )
            ww_value = (
                float(ww[0])
                if hasattr(ww, "__iter__") and not isinstance(ww, str)
                else float(ww)
            )
            if ww_value > 1:
                low = wc_value - ww_value / 2.0
                high = wc_value + ww_value / 2.0
        except (ValueError, TypeError):
            pass

    if not high > low:
        high = low + 1.0
    normalized = np.clip((values - low) / (high - low), 0.0, 1.0)

    photometric = str(
        getattr(ds, "PhotometricInterpretation", "MONOCHROME2")
    ).upper()
    if "MONOCHROME1" in photometric:
        normalized = 1.0 - normalized

    return np.rint(normalized * 255.0).astype(np.float32)


def render_to_pil(arr: np.ndarray):
    """Convert a 2D float array to an 8-bit PIL Image for thumbnail."""
    from PIL import Image

    vmin, vmax = arr.min(), arr.max()
    if vmax > vmin:
        normalized = (arr - vmin) / (vmax - vmin) * 255.0
    else:
        normalized = np.zeros_like(arr)
    img_array = np.clip(normalized, 0, 255).astype(np.uint8)
    return Image.fromarray(img_array, mode="L")


def generate_thumbnail(arr: np.ndarray, size: tuple[int, int] = (256, 256)) -> bytes:
    """Generate a PNG thumbnail from a 2D float array."""
    from PIL import Image
    import io as _io

    img = render_to_pil(arr)
    img = img.resize(size, Image.LANCZOS)
    buf = _io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def parse_dicom_bytes(data: bytes) -> object:
    """Parse DICOM bytes with pydicom, enabling all plugins."""
    import pydicom
    from pydicom.filebase import DicomBytesIO

    # Register pylibjpeg handlers if available
    try:
        import pylibjpeg  # noqa: F401
    except ImportError:
        pass
    try:
        import pydicom.config
        pydicom.config.convert_wrong_length_to_UN = True
    except Exception:
        pass

    ds = pydicom.dcmread(DicomBytesIO(data), force=True)
    if "PixelData" not in ds:
        raise ValueError("DICOM instance does not contain PixelData")
    if not getattr(ds, "Rows", None) or not getattr(ds, "Columns", None):
        raise ValueError("DICOM instance is missing Rows or Columns")
    return ds


def make_synthetic_uids(seed_bytes: bytes) -> dict:
    """Generate synthetic Study/Series/SOP UIDs from file bytes for non-DICOM uploads."""
    seed = hashlib.sha256(seed_bytes).hexdigest()
    return {
        "study_instance_uid": generate_synthetic_uid(seed + "study"),
        "series_instance_uid": generate_synthetic_uid(seed + "series"),
        "sop_instance_uid": generate_synthetic_uid(seed + "sop"),
    }


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
