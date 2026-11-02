"""The independent Railway scaler applies bounded queue recommendations."""

from __future__ import annotations

import io
import json

import pytest

from chester import scaler


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


class TestRailwayGraphql:
    def test_project_token_uses_project_access_header(self, monkeypatch):
        monkeypatch.setenv("RAILWAY_API_TOKEN", "project-token")
        monkeypatch.setenv("RAILWAY_TOKEN_TYPE", "project")
        captured = {}

        def fake_urlopen(request, timeout):
            captured["header"] = request.get_header("Project-access-token")
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data)
            assert request.get_header("User-agent") == "torax-queue-scaler/1.0"
            assert request.get_header("Accept") == "application/json"
            return FakeResponse(b'{"data":{"ok":true}}')

        monkeypatch.setattr(scaler, "urlopen", fake_urlopen)

        assert scaler._graphql("query { ok }", {}) == {"ok": True}
        assert captured == {
            "header": "project-token",
            "timeout": 20,
            "body": {"query": "query { ok }", "variables": {}},
        }

    def test_account_token_uses_bearer_authorization(self, monkeypatch):
        monkeypatch.setenv("RAILWAY_API_TOKEN", "account-token")
        monkeypatch.setenv("RAILWAY_TOKEN_TYPE", "account")
        captured = {}

        def fake_urlopen(request, timeout):
            captured["header"] = request.get_header("Authorization")
            return FakeResponse(b'{"data":{"ok":true}}')

        monkeypatch.setattr(scaler, "urlopen", fake_urlopen)

        scaler._graphql("query { ok }", {})

        assert captured["header"] == "Bearer account-token"

    def test_graphql_errors_raise_without_returning_partial_data(self, monkeypatch):
        monkeypatch.setenv("RAILWAY_API_TOKEN", "project-token")
        monkeypatch.setattr(
            scaler,
            "urlopen",
            lambda *_args, **_kwargs: FakeResponse(
                b'{"errors":[{"message":"not authorized"}],"data":null}'
            ),
        )

        with pytest.raises(RuntimeError, match="not authorized"):
            scaler._graphql("query { ok }", {})


