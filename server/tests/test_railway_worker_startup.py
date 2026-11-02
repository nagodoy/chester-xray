"""External worker preflight must fail before contacting any database."""

import os
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "run_railway_worker.sh"


@pytest.mark.parametrize(
    "missing", ["DATABASE_URL", "SESSION_SECRET", "PSEUDONYM_SECRET", "DICOM_INGEST_TOKEN"]
)
def test_refuses_missing_required_variable_without_leaking_values(missing):
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://example:do-not-print@invalid/database",
        "SESSION_SECRET": "session-do-not-print",
        "PSEUDONYM_SECRET": "pseudonym-do-not-print",
        "DICOM_INGEST_TOKEN": "ingest-do-not-print",
    }
    env.pop(missing)
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert missing in result.stderr
    assert "do-not-print" not in result.stderr + result.stdout


def test_refuses_sqlite_in_external_worker():
    env = {
        **os.environ,
        "DATABASE_URL": "sqlite:///accidental-local.db",
        "SESSION_SECRET": "unused",
        "PSEUDONYM_SECRET": "unused",
        "DICOM_INGEST_TOKEN": "unused",
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "PostgreSQL" in result.stderr


def test_refuses_missing_model_before_database_connection(tmp_path):
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://example:do-not-print@invalid/database",
        "SESSION_SECRET": "unused",
        "PSEUDONYM_SECRET": "unused",
        "DICOM_INGEST_TOKEN": "unused",
        "MODEL_PATH": str(tmp_path / "missing.onnx"),
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "ONNX model is missing" in result.stderr
    assert "do-not-print" not in result.stderr + result.stdout
