"""Shared test fixtures.

The schema under test is built by running the real migrations rather than
``Base.metadata.create_all``. That way a migration that fails to reproduce the models
breaks the tests here, instead of only in the environment that applies it.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DEBUG", "1")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("PSEUDONYM_SECRET", "test-pseudonym-secret")
os.environ.setdefault("DICOM_INGEST_TOKEN", "test-dicom-ingest-token")

SERVER_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    return f"sqlite+pysqlite:///{tmp_path_factory.mktemp('db') / 'test.db'}"


@pytest.fixture(scope="session")
def migrated_engine(database_url: str):
    """A database whose schema was produced by ``alembic upgrade head``."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    config = Config(str(SERVER_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous

    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    yield engine
    engine.dispose()


@pytest.fixture
def session(migrated_engine) -> Generator:
    """A session wrapped in a transaction that is rolled back after each test."""
    from sqlalchemy.orm import sessionmaker

    connection = migrated_engine.connect()
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
