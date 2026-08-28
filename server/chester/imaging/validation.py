"""Conservative chest-radiograph validation.

The rule that matters: anything not confidently chest is held for human review
rather than silently analysed or silently dropped. Only positive evidence --
a different body part, an incompatible modality, or a lateral projection --
produces a rejection.

The model reads frontal chest radiographs. A lateral film is a different
picture of the same anatomy, and scoring one produces numbers that look like
findings and are not, so a projection recognised as lateral is refused rather
than analysed. Where the projection cannot be established the study is held for
a human, as everything else uncertain is.
"""

from __future__ import annotations

import logging
import re
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
CODE_LATERAL_VIEW = "lateral_view"
CODE_PROJECTION_AMBIGUOUS = "projection_ambiguous"
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
    CODE_LATERAL_VIEW: "Lateral projection; only frontal (PA/AP) chest views are analysed",
    CODE_PROJECTION_AMBIGUOUS: (
        "Both a frontal and a lateral projection are named and neither is confirmed; "
        "manual review required"
    ),
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

# What a study is a picture of, as far as the metadata says.
FRONTAL = "frontal"
LATERAL = "lateral"
UNKNOWN_PROJECTION = "unknown"
AMBIGUOUS_PROJECTION = "ambiguous"

FRONTAL_VIEWS = frozenset({"PA", "AP", "PA/AP", "AP/PA", "FRONTAL"})
# ViewPosition (0018,5101) as the modalities actually write it. LL and RL are the
# defined terms; the rest are the non-standard strings vendors emit for the same
# film. "L" is included: as a view position it means lateral, laterality having
# its own tag.
LATERAL_VIEWS = frozenset(
    {"LL", "RL", "L", "LAT", "LATERAL", "LLAT", "RLAT", "XTABLE LATERAL", "LATERAL DECUBITUS"}
)

CHEST_DESCRIPTION_HINTS = ("CHEST", "THORAX", "CXR", "LUNG", "PA", "AP VIEW")

# Words that name a projection when no ViewPosition does. Matched as whole words
# against the study, series and protocol descriptions, never as substrings: "LL"
# inside another word is not a lateral film, and a report that discards a frontal
# exam by accident is worse than one that holds it for review.
FRONTAL_WORDS = frozenset({"PA", "AP", "FRONTAL", "FRENTE"})
LATERAL_WORDS = frozenset({"LAT", "LATERAL", "PERFIL", "LL", "RL", "LLAT", "RLAT"})

DESCRIPTION_FIELDS = ("description", "series_description", "protocol_name")
_WORDS = re.compile(r"[^A-Z0-9]+")

MIN_DIMENSION = 64
MIN_ASPECT_RATIO = 0.5
MAX_ASPECT_RATIO = 2.5
MIN_ENTROPY = 0.5
MAX_ENTROPY = 7.5


def _words(meta: dict) -> set[str]:
    """Every whole word the descriptive fields of a study carry, upper-cased."""
    found: set[str] = set()
    for field in DESCRIPTION_FIELDS:
        text = (meta.get(field) or "").upper()
        found.update(token for token in _WORDS.split(text) if token)
    return found


def projection(meta: dict) -> str:
    """Say whether a study is frontal, lateral, ambiguous or simply unknown.

    ViewPosition decides when it says anything this recognises: it describes the
    instance, while a description is often written for the whole exam. Only when
    it is absent or unrecognised do the words get a say, and a description naming
    both projections -- "TORAX PA E PERFIL", one string covering two films --
    settles nothing, so it is reported as ambiguous rather than guessed.
    """
    view_position = (meta.get("view_position") or "").upper().strip()
    if view_position in FRONTAL_VIEWS:
        return FRONTAL
    if view_position in LATERAL_VIEWS:
        return LATERAL

    words = _words(meta)
    frontal = bool(words & FRONTAL_WORDS)
    lateral = bool(words & LATERAL_WORDS)
    if frontal and lateral:
        return AMBIGUOUS_PROJECTION
    if lateral:
        return LATERAL
    if frontal:
        return FRONTAL
    return UNKNOWN_PROJECTION


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

    # The projection is decided before anything else about the chest, so that a
    # lateral film is refused whatever else its metadata would have established.
    view = projection(meta)
    if view == LATERAL:
        return outcome(NON_CHEST, CODE_LATERAL_VIEW)
    if view == AMBIGUOUS_PROJECTION:
        return outcome(UNCERTAIN, CODE_PROJECTION_AMBIGUOUS)

    is_chest_modality = modality in CHEST_MODALITIES
    is_chest_body = body_part in CHEST_BODY_PARTS
    # Ruling a study in still needs the tag. A word in a description can say a
    # film is lateral, which refuses it, but "PA" written in an exam description
    # is not evidence that this instance is the frontal one.
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
