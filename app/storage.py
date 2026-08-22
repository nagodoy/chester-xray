"""Storage abstraction: Replit Object Storage (primary) or database-backed (fallback)."""
from __future__ import annotations

import hashlib
import io
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_BACKEND: Optional[str] = None


def _detect_backend() -> str:
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND

    try:
        from replit.object_storage import Client  # type: ignore
        client = Client(bucket_id=settings.replit_object_storage_bucket)
        client.list(max_results=1)
        _BACKEND = "replit_object_storage"
        logger.info("Storage backend: replit_object_storage")
        return _BACKEND
    except Exception as exc:
        if "DefaultBucketError" in type(exc).__name__:
            logger.warning("Replit Object Storage has no default bucket; using DB storage")
        else:
            logger.warning("Replit Object Storage probe failed (%s); using DB storage", exc)

    _BACKEND = "database"
    logger.info("Storage backend: database")
    return _BACKEND


def active_backend() -> str:
    return _detect_backend()


def _get_oss_client():
    from replit.object_storage import Client  # type: ignore
    return Client(bucket_id=settings.replit_object_storage_bucket)


def store_bytes(
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    session=None,
    instance_id: Optional[str] = None,
) -> dict:
    """
    Store bytes under `key`.

    Returns a dict with:
      - backend: str
      - object_key: str
      - sha256: str
      - size: int
    """
    if not data:
        raise ValueError("Cannot store empty bytes")

    sha256 = hashlib.sha256(data).hexdigest()
    backend = _detect_backend()

    if backend == "replit_object_storage":
        try:
            client = _get_oss_client()
            client.upload_from_bytes(key, data)
            return {
                "backend": "replit_object_storage",
                "object_key": key,
                "sha256": sha256,
                "size": len(data),
            }
        except Exception as exc:
            logger.error("Object storage upload failed for %s: %s", key, exc)
            raise

    # DB-backed storage
    if session is None:
        raise RuntimeError("DB storage requires a SQLAlchemy session")
    from app.models import StoredObject
    obj = session.query(StoredObject).filter_by(object_key=key).first()
    if obj is None:
        obj = StoredObject(
            object_key=key,
            storage_backend="database",
            content_type=content_type,
            sha256=sha256,
            file_size=len(data),
            inline_data=data,
            instance_id=instance_id,
        )
        session.add(obj)
    else:
        obj.inline_data = data
        obj.sha256 = sha256
        obj.file_size = len(data)
    session.flush()
    return {
        "backend": "database",
        "object_key": key,
        "sha256": sha256,
        "size": len(data),
    }


def retrieve_bytes(key: str, session=None) -> bytes:
    """Retrieve bytes stored under `key`."""
    backend = _detect_backend()

    if backend == "replit_object_storage":
        try:
            client = _get_oss_client()
            return client.download_as_bytes(key)
        except Exception as exc:
            logger.error("Object storage download failed for %s: %s", key, exc)
            raise FileNotFoundError(f"Object not found: {key}") from exc

    # DB-backed storage
    if session is None:
        raise RuntimeError("DB storage requires a SQLAlchemy session")
    from app.models import StoredObject
    obj = session.query(StoredObject).filter_by(object_key=key).first()
    if obj is None or obj.inline_data is None:
        raise FileNotFoundError(f"Object not found in DB: {key}")
    return bytes(obj.inline_data)


def delete_object(key: str, session=None) -> None:
    """Delete an object by key."""
    backend = _detect_backend()
    if backend == "replit_object_storage":
        try:
            client = _get_oss_client()
            client.delete(key)
        except Exception as exc:
            logger.warning("Object storage delete failed for %s: %s", key, exc)
    else:
        if session:
            from app.models import StoredObject
            session.query(StoredObject).filter_by(object_key=key).delete()
