#!/usr/bin/env python3
"""Export the CHESTER classifier to ONNX from the torchxrayvision source weights.

The application's historical runtime was a TensorFlow.js GraphModel converted from
these same weights (see docs/onnx-parity.md). This script regenerates the ONNX
artifact the server consumes, so the model is reproducible rather than a binary of
unknown provenance.

Usage:
    pip install torch torchxrayvision onnx onnxscript
    python tools/export_onnx.py --out models/chester-all-224.onnx
"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch
import torch.nn as nn
import torchxrayvision as xrv

WEIGHTS = "densenet121-res224-all"
IMAGE_SIZE = 224


class ChesterNet(nn.Module):
    """The pure forward path: features -> classifier -> sigmoid.

    torchxrayvision's own ``forward`` also runs ``warn_normalization`` and
    ``fix_resolution``, research helpers that branch on tensor values and so cannot
    be traced. It additionally applies ``op_norm`` whenever ``op_threshs`` is set,
    which would bake operating-point normalization into the graph. The server needs
    raw sigmoid scores and applies its own normalization, so both are bypassed here.
    """

    def __init__(self, inner: nn.Module) -> None:
        super().__init__()
        self.inner = inner

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.inner.classifier(self.inner.features2(image)))


def export(out_path: Path) -> None:
    base = xrv.models.DenseNet(weights=WEIGHTS).eval()
    net = ChesterNet(base).eval()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        net,
        torch.zeros(1, 1, IMAGE_SIZE, IMAGE_SIZE, dtype=torch.float32),
        str(out_path),
        input_names=["image"],
        output_names=["scores"],
        dynamic_axes={"image": {0: "batch"}, "scores": {0: "batch"}},
        opset_version=17,
        do_constant_folding=True,
        dynamo=False,
    )
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"wrote {out_path} ({size_mb:.1f} MB)")
    print(f"pathologies: {list(base.pathologies)}")
    print(f"op_threshs:  {[float(v) for v in base.op_threshs]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("models/chester-all-224.onnx"),
        help="destination .onnx path",
    )
    export(parser.parse_args().out)


if __name__ == "__main__":
    main()
