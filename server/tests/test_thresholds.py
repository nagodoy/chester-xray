"""Operating points an organization has moved off the built-in default.

The thing worth protecting here is the boundary between a default and an
override. A default is a clinical decision that went through code review with its
evidence written beside it; an override is a local adjustment made from a form.
So these tests care about three things: that an override reaches the next
analysis, that it cannot reach a stored one, and that a typo in the form cannot
turn an output into "report everything" or "report nothing".
"""

from __future__ import annotations

import pytest

from chester import inference, thresholds
from chester.models import AuditEvent, ThresholdOverride
from chester.security.roles import ROLE_ADMIN, ROLE_TECHNICIAN


@pytest.fixture
def admin(make_user):
    return make_user("admin@example.com", ROLE_ADMIN)


@pytest.fixture
def headers(signed_in, admin):
    return signed_in("admin@example.com")[0]


@pytest.fixture
def technician(make_user):
    return make_user("tech@example.com", ROLE_TECHNICIAN)


class TestTheDefaults:
    def test_every_reported_output_has_one_and_nothing_else_does(self):
        assert set(thresholds.DEFAULTS) == set(inference.REPORTED_PATHOLOGIES)

    def test_a_default_is_the_operating_point_at_its_own_index(self):
        for index, name in enumerate(inference.PATHOLOGIES):
            if name in thresholds.DEFAULTS:
                assert thresholds.DEFAULTS[name] == inference.OPERATING_POINTS[index]

    def test_a_suppressed_output_cannot_be_adjusted(self):
        """There is no result to apply it to, so the form must not offer one."""
        assert not thresholds.is_adjustable("Fibrosis")
        with pytest.raises(ValueError):
            thresholds.normalize("Fibrosis", 0.5)

    def test_the_defaults_keep_the_models_own_order(self):
        """The table reads in the same order as the report sheet."""
        assert list(thresholds.DEFAULTS) == list(inference.REPORTED_PATHOLOGIES)


class TestBounds:
    def test_a_value_inside_the_range_is_accepted(self):
        default = thresholds.DEFAULTS["Infiltration"]
        assert thresholds.normalize("Infiltration", default * 1.5) == pytest.approx(default * 1.5)

    def test_zero_is_refused(self):
        """Zero reports every finding on every exam."""
        with pytest.raises(ValueError):
            thresholds.normalize("Infiltration", 0.0)

    def test_one_is_refused(self):
        """One reports nothing, ever."""
        with pytest.raises(ValueError):
            thresholds.normalize("Infiltration", 1.0)

    def test_the_edges_of_the_range_are_inside_it(self):
        low, high = thresholds.bounds("Infiltration")
        assert thresholds.normalize("Infiltration", low) == pytest.approx(low)
        assert thresholds.normalize("Infiltration", high) == pytest.approx(high)

    def test_just_outside_the_range_is_refused(self):
        low, high = thresholds.bounds("Infiltration")
        with pytest.raises(ValueError):
            thresholds.normalize("Infiltration", low * 0.99)
        with pytest.raises(ValueError):
            thresholds.normalize("Infiltration", high * 1.01)

    def test_a_value_that_is_not_a_number_is_refused(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ValueError):
                thresholds.normalize("Infiltration", bad)


class TestResolving:
    def test_an_organization_with_no_overrides_gets_the_defaults(self, session, organization):
        assert thresholds.in_force(session, organization.id) == thresholds.DEFAULTS

    def test_an_override_replaces_only_its_own_output(self, session, organization):
        thresholds.set_override(
            session, organization.id, "Infiltration", 0.2, actor="admin@example.com"
        )
        session.flush()

        points = thresholds.in_force(session, organization.id)
        assert points["Infiltration"] == pytest.approx(0.2)
        assert points["Effusion"] == thresholds.DEFAULTS["Effusion"]

    def test_setting_twice_updates_rather_than_duplicates(self, session, organization):
        for value in (0.2, 0.25):
            thresholds.set_override(
                session, organization.id, "Infiltration", value, actor="admin@example.com"
            )
        session.flush()

        rows = (
            session.query(ThresholdOverride)
            .filter(ThresholdOverride.pathology == "Infiltration")
            .all()
        )
        assert len(rows) == 1
        assert thresholds.in_force(session, organization.id)["Infiltration"] == pytest.approx(0.25)

    def test_clearing_returns_the_output_to_its_default(self, session, organization):
        thresholds.set_override(
            session, organization.id, "Infiltration", 0.2, actor="admin@example.com"
        )
        session.flush()
        assert thresholds.clear_override(
            session, organization.id, "Infiltration", actor="admin@example.com"
        )
        session.flush()

        assert thresholds.in_force(session, organization.id) == thresholds.DEFAULTS

    def test_one_organizations_override_does_not_reach_another(
        self, session, organization, make_user
    ):
        from chester.models import Organization

        other = Organization(name="Other", slug="other-org")
        session.add(other)
        session.flush()

        thresholds.set_override(
            session, organization.id, "Infiltration", 0.2, actor="admin@example.com"
        )
        session.flush()

        assert thresholds.in_force(session, other.id) == thresholds.DEFAULTS


