"""Storage round-trips through whichever backend configuration selects."""

from __future__ import annotations

import pytest

from chester import storage


@pytest.fixture(autouse=True)
def _clear_backend_cache():
    storage.reset_backend_cache()
    yield
    storage.reset_backend_cache()


def test_database_backend_is_the_default_without_a_bucket(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_bucket", "", raising=False)
    assert storage.active_backend() == storage.BACKEND_DATABASE


def test_s3_backend_is_selected_by_configuration(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_bucket", "chester-test", raising=False)
    assert storage.active_backend() == storage.BACKEND_S3


def test_database_round_trip(session, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_bucket", "", raising=False)
    payload = b"\x00DICM-ish bytes"

    result = storage.store_bytes("originals/a/b.dcm", payload, "application/dicom", session)

    assert result["backend"] == storage.BACKEND_DATABASE
    assert result["size"] == len(payload)
    assert storage.retrieve_bytes("originals/a/b.dcm", session) == payload


def test_database_overwrite_replaces_bytes(session, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_bucket", "", raising=False)
    storage.store_bytes("k", b"first", "text/plain", session)
    storage.store_bytes("k", b"second-and-longer", "text/plain", session)

    assert storage.retrieve_bytes("k", session) == b"second-and-longer"


def test_missing_key_raises_object_not_found(session, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_bucket", "", raising=False)
    with pytest.raises(storage.ObjectNotFound):
        storage.retrieve_bytes("nothing/here.png", session)


def test_delete_removes_the_object(session, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_bucket", "", raising=False)
    storage.store_bytes("gone", b"bytes", "text/plain", session)
    storage.delete_object("gone", session)

    with pytest.raises(storage.ObjectNotFound):
        storage.retrieve_bytes("gone", session)


def test_empty_payload_is_refused(session, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_bucket", "", raising=False)
    with pytest.raises(ValueError):
        storage.store_bytes("empty", b"", "text/plain", session)


def test_database_backend_requires_a_session(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_bucket", "", raising=False)
    with pytest.raises(RuntimeError):
        storage.store_bytes("k", b"bytes", "text/plain", session=None)


class TestS3Backend:
    """The same contract, exercised against an S3-compatible server."""

    @pytest.fixture
    def s3_bucket(self, monkeypatch):
        boto3 = pytest.importorskip("boto3")
        moto = pytest.importorskip("moto")

        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        with moto.mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="chester-test")
            monkeypatch.setattr(storage.settings, "storage_bucket", "chester-test", raising=False)
            monkeypatch.setattr(storage.settings, "storage_endpoint_url", "", raising=False)
            monkeypatch.setattr(storage.settings, "storage_region", "us-east-1", raising=False)
            storage.reset_backend_cache()
            yield "chester-test"

    def test_round_trip(self, s3_bucket):
        payload = b"\x00DICM-ish bytes"

        result = storage.store_bytes("originals/a/b.dcm", payload, "application/dicom")

        assert result["backend"] == storage.BACKEND_S3
        assert result["size"] == len(payload)
        assert storage.retrieve_bytes("originals/a/b.dcm") == payload

    def test_overwrite_replaces_bytes(self, s3_bucket):
        storage.store_bytes("k", b"first", "text/plain")
        storage.store_bytes("k", b"second-and-longer", "text/plain")

        assert storage.retrieve_bytes("k") == b"second-and-longer"

    def test_missing_key_raises_object_not_found(self, s3_bucket):
        with pytest.raises(storage.ObjectNotFound):
            storage.retrieve_bytes("nothing/here.png")

    def test_delete_removes_the_object(self, s3_bucket):
        storage.store_bytes("gone", b"bytes", "text/plain")
        storage.delete_object("gone")

        with pytest.raises(storage.ObjectNotFound):
            storage.retrieve_bytes("gone")

    def test_no_session_is_required(self, s3_bucket):
        """Unlike the database backend, S3 needs no SQLAlchemy session."""
        storage.store_bytes("standalone", b"bytes", "text/plain", session=None)
        assert storage.retrieve_bytes("standalone", session=None) == b"bytes"
