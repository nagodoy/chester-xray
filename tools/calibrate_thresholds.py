#!/usr/bin/env python3
"""Measure each output against labelled exams and propose a local threshold.

The published operating points come from the population these weights were
fitted on. Nothing guarantees they transfer: `docs/chester-vs-torax-ia.md`
records Fibrosis firing on 7 of 7 reference images whose label is not fibrosis,
which is why it is suppressed, and Nodule was withdrawn on the same measurement.
This runs that measurement on demand, over a set of exams a radiologist has read.

What it reports, per output:

  fp_rate   how often the output fires on an exam whose report does not carry it
  recall    how often it fires on an exam whose report does
  auc       how well the score separates the two on this set, threshold aside
  suggested a threshold chosen by --method, and what it gives on this same set:
              specificity  the lowest threshold meeting --target-specificity
              youden       the threshold maximising sensitivity + specificity - 1
                           (Youden's J), the operating point Rudolph et al.,
                           CHEST 2024, fitted readers' ROC curves to
              sensitivity  the highest threshold still meeting
                           --target-sensitivity -- the sensitive operating
                           point, priced in specificity
  bounds    whether the suggestion is inside the range Settings accepts as an
            override (chester.thresholds), around the point the deployment runs

Labels may carry the reader's confidence, as in that study: `Effusion:3` on the
0-4 scale (0 no suspicion, 1 unlikely, 2 possible, 3 likely, 4 certain), and a
bare label is a 4. --reference-standard turns the grades into the yes-or-no
truth each row is scored against, as the study's Table 2 does:

  I    only 4 is positive              (very specific)
  II   3 and 4
  III  2, 3 and 4
  IV   anything above 0                (very sensitive)

Exams graded below the cut count as negatives for that output, which is what
makes RFS IV the demanding standard: a finding the reader only called unlikely
must still be caught.

The suggestion is a starting point for a radiologist to judge, not a value to
paste into the deployment. Whether an output is reported at all is decided in
`server/chester/inference.py`; this tool never writes there.

Usage:
    pip install numpy pillow onnxruntime

    # a set you have labelled: CSV of `path,labels` with ; between labels,
    # an empty labels field meaning the report found nothing
    python tools/calibrate_thresholds.py --manifest exams.csv

    # graded labels (`Pneumothorax:2;Effusion:4`), Youden point, sensitive standard
    python tools/calibrate_thresholds.py --manifest graded.csv \
        --reference-standard IV --method youden

    # the sensitive point: keep 90% recall and see what specificity it costs
    python tools/calibrate_thresholds.py --manifest exams.csv \
        --method sensitivity --target-sensitivity 0.90

    # the reference images in examples/, labelled by filename
    python tools/calibrate_thresholds.py --from-filenames examples/*.png

    python tools/calibrate_thresholds.py --manifest exams.csv --json out.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMAGE_SIZE = 224
IMAGE_SCALE = 1024.0
LEGACY_CONFIG = ROOT / "models" / "xrv-all-45rot15trans15scale" / "config.json"
SERVER_INFERENCE = ROOT / "server" / "chester" / "inference.py"
SERVER_THRESHOLDS = ROOT / "server" / "chester" / "thresholds.py"

# The reader's 0-4 confidence, and the lowest grade each reference standard
# counts as positive. Rudolph et al., CHEST 2024, Table 2.
MAX_GRADE = 4
REFERENCE_STANDARDS = {"I": 4, "II": 3, "III": 2, "IV": 1}

# Canonical torchxrayvision order for densenet121-res224-all. The vendored
# config blanks six of these labels, so the names cannot be read from it.
# `server/chester/inference.py` holds the same tuple and is the source of truth.
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

# Below this many negatives an fp_rate is arithmetic, not evidence. Rows under
# the floor are still printed -- hiding them would hide that the set is thin --
# but they are marked, and they never drive the exit code.
MIN_NEGATIVES = 20


def load_grayscale(path: Path) -> np.ndarray:
    """Match the server's image decode: RGB then channel mean."""
    with Image.open(path) as img:
        rgb = np.array(img.convert("RGB"), dtype=np.float32)
    return rgb.mean(axis=2)


