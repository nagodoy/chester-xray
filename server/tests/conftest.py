"""Shared test fixtures.

The schema under test is built exactly the way the application builds it, through
``chester.schema.create``. There is no migration tool to diverge from, so the ORM
is the only definition and the tests exercise the same path production runs.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

# Configure the environment before anything imports chester. chester.config reads
# these once at import time and chester.db builds its engine from them, so the
# application, the schema step and the tests must agree on one database from the
# outset -- including the engine the application's own lifespan uses.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="chester-tests-")
DATABASE_URL = f"sqlite+pysqlite:///{Path(_TEST_DB_DIR) / 'test.db'}"

os.environ["DATABASE_URL"] = DATABASE_URL
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DEBUG", "1")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("PSEUDONYM_SECRET", "test-pseudonym-secret")
os.environ["DICOM_INGEST_TOKEN"] = "test-dicom-ingest-token"
os.environ["DICOM_WADO_ANONYMOUS_INGEST"] = "false"
os.environ.setdefault("ADMIN_USERS", "")


@pytest.fixture(scope="session")
def database_url() -> str:
    return DATABASE_URL


@pytest.fixture(scope="session")
def schema_engine(database_url: str):
    """The application's own engine, with the schema created from the models."""
    from chester.db import engine
    from chester.schema import create

    create()

    yield engine
    engine.dispose()


@pytest.fixture
def session(schema_engine) -> Generator:
    """A session wrapped in a transaction that is rolled back after each test."""
    from sqlalchemy.orm import sessionmaker

    connection = schema_engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, autoflush=False, autocommit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def organization(session):
    from chester.models import Organization

    org = Organization(name="Test Org", slug="test-org")
    session.add(org)
    session.flush()
    return org


@pytest.fixture
def make_user(session, organization):
    from chester.models import User
    from chester.security.roles import ROLE_TECHNICIAN

    def _make(email: str, role: str = ROLE_TECHNICIAN, *, org=None, **kwargs):
        user = User(
            email=email.strip().casefold(),
            organization_id=(org or organization).id,
            role=role,
            **kwargs,
        )
        session.add(user)
        session.flush()
        return user

    return _make


@pytest.fixture
def make_dicom():
    """Build a minimal but valid PS3.10 file in memory."""

    def _make(
        *,
        rows: int = 64,
        columns: int = 64,
        modality: str = "DX",
        body_part: str = "CHEST",
        view_position: str = "PA",
        patient_id: str = "TEST001",
        bits_allocated: int = 16,
        photometric: str = "MONOCHROME2",
        frame_count: int = 1,
        window_center: str | None = "512",
        window_width: str | None = "1024",
        rescale_slope: str = "1.0",
        rescale_intercept: str = "0.0",
        sop_uid: str | None = None,
        study_uid: str | None = None,
        pixels=None,
        study_description: str = "CHEST PA",
    ) -> bytes:
        import io

        import numpy as np
        from pydicom.dataset import FileDataset, FileMetaDataset
        from pydicom.uid import (
            DigitalXRayImageStorageForPresentation,
            ExplicitVRLittleEndian,
            generate_uid,
        )

        sop_uid = sop_uid or generate_uid()
        study_uid = study_uid or generate_uid()

        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
        file_meta.MediaStorageSOPInstanceUID = sop_uid
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

        ds = FileDataset("", {}, file_meta=file_meta, preamble=b"\x00" * 128)
        ds.PatientID = patient_id
        ds.PatientName = "DEIDENTIFIED"
        ds.PatientAge = "045Y"
        ds.PatientSex = "M"
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = generate_uid()
        ds.SOPInstanceUID = sop_uid
        ds.SOPClassUID = DigitalXRayImageStorageForPresentation
        ds.StudyDate = "20240101"
        ds.Modality = modality
        ds.BodyPartExamined = body_part
        ds.ViewPosition = view_position
        ds.StudyDescription = study_description
        ds.Rows = rows
        ds.Columns = columns
        ds.BitsAllocated = bits_allocated
        ds.BitsStored = bits_allocated
        ds.HighBit = bits_allocated - 1
        ds.PixelRepresentation = 0
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = photometric
        ds.NumberOfFrames = frame_count
        ds.RescaleSlope = rescale_slope
        ds.RescaleIntercept = rescale_intercept
        if window_center is not None:
            ds.WindowCenter = window_center
        if window_width is not None:
            ds.WindowWidth = window_width

        dtype = np.uint8 if bits_allocated == 8 else np.uint16
        if pixels is None:
            rng = np.random.default_rng(42)
            shape = (frame_count, rows, columns) if frame_count > 1 else (rows, columns)
            pixels = rng.integers(0, 255, shape, dtype=dtype)
        ds.PixelData = np.asarray(pixels, dtype=dtype).tobytes()

        buffer = io.BytesIO()
        ds.save_as(buffer, enforce_file_format=True)
        return buffer.getvalue()

    return _make


@pytest.fixture
def client(session):
    """Test client wired to the transactional test session."""
    from fastapi.testclient import TestClient

    from chester.db import get_session
    from chester.main import app

    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def capture_otp(monkeypatch):
    """Capture codes instead of sending mail. Returns the list of (email, code)."""
    from chester.api import auth

    sent: list[tuple[str, str]] = []
    # Delivery is treated as configured; the endpoint refuses outright when it is
    # not, so tests exercising the sign-in flow have to stand in for a working SMTP.
    monkeypatch.setattr(auth, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(
        auth, "send_otp_email", lambda recipient, code: sent.append((recipient, code))
    )
    return sent


@pytest.fixture
def signed_in(client, capture_otp):
    """Sign an address in and return (headers, access payload)."""

    def _sign_in(email: str):
        requested = client.post("/api/auth/request-code", json={"email": email})
        assert requested.status_code == 200, requested.text
        assert capture_otp, f"no code was sent to {email}"
        code = capture_otp[-1][1]
        verified = client.post("/api/auth/verify-code", json={"email": email, "code": code})
        assert verified.status_code == 200, verified.text
        payload = verified.json()
        return {"X-Session-Token": payload["session_token"]}, payload["access"]

    return _sign_in


@pytest.fixture
def make_png():
    """A small grayscale PNG."""

    def _make(rows: int = 64, columns: int = 64, seed: int = 7) -> bytes:
        import io

        import numpy as np
        from PIL import Image

        rng = np.random.default_rng(seed)
        array = rng.integers(0, 255, (rows, columns), dtype=np.uint8)
        buffer = io.BytesIO()
        Image.fromarray(array, mode="L").save(buffer, format="PNG")
        return buffer.getvalue()

    return _make


@pytest.fixture
def make_stow_body():
    """Build a multipart/related body carrying one or more DICOM parts."""

    def _make(parts: list[bytes], boundary: str = "STOW-BOUNDARY-001") -> tuple[bytes, str]:
        chunks = []
        for payload in parts:
            chunks.append(
                f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
                + payload
                + b"\r\n"
            )
        body = b"".join(chunks) + f"--{boundary}--\r\n".encode()
        content_type = f'multipart/related; type="application/dicom"; boundary={boundary}'
        return body, content_type

    return _make
