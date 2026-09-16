"""Where in the image a finding's score comes from.

The classifier ends `features -> ReLU -> global average pool -> linear`, and that
shape is the whole of why this module can be exact. Writing `A` for the activation
this tensor holds and `W` for the classifier matrix, the logit for output `c` is

    logit_c = sum_k W[c,k] * mean_xy A[k,x,y] + b_c
            = mean_xy ( sum_k W[c,k] * A[k,x,y] ) + b_c

so the inner sum is a map over the image whose spatial mean *is* the logit, bias
aside. Nothing is fitted, perturbed or approximated: the map is the model's own
decision written out per position, and summing it back reproduces the score the
worklist shows to float32 precision. tests/test_saliency.py asserts exactly that,
because it is the property that makes the picture worth showing.

That is a stronger claim than the gradient overlays the original CHESTER browser
app drew, and it is available here for nothing -- the activation is already
computed in order to be pooled. It is also a weaker claim than it looks, in one
way that matters and is easy to misread: it says which positions moved *this
model's* score, not where a finding is. A high region is where the evidence the
model used sits, which for a model that keys on something spurious is where the
spurious thing sits. The interface says so beside the picture.

Two deliberate limits:

The map is the model's native 7x7, smoothed up to the display size. It cannot be
sharper -- there are 49 positions behind it -- and rendering it at 512 does not
add detail, it only avoids showing a chest radiograph as a grid of blocks.

Only positive contributions are drawn. A position that argued *against* the
finding is as real, but a single picture that means "for" in one place and
"against" in another is read as "here" in both.
"""

from __future__ import annotations

import io
import logging

import numpy as np

from chester import inference
from chester.inference import IMAGE_SCALE, IMAGE_SIZE, PATHOLOGIES

logger = logging.getLogger(__name__)

# What the overlay is rendered at. The map behind it is 7x7 either way; this is a
# display size, chosen to match the thumbnail's longest side.
RENDER_SIZE = 512

# How strongly the evidence is tinted, per channel, at full weight. Blue, as the
# original CHESTER drew it, and lifted rather than replaced so the ribs and the
# lung markings stay readable underneath.
TINT = (-40.0, 60.0, 210.0)


def explainable(pathology: str) -> bool:
    """Whether a map may be drawn for this output.

    Reported outputs only. The five suppressed ones are a clinical decision
    recorded in chester.inference, and an explanation for a finding the interface
    refuses to report would reintroduce it through the back door.
    """
    return inference.is_reported(pathology)


def activation_map(pixels: np.ndarray, pathology: str) -> np.ndarray:
    """The evidence for one finding, as a (IMAGE_SIZE, IMAGE_SIZE) array in 0..1.

    `pixels` is the same 0..255 grayscale raster `inference.infer` takes, and goes
    through the same `preprocess`, so the map lines up with what the model scored
    rather than with the original framing.
    """
    if not explainable(pathology):
        raise ValueError(f"{pathology} is not an output this deployment explains.")

    index = PATHOLOGIES.index(pathology)
    prepared = inference.preprocess(pixels).reshape(1, 1, IMAGE_SIZE, IMAGE_SIZE)
    maps = inference.activation(prepared)
    weights = inference.classifier_weights()

    if weights.shape[0] != len(PATHOLOGIES) or weights.shape[1] != maps.shape[0]:
        raise RuntimeError(
            f"Classifier {weights.shape} does not match activation {maps.shape}; "
            "the model artifact and this code disagree."
        )

    contribution = np.einsum("k,kxy->xy", weights[index], maps)
    return _to_unit_scale(contribution)


def _to_unit_scale(contribution: np.ndarray) -> np.ndarray:
    """Clamp to positive evidence and scale so the strongest position is 1.

    A map with no positive position anywhere comes back all zeros rather than
    being stretched: there is nothing to point at, and normalizing noise would
    invent a hot spot out of it.
    """
    positive = np.maximum(contribution, 0.0)
    peak = float(positive.max())
    if peak <= 0.0:
        # Still at the display size: callers render whatever comes back, and a
        # map that changed shape when it had nothing to say would be a trap.
        return np.zeros((IMAGE_SIZE, IMAGE_SIZE), dtype=np.float32)
    return _resize(positive / peak, IMAGE_SIZE)


def _resize(array: np.ndarray, size: int) -> np.ndarray:
    from PIL import Image

    image = Image.fromarray((np.clip(array, 0.0, 1.0) * 255.0).astype(np.uint8))
    resized = image.resize((size, size), Image.BICUBIC)
    return np.asarray(resized, dtype=np.float32) / 255.0


def overlay_png(pixels: np.ndarray, pathology: str, size: int = RENDER_SIZE) -> bytes:
    """The square the model scored, tinted where the evidence for `pathology` is.

    Square, and cropped the way `preprocess` crops, because that is the image the
    map describes. Drawing it back onto the original framing would put the tint
    somewhere the model never looked.
    """
    from PIL import Image

    from chester.imaging.dicom import to_pil_image

    heat = _resize(activation_map(pixels, pathology), size)

    # Rendered by the same function that draws the thumbnail, so the overlay reads
    # as the picture beside it with a tint added rather than as a second, paler
    # copy of the study. `preprocess` is inverted rather than re-cropped so the
    # square is byte-for-byte the one the model was given.
    prepared = inference.preprocess(pixels)
    cropped = (prepared / IMAGE_SCALE + 1.0) / 2.0 * 255.0
    base = np.asarray(
        to_pil_image(cropped).convert("RGB").resize((size, size), Image.LANCZOS),
        dtype=np.float32,
    )

    tinted = base.copy()
    for channel, strength in enumerate(TINT):
        tinted[..., channel] = np.clip(base[..., channel] + heat * strength, 0.0, 255.0)

    buffer = io.BytesIO()
    Image.fromarray(tinted.astype(np.uint8)).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