def preprocess(pixels: np.ndarray) -> np.ndarray:
    """Resize the short side to 224, centre-crop, scale to [-1024, 1024].

    Mirrors `chester.inference.preprocess`. Kept here rather than imported so
    the tool runs against a checkout without the server package installed, the
    same trade parity_check.py makes. If one changes, change both.
    """
    clipped = np.clip(np.asarray(pixels, dtype=np.float32), 0.0, 255.0)
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


def operating_points() -> np.ndarray:
    """The published thresholds, read from the vendored config.

    Published, not deployed. The config carries the values that shipped with the
    weights and is left that way on purpose -- it is the record of the model's
    lineage. `server/chester/inference.py` may raise or lower any of them for
    this node, and at present raises Infiltration and Pneumothorax by 8%. So the
    `published` column below is the baseline this measurement is being read
    against, not necessarily the line production draws.
    """
    return np.asarray(json.loads(LEGACY_CONFIG.read_text())["OP_POINT"], dtype=np.float64)


def _server_constant(path: Path, name: str):
    """A literal assigned at module level in a server source file, or None.

    Read with `ast` rather than imported: importing chester.inference needs the
    server's settings and a database URL, and this tool must run on a bare
    checkout. A constant that stops being a literal comes back None and the
    columns built on it are left blank rather than guessed.
    """
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            target, value = (first.id if isinstance(first, ast.Name) else None), node.value
        if target == name and value is not None:
            # `frozenset({5, 6})` is a call, not a literal; its argument is.
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in {"frozenset", "set"}
                and len(value.args) == 1
            ):
                value = value.args[0]
            try:
                return ast.literal_eval(value)
            except ValueError:
                return None
    return None


def deployed_points() -> np.ndarray | None:
    """The points the server runs by default: `OPERATING_POINTS` in inference.py."""
    points = _server_constant(SERVER_INFERENCE, "OPERATING_POINTS")
    if not points or len(points) != len(PATHOLOGIES):
        return None
    return np.asarray(points, dtype=np.float64)


def suppressed_indices() -> frozenset[int]:
    """`SUPPRESSED_INDICES` from inference.py: outputs Settings cannot override."""
    return frozenset(_server_constant(SERVER_INFERENCE, "SUPPRESSED_INDICES") or ())


def override_factors() -> tuple[float, float] | None:
    """`MIN_FACTOR` and `MAX_FACTOR` from chester.thresholds."""
    low = _server_constant(SERVER_THRESHOLDS, "MIN_FACTOR")
    high = _server_constant(SERVER_THRESHOLDS, "MAX_FACTOR")
    if low is None or high is None:
        return None
    return float(low), float(high)


def normalize(name: str) -> str:
    """Fold a label to compare it: case, spacing and underscores do not matter."""
    return re.sub(r"[^a-z]", "", name.lower())


BY_NORMALIZED = {normalize(name): name for name in PATHOLOGIES}
NO_FINDING = {"nofinding", "normal", "semachado", "semachados"}


class Exam(NamedTuple):
    """One labelled exam: what the report carries, and what it cannot rule out.

    `excluded` is the part a plain label set cannot express. A PadChest report
    reading "COPD signs" says nothing about Emphysema either way: counting that
    exam as an Emphysema negative would manufacture a false positive, and
    counting it as a positive would invent a diagnosis. It is dropped from that
    one output's arithmetic and kept for every other.
    """

    path: Path
    positives: frozenset[str]
    excluded: frozenset[str] = frozenset()
    # The reader's 0-4 confidence per output, where the label carried one. Every
    # name in `positives` was graded at least the cut of the reference standard
    # the set was read under; this keeps the grade for the record.
    grades: tuple[tuple[str, int], ...] = ()


# PadChest labels that mean the same finding as a model output.
PADCHEST_EQUIVALENT = {
    "pleural effusion": "Effusion",
    "infiltrates": "Infiltration",
    "laminar atelectasis": "Atelectasis",
    "pulmonary fibrosis": "Fibrosis",
    "rib fracture": "Fracture",
    "lung metastasis": "Lung Lesion",
}

