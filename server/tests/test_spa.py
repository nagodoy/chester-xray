"""Serving the built single-page application alongside the API."""

from __future__ import annotations


def test_api_routes_keep_their_own_404(client):
    """The shell must not answer for an unknown API path."""
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert "<!doctype html" not in response.text.lower()


def test_dicomweb_routes_keep_their_own_404(client):
    assert client.get("/dicomweb/nope").status_code == 404


def test_posting_to_the_root_is_not_an_upload_path(client):
    """OsiriX misconfigurations post to /; that must not reach ingestion."""
    assert client.post("/", content=b"x").status_code == 405


def test_a_traversal_attempt_does_not_escape_the_build_directory(client):
    """Either the shell or a 503, never a file from outside dist/."""
    response = client.get("/../server/chester/config.py")

    assert "session_secret" not in response.text
    assert response.status_code in (200, 404, 503)
