"""Clerk proxy for Replit Clerk proxy template compatibility."""
from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.background import BackgroundTask

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

CLERK_FRONTEND_API = "https://frontend-api.clerk.dev"

# Hop-by-hop headers to strip per RFC 2616 §14.10
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _strip_hop_by_hop(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


@router.api_route("/api/__clerk/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def clerk_proxy(path: str, request: Request) -> Response:
    """
    Production-only Clerk proxy compatible with Replit Clerk proxy template.
    Forwards requests to https://frontend-api.clerk.dev, preserving method/body/query.
    Sets Clerk-Proxy-Url and Clerk-Secret-Key headers.
    """
    if not settings.clerk_secret_key:
        return Response(content="Clerk not configured", status_code=503)

    # Build target URL
    query_string = request.url.query
    target_url = f"{CLERK_FRONTEND_API}/{path}"
    if query_string:
        target_url = f"{target_url}?{query_string}"

    # Build forwarded headers
    proxy_url = str(request.base_url).rstrip("/") + "/api/__clerk"
    forward_headers = dict(request.headers)

    # Strip hop-by-hop
    forward_headers = _strip_hop_by_hop(forward_headers)

    # Set required Clerk proxy headers
    forward_headers["Clerk-Proxy-Url"] = proxy_url
    forward_headers["Clerk-Secret-Key"] = settings.clerk_secret_key

    # Read body
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=forward_headers,
                content=body,
            )

        response_headers = dict(resp.headers)
        response_headers = _strip_hop_by_hop(response_headers)
        # Set content-length explicitly
        response_headers["content-length"] = str(len(resp.content))

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=response_headers,
            media_type=resp.headers.get("content-type"),
        )
    except httpx.TimeoutException:
        logger.error("Clerk proxy timeout for %s", target_url)
        return Response(content="Clerk API timeout", status_code=504)
    except Exception as exc:
        logger.error("Clerk proxy error: %s", exc)
        return Response(content="Clerk proxy error", status_code=502)
