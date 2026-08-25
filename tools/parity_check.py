#!/usr/bin/env python3
"""Verify the ONNX model reproduces the legacy TensorFlow.js GraphModel scores.

Both runtimes are fed the *same* preprocessed 224x224 tensor, bypassing each
library's own preprocessing, so this isolates model parity from preprocessing
parity. See docs/onnx-parity.md for the recorded result.

Usage:
    pip install numpy pillow onnxruntime
    npm install @tensorflow/tfjs        # only needed for --with-tfjs
    python tools/parity_check.py examples/*.png
    python tools/parity_check.py --with-tfjs examples/00000003_000-Hernia.png
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMAGE_SIZE = 224
IMAGE_SCALE = 1024.0
TOLERANCE = 1e-5

# Only 12 of the 18 outputs carry a label; the CHESTER config deliberately blanks
# Infiltration, Pneumothorax, Pneumonia, Nodule, Lung Lesion and Fracture.
LEGACY_CONFIG = ROOT / "models" / "xrv-all-45rot15trans15scale" / "config.json"


def load_grayscale(path: Path) -> np.ndarray:
    """Match the server's image decode: RGB then channel mean."""
    with Image.open(path) as img:
        rgb = np.array(img.convert("RGB"), dtype=np.float32)
    return rgb.mean(axis=2)


def preprocess(pixels: np.ndarray) -> np.ndarray:
    """Resize the short side to 224, centre-crop, scale to [-1024, 1024]."""
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


def tfjs_scores(tensors: dict[str, np.ndarray]) -> dict[str, list[float]]:
    """Run the legacy Node runtime over its stdin/stdout protocol."""
    runtime = ROOT / "scripts" / "chester_runtime.cjs"
    model_dir = ROOT / "models" / "xrv-all-45rot15trans15scale"
    if not runtime.is_file() or not model_dir.is_dir():
        raise SystemExit("legacy tfjs runtime or model directory is missing")

    process = subprocess.Popen(
        ["node", str(runtime), str(model_dir)],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, bufsize=1,
    )
    assert process.stdin and process.stdout
    ready = json.loads(process.stdout.readline())
    if ready.get("type") != "ready":
        raise SystemExit(f"tfjs runtime failed to start: {ready}")

    out: dict[str, list[float]] = {}
    for name, tensor in tensors.items():
        payload = {"id": name, "pixels": tensor.reshape(-1).tolist()}
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        message = json.loads(process.stdout.readline())
        if message.get("error"):
            raise SystemExit(f"tfjs inference failed for {name}: {message['error']}")
        out[name] = message["scores"]
    process.stdin.close()
    process.wait(timeout=10)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--onnx", type=Path, default=ROOT / "models" / "chester-all-224.onnx")
    parser.add_argument("--with-tfjs", action="store_true",
                        help="also run the legacy Node runtime and compare")
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    args = parser.parse_args()

    tensors = {str(p): preprocess(load_grayscale(p)) for p in args.images}
    session = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])

    onnx_out = {
        name: session.run(["scores"], {"image": t.reshape(1, 1, IMAGE_SIZE, IMAGE_SIZE)})[0]
        .reshape(-1)
        .astype(np.float64)
        for name, t in tensors.items()
    }

    if not args.with_tfjs:
        for name, scores in onnx_out.items():
            top = np.argsort(scores)[::-1][:3]
            summary = ", ".join(f"{i}:{scores[i]:.4f}" for i in top)
            print(f"{Path(name).name:<46} top3 -> {summary}")
        return 0

    labels = json.loads(LEGACY_CONFIG.read_text())["LABELS"]
    legacy = tfjs_scores(tensors)
    worst = 0.0
    for name in tensors:
        delta = np.abs(onnx_out[name] - np.asarray(legacy[name], dtype=np.float64))
        worst = max(worst, float(delta.max()))
        print(f"{Path(name).name:<46} delta_max={delta.max():.3e}")
        for index in np.argsort(delta)[::-1][:3]:
            label = labels[index] or "(suppressed)"
            print(f"    [{index:>2}] {label:<24} "
                  f"onnx={onnx_out[name][index]:.6f} tfjs={legacy[name][index]:.6f} "
                  f"d={delta[index]:.2e}")

    print(f"\nworst delta across all outputs: {worst:.3e} (tolerance {args.tolerance:.1e})")
    if worst > args.tolerance:
        print("PARITY FAILED", file=sys.stderr)
        return 1
    print("PARITY OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
