"""Where the lungs and the heart are, in the square the classifier scored.

chester.topography needs this to say which hemithorax a finding's evidence is in,
and above all whether it is in a hemithorax at all. The classifier's evidence map
is happy to sit on the neck or the stomach -- a model that keys on something
outside the chest points there -- and dividing the square into thirds without
knowing where the lungs are called that "upper" and "lower". It was reported on a
real study as exactly that, and it is why this module exists.

The segmenter is torchxrayvision's ChestX-Det PSPNet, exported with int8 weights
by tools/export_segmentation.py; models/chest-segmentation-512.json records where
it came from and how closely the int8 masks match the float32 ones. It is given
the same square `inference.preprocess` gives the classifier, scaled to its 512
input, so its masks and the 7x7 evidence map describe the same pixels. Its sides
are the patient's, learned from films displayed by convention.

Like the activation behind explanations, it is optional. An artifact that is
missing or will not load leaves results without topography, never without scores.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image

from chester import inference
from chester.config import settings
from chester.inference import IMAGE_SIZE

logger = logging.getLogger(__name__)

INPUT_SIZE = 512
THRESHOLD = 0.5
# The order tools/export_segmentation.py writes its output channels in.
CHANNELS = ("right_lung", "left_lung", "heart")

_session = None
_unavailable = False
_lock = threading.Lock()


class Masks(NamedTuple):
    """Boolean masks over the IMAGE_SIZE square, in the patient's sides."""

    right_lung: np.ndarray
    left_lung: np.ndarray
    heart: np.ndarray


def _model_path() -> Path:
    path = Path(settings.segmentation_model_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent.parent / path
    return path


def _get_session():
    """The ONNX session, or None when there is no segmenter to load."""
    global _session, _unavailable
    if _session is not None or _unavailable:
        return _session
    with _lock:
        if _session is None and not _unavailable:
            path = _model_path()
            if not path.is_file():
                logger.warning("No segmentation model at %s; topography disabled", path)
                _unavailable = True
                return None
            try:
                import onnxruntime as ort

                _session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            except Exception:
                logger.exception("Could not load %s; topography disabled", path)
                _unavailable = True
    return _session


def available() -> bool:
    return _get_session() is not None


def reset_session() -> None:
    """Forget the loaded segmenter. For tests."""
    global _session, _unavailable
    with _lock:
        _session = None
        _unavailable = False


def lung_masks(pixels: np.ndarray) -> Masks | None:
    """Right lung, left lung and heart over the square the classifier scored."""
    session = _get_session()
    if session is None:
        return None
    square = inference.preprocess(pixels)
    resized = Image.fromarray(square.astype(np.float32)).resize(
        (INPUT_SIZE, INPUT_SIZE), Image.Resampling.BILINEAR
    )
    tensor = np.asarray(resized, dtype=np.float32).reshape(1, 1, INPUT_SIZE, INPUT_SIZE)
    probabilities = session.run(["masks"], {"image": tensor})[0][0]
    if probabilities.shape[0] != len(CHANNELS):
        raise RuntimeError(
            f"Segmenter returned {probabilities.shape[0]} channels, expected {len(CHANNELS)}"
        )
    masks = []
    for channel in probabilities:
        # Back to the classifier's square, by area, then thresholded.
        small = Image.fromarray(channel.astype(np.float32)).resize(
            (IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.BOX
        )
        masks.append(np.asarray(small, dtype=np.float32) > THRESHOLD)
    return Masks(*masks)
