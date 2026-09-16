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
from collections.abc import Mapping
from pathlib import Path

import numpy as np
from PIL import Image

from chester import onnx_graph
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

# Outputs that are computed and never reported. Which findings are surfaced is a
# clinical decision, not an implementation detail to change in passing.
#
# Five are withheld, on three different kinds of evidence. The kind matters:
# it is what a later reader needs to know whether a decision can be revisited.
#
# Index 15, Fracture, is inherited. The CHESTER configuration blanked its label
# and nothing here ever re-examined it. No measurement supports or opposes it.
#
# Indices 11 Nodule, 6 Fibrosis and 5 Emphysema fail the project's own bar:
# firing on reference images in examples/ whose known label is not that finding,
# measured on the one image labelled No Finding, which is the only real negative
# claim in the set.
#
#   Emphysema   5 of 6, and 13.9x its threshold on No Finding -- the highest of
#               the eighteen, above both of the others
#   Fibrosis    7 of 7, 8.5x
#   Nodule      7 of 7, 4.7x
#
# Emphysema was reported throughout the period the other two were withdrawn for
# behaving less badly than it does. That was an inconsistency, not a judgement.
#
# Index 8, Pneumonia, is withheld on something else, and the difference is worth
# stating plainly. It fires on 0 of 7, at 0.02x its threshold on No Finding --
# the quietest output in the set. Its case is instability: across four renderings
# of the same anatomy on five real CR exams, its score swings by a median factor
# of 20.5, the widest of the eighteen on that data. A verdict decided by the
# window rather than by the chest is the argument that withdrew Fibrosis, so the
# same reasoning applies. But that measurement is five exams from one
# manufacturer, with rendering variants chosen here rather than observed in a
# deployment. It is the thinnest basis of the three kinds, and the first that
# should be re-run when more exams are available.
#
# What remains reported and unresolved: 2 Infiltration fires on 6 of 7, the
# worst of the outputs still surfaced. 3 Pneumothorax ran the second lowest
# operating point of the eighteen, 0.0098, below Fibrosis's 0.0101, though on
# the five real exams it was among the least render-sensitive at 2.1x.
#
# Both were raised in September 2026, Infiltration twice. Re-running the
# reference set at each new point says plainly what the raises buy, which on
# this evidence is nothing:
#
#   Infiltration    fires on 6 of 7 at the published point, 6 of 7 at 1.08x,
#                   and 6 of 7 at 1.242x -- the same six every time. They sit at
#                   0.139 to 0.216, so the point would have to reach 0.139, or
#                   1.42x published, before the first of them stops. Its score
#                   on the No Finding image is 0.142: still over the line at
#                   1.17x the threshold, down from 1.45x.
#   Pneumothorax    fires on 0 of 7 at either point. It never fired here.
#
# Across all eleven images in examples/ the 8% moved exactly one verdict, a
# score already under the line going from DUVIDOSO to ABAIXO; the further 15%
# moves one more, an unlabelled image at 0.118 that stops firing. Neither raise
# touches a false positive on a labelled exam, which is the complaint that
# motivates them.
#
# What they do change is the ranking. Pneumothorax at 0.0106 is no longer the
# second lowest operating point of the eighteen, sitting just above Fibrosis,
# which takes that position back. Infiltration at 0.1219 passed Effusion at
# 0.1032 to become the second highest, behind only Lung Opacity at 0.2020.
#
# The honest reading is that these factors are judgements about a population
# these points were not fitted on, and seven reference images can neither
# confirm nor refute them. The one thing they do establish is that raising the
# point in these increments is not what would fix Infiltration.
#
# All of this rests on seven reference images and five uncalibrated exams. It
# decides nothing about a population. It was enough for these withdrawals only
# because each failed a bar this project had already set for itself.
# tools/calibrate_thresholds.py runs the measurement that would settle it, over
# exams a radiologist has read.
#
# Fibrosis carries a second finding from its own withdrawal, kept because it is
# the only render-sensitivity figure measured before the five CR exams above: on
# that earlier set it swung by a median factor of 48.6, against 14.6 for the next
# output and 1.5 for Lung Opacity. Its operating point, 0.0101, is the second
# lowest of the eighteen. That reading has moved twice: it was written as second
# lowest while Pneumothorax below it was suppressed and not counted, became third
# when Pneumothorax was restored, and is second again now that Pneumothorax has
# been raised past it. The number never changed; what it is being ranked against
# did.
#
# None of these operating points is wrong for the population it was fitted on.
# They do not transfer to this one, which is the whole of the problem.
SUPPRESSED_INDICES: frozenset[int] = frozenset({5, 6, 8, 11, 15})

