"""Which hemithorax, and which third of it, a finding's evidence sits in.

Rudolph et al. (CHEST 2024, doi 10.1016/j.chest.2024.01.039) had every reader
score each finding separately for the right and the left hemithorax, and the AI
they evaluated drew a box where it saw one. A reader handed "Effusion ACIMA" with
no side has to find it themselves, which is the work the study measured the AI
saving. docs/rudolph-2024-ia-raiox-torax.md has the rest of that reading.

This model draws no boxes. What it has is the map chester.saliency already
explains a score with: per position of its native 7x7 grid, how much that
position added to the logit. The positive part of that map is divided here,
exactly by area, into the two halves of the image and its upper, middle and lower
thirds. Nothing is fitted and nothing is resampled -- each grid cell's share is
the fraction of it that falls on each side of the line.

What this says, and what it does not:

It says where the evidence *this model used* sits inside the square it scored.
`inference.preprocess` crops that square from the centre of the film, so on a
portrait PA the apices and the costophrenic angles can lie partly outside it, and
"upper" and "lower" are thirds of the square, not of the lung fields. There is no
lung segmentation here: the middle column of the grid is the mediastinum and is
split evenly between the sides, because nothing here knows better. And a model
that keys on something spurious points at the spurious thing. It is a pointer for
the reader's eye, not a localisation of a lesion.

Laterality follows radiological convention: on a PA film displayed as acquired,
the patient's right is on the viewer's left. A DICOM instance that says otherwise
in PatientOrientation (0020,0020) is read the other way round. An image without
that tag -- every PNG and JPEG, and DICOM that omits it -- is read by convention,
and the result says so, because a mirrored export would put the finding on the
wrong side and nothing in the pixels could tell.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from chester import inference
from chester.inference import PATHOLOGIES, REPORTED_PATHOLOGIES

logger = logging.getLogger(__name__)

SIDE_RIGHT = "right"
SIDE_LEFT = "left"
SIDE_BILATERAL = "bilateral"

ZONES: tuple[str, ...] = ("upper", "middle", "lower")

ORIENTATION_DICOM = "dicom"
ORIENTATION_ASSUMED = "assumed"

# How much of the evidence one side must hold to be named on its own. Below
# this the finding is called bilateral, which is also the honest answer for
# evidence spread across the mediastinum.
SIDE_DOMINANCE = 0.65

# How much of the evidence a third must hold to be listed. Several may be: a
# finding over the middle and lower thirds is reported as both.
ZONE_MINIMUM = 0.25

# Short forms for the rendered sheet and the DICOM tags, which have one narrow
# cell for this. The interface localises from the stored entry instead.
SHEET_SIDE = {SIDE_RIGHT: "HTD", SIDE_LEFT: "HTE", SIDE_BILATERAL: "Bilateral"}
SHEET_ZONE = {"upper": "superior", "middle": "médio", "lower": "inferior"}


def _overlaps(cells: int, edges: tuple[float, ...]) -> np.ndarray:
    """(len(edges) - 1, cells): the fraction of each grid cell inside each band.

    Edges are in units of the whole axis, 0..1. Each column of the result sums to
    one, so the shares it produces always add up to the mass they divide.
    """
    starts = np.arange(cells) / cells
    ends = (np.arange(cells) + 1) / cells
    bands = []
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        covered = np.clip(np.minimum(ends, high) - np.maximum(starts, low), 0.0, None)
        bands.append(covered * cells)
    return np.asarray(bands)


def mirrored_from_dicom(dataset: Any) -> bool | None:
    """Whether PatientOrientation says the image's left is the patient's left.

    The first value names the direction of the rows, left to right across the
    image. `L` -- toward the patient's left -- is the conventional PA display,
    with the patient's right on the viewer's left. `R` is that picture mirrored.
    Anything else, or no tag, is None: not known, so read by convention.
    """
    try:
        value = dataset.get("PatientOrientation")
    except Exception:
        return None
    if value is None:
        return None
    # pydicom hands back a MultiValue for "L\F", and a plain string when the tag
    # holds one value or was written unsplit.
    parts = value.split("\\") if isinstance(value, str) else list(value)
    first = str(parts[0]).strip().upper()[:1] if parts else ""
    if first == "L":
        return False
    if first == "R":
        return True
    return None


def locate(contribution: np.ndarray, *, mirrored: bool | None = None) -> dict | None:
    """Side and zones for one output's signed evidence map, or None if it has none.

    `mirrored` is what `mirrored_from_dicom` returned: False or None reads the
    image by convention, True reads it the other way round.
    """
    grid = np.maximum(np.asarray(contribution, dtype=np.float64), 0.0)
    if grid.ndim != 2:
        raise ValueError("expected a 2D evidence map")
    total = float(grid.sum())
    if total <= 0.0:
        return None

    rows, columns = grid.shape
    halves = _overlaps(columns, (0.0, 0.5, 1.0))
    thirds = _overlaps(rows, (0.0, 1 / 3, 2 / 3, 1.0))

    by_column = grid.sum(axis=0)
    image_left, _image_right = (halves @ by_column) / total
    # Convention: the viewer's left is the patient's right.
    right_share = float(1.0 - image_left if mirrored else image_left)

    zone_shares = {
        zone: float(share)
        for zone, share in zip(ZONES, (thirds @ grid.sum(axis=1)) / total, strict=True)
    }

    if right_share >= SIDE_DOMINANCE:
        side = SIDE_RIGHT
    elif 1.0 - right_share >= SIDE_DOMINANCE:
        side = SIDE_LEFT
    else:
        side = SIDE_BILATERAL

    zones = [zone for zone in ZONES if zone_shares[zone] >= ZONE_MINIMUM]
    if not zones:
        # Cannot happen with three thirds and a 0.25 floor -- one of them holds
        # at least a third -- but a changed constant must not yield no zone.
        zones = [max(ZONES, key=zone_shares.__getitem__)]

    return {
        "side": side,
        "zones": zones,
        "right_share": round(right_share, 4),
        "zone_shares": {zone: round(share, 4) for zone, share in zone_shares.items()},
        "orientation": ORIENTATION_ASSUMED if mirrored is None else ORIENTATION_DICOM,
    }


def locate_all(pixels: np.ndarray, *, mirrored: bool | None = None) -> dict[str, dict] | None:
    """Topography for every reported output, from one forward pass.

    None when the model artifact exposes no activation to read, which is the
    same condition that disables explanations: the score stands without it.
    """
    from chester import saliency

    if not inference.activation_available():
        return None
    maps = saliency.contributions(pixels)
    located: dict[str, dict] = {}
    for pathology in REPORTED_PATHOLOGIES:
        entry = locate(maps[PATHOLOGIES.index(pathology)], mirrored=mirrored)
        if entry is not None:
            located[pathology] = entry
    return located


def sheet_label(entry: dict | None) -> str:
    """The short Portuguese form the report sheet prints, e.g. "HTD inferior"."""
    if not entry:
        return "-"
    side = SHEET_SIDE.get(entry.get("side", ""), "")
    zones = [SHEET_ZONE[zone] for zone in entry.get("zones", []) if zone in SHEET_ZONE]
    place = "difuso" if len(zones) == len(ZONES) else "/".join(zones)
    return " ".join(part for part in (side, place) if part) or "-"


def dicom_code(entry: dict | None) -> str:
    """The form the private DICOM tag carries, e.g. "RIGHT/MIDDLE+LOWER"."""
    if not entry:
        return ""
    zones = "+".join(zone.upper() for zone in entry.get("zones", []) if zone in ZONES)
    return f"{str(entry.get('side', '')).upper()}/{zones}"
