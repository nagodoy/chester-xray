"""Outputs this deployment computes and does not report.

Fibrosis joined the six the CHESTER configuration already blanked. The rule that
matters here is that suppression reaches *stored* results too: a study analysed
before the change still carries the output in its document, and a report sheet or
a DICOM tag built from it must not print a finding this node no longer stands
behind.
"""

from __future__ import annotations

from types import SimpleNamespace

from chester import inference, report
from chester.schemas import AnalysisResultSchema


class TestTheReportedSet:
    def test_fibrosis_is_not_reported(self):
        assert "Fibrosis" not in inference.REPORTED_PATHOLOGIES
        assert not inference.is_reported("Fibrosis")

    def test_eleven_outputs_are_reported(self):
        assert len(inference.REPORTED_PATHOLOGIES) == 11

    def test_the_reported_set_keeps_the_models_own_order(self):
        expected = [
            name
            for index, name in enumerate(inference.PATHOLOGIES)
            if index not in inference.SUPPRESSED_INDICES
        ]
        assert list(inference.REPORTED_PATHOLOGIES) == expected

    def test_the_outputs_the_previous_deployment_blanked_are_still_suppressed(self):
        for name in (
            "Infiltration",
            "Pneumothorax",
            "Pneumonia",
            "Nodule",
            "Lung Lesion",
            "Fracture",
        ):
            assert not inference.is_reported(name)

    def test_the_findings_that_remain_are_still_reported(self):
        for name in ("Atelectasis", "Effusion", "Cardiomegaly", "Lung Opacity"):
            assert inference.is_reported(name)


def stored_result(**overrides):
    """A result recorded before Fibrosis was suppressed."""
    base = {
        "raw_scores": {"Fibrosis": 0.0179, "Effusion": 0.0045, "Mass": 0.0127},
        "op_normalized_scores": {"Fibrosis": 0.504, "Effusion": 0.022, "Mass": 0.327},
        "thresholds": {"Fibrosis": 0.010060724, "Effusion": 0.103246614, "Mass": 0.01939503},
        "above_threshold": {"Fibrosis": True, "Effusion": False, "Mass": False},
        "above_threshold_findings": ["Fibrosis"],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestHistoricalResults:
    def test_the_report_sheet_drops_a_suppressed_finding(self):
        rows = report.finding_rows(stored_result())
        assert [row["pathology"] for row in rows] == ["Effusion", "Mass"]

    def test_the_dicom_tags_cannot_carry_it_either(self):
        """The tags are built from the same rows, so one filter covers both."""
        rows = report.finding_rows(stored_result())
        assert all(row["code_meaning"] != "FIBROSIS" for row in rows)

    def test_the_findings_that_remain_keep_their_scores(self):
        rows = {row["pathology"]: row for row in report.finding_rows(stored_result())}
        assert rows["Mass"]["score"] == 0.0127
        assert rows["Mass"]["threshold"] == 0.01939503
        # 0.0127 sits below the 0.00194 band around Mass's operating point.
        assert rows["Mass"]["confidence"] == report.CONFIDENCE_ABSENT

    def test_the_api_does_not_serve_a_suppressed_output(self):
        import uuid
        from datetime import UTC, datetime

        schema = AnalysisResultSchema(
            id=uuid.uuid4(),
            model_version="chester-onnx:densenet121-res224-all",
            preprocessing_version="2.0.0",
            created_at=datetime.now(UTC),
            **vars(stored_result()),
        )
        assert "Fibrosis" not in (schema.raw_scores or {})
        assert "Fibrosis" not in (schema.op_normalized_scores or {})
        assert "Fibrosis" not in (schema.thresholds or {})
        assert "Fibrosis" not in (schema.above_threshold or {})
        assert schema.above_threshold_findings == []
        # Everything else survives untouched.
        assert schema.raw_scores == {"Effusion": 0.0045, "Mass": 0.0127}


class TestFreshResults:
    def test_a_new_run_never_records_a_suppressed_output(self, monkeypatch):
        """infer() skips the suppressed indices, so nothing downstream sees them."""
        import numpy as np

        scores = np.full(inference.OUTPUT_COUNT, 0.5, dtype=np.float32)

        class FakeSession:
            def run(self, _outputs, _inputs):
                return [scores.reshape(1, -1)]

        monkeypatch.setattr(inference, "get_session", lambda: FakeSession())
        outcome = inference.infer(np.full((256, 256), 128.0, dtype=np.float32))

        assert "Fibrosis" not in outcome["raw_scores"]
        assert "Fibrosis" not in outcome["above_threshold_findings"]
        assert len(outcome["raw_scores"]) == 11