class TestScaling:
    def test_live_schema_reads_legacy_count_and_writes_explicit_region(self, monkeypatch):
        monkeypatch.setenv("RAILWAY_TARGET_WORKER_SERVICE_ID", "worker")
        monkeypatch.setenv("RAILWAY_TARGET_ENVIRONMENT_ID", "production")
        monkeypatch.setenv("RAILWAY_TARGET_WORKER_REGION", "sfo")
        monkeypatch.setattr(
            scaler, "worker_instance", lambda: {"numReplicas": 1, "region": None},
        )
        calls = []
        monkeypatch.setattr(scaler, "_graphql", lambda query, variables: calls.append(variables))
        assert scaler.current_replicas() == 1
        scaler.set_replicas(0)
        assert calls[0]["input"] == {
            "numReplicas": 0,
            "multiRegionConfig": {"sfo": {"numReplicas": 0}},
        }

    def test_explicit_region_count_overrides_legacy_count(self, monkeypatch):
        monkeypatch.setattr(
            scaler, "worker_instance",
            lambda: {"numReplicas": 1, "multiRegionConfig": {"sfo": {"numReplicas": 4}}},
        )
        assert scaler.current_replicas() == 4

    def test_scale_updates_existing_region_and_legacy_count(self, monkeypatch):
        monkeypatch.setenv("RAILWAY_TARGET_WORKER_SERVICE_ID", "worker")
        monkeypatch.setenv("RAILWAY_TARGET_ENVIRONMENT_ID", "production")
        monkeypatch.setattr(
            scaler, "worker_instance",
            lambda: {"numReplicas": 1, "multiRegionConfig": {"sfo": {"numReplicas": 4}}},
        )
        calls = []
        monkeypatch.setattr(scaler, "_graphql", lambda query, variables: calls.append(variables))
        scaler.set_replicas(0)
        assert calls[0]["input"] == {
            "numReplicas": 0,
            "multiRegionConfig": {"sfo": {"numReplicas": 0}},
        }

    def test_scale_up_to_bounded_target_and_skip_when_unchanged(self, monkeypatch):
        monkeypatch.setattr(
            scaler,
            "recommended_replicas",
            lambda: (
                {"queued_total": 12, "oldest_ready_seconds": 45},
                3,
            ),
        )
        monkeypatch.setattr(scaler, "current_replicas", lambda: 1)
        applied = []
        monkeypatch.setattr(scaler, "set_replicas", applied.append)

        assert scaler.scale_once() == 3
        assert applied == [3]

        monkeypatch.setattr(scaler, "current_replicas", lambda: 3)
        assert scaler.scale_once() == 3
        assert applied == [3]

    def test_empty_queue_target_can_be_zero(self, monkeypatch):
        monkeypatch.setattr(
            scaler,
            "recommended_replicas",
            lambda: (
                {"queued_total": 0, "processing_total": 0, "oldest_ready_seconds": 0},
                0,
            ),
        )
        monkeypatch.setattr(scaler, "current_replicas", lambda: 1)
        applied = []
        monkeypatch.setattr(scaler, "set_replicas", applied.append)

        assert scaler.scale_once() == 0
        assert applied == [0]

    @pytest.mark.parametrize("queued,processing", [(0, 1), (0, 4), (1, 0), (2, 2)])
    def test_busy_fleet_is_not_scaled_down(self, monkeypatch, queued, processing):
        monkeypatch.setattr(
            scaler,
            "recommended_replicas",
            lambda: (
                {
                    "queued_total": queued,
                    "processing_total": processing,
                    "oldest_ready_seconds": 0,
                },
                0 if queued == 0 else 1,
            ),
        )
        monkeypatch.setattr(scaler, "current_replicas", lambda: 4)
        applied = []
        monkeypatch.setattr(scaler, "set_replicas", applied.append)

        assert scaler.scale_once() == 4
        assert applied == []

    def test_failed_scaling_is_propagated_in_single_check_mode(self, monkeypatch):
        monkeypatch.setattr(
            scaler,
            "recommended_replicas",
            lambda: ({"queued_total": 1, "oldest_ready_seconds": 0}, 1),
        )
        monkeypatch.setattr(scaler, "current_replicas", lambda: 0)
        monkeypatch.setattr(
            scaler,
            "set_replicas",
            lambda _target: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
        )

        with pytest.raises(RuntimeError, match="provider unavailable"):
            scaler.run(once=True)

    def test_target_respects_configured_and_provider_ceilings(self, monkeypatch):
        monkeypatch.setattr(scaler.settings, "worker_min_replicas", 0)
        monkeypatch.setattr(scaler.settings, "worker_max_replicas", 3)
        monkeypatch.setattr(
            scaler.worker,
            "queue_pressure",
            lambda _db: {
                "queued_total": 100,
                "oldest_ready_seconds": 100,
            },
        )
        monkeypatch.setattr(scaler.worker, "desired_worker_replicas", lambda _pressure: 50)
        monkeypatch.setattr(scaler, "session_scope", lambda: _fake_session_scope())

        _pressure, target = scaler.recommended_replicas()

        assert target == 3

    def test_target_respects_railway_provider_limit(self, monkeypatch):
        monkeypatch.setattr(scaler.settings, "worker_min_replicas", 0)
        monkeypatch.setattr(scaler.settings, "worker_max_replicas", 80)
        monkeypatch.setattr(
            scaler.worker,
            "queue_pressure",
            lambda _db: {
                "queued_total": 100,
                "oldest_ready_seconds": 100,
            },
        )
        monkeypatch.setattr(scaler.worker, "desired_worker_replicas", lambda _pressure: 60)
        monkeypatch.setattr(scaler, "session_scope", lambda: _fake_session_scope())

        _pressure, target = scaler.recommended_replicas()

        assert target == 50

    def test_poll_loop_retries_after_scaling_failure(self, monkeypatch, caplog):
        calls = []
        monkeypatch.setattr(
            scaler,
            "scale_once",
            lambda: calls.append(True) or (_ for _ in ()).throw(RuntimeError("API offline")),
        )

        class StopAfterPoll:
            stopped = False

            def is_set(self):
                return self.stopped

            def wait(self, timeout):
                self.stopped = True

        scaler.run(StopAfterPoll())

        assert calls == [True]
        assert "retrying next interval" in caplog.text

    def test_validate_configuration_requires_railway_target_and_token(self, monkeypatch):
        monkeypatch.setattr(
            scaler.settings,
            "database_url",
            "postgresql+psycopg://user:password@database.example/db",
        )
        monkeypatch.delenv("RAILWAY_API_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="RAILWAY_API_TOKEN"):
            scaler.validate_configuration()


class _FakeSession:
    pass


class _fake_session_scope:
    def __enter__(self):
        return _FakeSession()

    def __exit__(self, *_exc):
        return False
