"""Render the two-panel sheet that becomes the ANALISADA secondary capture.

The radiograph on top, the identification cell and the findings table beneath
it, on one RGB canvas. Everything the reader needs is on the picture itself,
because a secondary capture is often all that reaches a viewer -- the private
tags carry the same values, but nothing guarantees they are shown.
"""

from __future__ import annotations

import io

import numpy as np

from chester.report import SIGNAL_ABOVE, SIGNAL_BELOW, SIGNAL_BORDERLINE

# The sheet used to reserve the picture seven parts to the table's three,
# regardless of the radiograph's own shape. A chest film is roughly square and
# the reserved frame was portrait, so the picture was centred in it and the
# leftover became black: on a 1024x1024 frontal, 336px of it under the picture
# and 336px above, a seventh of the sheet spent on nothing. The frame is now cut
# to the picture, and IMAGE_GAP alone separates it from the identification cell.
IMAGE_GAP = 16

# The radiograph is drawn at three quarters of the size it would otherwise take.
IMAGE_SCALE = 0.75

# A ceiling for a very tall portrait film, so one cannot stretch the sheet
# without limit now that the height follows the picture. Applied before
# IMAGE_SCALE, and it never enlarges anything: a small image stays small.
MAX_IMAGE_HEIGHT = 1600

WIDTH = 1240
MARGIN = 28
HEADER_HEIGHT = 74
IDENTITY_HEIGHT = 130
# The table carries the finding, so it is set larger than anything else on the
# sheet: a secondary capture is read on a viewer at whatever zoom the reader
# happens to be at, often reduced to fit the pane.
TABLE_HEADER_HEIGHT = 62
ROW_HEIGHT = 56

BACKGROUND = (8, 12, 22)
PANEL = (17, 26, 46)
LINE = (51, 65, 85)
INK = (248, 250, 252)
INK_SOFT = (148, 163, 184)
TEAL = (45, 212, 191)

# Keyed off the constants rather than the words themselves. Written out by hand,
# a change to the wording would leave every key unmatched, and the .get default
# below would quietly render the whole column in plain ink -- the colour coding
# gone with nothing raised.
CONFIDENCE_COLOURS = {
    SIGNAL_BELOW: (148, 163, 184),
    SIGNAL_BORDERLINE: (251, 191, 36),
    SIGNAL_ABOVE: (248, 113, 113),
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
    title: str = "ANALISADA",
) -> bytes:
    """Draw the sheet and return it as PNG bytes."""
    from PIL import Image, ImageDraw

    # The picture is measured before the canvas exists, because the sheet's
    # height follows it. Sizing the canvas first is what produced the black
    # letterbox this replaced.
    picture = _to_grayscale_image(pixels).convert("RGB")
    content_width = WIDTH - 2 * MARGIN - 2
    picture.thumbnail((content_width, MAX_IMAGE_HEIGHT), Image.LANCZOS)
    picture = picture.resize(
        (
            max(1, int(round(picture.width * IMAGE_SCALE))),
            max(1, int(round(picture.height * IMAGE_SCALE))),
        ),
        Image.LANCZOS,
    )

    table_height = TABLE_HEADER_HEIGHT + ROW_HEIGHT * max(len(rows), 1) + MARGIN
    bottom_height = IDENTITY_HEIGHT + table_height + MARGIN
    # +2 for the frame's one-pixel outline on each side.
    image_height = picture.height + 2
    height = HEADER_HEIGHT + image_height + IMAGE_GAP + bottom_height

    canvas = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)

    title_font = _font(30, bold=True)
    label_font = _font(20)
    value_font = _font(26, bold=True)
    head_font = _font(26, bold=True)
    cell_font = _font(30)

    draw.text((MARGIN, 24), title, font=title_font, fill=TEAL)
    draw.line(
        [(MARGIN, HEADER_HEIGHT - 1), (WIDTH - MARGIN, HEADER_HEIGHT - 1)], fill=LINE, width=1
    )

    # --- the radiograph, in a frame cut to it -------------------------------
    # Centred across the sheet, but only horizontally: the frame is exactly as
    # tall as the picture, so there is nothing to centre it against vertically.
    left = (WIDTH - picture.width) // 2
    frame = (left - 1, HEADER_HEIGHT, left + picture.width, HEADER_HEIGHT + picture.height + 1)
    draw.rectangle(frame, fill=(0, 0, 0), outline=LINE)
    canvas.paste(picture, (left, HEADER_HEIGHT + 1))

    # --- who and when -------------------------------------------------------
    top = HEADER_HEIGHT + image_height + IMAGE_GAP
    cell = (MARGIN, top, WIDTH - MARGIN, top + IDENTITY_HEIGHT - 14)
    draw.rectangle(cell, fill=PANEL, outline=LINE)
    # Weighted rather than in equal thirds: a patient name is far longer than an
    # accession number or a date, and at this size equal columns truncated names
    # that used to fit.
    columns = [
        ("PACIENTE", patient_name or "-", 0.5),
        ("ACCESSION NUMBER", accession_number or "-", 0.25),
        ("DATA DO EXAME", study_date or "-", 0.25),
    ]
    available = cell[2] - cell[0]
    x = cell[0] + 20
    for label, value, share in columns:
        column_width = int(available * share)
        draw.text((x, cell[1] + 24), label, font=label_font, fill=INK_SOFT)
        draw.text(
            (x, cell[1] + 58),
            _fit_text(draw, str(value), value_font, column_width - 30),
            font=value_font,
            fill=INK,
        )
        x += column_width

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
