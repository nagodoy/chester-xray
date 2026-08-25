"""Local model inference.

Runs the CHESTER classifier through ONNX Runtime in this process. The previous
implementation spawned a Node subprocess hosting a TensorFlow.js GraphModel and
shipped each image to it as a JSON array of roughly 50,000 floats, serialized
behind a lock. docs/onnx-parity.md records the check that the two agree to within
float32 noise.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from chester.config import settings
from chester.imaging import PREPROCESSING_VERSION

logger = logging.getLogger(__name__)

MODEL_VERSION = "chester-onnx:densenet121-res224-all"
IMAGE_SIZE = 224
IMAGE_SCALE = 1024.0
OUTPUT_COUNT = 18

# Canonical torchxrayvision output order for densenet121-res224-all.
PATHOLOGIES: tuple[str, ...] = (
    "Atelectasis",
    "Consolidation",
    "Infiltration",
    "Pneumothorax",
    "Edema",
    "Emphysema",
    "Fibrosis",
    "Effusion",
    "Pneumonia",
    "Pleural Thickening",
    "Cardiomegaly",
    "Nodule",
    "Mass",
    "Hernia",
    "Lung Lesion",
    "Fracture",
    "Lung Opacity",
    "Enlarged Cardiomediastinum",
)

# Outputs the CHESTER configuration deliberately blanked out and never reported.
# Preserved from the previous deployment: which findings are surfaced is a
# clinical decision, not an implementation detail to change in passing.
SUPPRESSED_INDICES: frozenset[int] = frozenset({2, 3, 8, 11, 14, 15})

# Operating points published for these weights. Verified identical to the values
# in the retired TensorFlow.js config to nine decimal places.
OPERATING_POINTS: tuple[float, ...] = (
    0.07422872,
    0.038290843,
    0.09814756,
    0.0098118475,
    0.023601074,
    0.0022490358,
    0.010060724,
    0.103246614,
    0.056810737,
    0.026791653,
    0.050318155,
    0.023985857,
    0.01939503,
    0.042889766,
    0.053369623,
    0.035975814,
    0.20204692,
    0.05015312,
)

# Presentation-only boost applied above the midpoint, carried over from CHESTER.
SCALE_UPPER = 1.3

_session = None
_session_lock = threading.Lock()


def model_version() -> str:
    return MODEL_VERSION


def _model_path() -> Path:
    path = Path(settings.model_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent.parent / path
    return path


def get_session():
    """Load the ONNX session once per process."""
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is None:
            import onnxruntime as ort

            path = _model_path()
            if not path.is_file():
                raise RuntimeError(f"Model artifact is missing: {path}")
            logger.info("Loading model from %s", path)
            _session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    return _session


def reset_session() -> None:
    """Drop the loaded session. For tests and for reloading after a config change."""
    global _session
    with _session_lock:
        _session = None


def preprocess(pixels: np.ndarray) -> np.ndarray:
    """Resize the short side to 224, centre-crop, and scale to [-1024, 1024].

    Input is a 0..255 grayscale raster. This transform is part of
    PREPROCESSING_VERSION; changing it changes recorded scores.
    """
    values = np.asarray(pixels, dtype=np.float32)
    if values.ndim != 2 or values.size == 0:
        raise ValueError("expected a non-empty 2D grayscale image")
    if not np.isfinite(values).all():
        raise ValueError("input contains non-finite pixel values")

    clipped = np.clip(values, 0.0, 255.0)
    height, width = clipped.shape
    if width < height:
        resized_width = IMAGE_SIZE
        resized_height = max(IMAGE_SIZE, int(IMAGE_SIZE * height / width))
    else:
        resized_height = IMAGE_SIZE
        resized_width = max(IMAGE_SIZE, int(IMAGE_SIZE * width / height))

    resized = Image.fromarray(clipped).resize(
        (resized_width, resized_height), Image.Resampling.BILINEAR
    )
    left = resized_width // 2 - IMAGE_SIZE // 2
    top = resized_height // 2 - IMAGE_SIZE // 2
    cropped = resized.crop((left, top, left + IMAGE_SIZE, top + IMAGE_SIZE))

    return (np.asarray(cropped, dtype=np.float32) / 255.0 * 2.0 - 1.0) * IMAGE_SCALE


def normalize_to_operating_point(raw: float, threshold: float) -> float:
    """Map a raw sigmoid score so the operating point sits at 0.5.

    The same piecewise map torchxrayvision calls op_norm, plus the SCALE_UPPER
    boost CHESTER applied above 0.6. The result is a presentation score, not a
    calibrated probability.
    """
    if raw < threshold:
        normalized = raw / (threshold * 2.0)
    else:
        normalized = 1.0 - ((1.0 - raw) / ((1.0 - threshold) * 2.0))
        if normalized > 0.6:
            normalized = min(1.0, normalized * SCALE_UPPER)
    return min(1.0, max(0.0, normalized))


def infer(pixels: np.ndarray) -> dict:
    """Score one image and return raw, normalized and thresholded results."""
    prepared = preprocess(pixels).reshape(1, 1, IMAGE_SIZE, IMAGE_SIZE)
    scores = get_session().run(["scores"], {"image": prepared})[0].reshape(-1)

    if scores.shape != (OUTPUT_COUNT,) or not np.isfinite(scores).all():
        raise RuntimeError("model returned an invalid score vector")

    raw_scores: dict[str, float] = {}
    normalized_scores: dict[str, float] = {}
    thresholds: dict[str, float] = {}
    above_threshold: dict[str, bool] = {}
    findings: list[str] = []

    for index, pathology in enumerate(PATHOLOGIES):
        if index in SUPPRESSED_INDICES:
            continue
        raw = float(scores[index])
        threshold = float(OPERATING_POINTS[index])
        raw_scores[pathology] = raw
        thresholds[pathology] = threshold
        normalized_scores[pathology] = normalize_to_operating_point(raw, threshold)
        is_above = raw >= threshold
        above_threshold[pathology] = is_above
        if is_above:
            findings.append(pathology)

    return {
        "raw_scores": raw_scores,
        "op_normalized_scores": normalized_scores,
        "thresholds": thresholds,
        "above_threshold": above_threshold,
        "above_threshold_findings": findings,
        "model_version": MODEL_VERSION,
        "preprocessing_version": PREPROCESSING_VERSION,
    }
