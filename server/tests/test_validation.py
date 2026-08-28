"""Chest-radiograph validation.

Ported from the previous suite, which is the behavioural specification for this
logic, plus cases covering the conservative rule the previous tests left implicit:
uncertain input is held for review rather than analysed or dropped.
"""

from __future__ import annotations

import numpy as np
import pytest

from chester.imaging.validation import (
    AMBIGUOUS_PROJECTION,
    CHEST,
    ENGLISH_REASONS,
    FRONTAL,
    LATERAL,
    NON_CHEST,
    UNCERTAIN,
    UNKNOWN_PROJECTION,
    projection,
    validate_image,
    validate_study,
)


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
    assert validate_study(meta(**fields)).state == CHEST


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
    result = validate_study(meta(**fields))
    assert result.state == NON_CHEST
    assert result.code and result.reason


def test_absent_metadata_is_held_for_review():
    assert validate_study(meta()).state == UNCERTAIN


def test_partial_evidence_is_not_enough_on_its_own():
    """A chest modality with nothing corroborating it is only a hint."""
    assert validate_study(meta(modality="DX")).state == UNCERTAIN


def test_chest_body_part_needs_a_frontal_view_without_a_chest_modality():
    assert validate_study(meta(body_part="CHEST")).state == UNCERTAIN
    assert validate_study(meta(body_part="CHEST", view_position="PA")).state == CHEST


def test_image_alone_never_confirms_chest():
    """Pixel heuristics can rule an image out, but must never rule one in."""
    rng = np.random.default_rng(0)
    image = rng.integers(0, 200, (256, 256), dtype=np.uint8).astype(np.float32)
    assert validate_study(meta(), image).state == UNCERTAIN
    assert validate_image(image).state == UNCERTAIN


def test_tiny_images_are_rejected():
    result = validate_image(np.full((10, 10), 128, dtype=np.float32))
    assert result.state == NON_CHEST
    assert result.code == "image_too_small"


def test_extreme_aspect_ratio_is_held_for_review():
    rng = np.random.default_rng(1)
    panorama = rng.integers(0, 200, (64, 1024), dtype=np.uint8).astype(np.float32)
    assert validate_image(panorama).state == UNCERTAIN


def test_blank_image_is_held_for_review():
    result = validate_image(np.zeros((256, 256), dtype=np.float32))
    assert result.state == UNCERTAIN
    assert result.code == "low_entropy"


def test_non_chest_body_part_beats_a_chest_modality():
    """Positive evidence of another body part wins over a suggestive modality."""
    assert validate_study(meta(modality="DX", body_part="PELVIS")).state == NON_CHEST


def test_every_code_has_english_prose():
    """The prose is rendered from the code, so a new code needs an entry."""
    from chester.imaging import validation

    codes = {
        value
        for name, value in vars(validation).items()
        if name.startswith("CODE_") and isinstance(value, str)
    }
    assert codes == set(ENGLISH_REASONS)


def test_a_parameterised_reason_carries_its_value():
    result = validate_study(meta(modality="CT", body_part="HEAD"))

    assert result.code == "non_chest_modality"
    assert "CT" in result.reason


@pytest.mark.parametrize("view", ["LL", "RL", "L", "LAT", "LATERAL", "lateral"])
def test_a_lateral_view_position_is_refused(view):
    """The model reads frontal films; a lateral is not analysed at all."""
    result = validate_study(meta(modality="DX", body_part="CHEST", view_position=view))

    assert result.state == NON_CHEST
    assert result.code == "lateral_view"


@pytest.mark.parametrize(
    "fields",
    [
        {"description": "RX TORAX PERFIL"},
        {"series_description": "TORAX PERFIL"},
        {"protocol_name": "CHEST LATERAL"},
        {"description": "TORAX", "series_description": "LAT"},
    ],
)
def test_a_lateral_named_in_a_description_is_refused(fields):
    """Most exams say 'perfil' or 'lateral' somewhere, even without ViewPosition."""
    result = validate_study(meta(modality="DX", body_part="CHEST", **fields))

    assert result.state == NON_CHEST
    assert result.code == "lateral_view"


def test_the_view_position_beats_the_words_around_it():
    """The tag describes this instance; a description often covers the whole exam."""
    fields = meta(
        modality="DX",
        body_part="CHEST",
        view_position="PA",
        description="TORAX PA E PERFIL",
    )
    assert validate_study(fields).state == CHEST


def test_naming_both_projections_with_no_tag_is_held_for_review():
    """One string covering two films settles nothing, so a human decides."""
    result = validate_study(meta(modality="DX", body_part="CHEST", description="TORAX PA E PERFIL"))

    assert result.state == UNCERTAIN
    assert result.code == "projection_ambiguous"


@pytest.mark.parametrize(
    "description",
    ["COLLATERAL VESSELS", "BILATERAL COMPARISON", "SMALL FIELD"],
)
def test_a_word_that_merely_contains_a_view_is_not_a_lateral(description):
    """Matching is by whole word: discarding a frontal exam is the worse error."""
    result = validate_study(meta(modality="DX", body_part="CHEST", description=description))

    assert result.state == CHEST


def test_a_lateral_refusal_beats_every_chest_indicator():
    """A perfectly described chest DX is still refused when it is the lateral film."""
    fields = meta(
        modality="DX",
        body_part="CHEST",
        view_position="LL",
        description="CHEST",
        series_description="TORAX",
    )
    assert validate_study(fields).state == NON_CHEST


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"view_position": "PA"}, FRONTAL),
        ({"view_position": "AP"}, FRONTAL),
        ({"view_position": "LL"}, LATERAL),
        ({"description": "TORAX PERFIL"}, LATERAL),
        ({"description": "CHEST PA"}, FRONTAL),
        ({"description": "TORAX PA E PERFIL"}, AMBIGUOUS_PROJECTION),
        ({"description": "TORAX"}, UNKNOWN_PROJECTION),
        ({}, UNKNOWN_PROJECTION),
        # An oblique is neither, and is not guessed at from the words either.
        ({"view_position": "RLO", "description": "TORAX"}, UNKNOWN_PROJECTION),
    ],
)
def test_the_projection_is_read_the_same_way_everywhere(fields, expected):
    """The instance selector shares this classifier, so it is tested directly."""
    assert projection(meta(**fields)) == expected
