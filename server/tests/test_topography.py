"""Side and zone of a finding's evidence, within the segmented lungs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from chester import inference, saliency, segmentation, topography
from chester.inference import IMAGE_SIZE, PATHOLOGIES, REPORTED_PATHOLOGIES

S = IMAGE_SIZE


def box(top: int, bottom: int, left: int, right: int) -> np.ndarray:
    mask = np.zeros((S, S), dtype=bool)
    mask[top:bottom, left:right] = True
    return mask


def chest(*, heart_on_viewers_right: bool = True) -> topography.Regions:
    """Two lungs in the conventional places: patient's right on the viewer's left.

    Rows 40..136 of the square, so the neck sits above them and the abdomen
    below, as on a real PA film.
    """
    heart = box(100, 140, 120, 160) if heart_on_viewers_right else box(100, 140, 64, 104)
    masks = segmentation.Masks(
        right_lung=box(40, 136, 30, 100),
        left_lung=box(40, 136, 124, 194),
        heart=heart,
    )
    regions = topography.prepare(masks)
    assert regions is not None
    return regions


def evidence(cells: dict[tuple[int, int], float]) -> np.ndarray:
    """A 7x7 evidence map with weight at (row, column); each cell is 32x32 pixels."""
    grid = np.zeros((7, 7), dtype=np.float32)
    for (row, column), value in cells.items():
        grid[row, column] = value
    return grid


class TestThorax:
    def test_evidence_on_the_neck_and_abdomen_is_outside_the_lung_fields(self):
        # The study this version was written for: the map lit the neck (top row,
        # centre) and the upper abdomen (bottom row), and was read as
        # "HTD superior/inferior".
        entry = topography.locate(evidence({(0, 3): 1.0, (6, 1): 1.0, (6, 5): 1.0}), chest())
        assert entry["status"] == topography.STATUS_EXTRAPULMONARY
        assert entry["thoracic_share"] < topography.THORACIC_MINIMUM
        assert topography.sheet_label(entry) == "Fora dos campos pulmonares"
        assert topography.dicom_code(entry) == "EXTRAPULMONARY"

    def test_evidence_in_the_right_lung_is_the_right_hemithorax(self):
        # Row 4 is pixels 128..160: the bottom of a lung spanning 40..136 and the
        # recess under it, all of it the lower third.
        entry = topography.locate(evidence({(4, 1): 1.0}), chest())
        assert entry["status"] == topography.STATUS_LOCATED
        assert entry["side"] == topography.SIDE_RIGHT
        assert entry["zones"] == ["lower"]
        assert topography.sheet_label(entry) == "HTD inferior"

    def test_evidence_in_the_left_lung_is_the_left_hemithorax(self):
        entry = topography.locate(evidence({(1, 5): 1.0}), chest())
        assert entry["side"] == topography.SIDE_LEFT
        assert entry["zones"] == ["upper"]

    def test_both_lungs_are_bilateral(self):
        entry = topography.locate(evidence({(2, 1): 1.0, (2, 5): 1.0}), chest())
        assert entry["side"] == topography.SIDE_BILATERAL

    def test_thirds_are_of_the_lung_not_of_the_square(self):
        # Row 2 is pixels 64..96: the middle third of the square, and the upper
        # and middle thirds of a lung spanning 40..136.
        entry = topography.locate(evidence({(2, 1): 1.0}), chest())
        assert entry["zones"] == ["upper", "middle"]

    def test_just_under_the_aerated_lung_is_still_the_hemithorax(self):
        # An effusion replaces the lung base: its evidence lies under the outline.
        regions = chest()
        assert regions.right[136 + 10, 60]
        assert not regions.right[136 + 40, 60]

    def test_nothing_above_the_apex_is_thoracic(self):
        regions = chest()
        assert not regions.right[40 - topography.LUNG_MARGIN - 2, 60]

    def test_the_lungs_do_not_share_pixels(self):
        regions = chest()
        assert not (regions.right & regions.left).any()

    def test_negative_evidence_is_not_counted(self):
        entry = topography.locate(evidence({(3, 1): 1.0, (3, 5): -5.0}), chest())
        assert entry["side"] == topography.SIDE_RIGHT

    def test_a_map_with_no_positive_evidence_has_no_topography(self):
        assert topography.locate(evidence({(3, 1): -1.0}), chest()) is None

    def test_the_spread_over_pixels_keeps_the_total(self):
        rng = np.random.default_rng(3)
        grid = rng.random((7, 7))
        assert topography.evidence_pixels(grid).sum() == pytest.approx(grid.sum())


class TestOrientation:
    def test_the_conventional_picture_is_trusted(self):
        assert chest().orientation == topography.ORIENTATION_CONVENTION

    def test_a_heart_on_the_viewers_left_leaves_the_side_undetermined(self):
        regions = chest(heart_on_viewers_right=False)
        assert regions.orientation == topography.ORIENTATION_UNCERTAIN
        entry = topography.locate(evidence({(4, 1): 1.0}), regions)
        assert topography.sheet_label(entry) == "Lado indeterminado inferior"
        assert topography.dicom_code(entry) == "UNDETERMINED/LOWER"

    def test_without_two_lungs_nothing_is_located(self):
        masks = segmentation.Masks(
            right_lung=box(40, 136, 30, 100),
            left_lung=np.zeros((S, S), dtype=bool),
            heart=np.zeros((S, S), dtype=bool),
        )
        assert topography.prepare(masks) is None


class TestVersions:
    def test_entries_from_before_segmentation_are_not_current(self):
        legacy = {"side": "right", "zones": ["upper", "lower"], "orientation": "assumed"}
        assert not topography.is_current(legacy)
        assert topography.sheet_label(legacy) == "-"
        assert topography.dicom_code(legacy) == ""

    def test_the_sheet_says_side_and_zone_in_portuguese(self):
        entry = {
            "version": topography.VERSION,
            "status": "located",
            "side": "left",
            "zones": ["upper", "middle", "lower"],
        }
        assert topography.sheet_label(entry) == "HTE difuso"
        assert topography.dicom_code(entry) == "LEFT/UPPER+MIDDLE+LOWER"


@pytest.fixture
def effusion_film() -> np.ndarray:
    """The reference image with a frank effusion, the one the docs measure."""
    from PIL import Image

    path = Path(__file__).resolve().parents[2] / "examples" / "Pneumonia-X-rays-Pictures-7.jpg"
    with Image.open(path) as image:
        return np.array(image.convert("RGB"), dtype=np.float32).mean(axis=2)


needs_segmenter = pytest.mark.skipif(
    not segmentation.available(), reason="segmentation model artifact is not present"
)


@needs_segmenter
class TestWithTheModels:
    def test_the_lungs_and_heart_are_where_convention_puts_them(self, effusion_film):
        masks = segmentation.lung_masks(effusion_film)
        right_x = np.nonzero(masks.right_lung)[1].mean()
        left_x = np.nonzero(masks.left_lung)[1].mean()
        heart_x = np.nonzero(masks.heart)[1].mean()
        assert right_x < S / 2 < left_x
        assert heart_x > (right_x + left_x) / 2

    def test_the_effusion_is_placed_in_the_lower_hemithoraces(self, effusion_film):
        located = topography.locate_all(effusion_film)
        entry = located["Effusion"]
        assert entry["status"] == topography.STATUS_LOCATED
        assert "lower" in entry["zones"]
        assert entry["orientation"] == topography.ORIENTATION_CONVENTION

    def test_central_structures_get_no_hemithorax(self, effusion_film):
        located = topography.locate_all(effusion_film)
        assert set(located) <= set(REPORTED_PATHOLOGIES) - topography.NOT_LATERALIZED
        assert "Cardiomegaly" not in located

    def test_contributions_reproduce_the_map_saliency_draws(self, effusion_film):
        maps = saliency.contributions(effusion_film)
        index = PATHOLOGIES.index("Effusion")
        drawn = saliency.activation_map(effusion_film, "Effusion")
        assert np.allclose(drawn, saliency._to_unit_scale(maps[index]))


def test_without_an_activation_there_is_no_topography(monkeypatch, effusion_film):
    monkeypatch.setattr(inference, "activation_available", lambda: False)
    assert topography.locate_all(effusion_film) is None


def test_without_a_segmenter_there_is_no_topography(monkeypatch, effusion_film):
    monkeypatch.setattr(segmentation, "lung_masks", lambda pixels: None)
    assert topography.locate_all(effusion_film) is None
