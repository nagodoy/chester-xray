"""tools/calibrate_thresholds.py: the operating points it proposes, on synthetic scores.

The tool deliberately runs without the server package, so it is loaded here by
path. Nothing below runs the model: the estimators take score arrays, and those
are what a reader of the table has to trust.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "calibrate_thresholds.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("calibrate_thresholds", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestYouden:
    def test_a_separable_set_is_split_where_both_rates_are_perfect(self, tool):
        negatives = np.array([0.1, 0.2, 0.3])
        positives = np.array([0.6, 0.7])
        cut = tool.youden(negatives, positives)
        assert 0.3 < cut <= 0.6
        assert (positives >= cut).all() and (negatives < cut).all()

    def test_it_maximises_sensitivity_plus_specificity(self, tool):
        negatives = np.array([0.1, 0.2, 0.35, 0.5])
        positives = np.array([0.3, 0.4, 0.6, 0.8])
        cut = tool.youden(negatives, positives)

        def j(t):
            return (positives >= t).mean() + (negatives < t).mean() - 1

        candidates = np.unique(np.concatenate([negatives, positives]))
        assert j(cut) == pytest.approx(max(j(t) for t in candidates))

    def test_a_tie_goes_to_the_more_sensitive_point(self, tool):
        # 0.3 and 0.6 both give J = 0.5; the lower one misses less.
        negatives = np.array([0.1, 0.5])
        positives = np.array([0.3, 0.6])
        assert tool.youden(negatives, positives) == pytest.approx(0.3)

    def test_it_needs_both_classes(self, tool):
        assert tool.youden(np.array([]), np.array([0.5])) is None
        assert tool.youden(np.array([0.5]), np.array([])) is None


class TestSensitivityCut:
    def test_it_is_the_highest_threshold_keeping_the_target(self, tool):
        positives = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05])
        cut = tool.sensitivity_cut(positives, 0.9)
        assert (positives >= cut).mean() >= 0.9
        assert (positives >= np.nextafter(cut, np.inf)).mean() < 0.9

    def test_a_target_that_is_not_exact_in_binary_is_still_met_exactly(self, tool):
        # 0.57 * 100 is 56.99999999999999; the 57 positives it asks for must be
        # 57, so the cut sits at the 57th highest score.
        positives = np.arange(100, 0, -1, dtype=float)
        cut = tool.sensitivity_cut(positives, 0.57)
        assert (positives >= cut).sum() == 57

    def test_full_sensitivity_sits_at_the_lowest_positive(self, tool):
        assert tool.sensitivity_cut(np.array([0.4, 0.2, 0.9]), 1.0) == pytest.approx(0.2)

    def test_no_positives_proposes_nothing(self, tool):
        assert tool.sensitivity_cut(np.array([]), 0.9) is None


class TestAuc:
    def test_perfect_and_inverted_separation(self, tool):
        assert tool.auc(np.array([0.1, 0.2]), np.array([0.8, 0.9])) == pytest.approx(1.0)
        assert tool.auc(np.array([0.8, 0.9]), np.array([0.1, 0.2])) == pytest.approx(0.0)

    def test_ties_count_half(self, tool):
        assert tool.auc(np.array([0.5]), np.array([0.5])) == pytest.approx(0.5)

    def test_it_matches_the_pairwise_definition(self, tool):
        rng = np.random.default_rng(3)
        negatives = rng.integers(0, 10, 40).astype(float)
        positives = rng.integers(3, 13, 30).astype(float)
        pairs = (positives[:, None] > negatives[None, :]).mean() + 0.5 * (
            positives[:, None] == negatives[None, :]
        ).mean()
        assert tool.auc(negatives, positives) == pytest.approx(pairs)


class TestReferenceStandards:
    """Rudolph et al., CHEST 2024, Table 2: grades 0-4 to yes-or-no."""

    def test_a_bare_label_is_certain(self, tool):
        assert tool.split_grade("Effusion", "x") == ("Effusion", 4)
        assert tool.split_grade("Effusion:2", "x") == ("Effusion", 2)

    @pytest.mark.parametrize("bad", ["Effusion:5", "Effusion:-1", "Effusion:likely"])
    def test_a_grade_off_the_scale_is_refused(self, tool, bad):
        with pytest.raises(SystemExit):
            tool.split_grade(bad, "x")

    @pytest.mark.parametrize(
        ("standard", "positive"),
        [
            ("I", {"Pneumothorax"}),
            ("II", {"Pneumothorax", "Effusion"}),
            ("III", {"Pneumothorax", "Effusion", "Consolidation"}),
            ("IV", {"Pneumothorax", "Effusion", "Consolidation", "Mass"}),
        ],
    )
    def test_each_standard_cuts_at_its_grade(self, tool, standard, positive):
        labels = ["Pneumothorax:4", "Effusion:3", "Consolidation:2", "Mass:1", "Edema:0"]
        cut = tool.REFERENCE_STANDARDS[standard]
        found, grades = tool.resolve_graded(labels, "x", True, cut)
        assert found == positive
        assert grades["Edema"] == 0

    def test_a_repeated_label_keeps_its_higher_grade(self, tool):
        # The study scored each hemithorax and kept the higher of the two.
        found, grades = tool.resolve_graded(["Effusion:1", "Effusion:3"], "x", True, 3)
        assert found == {"Effusion"}
        assert grades["Effusion"] == 3

    def test_a_graded_manifest_reads_under_the_chosen_standard(self, tool, tmp_path):
        image = tmp_path / "a.png"
        image.write_bytes(b"")
        manifest = tmp_path / "exams.csv"
        manifest.write_text("path,labels\na.png,Effusion:2;Pneumothorax:4\n", encoding="utf-8")

        strict = tool.read_manifest(manifest, True, tool.REFERENCE_STANDARDS["I"])
        sensitive = tool.read_manifest(manifest, True, tool.REFERENCE_STANDARDS["IV"])

        assert strict[0].positives == {"Pneumothorax"}
        assert sensitive[0].positives == {"Pneumothorax", "Effusion"}
        assert dict(sensitive[0].grades) == {"Effusion": 2, "Pneumothorax": 4}


class TestServerConstants:
    """Read from the server's source, so the bounds column cannot drift from it."""

    def test_the_deployed_points_are_the_servers(self, tool):
        from chester.inference import OPERATING_POINTS, SUPPRESSED_INDICES

        assert tuple(tool.deployed_points()) == pytest.approx(OPERATING_POINTS)
        assert tool.suppressed_indices() == SUPPRESSED_INDICES

    def test_the_override_factors_are_the_servers(self, tool):
        from chester.thresholds import MAX_FACTOR, MIN_FACTOR

        assert tool.override_factors() == (MIN_FACTOR, MAX_FACTOR)

    def test_the_tools_pathologies_match_the_servers(self, tool):
        from chester.inference import PATHOLOGIES

        assert tool.PATHOLOGIES == PATHOLOGIES
