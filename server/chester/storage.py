"""Object storage: S3-compatible when configured, database-backed otherwise.

The backend is decided by configuration, not by probing at first use. The previous
implementation probed the object store once and cached the result forever, so a
transient outage at startup silently demoted the process to database storage for its
whole lifetime and the two backends could end up holding different objects.
"""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from typing import Protocol

from sqlalchemy.orm import Session

from chester.config import settings

logger = logging.getLogger(__name__)

BACKEND_S3 = "s3"
BACKEND_DATABASE = "database"


class ObjectNotFound(LookupError):
    """Raised when a key has no stored bytes."""


class StorageBackend(Protocol):
    name: str

    def store(self, key: str, data: bytes, content_type: str, session: Session | None) -> None: ...

    def retrieve(self, key: str, session: Session | None) -> bytes: ...

    def delete(self, key: str, session: Session | None) -> None: ...


class DatabaseStorage:
    """Stores bytes inline in stored_objects.

    Intended for development and small deployments. Large objects in the database
    are a known trade-off, not an oversight.
    """

    name = BACKEND_DATABASE

    @staticmethod
    def _require(session: Session | None) -> Session:
        if session is None:
            raise RuntimeError("database-backed storage requires a SQLAlchemy session")
        return session

    def store(self, key: str, data: bytes, content_type: str, session: Session | None) -> None:
        from chester.models import StoredObject

        db = self._require(session)
        existing = db.query(StoredObject).filter_by(object_key=key).first()
        if existing is None:
            db.add(
                StoredObject(
                    object_key=key,
                    storage_backend=self.name,
                    content_type=content_type,
                    sha256=hashlib.sha256(data).hexdigest(),
                    file_size=len(data),
                    inline_data=data,
                )
            )
        else:
            existing.inline_data = data
            existing.content_type = content_type
            existing.sha256 = hashlib.sha256(data).hexdigest()
            existing.file_size = len(data)
        db.flush()

    def retrieve(self, key: str, session: Session | None) -> bytes:
        from chester.models import StoredObject

        db = self._require(session)
        stored = db.query(StoredObject).filter_by(object_key=key).first()
        if stored is None or stored.inline_data is None:
            raise ObjectNotFound(key)
        return bytes(stored.inline_data)

    def delete(self, key: str, session: Session | None) -> None:
        from chester.models import StoredObject

        db = self._require(session)
        db.query(StoredObject).filter_by(object_key=key).delete()
        db.flush()


class S3Storage:
    """Stores bytes in any S3-compatible bucket (AWS S3, R2, MinIO)."""

    name = BACKEND_S3

    def __init__(self, bucket: str, endpoint_url: str = "", region: str = "") -> None:
        self.bucket = bucket
        self._endpoint_url = endpoint_url or None
        self._region = region or None

    @property
    def _client(self):
        return _s3_client(self._endpoint_url, self._region)

    def store(self, key: str, data: bytes, content_type: str, session: Session | None) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def retrieve(self, key: str, session: Session | None) -> bytes:
        client = self._client
        try:
            response = client.get_object(Bucket=self.bucket, Key=key)
        except client.exceptions.NoSuchKey as exc:
            raise ObjectNotFound(key) from exc
        except Exception as exc:
            # Botocore raises ClientError with a 404 code rather than NoSuchKey for
            # some S3-compatible implementations.
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
            }:
                raise ObjectNotFound(key) from exc
            raise
        return response["Body"].read()

    def delete(self, key: str, session: Session | None) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


@lru_cache(maxsize=4)
def _s3_client(endpoint_url: str | None, region: str | None):
    import boto3

    return boto3.client("s3", endpoint_url=endpoint_url, region_name=region)


@lru_cache(maxsize=1)
def get_backend() -> StorageBackend:
    """Return the configured backend. STORAGE_BUCKET selects S3."""
    if settings.storage_bucket:
        logger.info("Storage backend: s3 (bucket=%s)", settings.storage_bucket)
        return S3Storage(
            settings.storage_bucket,
            settings.storage_endpoint_url,
            settings.storage_region,
        )
    logger.info("Storage backend: database")
    return DatabaseStorage()


def reset_backend_cache() -> None:
    """Clear the memoized backend. For tests and configuration reloads."""
    get_backend.cache_clear()
    _s3_client.cache_clear()


def active_backend() -> str:
    return get_backend().name


def store_bytes(
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    session: Session | None = None,
) -> dict:
    if not data:
        raise ValueError("cannot store empty bytes")
    backend = get_backend()
    backend.store(key, data, content_type, session)
    return {
        "backend": backend.name,
        "object_key": key,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def retrieve_bytes(key: str, session: Session | None = None) -> bytes:
    return get_backend().retrieve(key, session)


def delete_object(key: str, session: Session | None = None) -> None:
    get_backend().delete(key, session)
