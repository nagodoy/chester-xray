"""Tests for the authenticated, read-only DICOMweb settings endpoint."""
from __future__ import annotations


def test_dicomweb_settings_uses_configured_public_origin_not_request_headers(
    auth_client,
    monkeypatch,
):
    from app.config import settings

    monkeypatch.setattr(settings, "public_app_url", "https://console.example.test")
    response = auth_client.get(
        "/api/settings/dicomweb",
        headers={
            "x-forwarded-host": "attacker.example.test",
            "x-forwarded-proto": "ftp",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["stow_rs"]["status"] == "configured"
    assert data["stow_rs"]["url"] == "https://console.example.test/dicomweb/studies"
    assert data["stow_rs"]["hostname"] == "console.example.test"
    assert data["stow_rs"]["port"] == "443"
    assert data["stow_rs"]["https"] is True
    assert data["stow_rs"]["services"] == ["STOW-RS"]
    assert data["scp"]["status"] == "not_configured"
    assert data["scp"]["services"] == ["C-STORE"]
    assert data["service_token_configured"] is True
    assert "test-session-secret" not in response.text


def test_dicomweb_settings_marks_public_endpoint_local_when_unconfigured(
    auth_client,
    monkeypatch,
):
    from app.config import settings

    monkeypatch.setattr(settings, "public_app_url", "")
    response = auth_client.get("/api/settings/dicomweb")

    assert response.status_code == 200
    data = response.json()
    assert data["stow_rs"]["status"] == "local_only"
    assert data["stow_rs"]["url"] == "/dicomweb/studies"
    assert data["stow_rs"]["hostname"] == "Não configurado"
    assert data["stow_rs"]["port"] == "—"


def test_dicomweb_settings_sanitizes_gateway_target(auth_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(
        settings,
        "dicom_stow_url",
        "https://operator:secret@remote.example.test/worklist/stow?token=secret",
    )
    response = auth_client.get("/api/settings/dicomweb")

    assert response.status_code == 200
    assert response.json()["scp"]["gateway_target"] == "/dicomweb/studies"
    assert "secret" not in response.text


def test_dicomweb_settings_requires_auth_when_development_bypass_is_disabled(
    client,
    monkeypatch,
):
    from app.config import settings

    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "clerk_secret_key", "")
    response = client.get("/api/settings/dicomweb")

    assert response.status_code == 503