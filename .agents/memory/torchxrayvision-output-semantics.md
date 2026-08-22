---
name: TorchXRayVision output semantics
description: How to preserve raw sigmoid and operating-point-normalized outputs from weighted XRV DenseNet models.
---

Do not apply sigmoid to the output of a weighted TorchXRayVision DenseNet's
public `forward` method. That output has already passed through sigmoid and
operating-point normalization when the model includes `op_threshs`.

**Why:** Applying sigmoid again silently compresses and corrupts both raw and
normalized pathology scores. A simple finite-output test will not catch this
because the incorrect values still look numerically valid.

**How to apply:** When both representations are required, obtain classifier
logits through the model's feature extractor and classifier, apply sigmoid
exactly once for raw scores, then apply the library's operating-point mapping
once. Keep a contract test that would fail if normalized forward output were
fed through sigmoid again, and validate model upgrades with real weights.