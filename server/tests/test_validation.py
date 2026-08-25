"""Chest-radiograph validation.

Ported from the previous suite, which is the behavioural specification for this
logic, plus cases covering the conservative rule the previous tests left implicit:
uncertain input is held for review rather than analysed or dropped.
"""

from __future__ import annotations

import numpy as np
import pytest

from chester.imaging.validation import CHEST, NON_CHEST, UNCERTAIN, validate_image, validate_study


def meta(**overrides) -> dict:
    base = {"modality": "", "body_part": "", "view_position": "", "description": ""}
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "fields",
    [
        {"modality": "DX", "body_part": "CHEST", "view_position": "PA", "description": "CHEST PA"},
        {"modality": "CR", "body_part": "THORAX", "view_position": "AP"},
        {"modality": "DX", "body_part": "CHEST"},
    ],
)
def test_chest_metadata_is_accepted(fields):
    state, _ = validate_study(meta(**fields))
    assert state == CHEST


@pytest.mark.parametrize(
    "fields",
    [
        {"modality": "CT", "body_part": "HEAD"},
        {"modality": "MR", "body_part": "HEAD"},
        {"modality": "DX", "body_part": "KNEE"},
        {"modality": "US"},
    ],
)
def test_incompatible_studies_are_rejected(fields):
    state, reason = validate_study(meta(**fields))
    assert state == NON_CHEST
    assert reason


def test_absent_metadata_is_held_for_review():
    state, _ = validate_study(meta())
    assert state == UNCERTAIN


def test_partial_evidence_is_not_enough_on_its_own():
    """A chest modality with nothing corroborating it is only a hint."""
    state, _ = validate_study(meta(modality="DX"))
    assert state == UNCERTAIN


def test_chest_body_part_needs_a_frontal_view_without_a_chest_modality():
    assert validate_study(meta(body_part="CHEST"))[0] == UNCERTAIN
    assert validate_study(meta(body_part="CHEST", view_position="PA"))[0] == CHEST


def test_image_alone_never_confirms_chest():
    """Pixel heuristics can rule an image out, but must never rule one in."""
    rng = np.random.default_rng(0)
    image = rng.integers(0, 200, (256, 256), dtype=np.uint8).astype(np.float32)
    assert validate_study(meta(), image)[0] == UNCERTAIN
    assert validate_image(image)[0] == UNCERTAIN


def test_tiny_images_are_rejected():
    state, reason = validate_image(np.full((10, 10), 128, dtype=np.float32))
    assert state == NON_CHEST
    assert "too small" in reason.lower()


def test_extreme_aspect_ratio_is_held_for_review():
    rng = np.random.default_rng(1)
    panorama = rng.integers(0, 200, (64, 1024), dtype=np.uint8).astype(np.float32)
    assert validate_image(panorama)[0] == UNCERTAIN


def test_blank_image_is_held_for_review():
    state, reason = validate_image(np.zeros((256, 256), dtype=np.float32))
    assert state == UNCERTAIN
    assert "entropy" in reason.lower()


def test_non_chest_body_part_beats_a_chest_modality():
    """Positive evidence of another body part wins over a suggestive modality."""
    assert validate_study(meta(modality="DX", body_part="PELVIS"))[0] == NON_CHEST
