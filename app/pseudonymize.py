"""Patient ID pseudonymization using HMAC-SHA256."""
from __future__ import annotations

import hashlib
import hmac

from app.config import settings


def pseudonymize_patient_id(raw_id: str) -> str:
    """
    Return HMAC-SHA256 pseudonym for a patient ID.
    Never stores original patient name or ID.
    """
    if not raw_id:
        return ""
    key = settings.session_secret.encode()
    digest = hmac.new(key, raw_id.encode(), hashlib.sha256).hexdigest()
    return f"P-{digest[:16].upper()}"
