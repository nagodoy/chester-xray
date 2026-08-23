"""Clerk proxy for Replit Clerk proxy template compatibility."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

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
REQUEST_HEADERS_TO_REBUILD = HOP_BY_HOP | {"host", "content-length", "accept-encoding"}


def _strip_hop_by_hop(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


def _first_forwarded_value(request: Request, header_name: str) -> str:
    value = request.headers.get(header_name, "")
    return value.split(",")[0].strip()


def _clerk_proxy_url(request: Request) -> str:
    """Build Clerk's required public proxy URL behind the deployment edge."""
    host = _first_forwarded_value(request, "x-forwarded-host") or request.headers.get(
        "host", ""
    ).strip()
    protocol = _first_forwarded_value(request, "x-forwarded-proto") or "https"
    if protocol not in {"http", "https"}:
        protocol = "https"
    return f"{protocol}://{host}/api/__clerk"


def _upstream_headers(request: Request) -> dict[str, str]:
    """Prepare headers for Clerk without forwarding the app's Host header."""
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in REQUEST_HEADERS_TO_REBUILD
    }
    client_ip = _first_forwarded_value(request, "x-forwarded-for")
    if not client_ip and request.client:
        client_ip = request.client.host
    if client_ip:
        headers["X-Forwarded-For"] = client_ip
    # httpx reads complete responses before relaying them; avoid returning a
    # decompressed body with stale compression headers.
    headers["Accept-Encoding"] = "identity"
    headers["Clerk-Proxy-Url"] = _clerk_proxy_url(request)
    headers["Clerk-Secret-Key"] = settings.clerk_secret_key
    return headers


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
    target_url = f"{CLERK_FRONTEND_API}/{path.lstrip('/')}"
    if query_string:
        target_url = f"{target_url}?{query_string}"

    # Read body
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=_upstream_headers(request),
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