# PadChest labels that bear on an output without settling it. An exam carrying
# one is excluded from that output's counts rather than scored against it.
# Radiology, not string matching, decides this table; it is deliberately short.
PADCHEST_AMBIGUOUS = {
    "copd signs": "Emphysema",
    "air trapping": "Emphysema",
    "alveolar pattern": "Lung Opacity",
    "interstitial pattern": "Lung Opacity",
    "heart insufficiency": "Edema",
    "pulmonary edema": "Edema",
    "costophrenic angle blunting": "Effusion",
}


def split_grade(item: str, source: str) -> tuple[str, int]:
    """`Effusion:3` -> ("Effusion", 3); a bare label is a 4."""
    name, sep, grade = item.rpartition(":")
    if not sep:
        return item, MAX_GRADE
    try:
        value = int(grade.strip())
    except ValueError:
        raise SystemExit(f"{source}: grade in {item!r} is not a whole number") from None
    if not 0 <= value <= MAX_GRADE:
        raise SystemExit(f"{source}: grade in {item!r} must be between 0 and {MAX_GRADE}")
    return name, value


def resolve_graded(
    raw: list[str], source: str, strict: bool, cut: int = MAX_GRADE
) -> tuple[set[str], dict[str, int]]:
    """Graded labels -> the outputs positive at `cut`, and every output's grade.

    A label repeated keeps its highest grade: the study scored each hemithorax
    and kept the higher of the two, and two grades for one output mean the same.
    """
    grades: dict[str, int] = {}
    for item in raw:
        name, grade = split_grade(item, source)
        for resolved in resolve_labels([name], source, strict):
            grades[resolved] = max(grade, grades.get(resolved, 0))
    return {name for name, grade in grades.items() if grade >= cut}, grades


def resolve_labels(raw: list[str], source: str, strict: bool) -> set[str]:
    """Map written labels onto model outputs, refusing what it cannot place."""
    resolved: set[str] = set()
    for item in raw:
        key = normalize(item)
        if not key or key in NO_FINDING:
            continue
        if key in BY_NORMALIZED:
            resolved.add(BY_NORMALIZED[key])
        elif strict:
            raise SystemExit(
                f"{source}: label {item!r} is not one of the model's outputs. "
                f"Pass --lenient to ignore labels the model has no output for."
            )
    return resolved


def looks_like_padchest(path: Path) -> bool:
    """A PadChest export names its columns; a plain manifest does not."""
    with path.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle), [])
    return "ImageID" in header and "Labels" in header


def read_padchest(path: Path, images_dir: Path) -> list[Exam]:
    """Read a PadChest CSV: `ImageID` plus a Python-literal `Labels` list.

    PadChest's vocabulary is far wider than the model's eighteen outputs, and
    that width is the point of this reader. A label is handled three ways:

      equivalent  same finding -- counted as a positive for that output
      ambiguous   bears on an output without settling it -- that exam is
                  excluded from that output's counts (see Exam.excluded)
      unrelated   no bearing at all ("pacemaker", "kyphosis") -- ignored, and
                  the exam still counts as a negative for every model output

    That last case is what makes the set usable: a report describing a pacemaker
    and nothing else is a genuine negative for all eighteen findings.
    """
    import ast

    exams: list[Exam] = []
    dropped: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            image = images_dir / (row.get("ImageID") or "").strip()
            if not image.is_file():
                raise SystemExit(
                    f"{path}:{line_number}: image not found: {image}\n"
                    f"Pass --images-dir pointing at the directory holding the PNGs."
                )
            try:
                labels = ast.literal_eval(row.get("Labels") or "[]")
            except (ValueError, SyntaxError):
                raise SystemExit(f"{path}:{line_number}: cannot parse Labels") from None

            positives: set[str] = set()
            excluded: set[str] = set()
            for label in labels:
                key = str(label).strip().lower()
                if not key or normalize(key) in NO_FINDING:
                    continue
                if normalize(key) in BY_NORMALIZED:
                    positives.add(BY_NORMALIZED[normalize(key)])
                elif key in PADCHEST_EQUIVALENT:
                    positives.add(PADCHEST_EQUIVALENT[key])
                elif key in PADCHEST_AMBIGUOUS:
                    excluded.add(PADCHEST_AMBIGUOUS[key])
                else:
                    dropped.add(key)
            exams.append(Exam(image, frozenset(positives), frozenset(excluded - positives)))

    if dropped:
        print(
            f"  {len(dropped)} label(s) have no model output and were treated as "
            f"unrelated: {', '.join(sorted(dropped)[:8])}"
            + (" ..." if len(dropped) > 8 else ""),
            file=sys.stderr,
        )
    if not exams:
        raise SystemExit(f"{path}: no rows")
    return exams


