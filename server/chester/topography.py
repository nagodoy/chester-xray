"""Which hemithorax, and which third of it, a finding's evidence sits in.

Rudolph et al. (CHEST 2024, doi 10.1016/j.chest.2024.01.039) had every reader
score each finding separately for the right and the left hemithorax, and the AI
they evaluated drew a box where it saw one. docs/rudolph-2024-ia-raiox-torax.md
has the rest of that reading.

This model draws no boxes. What it has is the map chester.saliency explains a
score with: per position of its native 7x7 grid, how much that position added to
the logit. Its positive part is laid over the lungs chester.segmentation finds in
the same square, and only the evidence inside them is placed.

That is the second version of this module, and the first is worth recording
because it failed in a way that looked like an answer. It divided the whole
square into halves and thirds. A PA film's square holds the neck at the top and
the upper abdomen at the bottom, and this classifier puts evidence on both; a
real study came back with Lung Opacity and Atelectasis at "HTD superior/inferior"
for evidence on the neck and the stomach. Stored entries from that version carry
no `version` and are not shown -- see `is_current`.

The rules, each a named constant below:

- Each hemithorax is its segmented lung, extended downward by
  INFERIOR_EXTENSION of the lung's height and then widened by LUNG_MARGIN
  pixels, less the corridor between the two lungs and the heart, so the
  mediastinum is not counted as lung. The segmenter outlines *aerated* lung,
  and an effusion, a basal consolidation or a collapsed lower lobe is
  precisely lung that is no longer aerated: on the four reference films with
  an effusion, 4-28% of its evidence fell inside the bare masks and 53-71%
  inside these. Nothing extends upward, so the neck stays out.
- Under THORACIC_MINIMUM of the evidence inside them, the finding is reported
  as outside the lung fields, and nothing else is said about it.
- Otherwise the side is the lung holding at least SIDE_DOMINANCE of the evidence
  that is inside a lung, or bilateral; and the zones are thirds of that lung's
  own height, listed when they hold ZONE_MINIMUM.
- Outputs about central structures (NOT_LATERALIZED) get no hemithorax at all.

Sides are the patient's, read off the pixels as they are stored and displayed --
which is what the reader sees, with the patient's right on the viewer's left.
PatientOrientation is not consulted: on the study above it disagreed with the
picture. The anatomy is checked instead. The heart lies to the patient's left in
all but a vanishing few chests, so a heart on the viewer's left means either a
mirrored image or dextrocardia, and the side is then reported as undetermined
rather than guessed.

What this still is not: a localisation of a lesion. It is where the evidence
*this model used* sits, inside the lungs as segmented, inside the square the
model scored -- which on a portrait film can cut the apices.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

import numpy as np

from chester import inference, segmentation
from chester.inference import IMAGE_SIZE, PATHOLOGIES, REPORTED_PATHOLOGIES

logger = logging.getLogger(__name__)

VERSION = 2

STATUS_LOCATED = "located"
STATUS_EXTRAPULMONARY = "extrapulmonary"

SIDE_RIGHT = "right"
SIDE_LEFT = "left"
SIDE_BILATERAL = "bilateral"

ZONES: tuple[str, ...] = ("upper", "middle", "lower")

ORIENTATION_CONVENTION = "convention"
ORIENTATION_UNCERTAIN = "uncertain"

# Outputs about the heart, the mediastinum and the diaphragm. A hemithorax is
# not an answer for them, so they are not located at all.
NOT_LATERALIZED: frozenset[str] = frozenset(
    {"Cardiomegaly", "Enlarged Cardiomediastinum", "Hernia"}
)

# How far past the segmented lung edge still counts as thoracic, in pixels of the
# 224 square, to take in the pleura and the lateral recesses.
LUNG_MARGIN = 10

# How far below each column of aerated lung the hemithorax still runs, as a
# fraction of that lung's height: the costophrenic recess and an opacified base.
INFERIOR_EXTENSION = 0.25

# The mediastinal corridor is taken only on rows where each lung is at least
# this fraction of its own widest row.
CORRIDOR_WIDTH = 0.3

# The share of a finding's evidence that must lie in the lungs for it to be
# placed in one. Under it, the model was looking somewhere else.
THORACIC_MINIMUM = 0.5

# The share of the in-lung evidence one lung must hold to be named alone.
SIDE_DOMINANCE = 0.65

# The share a third of a lung must hold to be listed.
ZONE_MINIMUM = 0.25

# A lung smaller than this, in pixels of the square, is a failed segmentation
# rather than a lung, and nothing is located on the strength of it.
MINIMUM_LUNG_PIXELS = 400

SHEET_SIDE = {SIDE_RIGHT: "HTD", SIDE_LEFT: "HTE", SIDE_BILATERAL: "Bilateral"}
SHEET_ZONE = {"upper": "superior", "middle": "médio", "lower": "inferior"}
SHEET_EXTRAPULMONARY = "Fora dos campos pulmonares"
SHEET_UNDETERMINED_SIDE = "Lado indeterminado"


class Regions(NamedTuple):
    """The thoracic regions of one image, prepared once for all its outputs."""

    right: np.ndarray  # bool, right hemithorax
    left: np.ndarray  # bool, left hemithorax, disjoint from `right`
    zone: np.ndarray  # int, 0..2 per pixel, the third of its own lung
    orientation: str


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    from scipy import ndimage

    if radius <= 0:
        return mask.copy()
    yy, xx = np.ogrid[-radius : radius + 1, -radius : radius + 1]
    return ndimage.binary_dilation(mask, structure=(xx * xx + yy * yy) <= radius * radius)


def _grown(lung: np.ndarray) -> np.ndarray:
    """The lung, run down each of its columns by INFERIOR_EXTENSION, then widened."""
    rows = lung.shape[0]
    present = np.nonzero(lung.any(axis=1))[0]
    reach = int(round(INFERIOR_EXTENSION * (present.max() - present.min() + 1)))
    region = lung.copy()
    columns = np.nonzero(lung.any(axis=0))[0]
    # The lowest lung pixel of each column: argmax over the flipped column.
    bottoms = rows - 1 - np.argmax(lung[::-1, columns], axis=0)
    for column, bottom in zip(columns, bottoms, strict=True):
        region[bottom : min(rows, bottom + reach + 1), column] = True
    return _dilate(region, LUNG_MARGIN)


def _between(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Per row where both lungs are present, the pixels strictly between them.

    `first` is the lung on the viewer's left. This is the mediastinal corridor,
    which a widened lung must not claim.
    """
    corridor = np.zeros_like(first)
    # Only rows where both lungs are full width. Near the bases one lung may be
    # just the lateral tip of its costophrenic angle, and "between" that tip and
    # the other lung would be the whole base -- which is the hemithorax.
    widths = [mask.sum(axis=1) for mask in (first, second)]
    full = (widths[0] >= CORRIDOR_WIDTH * widths[0].max()) & (
        widths[1] >= CORRIDOR_WIDTH * widths[1].max()
    )
    for row in np.nonzero(full)[0]:
        inner = np.nonzero(first[row])[0].max()
        outer = np.nonzero(second[row])[0].min()
        if inner + 1 < outer:
            corridor[row, inner + 1 : outer] = True
    return corridor


