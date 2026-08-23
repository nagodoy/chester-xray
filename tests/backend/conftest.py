"""Test fixtures and configuration."""
from __future__ import annotations

import io
import os
import struct
import uuid
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Use SQLite for tests
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["TESTING"] = "1"
os.environ["DEBUG"] = "1"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["DICOM_INGEST_TOKEN"] = "test-dicom-ingest-token"
os.environ["DICOM_INGEST_OWNER_ID"] = "test-user-123"
os.environ["DICOM_WADO_ANONYMOUS_INGEST"] = "false"

from app.database import Base, get_db
from app.main import app

TEST_DB_URL = "sqlite:///./test.db"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


@pytest.fixture(scope="session")
def setup_test_db():
    """Create all tables for the test session."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    # Clean up test DB file
    if os.path.exists("test.db"):
        os.remove("test.db")


@pytest.fixture
def db_session(setup_test_db):
    """Provide a test database session with rollback."""
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestSessionLocal(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(setup_test_db, db_session):
    """Provide a FastAPI test client with DB override."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_client(client, monkeypatch):
    """Test client with a complete own-auth access context mocked."""
    from app.api.auth_deps import AccessContext, get_current_access

    def mock_auth():
        return AccessContext(
            email="test-user-123",
            role="admin",
            allowed_pages=None,
            is_admin=True,
        )

    app.dependency_overrides[get_current_access] = mock_auth
    yield client
    app.dependency_overrides.pop(get_current_access, None)


def _write_uint16_le(value: int) -> bytes:
    return struct.pack("<H", value)


def _write_uint32_le(value: int) -> bytes:
    return struct.pack("<I", value)


def make_minimal_dicom(
    rows: int = 64,
    cols: int = 64,
    modality: str = "DX",
    body_part: str = "CHEST",
    view_position: str = "PA",
    patient_id: str = "TEST001",
    bits_allocated: int = 16,
    photometric: str = "MONOCHROME2",
    frame_count: int = 1,
    sop_uid: str | None = None,
    study_uid: str | None = None,
) -> bytes:
    """
    Generate a minimal valid DICOM file in memory using pydicom.
    Returns bytes ready to upload.
    """
    import pydicom
    from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
    from pydicom.sequence import Sequence
    from pydicom.uid import (
        ExplicitVRLittleEndian,
        generate_uid,
        DigitalXRayImageStorageForPresentation,
    )
    import numpy as np

    if sop_uid is None:
        sop_uid = generate_uid()
    if study_uid is None:
        study_uid = generate_uid()

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset("", {}, file_meta=file_meta, preamble=b"\x00" * 128)

    ds.is_implicit_VR = False
    ds.is_little_endian = True

    # Patient
    ds.PatientID = patient_id
    ds.PatientName = "DEIDENTIFIED"
    ds.PatientAge = "045Y"
    ds.PatientSex = "M"

    # Study
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = DigitalXRayImageStorageForPresentation
    ds.StudyDate = "20240101"
    ds.Modality = modality
    ds.BodyPartExamined = body_part
    ds.ViewPosition = view_position
    ds.StudyDescription = "CHEST PA"

    # Image
    ds.Rows = rows
    ds.Columns = cols
    ds.BitsAllocated = bits_allocated
    ds.BitsStored = bits_allocated
    ds.HighBit = bits_allocated - 1
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = photometric
    ds.NumberOfFrames = frame_count
    ds.RescaleSlope = "1.0"
    ds.RescaleIntercept = "0.0"
    ds.WindowCenter = "512"
    ds.WindowWidth = "1024"

    # Generate pixel data
    if bits_allocated == 8:
        dtype = np.uint8
    else:
        dtype = np.uint16

    if frame_count > 1:
        pixel_data = np.random.randint(0, 255, (frame_count, rows, cols), dtype=dtype)
    else:
        pixel_data = np.random.randint(0, 255, (rows, cols), dtype=dtype)

    ds.PixelData = pixel_data.tobytes()

    buf = io.BytesIO()
    ds.save_as(buf, write_like_original=False)
    return buf.getvalue()


def make_png_image(rows: int = 64, cols: int = 64) -> bytes:
    """Generate a minimal grayscale PNG in memory."""
    import io as _io
    from PIL import Image
    import numpy as np

    arr = np.random.randint(0, 255, (rows, cols), dtype=np.uint8)
    img = Image.fromarray(arr, mode="L")
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_stow_multipart(dicom_bytes_list: list[bytes]) -> tuple[bytes, str]:
    """Build a multipart/related body for STOW-RS."""
    boundary = "TEST_STOW_BOUNDARY_001"
    parts = []
    for dcm in dicom_bytes_list:
        parts.append(
            f"--{boundary}\r\n"
            f"Content-Type: application/dicom\r\n"
            f"\r\n".encode() + dcm + b"\r\n"
        )
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    content_type = f"multipart/related; type=application/dicom; boundary={boundary}"
    return body, content_type
