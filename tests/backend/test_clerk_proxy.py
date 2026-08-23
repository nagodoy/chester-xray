"""Tests for the production-only Clerk frontend API proxy helpers."""
from starlette.requests import Request

from app.routers.clerk_proxy import _clerk_proxy_url, _upstream_headers


def make_request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/api/__clerk/v1/environment",
            "raw_path": b"/api/__clerk/v1/environment",
            "query_string": b"",
            "headers": headers,
            "client": ("10.0.0.5", 12345),
        }
    )


def test_proxy_url_uses_original_public_host_and_protocol():
    request = make_request(
        [
            (b"host", b"127.0.0.1:5000"),
            (b"x-forwarded-host", b"rx.nelsongodoy.com.br, internal-proxy"),
            (b"x-forwarded-proto", b"https, http"),
        ]
    )

    assert _clerk_proxy_url(request) == "https://rx.nelsongodoy.com.br/api/__clerk"


def test_upstream_headers_do_not_forward_app_host_or_compression():
    request = make_request(
        [
            (b"host", b"rx.nelsongodoy.com.br"),
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-for", b"203.0.113.25, 10.0.0.4"),
            (b"content-length", b"18"),
            (b"accept-encoding", b"br, gzip"),
            (b"connection", b"keep-alive"),
            (b"origin", b"https://rx.nelsongodoy.com.br"),
        ]
    )

    headers = _upstream_headers(request)

    assert "host" not in {key.lower() for key in headers}
    assert "content-length" not in {key.lower() for key in headers}
    assert headers["Accept-Encoding"] == "identity"
    assert headers["X-Forwarded-For"] == "203.0.113.25"
    assert headers["Clerk-Proxy-Url"] == "https://rx.nelsongodoy.com.br/api/__clerk"