def read_manifest(path: Path, strict: bool, cut: int = MAX_GRADE) -> list[Exam]:
    """CSV of `path,labels`, labels separated by ';'. Empty means no finding.

    A label may carry a grade, `Effusion:2`; `cut` is the lowest grade counted
    positive (see REFERENCE_STANDARDS).

    A header row is optional; one whose first field is not an existing file and
    reads like a column name is skipped.
    """
    rows: list[Exam] = []
    base = path.parent
    with path.open(newline="", encoding="utf-8") as handle:
        for line_number, fields in enumerate(csv.reader(handle), start=1):
            if not fields or not fields[0].strip() or fields[0].lstrip().startswith("#"):
                continue
            image = Path(fields[0].strip())
            if not image.is_absolute():
                image = base / image
            if line_number == 1 and not image.exists() and normalize(fields[0]) in {
                "path",
                "image",
                "file",
                "caminho",
                "arquivo",
            }:
                continue
            if not image.is_file():
                raise SystemExit(f"{path}:{line_number}: no such image: {image}")
            raw = fields[1].split(";") if len(fields) > 1 else []
            labels, grades = resolve_graded(raw, f"{path}:{line_number}", strict, cut)
            rows.append(Exam(image, frozenset(labels), grades=tuple(sorted(grades.items()))))
    if not rows:
        raise SystemExit(f"{path}: no rows")
    return rows


def read_filenames(paths: list[Path], strict: bool) -> list[Exam]:
    """Labels from NIH-style names: `00000001_001-Cardiomegaly-Emphysema.png`.

    Only the part after the first '-' is read, so a file with no '-' carries no
    label and is dropped rather than counted as a negative -- an unlabelled exam
    is not the same as one reported clean.
    """
    rows: list[Exam] = []
    for path in paths:
        stem = path.stem
        if "-" not in stem:
            print(f"  skipping {path.name}: no label in the filename", file=sys.stderr)
            continue
        parts = [re.sub(r"\d+$", "", part) for part in stem.split("-")[1:]]
        rows.append(Exam(path, frozenset(resolve_labels(parts, str(path), strict))))
    if not rows:
        raise SystemExit("no labelled images: names must read `id-Label[-Label].ext`")
    return rows


def score(rows: list[Exam], model: Path) -> np.ndarray:
    """One row of 18 raw scores per exam, in the order given."""
    session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    out = np.empty((len(rows), len(PATHOLOGIES)), dtype=np.float64)
    for index, exam in enumerate(rows):
        tensor = preprocess(load_grayscale(exam.path)).reshape(1, 1, IMAGE_SIZE, IMAGE_SIZE)
        out[index] = session.run(["scores"], {"image": tensor})[0].reshape(-1)
    return out


