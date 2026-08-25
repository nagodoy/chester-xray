"""Conservative chest-radiograph validation.

The rule that matters: anything not confidently chest is held for human review
rather than silently analysed or silently dropped. Only positive evidence of a
different body part or an incompatible modality produces a rejection.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

import numpy as np

logger = logging.getLogger(__name__)

CHEST = "chest"
UNCERTAIN = "uncertain"
NON_CHEST = "non_chest"


class Validation(NamedTuple):
    """The outcome of validating a study.

    ``code`` is a stable identifier the interface translates; ``reason`` is the
    same thing in English prose, kept for logs and for any consumer without a
    translation table. The prose is derived from the code, never the other way
    round, so the two cannot drift.
    """

    state: str
    code: str
    reason: str


# Stable identifiers. The interface has a translation for each; changing one is a
# breaking change for stored studies, so add rather than rename.
CODE_NON_CHEST_MODALITY = "non_chest_modality"
CODE_NON_CHEST_BODY_PART = "non_chest_body_part"
CODE_CHEST_MODALITY = "chest_modality"
CODE_CHEST_FRONTAL = "chest_frontal"
CODE_INCONCLUSIVE = "inconclusive_indicators"
CODE_NO_METADATA = "no_metadata"
CODE_INSUFFICIENT_METADATA = "insufficient_metadata"
CODE_ASPECT_RATIO = "unusual_aspect_ratio"
CODE_TOO_SMALL = "image_too_small"
CODE_LOW_ENTROPY = "low_entropy"
CODE_HIGH_ENTROPY = "high_entropy"
CODE_IMAGE_CHECKS_PASSED = "image_checks_passed"
CODE_IMAGE_ERROR = "image_validation_error"
CODE_IMAGE_ONLY = "image_only_upload"

ENGLISH_REASONS: dict[str, str] = {
    CODE_NON_CHEST_MODALITY: "Modality {modality} is not compatible with chest X-ray analysis",
    CODE_NON_CHEST_BODY_PART: "Body part {body_part} is not the chest",
    CODE_CHEST_MODALITY: "Chest modality with supporting metadata",
    CODE_CHEST_FRONTAL: "Chest body part with frontal view",
    CODE_INCONCLUSIVE: "Some chest indicators present but not conclusive",
    CODE_NO_METADATA: "No metadata available; manual review required",
    CODE_INSUFFICIENT_METADATA: "Insufficient metadata to confirm chest X-ray",
    CODE_ASPECT_RATIO: "Unusual aspect ratio for chest X-ray",
    CODE_TOO_SMALL: "Image too small to be a diagnostic chest X-ray",
    CODE_LOW_ENTROPY: "Low image entropy; may be blank or artifact",
    CODE_HIGH_ENTROPY: "High entropy; may not be a medical image",
    CODE_IMAGE_CHECKS_PASSED: "Image passes basic quality checks; metadata insufficient",
    CODE_IMAGE_ERROR: "Image validation error",
    CODE_IMAGE_ONLY: "Image-only upload; manual review required before analysis",
}


def outcome(state: str, code: str, **params: str) -> Validation:
    """Build a result, rendering the English prose from the code."""
    return Validation(state, code, ENGLISH_REASONS[code].format(**params))

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


def validate_study(meta: dict, image: np.ndarray | None = None) -> Validation:
    """Classify a study as chest, uncertain or non_chest, with a reason."""
    modality = (meta.get("modality") or "").upper()
    body_part = (meta.get("body_part") or "").upper()
    view_position = (meta.get("view_position") or "").upper()
    description = (meta.get("description") or "").upper()

    if modality in NON_CHEST_MODALITIES:
        return outcome(NON_CHEST, CODE_NON_CHEST_MODALITY, modality=modality)

    if body_part and body_part in NON_CHEST_BODY_PARTS:
        return outcome(NON_CHEST, CODE_NON_CHEST_BODY_PART, body_part=body_part)

    is_chest_modality = modality in CHEST_MODALITIES
    is_chest_body = body_part in CHEST_BODY_PARTS
    is_frontal = view_position in FRONTAL_VIEWS
    described_as_chest = any(hint in description for hint in CHEST_DESCRIPTION_HINTS)

    if is_chest_modality and (is_chest_body or is_frontal or described_as_chest):
        return outcome(CHEST, CODE_CHEST_MODALITY)

    if is_chest_body and is_frontal:
        return outcome(CHEST, CODE_CHEST_FRONTAL)

    if image is not None:
        from_image = validate_image(image)
        if from_image.state != UNCERTAIN:
            return from_image

    if is_chest_modality or is_chest_body or is_frontal or described_as_chest:
        return outcome(UNCERTAIN, CODE_INCONCLUSIVE)

    if not modality and not body_part:
        return outcome(UNCERTAIN, CODE_NO_METADATA)

    return outcome(UNCERTAIN, CODE_INSUFFICIENT_METADATA)


def validate_image(image: np.ndarray) -> Validation:
    """Heuristic checks on the pixels alone.

    Deliberately weak: these can rule an image out on size, but never rule one in.
    Passing every check still returns uncertain.
    """
    try:
        from skimage.measure import shannon_entropy

        height, width = image.shape[:2]

        ratio = height / max(width, 1)
        if ratio < MIN_ASPECT_RATIO or ratio > MAX_ASPECT_RATIO:
            return outcome(UNCERTAIN, CODE_ASPECT_RATIO)

        if height < MIN_DIMENSION or width < MIN_DIMENSION:
            return outcome(NON_CHEST, CODE_TOO_SMALL)

        values = image.astype(np.float32)
        spread = values.max() - values.min()
        normalized = (values - values.min()) / (spread + 1e-8)
        entropy = shannon_entropy(normalized)

        if entropy < MIN_ENTROPY:
            return outcome(UNCERTAIN, CODE_LOW_ENTROPY)
        if entropy > MAX_ENTROPY:
            return outcome(UNCERTAIN, CODE_HIGH_ENTROPY)

        return outcome(UNCERTAIN, CODE_IMAGE_CHECKS_PASSED)
    except Exception as exc:
        logger.debug("Image validation error: %s", exc)
        return outcome(UNCERTAIN, CODE_IMAGE_ERROR)