def _thirds(mask: np.ndarray, rows: int) -> np.ndarray:
    """Per row, which third of this lung's height it falls in (clamped outside it)."""
    present = np.nonzero(mask.any(axis=1))[0]
    top, bottom = int(present.min()), int(present.max()) + 1
    height = max(bottom - top, 1)
    index = np.arange(rows)
    return np.clip(((index - top) * 3) // height, 0, 2)


def prepare(masks: segmentation.Masks) -> Regions | None:
    """Widened lungs, their thirds and the orientation check, or None if no lungs."""
    right, left = masks.right_lung, masks.left_lung
    if right.sum() < MINIMUM_LUNG_PIXELS or left.sum() < MINIMUM_LUNG_PIXELS:
        return None

    rows, columns = right.shape
    right_x = float(np.nonzero(right)[1].mean())
    left_x = float(np.nonzero(left)[1].mean())

    # Grown down and out, minus the mediastinal corridor between the lungs and
    # the heart -- but never minus the segmented lung itself, which includes the
    # lung behind the heart.
    viewer_left, viewer_right = (right, left) if right_x < left_x else (left, right)
    excluded = _between(viewer_left, viewer_right) | masks.heart
    wide_right = (_grown(right) & ~excluded) | right
    wide_left = (_grown(left) & ~excluded) | left
    # Where the widened lungs meet, each pixel goes to the lung whose centre is
    # nearer: neither margin may claim the other lung's edge.
    overlap = wide_right & wide_left
    if overlap.any():
        x = np.broadcast_to(np.arange(columns), (rows, columns))
        nearer_right = np.abs(x - right_x) <= np.abs(x - left_x)
        wide_right &= ~overlap | nearer_right
        wide_left &= ~overlap | ~nearer_right

    zone = np.zeros((rows, columns), dtype=np.int8)
    zone[:] = np.where(
        (np.arange(columns) < (right_x + left_x) / 2)[None, :],
        _thirds(right, rows)[:, None],
        _thirds(left, rows)[:, None],
    )

    orientation = ORIENTATION_CONVENTION
    heart = masks.heart
    # Convention puts the patient's right lung on the viewer's left and the heart
    # on the viewer's right. Either failing means the side cannot be trusted.
    if right_x >= left_x or (
        heart.sum() > 0 and float(np.nonzero(heart)[1].mean()) < (right_x + left_x) / 2
    ):
        orientation = ORIENTATION_UNCERTAIN

    return Regions(wide_right, wide_left, zone, orientation)


def evidence_pixels(contribution: np.ndarray, size: int = IMAGE_SIZE) -> np.ndarray:
    """The positive evidence spread over the square, each cell's mass over its block.

    Exact rather than interpolated: a grid cell is a block of the square, and its
    mass is shared evenly among the block's pixels, so the total is preserved.
    """
    grid = np.maximum(np.asarray(contribution, dtype=np.float64), 0.0)
    rows, columns = grid.shape
    if size % rows or size % columns:
        raise ValueError(f"a {rows}x{columns} map does not tile a {size} square")
    block = (size // rows, size // columns)
    return np.kron(grid, np.ones(block)) / (block[0] * block[1])


def locate(contribution: np.ndarray, regions: Regions) -> dict | None:
    """Status, side and zones for one output's evidence map, or None if it has none."""
    evidence = evidence_pixels(contribution, regions.right.shape[0])
    total = float(evidence.sum())
    if total <= 0.0:
        return None

    in_right = float(evidence[regions.right].sum())
    in_left = float(evidence[regions.left].sum())
    in_lungs = in_right + in_left
    thoracic_share = in_lungs / total

    if thoracic_share < THORACIC_MINIMUM:
        return {
            "version": VERSION,
            "status": STATUS_EXTRAPULMONARY,
            "thoracic_share": round(thoracic_share, 4),
        }

    right_share = in_right / in_lungs
    if right_share >= SIDE_DOMINANCE:
        side = SIDE_RIGHT
    elif 1.0 - right_share >= SIDE_DOMINANCE:
        side = SIDE_LEFT
    else:
        side = SIDE_BILATERAL

    lungs = regions.right | regions.left
    zone_mass = np.bincount(regions.zone[lungs], weights=evidence[lungs], minlength=3)
    zone_shares = {zone: float(zone_mass[i] / in_lungs) for i, zone in enumerate(ZONES)}
    zones = [zone for zone in ZONES if zone_shares[zone] >= ZONE_MINIMUM]
    if not zones:
        zones = [max(ZONES, key=zone_shares.__getitem__)]

    return {
        "version": VERSION,
        "status": STATUS_LOCATED,
        "side": side,
        "zones": zones,
        "thoracic_share": round(thoracic_share, 4),
        "right_share": round(right_share, 4),
        "zone_shares": {zone: round(share, 4) for zone, share in zone_shares.items()},
        "orientation": regions.orientation,
    }


def locate_all(pixels: np.ndarray) -> dict[str, dict] | None:
    """Topography for every reported, lateralised output.

    None when there is nothing to locate against: no activation to read, no
    segmenter, or a segmentation without two lungs. The scores stand either way.
    """
    from chester import saliency

    if not inference.activation_available():
        return None
    masks = segmentation.lung_masks(pixels)
    if masks is None:
        return None
    regions = prepare(masks)
    if regions is None:
        logger.info("Segmentation found no pair of lungs; no topography")
        return None

    maps = saliency.contributions(pixels)
    located: dict[str, dict] = {}
    for pathology in REPORTED_PATHOLOGIES:
        if pathology in NOT_LATERALIZED:
            continue
        entry = locate(maps[PATHOLOGIES.index(pathology)], regions)
        if entry is not None:
            located[pathology] = entry
    return located


def is_current(entry: dict | None) -> bool:
    """Whether a stored entry was made by this version, and so may be shown."""
    return bool(entry) and entry.get("version") == VERSION


def _zones(entry: dict) -> list[str]:
    return [zone for zone in entry.get("zones", []) if zone in ZONES]


def sheet_label(entry: dict | None) -> str:
    """The short Portuguese form the report sheet prints, e.g. "HTD inferior"."""
    if not is_current(entry):
        return "-"
    if entry.get("status") == STATUS_EXTRAPULMONARY:
        return SHEET_EXTRAPULMONARY
    if entry.get("orientation") == ORIENTATION_UNCERTAIN:
        side = SHEET_UNDETERMINED_SIDE
    else:
        side = SHEET_SIDE.get(entry.get("side", ""), "")
    zones = [SHEET_ZONE[zone] for zone in _zones(entry)]
    place = "difuso" if len(zones) == len(ZONES) else "/".join(zones)
    return " ".join(part for part in (side, place) if part) or "-"


def dicom_code(entry: dict | None) -> str:
    """The form the private DICOM tag carries, e.g. "RIGHT/MIDDLE+LOWER"."""
    if not is_current(entry):
        return ""
    if entry.get("status") == STATUS_EXTRAPULMONARY:
        return "EXTRAPULMONARY"
    side = (
        "UNDETERMINED"
        if entry.get("orientation") == ORIENTATION_UNCERTAIN
        else str(entry.get("side", "")).upper()
    )
    return f"{side}/{'+'.join(zone.upper() for zone in _zones(entry))}"
