"""Patient identifier pseudonymization.

Keyed by PSEUDONYM_SECRET rather than SESSION_SECRET. The previous implementation
shared the session secret, which meant rotating it -- a thing you must be able to do
-- silently changed every future pseudonym, so the same patient stopped mapping to
the same identifier.
"""

from __future__ import annotations

import hashlib
import hmac

from chester.config import settings

PSEUDONYM_PREFIX = "P-"


def pseudonymize_patient_id(raw_id: str) -> str:
    """Return a stable pseudonym, or empty string when there is nothing to hash."""
    if not raw_id:
        return ""
    digest = hmac.new(
        settings.pseudonym_secret.encode("utf-8"),
        raw_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{PSEUDONYM_PREFIX}{digest[:16].upper()}"
