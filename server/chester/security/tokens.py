"""Session and one-time-code token handling.

Only HMACs are persisted. A database disclosure therefore does not hand out live
sessions or usable codes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from chester.config import settings

SESSION_TOKEN_BYTES = 48
OTP_DIGITS = 6


def new_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    return hmac.new(
        settings.session_secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def new_otp_code() -> str:
    return f"{secrets.randbelow(10**OTP_DIGITS):0{OTP_DIGITS}d}"


def hash_otp_code(email: str, code: str) -> str:
    """Bind the code to the address so a code cannot be replayed for another user."""
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        f"{email}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def tokens_equal(candidate: str, expected: str) -> bool:
    """Constant-time comparison.

    Always compare through this helper. A plain `==` short-circuits on the first
    differing byte and leaks the length of the matching prefix through timing; the
    previous implementation had exactly that fast path in front of its
    constant-time comparison, which defeated the point of it.
    """
    return hmac.compare_digest(candidate, expected)


def unmatchable_code_hash() -> str:
    """A hash no submitted code can reproduce.

    Lets an unauthorized sign-in request take exactly the same code path, and
    occupy the same rate-limit slot, as an authorized one without ever being
    verifiable.
    """
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        secrets.token_bytes(32),
        hashlib.sha256,
    ).hexdigest()
