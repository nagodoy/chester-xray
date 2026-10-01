"""Side and zone of a finding's evidence: the arithmetic, the orientation, the model."""

from __future__ import annotations

import numpy as np
import pytest
from pydicom.dataset import Dataset

from chester import inference, saliency, topography
from chester.inference import PATHOLOGIES, REPORTED_PATHOLOGIES


def grid(cells: dict[tuple[int, int], float], size: int = 7) -> np.ndarray:
    """A size x size evidence map with weight at (row, column)."""
    out = np.zeros((size, size), dtype=np.float32)
    for (row, column), value in cells.items():
        out[row, column] = value
    return out


class TestLocate:
    def test_the_viewers_left_is_the_patients_right(self):
        entry = topography.locate(grid({(5, 0): 1.0, (6, 1): 1.0}))
        assert entry["side"] == topography.SIDE_RIGHT
        assert entry["zones"] == ["lower"]
        assert entry["right_share"] == pytest.approx(1.0)

    def test_the_viewers_right_is_the_patients_left(self):
        entry = topography.locate(grid({(0, 6): 1.0}))
        assert entry["side"] == topography.SIDE_LEFT
        assert entry["zones"] == ["upper"]

    def test_a_mirrored_image_is_read_the_other_way_round(self):
        entry = topography.locate(grid({(5, 0): 1.0}), mirrored=True)
        assert entry["side"] == topography.SIDE_LEFT
        assert entry["orientation"] == topography.ORIENTATION_DICOM

    def test_without_an_orientation_the_convention_is_assumed_and_said(self):
        assert topography.locate(grid({(3, 0): 1.0}))["orientation"] == (
            topography.ORIENTATION_ASSUMED
        )
        assert topography.locate(grid({(3, 0): 1.0}), mirrored=False)["orientation"] == (
            topography.ORIENTATION_DICOM
        )

    def test_the_middle_column_is_split_evenly(self):
        entry = topography.locate(grid({(3, 3): 1.0}))
        assert entry["right_share"] == pytest.approx(0.5)
        assert entry["side"] == topography.SIDE_BILATERAL

    def test_evidence_on_both_sides_is_bilateral(self):
        entry = topography.locate(grid({(6, 0): 1.0, (6, 6): 1.0}))
        assert entry["side"] == topography.SIDE_BILATERAL

    def test_one_side_must_dominate_to_be_named(self):
        just_under = topography.SIDE_DOMINANCE - 0.01
        entry = topography.locate(grid({(6, 0): just_under, (6, 6): 1 - just_under}))
        assert entry["side"] == topography.SIDE_BILATERAL
        entry = topography.locate(grid({(6, 0): 0.7, (6, 6): 0.3}))
        assert entry["side"] == topography.SIDE_RIGHT

    def test_shares_are_exact_by_area_and_add_up(self):
        rng = np.random.default_rng(7)
        entry = topography.locate(rng.random((7, 7)))
        assert sum(entry["zone_shares"].values()) == pytest.approx(1.0, abs=1e-3)
        # Row 2 spans 2/7..3/7 and the upper third ends at 1/3: 1/3 of it is upper.
        entry = topography.locate(grid({(2, 0): 1.0}))
        assert entry["zone_shares"]["upper"] == pytest.approx(1 / 3, abs=1e-4)
        assert entry["zone_shares"]["middle"] == pytest.approx(2 / 3, abs=1e-4)
        assert entry["zones"] == ["upper", "middle"]

    def test_negative_evidence_is_not_counted(self):
        entry = topography.locate(grid({(6, 0): 1.0, (0, 6): -5.0}))
        assert entry["side"] == topography.SIDE_RIGHT
        assert entry["zones"] == ["lower"]

    def test_a_map_with_no_positive_evidence_has_no_topography(self):
        assert topography.locate(grid({(1, 1): -1.0})) is None
        assert topography.locate(np.zeros((7, 7))) is None


class TestLabels:
    def test_the_sheet_says_side_and_zone_in_portuguese(self):
        entry = {"side": "right", "zones": ["middle", "lower"]}
        assert topography.sheet_label(entry) == "HTD médio/inferior"
        assert topography.sheet_label({"side": "left", "zones": ["upper"]}) == "HTE superior"

    def test_all_three_thirds_read_as_diffuse(self):
        entry = {"side": "bilateral", "zones": ["upper", "middle", "lower"]}
        assert topography.sheet_label(entry) == "Bilateral difuso"

    def test_no_entry_prints_a_dash(self):
        assert topography.sheet_label(None) == "-"
        assert topography.dicom_code(None) == ""

    def test_the_dicom_code_is_upper_case_and_unspaced(self):
        entry = {"side": "bilateral", "zones": ["middle", "lower"]}
        assert topography.dicom_code(entry) == "BILATERAL/MIDDLE+LOWER"
        assert len(topography.dicom_code(entry)) <= 64  # LO


class TestOrientation:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (["L", "F"], False),
            ("L\\F", False),
            (["R", "F"], True),
            (["A", "F"], None),
            ("", None),
        ],
    )
    def test_patient_orientation_is_read_from_its_first_value(self, value, expected):
        dataset = Dataset()
        dataset.PatientOrientation = value
        assert topography.mirrored_from_dicom(dataset) is expected

    def test_a_dataset_without_the_tag_is_not_known(self):
        assert topography.mirrored_from_dicom(Dataset()) is None


@pytest.fixture
def effusion_film() -> np.ndarray:
    """The reference image with a frank finding, the one the docs measure."""
    from pathlib import Path

    from PIL import Image

    path = Path(__file__).resolve().parents[2] / "examples" / "Pneumonia-X-rays-Pictures-7.jpg"
    with Image.open(path) as image:
        return np.array(image.convert("RGB"), dtype=np.float32).mean(axis=2)


class TestWithTheModel:
    def test_every_reported_output_and_nothing_else_is_located(self, effusion_film):
        located = topography.locate_all(effusion_film)
        assert located is not None
        assert set(located) <= set(REPORTED_PATHOLOGIES)
        assert "Effusion" in located

    def test_contributions_reproduce_the_map_saliency_draws(self, effusion_film):
        maps = saliency.contributions(effusion_film)
        assert maps.shape[0] == len(PATHOLOGIES)
        index = PATHOLOGIES.index("Effusion")
        drawn = saliency.activation_map(effusion_film, "Effusion")
        assert np.allclose(drawn, saliency._to_unit_scale(maps[index]))

    def test_a_dicom_that_says_mirrored_swaps_every_side(self, effusion_film):
        # Flipping the pixels instead would not test this: the model is not
        # mirror-symmetric -- the heart is not -- so its evidence moves too.
        by_convention = topography.locate_all(effusion_film)
        mirrored = topography.locate_all(effusion_film, mirrored=True)
        assert set(mirrored) == set(by_convention)
        for pathology, entry in by_convention.items():
            assert mirrored[pathology]["right_share"] == pytest.approx(
                1.0 - entry["right_share"], abs=1e-3
            )
            assert mirrored[pathology]["zones"] == entry["zones"]

    def test_without_an_activation_there_is_no_topography(self, monkeypatch, effusion_film):
        monkeypatch.setattr(inference, "activation_available", lambda: False)
        assert topography.locate_all(effusion_film) is None