class TestTheTrail:
    def test_setting_one_records_who_and_what(self, session, organization):
        thresholds.set_override(
            session, organization.id, "Infiltration", 0.2, actor="admin@example.com"
        )
        session.flush()

        event = (
            session.query(AuditEvent).filter(AuditEvent.event_type == thresholds.AUDIT_EVENT).one()
        )
        assert event.actor == "admin@example.com"
        assert event.study_id is None
        assert event.detail["pathology"] == "Infiltration"
        assert event.detail["previous"] is None
        assert event.detail["current"] == pytest.approx(0.2)
        assert event.detail["default"] == thresholds.DEFAULTS["Infiltration"]

    def test_changing_one_records_what_it_was(self, session, organization):
        for value in (0.2, 0.25):
            thresholds.set_override(
                session, organization.id, "Infiltration", value, actor="admin@example.com"
            )
        session.flush()

        events = (
            session.query(AuditEvent).filter(AuditEvent.event_type == thresholds.AUDIT_EVENT).all()
        )
        assert len(events) == 2
        assert events[-1].detail["previous"] == pytest.approx(0.2)
        assert events[-1].detail["current"] == pytest.approx(0.25)

    def test_a_reset_is_recorded_as_one(self, session, organization):
        thresholds.set_override(
            session, organization.id, "Infiltration", 0.2, actor="admin@example.com"
        )
        session.flush()
        thresholds.clear_override(
            session, organization.id, "Infiltration", actor="admin@example.com"
        )
        session.flush()

        event = (
            session.query(AuditEvent)
            .filter(AuditEvent.event_type == thresholds.AUDIT_EVENT)
            .all()[-1]
        )
        assert event.detail["reset_to_default"] is True
        assert event.detail["current"] == thresholds.DEFAULTS["Infiltration"]

    def test_clearing_nothing_records_nothing(self, session, organization):
        assert not thresholds.clear_override(
            session, organization.id, "Infiltration", actor="admin@example.com"
        )
        session.flush()
        assert (
            session.query(AuditEvent)
            .filter(AuditEvent.event_type == thresholds.AUDIT_EVENT)
            .count()
            == 0
        )


class TestInference:
    """The override has to reach the decision, not just the database."""

    def _fake_session(self, monkeypatch, value: float):
        import numpy as np

        scores = np.full(inference.OUTPUT_COUNT, value, dtype=np.float32)

        class FakeSession:
            def run(self, _outputs, _inputs):
                return [scores.reshape(1, -1)]

        monkeypatch.setattr(inference, "get_session", lambda: FakeSession())

    def test_a_supplied_point_decides_instead_of_the_default(self, monkeypatch):
        import numpy as np

        default = thresholds.DEFAULTS["Infiltration"]
        self._fake_session(monkeypatch, default * 1.01)
        pixels = np.full((256, 256), 128.0, dtype=np.float32)

        on_default = inference.infer(pixels)
        assert on_default["above_threshold"]["Infiltration"] is True

        raised = inference.infer(pixels, {"Infiltration": default * 1.5})
        assert raised["above_threshold"]["Infiltration"] is False
        assert raised["thresholds"]["Infiltration"] == pytest.approx(default * 1.5)
        assert "Infiltration" not in raised["above_threshold_findings"]

    def test_an_output_the_mapping_omits_keeps_its_default(self, monkeypatch):
        import numpy as np

        self._fake_session(monkeypatch, 0.5)
        outcome = inference.infer(
            np.full((256, 256), 128.0, dtype=np.float32),
            {"Infiltration": 0.9},
        )
        assert outcome["thresholds"]["Effusion"] == thresholds.DEFAULTS["Effusion"]

    def test_no_mapping_is_the_behaviour_that_was_there_before(self, monkeypatch):
        import numpy as np

        self._fake_session(monkeypatch, 0.5)
        outcome = inference.infer(np.full((256, 256), 128.0, dtype=np.float32))
        assert outcome["thresholds"] == thresholds.DEFAULTS


