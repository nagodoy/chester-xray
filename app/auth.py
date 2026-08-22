"""Clerk authentication helpers for FastAPI."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from app.config import settings

logger = logging.getLogger(__name__)


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
