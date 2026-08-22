"""Tests for DICOM/image validation logic."""
from __future__ import annotations

import numpy as np
import pytest

from app.dicom_utils import validate_study, _image_validation


def test_validate_chest_dicom():
    meta = {
        "modality": "DX",
        "body_part": "CHEST",
        "view_position": "PA",
        "description": "CHEST PA",
    }
    state, reason = validate_study(meta)
    assert state == "chest"


def test_validate_chest_cr():
    meta = {
        "modality": "CR",
        "body_part": "THORAX",
        "view_position": "AP",
        "description": "",
    }
    state, reason = validate_study(meta)
    assert state == "chest"


def test_validate_non_chest_modality():
    meta = {
        "modality": "CT",
        "body_part": "HEAD",
        "view_position": "",
        "description": "",
    }
    state, reason = validate_study(meta)
    assert state == "non_chest"


def test_validate_non_chest_body_part():
    meta = {
        "modality": "DX",
        "body_part": "KNEE",
        "view_position": "",
        "description": "",
    }
    state, reason = validate_study(meta)
    assert state == "non_chest"


def test_validate_uncertain_no_metadata():
    meta = {
        "modality": "",
        "body_part": "",
        "view_position": "",
        "description": "",
    }
    state, reason = validate_study(meta)
    assert state == "uncertain"


def test_validate_uncertain_with_image():
    meta = {
        "modality": "",
        "body_part": "",
        "view_position": "",
        "description": "",
    }
    # Moderate-entropy grayscale image
    arr = np.random.randint(0, 200, (256, 256), dtype=np.uint8).astype(np.float32)
    state, reason = validate_study(meta, arr)
    assert state == "uncertain"


def test_validate_image_too_small():
    arr = np.ones((10, 10), dtype=np.float32) * 128
    state, reason = _image_validation(arr, "", "", "")
    assert state == "non_chest"


def test_validate_chest_frontal():
    meta = {
        "modality": "DX",
        "body_part": "",
        "view_position": "PA",
        "description": "CHEST",
    }
    state, reason = validate_study(meta)
    assert state in ("chest", "uncertain")


def test_validate_mr_head_non_chest():
    meta = {
        "modality": "MR",
        "body_part": "HEAD",
        "view_position": "",
        "description": "",
    }
    state, reason = validate_study(meta)
    assert state == "non_chest"


def test_validate_uncertain_partial_evidence():
    meta = {
        "modality": "DX",
        "body_part": "",
        "view_position": "",
        "description": "",
    }
    state, reason = validate_study(meta)
    # DX alone is only a hint
    assert state in ("chest", "uncertain")