# The findings this deployment surfaces, in the model's own order. Results
# recorded before an output was suppressed still carry it, so everything that
# shows a stored result filters through this rather than trusting what it reads.
REPORTED_PATHOLOGIES: tuple[str, ...] = tuple(
    name for index, name in enumerate(PATHOLOGIES) if index not in SUPPRESSED_INDICES
)


def is_reported(pathology: str) -> bool:
    """Whether this deployment surfaces an output at all."""
    return pathology in REPORTED_PATHOLOGIES


# Operating points for these weights. Sixteen of the eighteen are the values
# published with the model, verified identical to the retired TensorFlow.js
# config to nine decimal places.
#
# Two are not, both aimed at the outputs the note above leaves unresolved:
#
#   Pneumothorax    1.08x the published point (3 September 2026)
#   Infiltration    1.08x, then a further 1.15x on 7 September 2026, so 1.242x
#                   the published value in total
#
# The 15% was asked for as raising Infiltration's upper and lower threshold
# together. Those two edges are the doubt band in chester.report, which is a
# fixed fraction of the operating point, so both move exactly when the point
# does -- there is one number here, and raising it by 15% raises both edges by
# 15%. The band itself is unchanged at +/-10%.
#
# Neither factor is a measurement: no calibration set was fitted to produce
# them, and on the reference images they change neither what Infiltration fires
# on nor what Pneumothorax does, as the note records.
# tools/calibrate_thresholds.py is what would price the trade they mean to make,
# over exams a radiologist has read.
#
# These are the defaults, not the last word: an organization may override any of
# them from Settings, and chester.thresholds resolves what a given run uses.
#
# models/xrv-all-45rot15trans15scale/config.json keeps the published values
# unchanged; it is the record of the model's lineage, not this node's config.
# Where the two disagree, these are the numbers the deployment uses.
OPERATING_POINTS: tuple[float, ...] = (
    0.07422872,
    0.038290843,
    0.1218992695,  # Infiltration: 1.242 x the published 0.09814756
    0.0105967953,  # Pneumothorax: 1.08 x the published 0.0098118475
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

# The pre-pool activation, and the classifier matrix that reads it.
#
# The graph ends `... -> Relu -> GlobalAveragePool -> Gemm -> Sigmoid`, which is
# what makes chester.saliency exact rather than an approximation: the pooled
# vector the classifier sees is the spatial mean of this tensor, so the logit is
# the spatial mean of the weighted activation. Both names come from the export in
# tools/export_onnx.py; a model artifact that does not carry them still scores,
# and only the explanation is unavailable.
ACTIVATION_OUTPUT = "/Relu_output_0"
CLASSIFIER_WEIGHT = "inner.classifier.weight"

_session = None
_session_lock = threading.Lock()
_activation_available = False
_classifier_weights = None


def model_version() -> str:
    return MODEL_VERSION


def _model_path() -> Path:
    path = Path(settings.model_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent.parent / path
    return path


def _with_activation_output(path: Path) -> bytes | None:
    """The model with its pre-pool activation added as a second graph output.

    Returns None when the tensor is not in the graph, which is the one case where
    scoring must carry on without an explanation rather than fail.

    Adding the output changes nothing the classifier does: the tensor is already
    computed to be pooled, so it costs no extra work, and the scores come back
    bit-identical to the unmodified graph. tests/test_saliency.py holds both
    claims to that standard rather than to float32 tolerance.

    Done through `chester.onnx_graph` rather than the `onnx` package, and that is
    the point: `onnxruntime` does not depend on `onnx`, so an environment with
    everything needed to *score* would otherwise have explanations silently
    disabled -- which is exactly how they went missing in this deployment. The
    package is still used if it is installed and the direct read fails, which is
    the only case where it would know something the wire format does not say.
    """
    data = path.read_bytes()
    try:
        return onnx_graph.with_extra_output(data, ACTIVATION_OUTPUT)
    except Exception:
        logger.exception("Could not read %s directly; trying the onnx package", path.name)
        return _with_activation_output_via_onnx(data)


def _with_activation_output_via_onnx(data: bytes) -> bytes | None:
    """The same rewrite through the `onnx` package, for an artifact this cannot read.

    None where the package is not installed, which is the ordinary case at runtime.
    """
    try:
        import onnx
    except ImportError:
        return None

    model = onnx.load_from_string(data)
    produced = {name for node in model.graph.node for name in node.output}
    if ACTIVATION_OUTPUT not in produced:
        return None
    if any(out.name == ACTIVATION_OUTPUT for out in model.graph.output):
        return model.SerializeToString()
    model.graph.output.extend(
        [onnx.helper.make_tensor_value_info(ACTIVATION_OUTPUT, onnx.TensorProto.FLOAT, None)]
    )
    return model.SerializeToString()


def get_session():
    """Load the ONNX session once per process."""
    global _session, _activation_available
    if _session is not None:
        return _session
    with _session_lock:
        if _session is None:
            import onnxruntime as ort

            path = _model_path()
            if not path.is_file():
                raise RuntimeError(f"Model artifact is missing: {path}")
            logger.info("Loading model from %s", path)

            serialized = None
            try:
                serialized = _with_activation_output(path)
            except Exception:
                # An explanation is worth nothing if it costs the diagnosis. Any
                # failure reading or rewriting the graph falls back to the plain
                # artifact, and only chester.saliency notices.
                logger.exception("Could not expose %s; explanations disabled", ACTIVATION_OUTPUT)

            if serialized is None:
                logger.warning("Model has no %s output; explanations disabled", ACTIVATION_OUTPUT)
                _session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
                _activation_available = False
            else:
                _session = ort.InferenceSession(serialized, providers=["CPUExecutionProvider"])
                _activation_available = True
    return _session


def activation_available() -> bool:
    """Whether this artifact can be explained. Loads the session to find out."""
    get_session()
    return _activation_available


def classifier_weights() -> np.ndarray:
    """The classifier matrix, shaped (OUTPUT_COUNT, channels).

    Read from the artifact's own initializers rather than kept as a copy here, so
    it cannot drift from the weights the session is scoring with.
    """
    global _classifier_weights
    if _classifier_weights is not None:
        return _classifier_weights
    with _session_lock:
        if _classifier_weights is None:
            data = _model_path().read_bytes()
            weights = onnx_graph.initializer(data, CLASSIFIER_WEIGHT)
            if weights is None:
                weights = _initializer_via_onnx(data, CLASSIFIER_WEIGHT)
            if weights is None:
                raise RuntimeError(f"Model artifact has no readable {CLASSIFIER_WEIGHT}")
            _classifier_weights = weights
    return _classifier_weights


def _initializer_via_onnx(data: bytes, name: str) -> np.ndarray | None:
    """One initializer through the `onnx` package, where it is installed."""
    try:
        import onnx
        from onnx import numpy_helper
    except ImportError:
        return None

    model = onnx.load_from_string(data)
    for initializer in model.graph.initializer:
        if initializer.name == name:
            return numpy_helper.to_array(initializer)
    return None


def activation(prepared: np.ndarray) -> np.ndarray:
    """The pre-pool activation for one prepared image, shaped (channels, h, w)."""
    if not activation_available():
        raise RuntimeError("This model artifact does not expose an activation to explain.")
    maps = get_session().run([ACTIVATION_OUTPUT], {"image": prepared})[0]
    return np.asarray(maps[0], dtype=np.float32)


def reset_session() -> None:
    """Drop the loaded session. For tests and for reloading after a config change."""
    global _session, _activation_available, _classifier_weights
    with _session_lock:
        _session = None
        _activation_available = False
        _classifier_weights = None


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


def infer(pixels: np.ndarray, thresholds_in_force: Mapping[str, float] | None = None) -> dict:
    """Score one image and return raw, normalized and thresholded results.

    `thresholds_in_force` maps a pathology to the operating point this run should
    judge it against, overriding OPERATING_POINTS for the names it carries. The
    worker passes what chester.thresholds resolved for the study's organization;
    a name it omits keeps the default, and None means every default. Nothing is
    read from the database here -- this module stays free of one, and the caller
    that already holds a session decides what the points are.
    """
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
        if thresholds_in_force is not None and pathology in thresholds_in_force:
            threshold = float(thresholds_in_force[pathology])
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
