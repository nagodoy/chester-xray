---
name: Cornerstone DICOM runtime
description: Browser-worker packaging and metadata edge cases in Cornerstone WADO Image Loader.
---

Use real-browser fixtures whenever the Cornerstone WADO Image Loader or its codec packages change. Do not infer runtime asset behavior from source filenames alone.

**Why:** In version 4.13.2, the normal production bundle embeds its Blob worker and codec WASM data, while a separately emitted worker also exists. The same version's image-plane metadata provider throws when a valid study omits the optional Modality tag, leaving the image-load promise pending rather than rejecting it.

**How to apply:** Exercise RLE, JPEG, JPEG-LS, JPEG 2000, missing-Modality, and multi-frame files through the exact production scripts in Chromium. Keep fallback worker/codec assets self-contained and scope any metadata compatibility provider to one local image ID.