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
  suggested the lowest threshold meeting --target-specificity on this set, and
            the recall that threshold costs

The suggestion is a starting point for a radiologist to judge, not a value to
paste into the deployment. Whether an output is reported at all is decided in
`server/chester/inference.py`; this tool never writes there.

Usage:
    pip install numpy pillow onnxruntime

    # a set you have labelled: CSV of `path,labels` with ; between labels,
    # an empty labels field meaning the report found nothing
    python tools/calibrate_thresholds.py --manifest exams.csv

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
    """The published thresholds, read from the vendored config."""
    return np.asarray(json.loads(LEGACY_CONFIG.read_text())["OP_POINT"], dtype=np.float64)


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


def read_manifest(path: Path, strict: bool) -> list[Exam]:
    """CSV of `path,labels`, labels separated by ';'. Empty means no finding.

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
            labels = resolve_labels(raw, f"{path}:{line_number}", strict)
            rows.append(Exam(image, frozenset(labels)))
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
        "--max-fp-rate",
        type=float,
        default=None,
        help="exit non-zero if any output exceeds this fp_rate at its published point",
    )
    parser.add_argument(
        "--lenient", action="store_true", help="ignore labels with no matching output"
    )
    parser.add_argument("--json", type=Path, default=None, help="also write the table here")
    args = parser.parse_args()

    if not 0.0 < args.target_specificity < 1.0:
        raise SystemExit("--target-specificity must be between 0 and 1")
    if not args.onnx.is_file():
        raise SystemExit(f"model artifact is missing: {args.onnx}")

    strict = not args.lenient
    if args.manifest and looks_like_padchest(args.manifest):
        images_dir = args.images_dir or args.manifest.parent
        print(f"reading {args.manifest.name} as a PadChest export\n", file=sys.stderr)
        rows = read_padchest(args.manifest, images_dir)
    elif args.manifest:
        rows = read_manifest(args.manifest, strict)
    else:
        rows = read_filenames(args.from_filenames, strict)
    scores = score(rows, args.onnx)
    published = operating_points()

    labelled_positive = sum(1 for exam in rows if exam.positives)
    print(
        f"{len(rows)} exams, {labelled_positive} carrying at least one finding, "
        f"target specificity {args.target_specificity:.0%}\n"
    )
    if len(rows) < MIN_NEGATIVES:
        print(
            f"  NOTE: {len(rows)} exams is far below the {MIN_NEGATIVES} negatives a rate\n"
            "  needs to mean anything. Read what follows as a smoke test, not a result.\n",
            file=sys.stderr,
        )

    header = (
        f"{'output':<27}{'pos':>4}{'neg':>5}{'exc':>4}{'published':>11}{'fp_rate':>9}"
        f"{'recall':>8}{'suggested':>12}{'fp':>7}{'recall':>8}"
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

        proposal = suggest(negatives, positives, args.target_specificity)
        if proposal is None:
            new_fp = new_recall = float("nan")
        else:
            new_fp = float((negatives >= proposal).mean()) if negatives.size else float("nan")
            new_recall = (
                float((positives >= proposal).mean()) if positives.size else float("nan")
            )

        thin = negatives.size < MIN_NEGATIVES
        print(
            f"{name:<27}{positives.size:>4}{negatives.size:>5}{excluded_here:>4}"
            f"{published[index]:>11.5f}"
            f"{fmt(fp_rate):>9}{fmt(recall):>8}"
            f"{(f'{proposal:.5f}' if proposal else '--'):>12}"
            f"{fmt(new_fp):>7}{fmt(new_recall):>8}"
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
                "suggested_threshold": proposal,
                "suggested_fp_rate": None if np.isnan(new_fp) else new_fp,
                "suggested_recall": None if np.isnan(new_recall) else new_recall,
                "below_minimum_negatives": bool(thin),
            }
        )
        if args.max_fp_rate is not None and not thin and fp_rate > args.max_fp_rate:
            breached.append((name, fp_rate))

    print("\n  exc = exams whose report bears on this finding without settling it;")
    print("      they are dropped from this row rather than counted as negatives.")
    print("  ~ = fewer than "
          f"{MIN_NEGATIVES} negatives; the rate on that row is arithmetic, not evidence")
    print("  fp_rate/recall are at the published operating point; the last two columns")
    print("  are what the suggested threshold would give on this same set.")

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "exams": len(rows),
                    "target_specificity": args.target_specificity,
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
