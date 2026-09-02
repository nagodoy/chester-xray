# ONNX parity with the legacy TensorFlow.js runtime

**Date:** 25 August 2026
**Verdict:** parity confirmed. The Python/ONNX runtime reproduces the TensorFlow.js
GraphModel to within float32 noise, so the Node subprocess can be retired.

## Why this was checked

The application ran inference by spawning a Node process that loaded a TensorFlow.js
GraphModel (`models/xrv-all-45rot15trans15scale`) and exchanged line-delimited JSON
over stdin/stdout, sending each image as a JSON array of ~50,000 floats. That shape
is inherited from CHESTER having started as a browser demo, not from a server design.

Moving inference into Python removes the Node dependency, the JSON serialization of
pixel data, the subprocess lifecycle and the single-flight lock. That is only safe if
the replacement produces the same numbers.

## Evidence of shared lineage

`docs/torchxrayvision-comparison.md` recorded that shared lineage with
torchxrayvision's `densenet121-res224-all` was *probable but unproven*. Two facts
settle it:

1. The 18 `OP_POINT` values in
   `models/xrv-all-45rot15trans15scale/config.json` match `model.op_threshs` from
   `densenet121-res224-all` **to nine decimal places**.
2. The 12 populated entries of the config's `LABELS` sit at exactly the canonical
   torchxrayvision indices. The six blanks are positions 2, 3, 8, 11, 14 and 15 —
   Infiltration, Pneumothorax, Pneumonia, Nodule, Lung Lesion and Fracture. They are
   deliberate suppressions, not a different label set.

This describes the config file as it is vendored. It is not the deployment's
current reported set: four of those six -- 2, 3, 8 and 14 -- are reported again,
11 (Nodule) and 15 (Fracture) are still withheld, and index 6, Fibrosis, was added
to the withheld set. `server/chester/inference.py` is the authority on what this
node surfaces.

## Method

Model parity and preprocessing parity are separate questions, so they were separated.
Both runtimes were fed the **same** 224x224 tensor, built once in Python by
replicating the server's preprocessing (short side resized to 224, centre crop,
scaled to `[-1024, 1024]`). Neither library's own preprocessing was used.

One correction was needed to make the comparison meaningful. torchxrayvision's
`DenseNet.forward` applies `op_norm` whenever `op_threshs` is set, so a naive
comparison measures raw sigmoid against operating-point-normalized output and shows a
spurious delta of ~0.5, with scores clustered near 0.5. Setting `op_threshs = None`
yields the raw sigmoid the TensorFlow.js graph emits.

Worth noting for the port: `op_norm` is the same piecewise map the server implements
in `run_inference`, minus the `SCALE_UPPER = 1.3` boost applied above 0.6. The server
keeps its own normalization; the exported graph emits raw sigmoid only.

## Result

Four images from `examples/`, all 18 outputs each:

| Comparison | Worst absolute delta |
|---|---|
| torchxrayvision (raw sigmoid) vs TensorFlow.js | 1.79e-07 |
| ONNX Runtime vs TensorFlow.js | 2.68e-07 |

Both are float32 rounding noise against a 1e-5 tolerance. The models are numerically
the same.

| | TensorFlow.js | ONNX |
|---|---|---|
| Artifact size | 41 MB across 12 files | 26 MB, one file |
| Runtime dependency | Node + `@tensorflow/tfjs` | `onnxruntime` |
| Latency (CPU, single image) | — | 84 ms |

## Reproducing

```bash
pip install torch torchxrayvision onnx onnxscript onnxruntime numpy pillow
python tools/export_onnx.py --out models/chester-all-224.onnx

npm install @tensorflow/tfjs          # only for the --with-tfjs comparison
python tools/parity_check.py --with-tfjs examples/*.png
```

`tools/export_onnx.py` exports the pure forward path (`features2 -> classifier ->
sigmoid`), bypassing `warn_normalization` and `fix_resolution`. Those are research
helpers that branch on tensor values and cannot be traced; they are not part of the
model's arithmetic.

torch is a **build-time** dependency only. The server needs `onnxruntime`, which is
roughly 15 MB against torch's ~900 MB.