class TestTheEndpoint:
    def test_the_table_lists_every_reported_output_on_its_default(self, client, headers):
        response = client.get("/api/settings/thresholds", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()

        assert [item["pathology"] for item in body["items"]] == list(inference.REPORTED_PATHOLOGIES)
        assert all(item["overridden"] is False for item in body["items"])
        assert all(item["effective"] == item["default"] for item in body["items"])
        assert body["editable"] is True

    def test_a_row_carries_both_edges_of_the_doubt_band(self, client, headers):
        body = client.get("/api/settings/thresholds", headers=headers).json()
        row = next(item for item in body["items"] if item["pathology"] == "Infiltration")
        band = body["doubt_band"]
        assert row["lower"] == pytest.approx(row["effective"] * (1 - band))
        assert row["upper"] == pytest.approx(row["effective"] * (1 + band))

    def test_setting_one_returns_the_whole_table(self, client, headers):
        default = thresholds.DEFAULTS["Infiltration"]
        response = client.put(
            "/api/settings/thresholds/Infiltration",
            json={"threshold": default * 1.5},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()

        row = next(item for item in body["items"] if item["pathology"] == "Infiltration")
        assert row["overridden"] is True
        assert row["effective"] == pytest.approx(default * 1.5)
        assert row["factor"] == pytest.approx(1.5)
        assert row["updated_by"] == "admin@example.com"
        # And the band moved with it.
        assert row["lower"] == pytest.approx(default * 1.5 * (1 - body["doubt_band"]))

    def test_resetting_puts_it_back(self, client, headers):
        default = thresholds.DEFAULTS["Infiltration"]
        client.put(
            "/api/settings/thresholds/Infiltration",
            json={"threshold": default * 1.5},
            headers=headers,
        )
        response = client.delete("/api/settings/thresholds/Infiltration", headers=headers)
        assert response.status_code == 200, response.text

        row = next(item for item in response.json()["items"] if item["pathology"] == "Infiltration")
        assert row["overridden"] is False
        assert row["effective"] == pytest.approx(default)
        assert row["factor"] is None

    def test_a_value_outside_the_bounds_is_refused(self, client, headers):
        response = client.put(
            "/api/settings/thresholds/Infiltration",
            # Inside the schema's (0, 1) but outside MAX_FACTOR.
            json={"threshold": thresholds.DEFAULTS["Infiltration"] * 5},
            headers=headers,
        )
        assert response.status_code == 400
        assert "Infiltration" in response.json()["detail"]

    def test_a_suppressed_output_is_refused(self, client, headers):
        response = client.put(
            "/api/settings/thresholds/Fibrosis",
            json={"threshold": 0.02},
            headers=headers,
        )
        assert response.status_code == 400

    def test_a_threshold_of_zero_never_reaches_the_module(self, client, headers):
        """The schema refuses it before any of this runs."""
        response = client.put(
            "/api/settings/thresholds/Infiltration", json={"threshold": 0}, headers=headers
        )
        assert response.status_code == 422

    def test_a_technician_may_read_but_not_write(self, client, signed_in, technician):
        their_headers = signed_in("tech@example.com")[0]

        listed = client.get("/api/settings/thresholds", headers=their_headers)
        assert listed.status_code == 200
        assert listed.json()["editable"] is False

        refused = client.put(
            "/api/settings/thresholds/Infiltration",
            json={"threshold": 0.2},
            headers=their_headers,
        )
        assert refused.status_code == 403

        reset = client.delete("/api/settings/thresholds/Infiltration", headers=their_headers)
        assert reset.status_code == 403

    def test_signing_in_is_required(self, client):
        assert client.get("/api/settings/thresholds").status_code == 401


class TestStoredResults:
    def test_changing_a_threshold_does_not_rewrite_a_finished_study(
        self, session, organization, admin, client, headers
    ):
        """The report a radiologist read has to keep saying what it said."""
        from chester.models import AnalysisResult, Study

        study = Study(
            owner_user_id=admin.id,
            organization_id=organization.id,
            study_instance_uid="1.2.3.4.5",
            status="completed",
        )
        session.add(study)
        session.flush()
        recorded = {"Infiltration": thresholds.DEFAULTS["Infiltration"]}
        session.add(
            AnalysisResult(
                study_id=study.id,
                model_version="chester-onnx:densenet121-res224-all",
                preprocessing_version="2.0.0",
                raw_scores={"Infiltration": 0.15},
                op_normalized_scores={"Infiltration": 0.6},
                thresholds=dict(recorded),
                above_threshold={"Infiltration": True},
                above_threshold_findings=["Infiltration"],
            )
        )
        session.flush()

        client.put(
            "/api/settings/thresholds/Infiltration",
            json={"threshold": 0.4},
            headers=headers,
        )

        stored = session.query(AnalysisResult).filter_by(study_id=study.id).one()
        assert stored.thresholds == recorded
        assert stored.above_threshold_findings == ["Infiltration"]
