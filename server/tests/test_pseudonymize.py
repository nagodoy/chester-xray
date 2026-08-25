"""Patient pseudonyms are stable, opaque, and keyed independently of sessions."""

from __future__ import annotations

from chester import pseudonymize
from chester.config import Settings


def test_pseudonym_is_stable_for_the_same_input():
    assert pseudonymize.pseudonymize_patient_id("PAT001") == pseudonymize.pseudonymize_patient_id(
        "PAT001"
    )


def test_pseudonym_differs_between_patients():
    assert pseudonymize.pseudonymize_patient_id("PAT001") != pseudonymize.pseudonymize_patient_id(
        "PAT002"
    )


def test_pseudonym_does_not_leak_the_identifier():
    assert "PAT001" not in pseudonymize.pseudonymize_patient_id("PAT001")


def test_pseudonym_is_prefixed_and_fixed_width():
    value = pseudonymize.pseudonymize_patient_id("PAT001")
    assert value.startswith(pseudonymize.PSEUDONYM_PREFIX)
    assert len(value) == len(pseudonymize.PSEUDONYM_PREFIX) + 16


def test_empty_identifier_yields_empty_string():
    assert pseudonymize.pseudonymize_patient_id("") == ""


def test_pseudonym_key_is_independent_of_the_session_secret(monkeypatch):
    """Rotating SESSION_SECRET must not change pseudonyms.

    Sharing one secret meant a session-secret rotation silently remapped every
    patient, which is why the two are separate settings.
    """
    before = pseudonymize.pseudonymize_patient_id("PAT001")
    monkeypatch.setattr(pseudonymize.settings, "session_secret", "rotated-secret")
    assert pseudonymize.pseudonymize_patient_id("PAT001") == before

    monkeypatch.setattr(pseudonymize.settings, "pseudonym_secret", "different-secret")
    assert pseudonymize.pseudonymize_patient_id("PAT001") != before


def test_production_startup_refuses_default_secrets():
    production = Settings(
        debug=False,
        testing=False,
        session_secret="real",
        pseudonym_secret="real",
        dicom_ingest_token="real",
    )
    production.require_production_secrets()

    for field in ("session_secret", "pseudonym_secret"):
        broken = production.model_copy(update={field: f"dev-{field.replace('_', '-')}-change-me"})
        try:
            broken.require_production_secrets()
        except RuntimeError:
            continue
        raise AssertionError(f"default {field} should be refused in production")
