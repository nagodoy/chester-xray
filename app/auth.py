"""Clerk authentication helpers for FastAPI."""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from app.config import settings

logger = logging.getLogger(__name__)
_AUTHORIZATION_CACHE_TTL_SECONDS = 60
_authorization_cache: dict[str, tuple[float, bool]] = {}


def is_authorized_email(email: Optional[str]) -> bool:
    """Compare the configured allowlisted email without case sensitivity."""
    return bool(email) and email.strip().casefold() == settings.authorized_email.casefold()


def _primary_email(user) -> Optional[str]:
    primary_id = getattr(user, "primary_email_address_id", None)
    addresses = getattr(user, "email_addresses", []) or []
    for address in addresses:
        if getattr(address, "id", None) == primary_id:
            return getattr(address, "email_address", None)
    return None


def _fetch_authorization(subject: str) -> bool:
    """Fetch the verified primary email from Clerk for a signed-in subject."""
    from clerk_backend_api import Clerk

    clerk = Clerk(bearer_auth=settings.clerk_secret_key)
    user = clerk.users.get(user_id=subject)
    return is_authorized_email(_primary_email(user))


async def _is_authorized_subject(subject: str) -> bool:
    now = time.monotonic()
    cached = _authorization_cache.get(subject)
    if cached and now - cached[0] < _AUTHORIZATION_CACHE_TTL_SECONDS:
        return cached[1]
    authorized = await run_in_threadpool(_fetch_authorization, subject)
    _authorization_cache[subject] = (now, authorized)
    return authorized


async def _authenticate_clerk(request: Request) -> Optional[str]:
    """
    Authenticate a request using clerk_backend_api.
    Returns the Clerk subject (actor_id) on success, raises HTTPException on failure.

    Uses authenticate_request per official Clerk Python SDK guidance.
    """
    sk = settings.clerk_secret_key
    if not sk:
        # No Clerk configured — in dev/test, allow if DEBUG
        if settings.debug:
            return "dev-user"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service not configured",
        )

    try:
        from clerk_backend_api import AuthenticateRequestOptions, authenticate_request

        forwarded_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        host = forwarded_host or request.headers.get("host", "")
        protocol = forwarded_proto or request.url.scheme
        authorized_parties = [f"{protocol}://{host}"] if host else []

        options = AuthenticateRequestOptions(
            secret_key=sk,
            authorized_parties=authorized_parties or None,
            accepts_token=["session_token"],
        )
        request_state = await run_in_threadpool(authenticate_request, request, options)
        if not request_state.is_signed_in:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload = request_state.payload
        subject = payload.get("sub") if payload else None
        if not subject or not await _is_authorized_subject(subject):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not authorized for this application",
            )
        return subject
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Clerk auth error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def require_auth(request: Request) -> str:
    """FastAPI dependency that returns actor_id or raises 401."""
    actor_id = await _authenticate_clerk(request)
    if not actor_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return actor_id
