"""Tests for health endpoint."""
from __future__ import annotations

import pytest


def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "degraded")
    assert "storage_backend" in data
    assert "db_ok" in data
    assert data["db_ok"] is True


def test_health_public(client):
    """Health endpoint should not require auth."""
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_health_storage_backend(client):
    resp = client.get("/api/health")
    assert resp.json()["storage_backend"] in ("database", "replit_object_storage")