def suggest(negatives: np.ndarray, positives: np.ndarray, specificity: float) -> float | None:
    """The lowest threshold whose specificity on the negatives meets the target.

    Candidates are the observed scores themselves: a threshold between two
    adjacent observations behaves identically on this set, so nothing is gained
    by sweeping a grid. Returns None when there are no negatives to fit against.
    """
    if negatives.size == 0:
        return None
    # Rounded before flooring: (1.0 - 0.9) * 10 is 0.9999999999999998 in binary
    # floating point, and truncating that to 0 would allow no negative to fire
    # at all -- returning 100% specificity for a 90% request, and charging the
    # recall that costs.
    allowed = int(np.floor(round((1.0 - specificity) * negatives.size, 9)))
    ranked = np.sort(negatives)[::-1]
    # Fire on at most `allowed` negatives: sit just above the (allowed+1)-th
    # highest, or above the highest when none may fire.
    cutoff = ranked[allowed] if allowed < ranked.size else 0.0
    return float(np.nextafter(cutoff, np.inf))


def youden(negatives: np.ndarray, positives: np.ndarray) -> float | None:
    """The threshold maximising Youden's J = sensitivity + specificity - 1.

    Candidates are the observed scores, as in `suggest`: "fires at >= t" changes
    only at an observed value. Ties in J go to the lowest threshold -- the more
    sensitive of equally good points, which is the side a missed finding is on.
    None without both classes, since J needs both rates.
    """
    if negatives.size == 0 or positives.size == 0:
        return None
    candidates = np.unique(np.concatenate([negatives, positives]))
    best, best_j = None, -np.inf
    for cut in candidates:
        sensitivity = float((positives >= cut).mean())
        specificity = float((negatives < cut).mean())
        j = sensitivity + specificity - 1.0
        if j > best_j + 1e-12:
            best, best_j = float(cut), j
    return best


def sensitivity_cut(positives: np.ndarray, target: float) -> float | None:
    """The highest threshold whose sensitivity on the positives meets `target`.

    Highest, because every lower one is as sensitive and fires on more
    negatives. None without positives.
    """
    if positives.size == 0:
        return None
    # Rounded before ceiling for the same reason `suggest` rounds before flooring.
    required = int(np.ceil(round(target * positives.size, 9)))
    required = min(max(required, 1), positives.size)
    ranked = np.sort(positives)[::-1]
    return float(ranked[required - 1])


def auc(negatives: np.ndarray, positives: np.ndarray) -> float | None:
    """Area under the ROC curve: P(score of a positive > score of a negative).

    The Mann-Whitney form, ties counted as half, so it needs nothing beyond
    numpy and is the same quantity pROC reports for an empirical curve.
    """
    if negatives.size == 0 or positives.size == 0:
        return None
    combined = np.concatenate([negatives, positives])
    order = combined.argsort(kind="mergesort")
    ranks = np.empty(combined.size, dtype=np.float64)
    sorted_values = combined[order]
    position = 0
    while position < combined.size:
        end = position
        while end + 1 < combined.size and sorted_values[end + 1] == sorted_values[position]:
            end += 1
        ranks[order[position : end + 1]] = (position + end) / 2.0 + 1.0
        position = end + 1
    positive_ranks = ranks[negatives.size :].sum()
    u = positive_ranks - positives.size * (positives.size + 1) / 2.0
    return float(u / (positives.size * negatives.size))


