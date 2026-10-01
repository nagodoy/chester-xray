"""chester.retopography: version-1 topography replaced, scores left alone."""

from __future__ import annotations

from chester import retopography, worker
from chester.models import AnalysisResult, AuditEvent
from tests.test_worker import _bind_worker_sessions, queued_study  # noqa: F401

CURRENT = {"version": 2, "status": "located", "side": "right", "zones": ["lower"]}


def _analysed(session, queued_study, monkeypatch):  # noqa: F811
    study, job = queued_study
    _bind_worker_sessions(monkeypatch, session)
    worker.claim_job(session)
    worker.process_job(job.id)
    result = session.query(AnalysisResult).filter_by(job_id=job.id).one()
    # What the first version wrote: halves and thirds of the whole square.
    result.topography = {"Lung Opacity": {"side": "right", "zones": ["upper", "lower"]}}
    session.flush()
    return study, result


def test_an_old_entry_is_recomputed_and_audited(session, queued_study, monkeypatch):  # noqa: F811
    study, result = _analysed(session, queued_study, monkeypatch)
    scores = dict(result.raw_scores)
    monkeypatch.setattr("chester.topography.locate_all", lambda pixels: {"Lung Opacity": CURRENT})

    assert retopography.recompute(session, study, dry_run=False) == "written"
    session.flush()  # this session does not autoflush; the command's commit would

    assert result.topography == {"Lung Opacity": CURRENT}
    assert result.raw_scores == scores
    assert (
        session.query(AuditEvent)
        .filter_by(study_id=study.id, event_type="topography_recomputed")
        .count()
        == 1
    )


def test_a_dry_run_changes_nothing(session, queued_study, monkeypatch):  # noqa: F811
    study, result = _analysed(session, queued_study, monkeypatch)
    before = dict(result.topography)
    monkeypatch.setattr("chester.topography.locate_all", lambda pixels: {"Lung Opacity": CURRENT})

    assert retopography.recompute(session, study, dry_run=True) == "would-write"
    assert result.topography == before


def test_a_current_result_is_skipped(session, queued_study, monkeypatch):  # noqa: F811
    study, result = _analysed(session, queued_study, monkeypatch)
    result.topography = {"Lung Opacity": CURRENT}
    session.flush()

    assert retopography.recompute(session, study, dry_run=False) == "already-current"


def test_a_film_without_lungs_is_reported_not_written(session, queued_study, monkeypatch):  # noqa: F811
    study, result = _analysed(session, queued_study, monkeypatch)
    before = dict(result.topography)
    monkeypatch.setattr("chester.topography.locate_all", lambda pixels: None)

    assert retopography.recompute(session, study, dry_run=False) == "no-lungs"
    assert result.topography == before
