"""Conservative chest-radiograph validation.

The rule that matters: anything not confidently chest is held for human review
rather than silently analysed or silently dropped. Only positive evidence of a
different body part or an incompatible modality produces a rejection.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

CHEST = "chest"
UNCERTAIN = "uncertain"
NON_CHEST = "non_chest"

CHEST_MODALITIES = frozenset({"DX", "CR", "RG"})
NON_CHEST_MODALITIES = frozenset({"CT", "MR", "US", "NM", "PT", "MG", "OT", "XA", "RF", "SC"})

CHEST_BODY_PARTS = frozenset(
    {"CHEST", "THORAX", "LUNG", "HEART", "RIBCAGE", "STERNUM", "MEDIASTINUM", "TRACHEA", "BRONCHUS"}
)
NON_CHEST_BODY_PARTS = frozenset(
    {
        "HEAD",
        "SKULL",
        "BRAIN",
        "NECK",
        "ABDOMEN",
        "PELVIS",
        "SPINE",
        "CSPINE",
        "TSPINE",
        "LSPINE",
        "EXTREMITY",
        "ARM",
        "LEG",
        "KNEE",
        "HAND",
        "FOOT",
        "HIP",
        "SHOULDER",
        "ELBOW",
        "WRIST",
        "ANKLE",
        "MANDIBLE",
        "ORBIT",
        "SINUSES",
    }
)

FRONTAL_VIEWS = frozenset({"PA", "AP", "PA/AP"})
CHEST_DESCRIPTION_HINTS = ("CHEST", "THORAX", "CXR", "LUNG", "PA", "AP VIEW")

MIN_DIMENSION = 64
MIN_ASPECT_RATIO = 0.5
MAX_ASPECT_RATIO = 2.5
MIN_ENTROPY = 0.5
MAX_ENTROPY = 7.5


def validate_study(meta: dict, image: np.ndarray | None = None) -> tuple[str, str]:
    """Classify a study as chest, uncertain or non_chest, with a reason."""
    modality = (meta.get("modality") or "").upper()
    body_part = (meta.get("body_part") or "").upper()
    view_position = (meta.get("view_position") or "").upper()
    description = (meta.get("description") or "").upper()

    if modality in NON_CHEST_MODALITIES:
        return NON_CHEST, f"Modality {modality} is not compatible with chest X-ray analysis"

    if body_part and body_part in NON_CHEST_BODY_PARTS:
        return NON_CHEST, f"Body part {body_part} is not the chest"

    is_chest_modality = modality in CHEST_MODALITIES
    is_chest_body = body_part in CHEST_BODY_PARTS
    is_frontal = view_position in FRONTAL_VIEWS
    described_as_chest = any(hint in description for hint in CHEST_DESCRIPTION_HINTS)

    if is_chest_modality and (is_chest_body or is_frontal or described_as_chest):
        return CHEST, "Chest modality with supporting metadata"

    if is_chest_body and is_frontal:
        return CHEST, "Chest body part with frontal view"

    if image is not None:
        state, reason = validate_image(image)
        if state != UNCERTAIN:
            return state, reason

    if is_chest_modality or is_chest_body or is_frontal or described_as_chest:
        return UNCERTAIN, "Some chest indicators present but not conclusive"

    if not modality and not body_part:
        return UNCERTAIN, "No metadata available; manual review required"

    return UNCERTAIN, "Insufficient metadata to confirm chest X-ray"


def validate_image(image: np.ndarray) -> tuple[str, str]:
    """Heuristic checks on the pixels alone.

    Deliberately weak: these can rule an image out on size, but never rule one in.
    Passing every check still returns uncertain.
    """
    try:
        from skimage.measure import shannon_entropy

        height, width = image.shape[:2]

        ratio = height / max(width, 1)
        if ratio < MIN_ASPECT_RATIO or ratio > MAX_ASPECT_RATIO:
            return UNCERTAIN, "Unusual aspect ratio for chest X-ray"

        if height < MIN_DIMENSION or width < MIN_DIMENSION:
            return NON_CHEST, "Image too small to be a diagnostic chest X-ray"

        values = image.astype(np.float32)
        spread = values.max() - values.min()
        normalized = (values - values.min()) / (spread + 1e-8)
        entropy = shannon_entropy(normalized)

        if entropy < MIN_ENTROPY:
            return UNCERTAIN, "Low image entropy; may be blank or artifact"
        if entropy > MAX_ENTROPY:
            return UNCERTAIN, "High entropy; may not be a medical image"

        return UNCERTAIN, "Image passes basic quality checks; metadata insufficient"
    except Exception as exc:
        logger.debug("Image validation error: %s", exc)
        return UNCERTAIN, "Image validation error"