def propose(
    method: str,
    negatives: np.ndarray,
    positives: np.ndarray,
    *,
    target_specificity: float,
    target_sensitivity: float,
) -> float | None:
    """The threshold `method` picks on this set."""
    if method == "youden":
        return youden(negatives, positives)
    if method == "sensitivity":
        return sensitivity_cut(positives, target_sensitivity)
    return suggest(negatives, positives, target_specificity)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--manifest", type=Path, help="CSV of `path,labels`, or a PadChest export"
    )
    source.add_argument(
        "--from-filenames", nargs="+", type=Path, metavar="IMAGE", help="label by filename"
    )
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=None,
        help="where a PadChest export's ImageID files live (default: next to the CSV)",
    )
    parser.add_argument("--onnx", type=Path, default=ROOT / "models" / "chester-all-224.onnx")
    parser.add_argument(
        "--target-specificity",
        type=float,
        default=0.90,
        help="specificity the suggested threshold must reach (default 0.90)",
    )
    parser.add_argument(
        "--method",
        choices=("specificity", "youden", "sensitivity"),
        default="specificity",
        help="how the suggested threshold is chosen (default specificity)",
    )
    parser.add_argument(
        "--target-sensitivity",
        type=float,
        default=0.90,
        help="sensitivity the suggestion must keep with --method sensitivity (default 0.90)",
    )
    parser.add_argument(
        "--reference-standard",
        choices=tuple(REFERENCE_STANDARDS),
        default="I",
        help="which graded labels count as positive: I only 4, II >=3, III >=2, "
        "IV >=1 (default I; a bare label is a 4, so ungraded sets are unaffected)",
    )
    parser.add_argument(
        "--max-fp-rate",
        type=float,
        default=None,
        help="exit non-zero if any output exceeds this fp_rate at its published "
        "point (published, not the deployment's -- see the note under the table)",
    )
    parser.add_argument(
        "--lenient", action="store_true", help="ignore labels with no matching output"
    )
    parser.add_argument("--json", type=Path, default=None, help="also write the table here")
    args = parser.parse_args()

    if not 0.0 < args.target_specificity < 1.0:
        raise SystemExit("--target-specificity must be between 0 and 1")
    if not 0.0 < args.target_sensitivity <= 1.0:
        raise SystemExit("--target-sensitivity must be above 0 and at most 1")
    cut = REFERENCE_STANDARDS[args.reference_standard]
    if not args.onnx.is_file():
        raise SystemExit(f"model artifact is missing: {args.onnx}")

    strict = not args.lenient
    if args.manifest and looks_like_padchest(args.manifest):
        images_dir = args.images_dir or args.manifest.parent
        print(f"reading {args.manifest.name} as a PadChest export\n", file=sys.stderr)
        rows = read_padchest(args.manifest, images_dir)
    elif args.manifest:
        rows = read_manifest(args.manifest, strict, cut)
    else:
        rows = read_filenames(args.from_filenames, strict)
    scores = score(rows, args.onnx)
    published = operating_points()
    deployed = deployed_points()
    factors = override_factors()
    suppressed = suppressed_indices()
    target = {
        "specificity": f"specificity >= {args.target_specificity:.0%}",
        "youden": "Youden's J",
        "sensitivity": f"sensitivity >= {args.target_sensitivity:.0%}",
    }[args.method]

    labelled_positive = sum(1 for exam in rows if exam.positives)
    print(
        f"{len(rows)} exams, {labelled_positive} carrying at least one finding, "
        f"reference standard RFS {args.reference_standard}, suggesting by {target}\n"
    )
    if len(rows) < MIN_NEGATIVES:
        print(
            f"  NOTE: {len(rows)} exams is far below the {MIN_NEGATIVES} negatives a rate\n"
            "  needs to mean anything. Read what follows as a smoke test, not a result.\n",
            file=sys.stderr,
        )

    header = (
        f"{'output':<27}{'pos':>4}{'neg':>5}{'exc':>4}{'published':>11}{'fp_rate':>9}"
        f"{'recall':>8}{'auc':>6}{'suggested':>12}{'fp':>7}{'recall':>8}{'J':>6}"
        f"{'bounds':>8}"
    )
    print(header)
    print("-" * len(header))

    table = []
    breached = []
    for index, name in enumerate(PATHOLOGIES):
        truth = np.array([name in exam.positives for exam in rows], dtype=bool)
        kept = np.array([name not in exam.excluded for exam in rows], dtype=bool)
        column = scores[:, index]
        negatives, positives = column[~truth & kept], column[truth]
        excluded_here = int((~kept).sum())

        fires_neg = int((negatives >= published[index]).sum())
        fires_pos = int((positives >= published[index]).sum())
        fp_rate = fires_neg / negatives.size if negatives.size else float("nan")
        recall = fires_pos / positives.size if positives.size else float("nan")

        proposal = propose(
            args.method,
            negatives,
            positives,
            target_specificity=args.target_specificity,
            target_sensitivity=args.target_sensitivity,
        )
        if proposal is None:
            new_fp = new_recall = float("nan")
        else:
            new_fp = float((negatives >= proposal).mean()) if negatives.size else float("nan")
            new_recall = (
                float((positives >= proposal).mean()) if positives.size else float("nan")
            )
        # nan propagates, so a row missing either class has no J.
        new_j = new_recall - new_fp
        area = auc(negatives, positives)

        # Inside what Settings would accept as an override of the point the
        # deployment runs: chester.thresholds.bounds, read from the source.
        in_bounds = None
        if index in suppressed:
            in_bounds = "suppressed"
        elif proposal is not None and deployed is not None and factors is not None:
            low, high = deployed[index] * factors[0], deployed[index] * factors[1]
            in_bounds = bool(low <= proposal <= high)

        thin = negatives.size < MIN_NEGATIVES
        print(
            f"{name:<27}{positives.size:>4}{negatives.size:>5}{excluded_here:>4}"
            f"{published[index]:>11.5f}"
            f"{fmt(fp_rate):>9}{fmt(recall):>8}"
            f"{fmt(float('nan') if area is None else area):>6}"
            f"{(f'{proposal:.5f}' if proposal else '--'):>12}"
            f"{fmt(new_fp):>7}{fmt(new_recall):>8}{fmt(new_j):>6}"
            f"{({True: 'ok', False: 'out', None: '--', 'suppressed': 'supp'}[in_bounds]):>8}"
            + ("  ~" if thin else "")
        )

        table.append(
            {
                "output": name,
                "index": index,
                "positives": int(positives.size),
                "negatives": int(negatives.size),
                "excluded": excluded_here,
                "published_threshold": float(published[index]),
                "fp_rate": None if np.isnan(fp_rate) else fp_rate,
                "recall": None if np.isnan(recall) else recall,
                "deployed_threshold": None if deployed is None else float(deployed[index]),
                "auc": area,
                "method": args.method,
                "suggested_threshold": proposal,
                "suggested_fp_rate": None if np.isnan(new_fp) else new_fp,
                "suggested_recall": None if np.isnan(new_recall) else new_recall,
                "suggested_specificity": None if np.isnan(new_fp) else 1.0 - new_fp,
                "suggested_youden_j": None if np.isnan(new_j) else new_j,
                "within_override_bounds": in_bounds,
                "below_minimum_negatives": bool(thin),
            }
        )
        if args.max_fp_rate is not None and not thin and fp_rate > args.max_fp_rate:
            breached.append((name, fp_rate))

    print("\n  exc = exams whose report bears on this finding without settling it;")
    print("      they are dropped from this row rather than counted as negatives.")
    print("  ~ = fewer than "
          f"{MIN_NEGATIVES} negatives; the rate on that row is arithmetic, not evidence")
    print("  fp_rate/recall are at the published operating point; fp, recall and J")
    print("  are what the suggested threshold would give on this same set -- the set")
    print("  it was chosen on, so they flatter it. Confirm on exams not used to pick it.")
    print("  bounds = ok when Settings would accept the suggestion as an override of")
    print("      the deployed point (chester.thresholds, 0.25x-4x); out when it would not;")
    print("      supp when the output is suppressed and has no override to take.")
    print("  published = the point that shipped with the weights. The deployment may")
    print("      run a different one: server/chester/inference.py decides that.")
    if deployed is not None:
        moved = [
            f"{name} {deployed[i] / published[i]:.3g}x"
            for i, name in enumerate(PATHOLOGIES)
            if not np.isclose(deployed[i], published[i], rtol=1e-6)
        ]
        if moved:
            print("      It currently runs " + ", ".join(moved) + " the published point.")

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "exams": len(rows),
                    "method": args.method,
                    "reference_standard": args.reference_standard,
                    "target_specificity": args.target_specificity,
                    "target_sensitivity": args.target_sensitivity,
                    "model": str(args.onnx),
                    "outputs": table,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")

    if breached:
        print(
            "\nover --max-fp-rate "
            f"{args.max_fp_rate:.2f}: " + ", ".join(f"{n} ({r:.2f})" for n, r in breached),
            file=sys.stderr,
        )
        return 1
    return 0


def fmt(value: float) -> str:
    return "--" if np.isnan(value) else f"{value:.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
