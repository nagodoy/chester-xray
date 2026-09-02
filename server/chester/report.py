"""Turning an analysis into the report the reader sees, and its DICOM form.

Three things live here because they have to agree with each other: how a score
becomes a confidence word, what the rendered sheet says, and what the private
DICOM tags carry. A viewer that reads the tags and a person who reads the
picture must not be told different things.
"""

from __future__ import annotations

from chester.inference import REPORTED_PATHOLOGIES

CONFIDENCE_ABSENT = "ABSENT"
CONFIDENCE_DOUBT = "DOUBT"
CONFIDENCE_CONFIDENT = "CONFIDENT"

# The band around the operating point, as a fraction of it, inside which the
# score is not called either way.
DOUBT_BAND = 0.10


def classify_confidence(score: float, threshold: float) -> str:
    """ABSENT below the operating point, CONFIDENT above, DOUBT either side of it.

    The band is checked first and deliberately straddles the threshold: a score
    a hair under the operating point is no more decidable than one a hair over,
    so calling the first ABSENT and the second CONFIDENT would read as a
    certainty the model does not have.

    The band is relative to the operating point, which is where the model's
    own uncertainty is, rather than to the score being judged.
    """
    if threshold <= 0:
        # No operating point to be near, so the only honest split is over/under.
        return CONFIDENCE_CONFIDENT if score > threshold else CONFIDENCE_ABSENT
    if abs(score - threshold) <= DOUBT_BAND * threshold:
        return CONFIDENCE_DOUBT
    return CONFIDENCE_ABSENT if score < threshold else CONFIDENCE_CONFIDENT


def dicom_code_meaning(pathology: str) -> str:
    """The finding name as the private tags spell it: upper case, unspaced."""
    return pathology.upper().replace(" ", "").replace("-", "")


def finding_rows(result) -> list[dict]:
    """One row per reported pathology, in the model's own order.

    Every finding is listed, not only the ones over their operating point: a
    report that showed only positives would leave the reader unable to tell a
    negative from something the model never looked at.

    The order comes from REPORTED_PATHOLOGIES rather than from the stored
    document's own keys. Those keys are not in the order they were written:
    PostgreSQL orders JSONB object keys by length and then bytewise, so reading
    the document back gave Mass, Edema, Hernia, Effusion -- shortest name first.
    That reordering reached the sheet a radiologist reads and the DICOM tags sent
    to a PACS, which is a storage detail deciding how a clinical artefact is laid
    out. On SQLite the keys came back in insertion order and the bug was
    invisible.

    Filtering rather than trusting the document also covers suppression: a result
    recorded before an output was withdrawn still carries it, and a study
    analysed last week must not keep printing a finding this deployment no longer
    stands behind.
    """
    raw = result.raw_scores or {}
    thresholds = result.thresholds or {}
    normalized = result.op_normalized_scores or {}
    return [
        {
            "pathology": pathology,
            "code_meaning": dicom_code_meaning(pathology),
            "score": float(raw[pathology]),
            "threshold": float(thresholds.get(pathology, 0.0)),
            "normalized": float(normalized.get(pathology, 0.0)),
            "confidence": classify_confidence(
                float(raw[pathology]), float(thresholds.get(pathology, 0.0))
            ),
        }
        # A result carries only the outputs that ran, so a name the document does
        # not have is skipped rather than reported as a score of zero.
        for pathology in REPORTED_PATHOLOGIES
        if pathology in raw
    ]
