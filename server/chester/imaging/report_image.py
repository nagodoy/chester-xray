"""Render the two-panel sheet that becomes the TORAX IA secondary capture.

The radiograph on top, the identification cell and the findings table beneath
it, on one RGB canvas. Everything the reader needs is on the picture itself,
because a secondary capture is often all that reaches a viewer -- the private
tags carry the same values, but nothing guarantees they are shown.
"""

from __future__ import annotations

import io

import numpy as np

# The picture keeps seven parts to the table's three, matching the study
# detail screen, so the sheet and the application read the same way.
IMAGE_SHARE = 7 / 3

WIDTH = 1240
MARGIN = 28
HEADER_HEIGHT = 74
IDENTITY_HEIGHT = 118
TABLE_HEADER_HEIGHT = 46
ROW_HEIGHT = 40

BACKGROUND = (8, 12, 22)
PANEL = (17, 26, 46)
LINE = (51, 65, 85)
INK = (248, 250, 252)
INK_SOFT = (148, 163, 184)
TEAL = (45, 212, 191)

CONFIDENCE_COLOURS = {
    "ABSENT": (148, 163, 184),
    "DOUBT": (251, 191, 36),
    "CONFIDENT": (248, 113, 113),
}

# Whatever the host has. The sheet must still render on a machine with no
# fonts installed, so the bitmap default is the last resort rather than an
# error.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
)
BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
)


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    for path in BOLD_CANDIDATES if bold else FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(draw, text: str, font, available: int) -> str:
    """Shorten with an ellipsis until it fits.

    The identity cell divides its width evenly, and a long patient name would
    otherwise run straight over the accession number beside it.
    """
    if draw.textlength(text, font=font) <= available:
        return text
    trimmed = text
    while trimmed and draw.textlength(trimmed + "\u2026", font=font) > available:
        trimmed = trimmed[:-1]
    return (trimmed + "\u2026") if trimmed else ""


def _to_grayscale_image(pixels: np.ndarray):
    """Window a rendered frame to 8-bit, the way the thumbnail does."""
    from chester.imaging.dicom import to_pil_image

    return to_pil_image(pixels)


def render_report(
    pixels: np.ndarray,
    *,
    patient_name: str,
    accession_number: str,
    study_date: str,
    rows: list[dict],
    title: str = "TORAX IA",
) -> bytes:
    """Draw the sheet and return it as PNG bytes."""
    from PIL import Image, ImageDraw

    table_height = TABLE_HEADER_HEIGHT + ROW_HEIGHT * max(len(rows), 1) + MARGIN
    bottom_height = IDENTITY_HEIGHT + table_height + MARGIN
    image_height = int(round(bottom_height * IMAGE_SHARE))
    height = HEADER_HEIGHT + image_height + bottom_height

    canvas = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)

    title_font = _font(30, bold=True)
    label_font = _font(17)
    value_font = _font(21, bold=True)
    head_font = _font(18, bold=True)
    cell_font = _font(20)

    draw.text((MARGIN, 24), title, font=title_font, fill=TEAL)
    draw.line(
        [(MARGIN, HEADER_HEIGHT - 1), (WIDTH - MARGIN, HEADER_HEIGHT - 1)], fill=LINE, width=1
    )

    # --- the radiograph, fitted rather than stretched -----------------------
    frame = (MARGIN, HEADER_HEIGHT, WIDTH - MARGIN, HEADER_HEIGHT + image_height - MARGIN)
    draw.rectangle(frame, fill=(0, 0, 0), outline=LINE)
    box_width = frame[2] - frame[0] - 2
    box_height = frame[3] - frame[1] - 2
    picture = _to_grayscale_image(pixels).convert("RGB")
    picture.thumbnail((box_width, box_height), Image.LANCZOS)
    canvas.paste(
        picture,
        (
            frame[0] + 1 + (box_width - picture.width) // 2,
            frame[1] + 1 + (box_height - picture.height) // 2,
        ),
    )

    # --- who and when -------------------------------------------------------
    top = HEADER_HEIGHT + image_height
    cell = (MARGIN, top, WIDTH - MARGIN, top + IDENTITY_HEIGHT - 14)
    draw.rectangle(cell, fill=PANEL, outline=LINE)
    columns = [
        ("PACIENTE", patient_name or "-"),
        ("ACCESSION NUMBER", accession_number or "-"),
        ("DATA DO EXAME", study_date or "-"),
    ]
    column_width = (cell[2] - cell[0]) // len(columns)
    for index, (label, value) in enumerate(columns):
        x = cell[0] + 20 + index * column_width
        draw.text((x, cell[1] + 22), label, font=label_font, fill=INK_SOFT)
        draw.text(
            (x, cell[1] + 52),
            _fit_text(draw, str(value), value_font, column_width - 30),
            font=value_font,
            fill=INK,
        )

    # --- the findings -------------------------------------------------------
    top = cell[3] + 18
    achado_x, score_x, confidence_x = MARGIN + 20, WIDTH - 520, WIDTH - 300
    draw.text((achado_x, top + 12), "ACHADO", font=head_font, fill=INK_SOFT)
    draw.text((score_x, top + 12), "SCORE", font=head_font, fill=INK_SOFT)
    draw.text((confidence_x, top + 12), "CONFIANÇA", font=head_font, fill=INK_SOFT)
    line_y = top + TABLE_HEADER_HEIGHT - 6
    draw.line([(MARGIN, line_y), (WIDTH - MARGIN, line_y)], fill=LINE, width=1)

    for index, row in enumerate(rows):
        y = line_y + 10 + index * ROW_HEIGHT
        draw.text(
            (achado_x, y),
            _fit_text(draw, str(row["pathology"]), cell_font, score_x - achado_x - 20),
            font=cell_font,
            fill=INK,
        )
        draw.text((score_x, y), f"{row['score']:.3f}", font=cell_font, fill=INK_SOFT)
        draw.text(
            (confidence_x, y),
            row["confidence"],
            font=cell_font,
            fill=CONFIDENCE_COLOURS.get(row["confidence"], INK),
        )
        if index < len(rows) - 1:
            rule = y + ROW_HEIGHT - 8
            draw.line([(MARGIN, rule), (WIDTH - MARGIN, rule)], fill=(30, 41, 59), width=1)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()
