#!/usr/bin/env python3
"""Export the chest anatomy segmenter that chester.topography reads lungs from.

The source is torchxrayvision's ChestX-Det PSPNet (Lian et al., IEEE TMI 2021),
which segments fourteen structures. Three of them are exported -- the right lung,
the left lung and the heart, in the patient's sides -- because they are all the
topography needs, and a smaller output is less to move per study.

The float32 graph is about 270 MB, past what GitHub will take as a file. It is
written with int8 weights instead (dynamic quantization) and checked against the
float32 graph mask for mask on the reference images in examples/: the intersection
over union per structure is printed, and the export fails if a lung falls under
--min-iou or the heart under --min-heart-iou. The heart's bar is lower because
all chester.topography takes from it is which side of the image its centre is
on. The figures are recorded in docs/onnx-parity.md.

Usage, from the repository root:

    pip install -e "server[export]"
    python tools/export_segmentation.py

Writes models/chest-segmentation-512-int8.onnx and models/chest-segmentation-512.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SIZE = 512
STRUCTURES = ("Right Lung", "Left Lung", "Heart")
THRESHOLD = 0.5
WEIGHTS = (
    "https://github.com/mlmed/torchxrayvision/releases/download/v1/"
    "pspnet_chestxray_best_model_4.pth"
)


def build_wrapper():
    """The PSPNet with its preprocessing fixed in the graph and three outputs kept."""
    import torch
    import torchxrayvision as xrv

    source = xrv.baseline_models.chestx_det.PSPNet()
    indices = [source.targets.index(name) for name in STRUCTURES]

    class Segmenter(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.model = source.model
            self.register_buffer("mean", torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
            self.register_buffer("std", torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))
            self.indices = indices

        def forward(self, image):
            # The same steps as PSPNet.forward, minus the resize and the warning,
            # which the caller has already made unnecessary.
            x = image.repeat(1, 3, 1, 1)
            x = (x + 1024.0) / 2048.0
            x = (x - self.mean) / self.std
            logits = self.model(x)
            return torch.sigmoid(logits[:, self.indices])

    return Segmenter().eval(), source.targets


def reference_inputs() -> list[tuple[str, np.ndarray]]:
    """Every image in examples/, prepared the way the server prepares the classifier's."""
    sys.path.insert(0, str(ROOT / "tools"))
    from calibrate_thresholds import load_grayscale, preprocess
    from PIL import Image

    out = []
    for path in sorted((ROOT / "examples").iterdir()):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        square = preprocess(load_grayscale(path))  # 224, in [-1024, 1024]
        resized = Image.fromarray(square.astype(np.float32)).resize((SIZE, SIZE), Image.BILINEAR)
        out.append((path.name, np.asarray(resized, dtype=np.float32).reshape(1, 1, SIZE, SIZE)))
    return out


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return 1.0 if union == 0 else float(np.logical_and(a, b).sum() / union)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "models" / "chest-segmentation-512-int8.onnx")
    parser.add_argument("--min-iou", type=float, default=0.95, help="for each lung")
    parser.add_argument("--min-heart-iou", type=float, default=0.90)
    args = parser.parse_args()

    import onnxruntime as ort
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic

    wrapper, targets = build_wrapper()

    with tempfile.TemporaryDirectory() as scratch:
        full = Path(scratch) / "segmentation-fp32.onnx"
        torch.onnx.export(
            wrapper,
            torch.zeros(1, 1, SIZE, SIZE),
            str(full),
            input_names=["image"],
            output_names=["masks"],
            opset_version=17,
            dynamo=False,
        )
        quantize_dynamic(str(full), str(args.out), weight_type=QuantType.QInt8)

        reference = ort.InferenceSession(str(full), providers=["CPUExecutionProvider"])
        quantized = ort.InferenceSession(str(args.out), providers=["CPUExecutionProvider"])

        worst = {name: 1.0 for name in STRUCTURES}
        for name, tensor in reference_inputs():
            a = reference.run(["masks"], {"image": tensor})[0][0] > THRESHOLD
            b = quantized.run(["masks"], {"image": tensor})[0][0] > THRESHOLD
            scores = [iou(a[i], b[i]) for i in range(len(STRUCTURES))]
            for structure, value in zip(STRUCTURES, scores, strict=True):
                worst[structure] = min(worst[structure], value)
            print(f"{name:<55}" + "  ".join(f"{s}={v:.3f}" for s, v in zip(STRUCTURES, scores, strict=True)))

    size_mb = args.out.stat().st_size / 1e6
    print(f"\nwrote {args.out} ({size_mb:.1f} MB); worst IoU int8 vs fp32: {worst}")

    sidecar = args.out.with_name("chest-segmentation-512.json")
    sidecar.write_text(
        json.dumps(
            {
                "source": "torchxrayvision ChestX-Det PSPNet",
                "weights": WEIGHTS,
                "all_targets": list(targets),
                "outputs": list(STRUCTURES),
                "input": {"name": "image", "shape": [1, 1, SIZE, SIZE], "range": [-1024, 1024]},
                "output": {"name": "masks", "activation": "sigmoid", "threshold": THRESHOLD},
                "quantization": "dynamic int8 weights (onnxruntime.quantization.quantize_dynamic)",
                "worst_iou_int8_vs_fp32_on_examples": worst,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    lungs = min(worst["Right Lung"], worst["Left Lung"])
    if lungs < args.min_iou or worst["Heart"] < args.min_heart_iou:
        print("IoU under the minimum; the int8 graph does not match", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
