"""Rendering a stored instance back to pixels.

Two paths, because two kinds of file are accepted: a DICOM goes through the
modality LUT and VOI windowing that render_frame applies, and a plain PNG or
JPEG is read as grayscale. Anything working from bytes already in storage --
rebuilding a thumbnail, drawing a report -- needs the same choice made the
same way, so it is made here once.
"""

from __future__ import annotations

import io

DICOM_CONTENT_TYPE = "application/dicom"


def pixels_from_stored(data: bytes, content_type: str | None):
    """Return the frame a stored instance renders to, as a float array."""
    import numpy as np
    from PIL import Image

    from chester.imaging.dicom import parse_dicom_bytes, render_frame

    if (content_type or "").startswith(DICOM_CONTENT_TYPE):
        return render_frame(parse_dicom_bytes(data), frame_index=0)
    with Image.open(io.BytesIO(data)) as image:
        return np.array(image.convert("L"), dtype=np.float32)
