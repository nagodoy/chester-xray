"""Thumbnails, connectivity settings and the health probe."""

from __future__ import annotations

import pytest

from chester.security.roles import ROLE_ADMIN, ROLE_TECHNICIAN


@pytest.fixture
def uploaded_study(client, signed_in, make_user, make_dicom):
    make_user("uploader@example.com", ROLE_ADMIN)
    headers, _ = signed_in("uploader@example.com")
    response = client.post(
        "/api/uploads",
        data={"confirm_deidentified": "true"},
        files=[("files", ("chest.dcm", make_dicom(), "application/dicom"))],
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return headers, response.json()["studies"][0]


class TestThumbnails:
    def test_a_thumbnail_is_available_after_upload(self, client, uploaded_study):
        headers, study = uploaded_study

        response = client.get(f"/api/studies/{study['id']}/thumbnail", headers=headers)

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG\r\n\x1a\n")

    def test_an_unknown_study_has_no_thumbnail(self, client, uploaded_study):
        headers, _ = uploaded_study
        missing = "00000000-0000-0000-0000-000000000000"

        assert client.get(f"/api/studies/{missing}/thumbnail", headers=headers).status_code == 404

    def test_a_thumbnail_requires_a_session(self, client, uploaded_study):
        _, study = uploaded_study
        assert client.get(f"/api/studies/{study['id']}/thumbnail").status_code == 401

    def test_another_organization_cannot_read_the_thumbnail(
        self, client, signed_in, uploaded_study, session, make_user
    ):
        from chester.models import Organization

        _, study = uploaded_study
        rival = Organization(name="Rival", slug="rival")
        session.add(rival)
        session.flush()
        make_user("outsider@rival.test", ROLE_ADMIN, org=rival)
        outsider_headers, _ = signed_in("outsider@rival.test")

        response = client.get(f"/api/studies/{study['id']}/thumbnail", headers=outsider_headers)

        assert response.status_code == 404


class TestSettings:
    def test_connectivity_settings_are_readable(self, client, signed_in, make_user):
        make_user("reader@example.com", ROLE_TECHNICIAN)
        headers, _ = signed_in("reader@example.com")

        body = client.get("/api/settings/dicomweb", headers=headers).json()

        assert body["stow_rs"]["path"] == "/dicomweb/studies"
        assert body["service_token_configured"] is True
        assert body["wado_anonymous"] is False

    def test_the_ingest_token_is_never_disclosed(self, client, signed_in, make_user):
        """The screen reports whether a token exists, never its value."""
        from chester.config import settings

        make_user("reader@example.com", ROLE_TECHNICIAN)
        headers, _ = signed_in("reader@example.com")

        response = client.get("/api/settings/dicomweb", headers=headers)

        assert settings.dicom_ingest_token not in response.text

    def test_anonymous_mode_advertises_the_wado_path(
        self, client, signed_in, make_user, monkeypatch
    ):
        from chester.config import settings

        monkeypatch.setattr(settings, "dicom_wado_anonymous_ingest", True)
        make_user("reader@example.com", ROLE_TECHNICIAN)
        headers, _ = signed_in("reader@example.com")

        body = client.get("/api/settings/dicomweb", headers=headers).json()

        assert body["stow_rs"]["path"] == "/wado/studies"
        assert body["wado_anonymous"] is True

    def test_settings_require_a_session(self, client):
        assert client.get("/api/settings/dicomweb").status_code == 401

    @pytest.mark.parametrize(
        "configured",
        [
            "https://user:secret@example.test",  # credentials must never be echoed
            "javascript:alert(1)",
            "not a url",
            "",
        ],
    )
    def test_unsafe_configured_urls_are_not_echoed(self, configured):
        from chester.api.settings_routes import safe_http_url

        assert safe_http_url(configured) == ""

    def test_a_clean_url_is_preserved(self):
        from chester.api.settings_routes import safe_http_url

        assert safe_http_url("https://rx.example.test/") == "https://rx.example.test"


class TestHealth:
    def test_health_is_public_and_reports_the_database(self, client):
        body = client.get("/api/health").json()

        assert body["db_ok"] is True
        assert body["status"] == "ok"
        assert body["storage_backend"] == "database"

    def test_health_reports_the_model_version(self, client):
        assert client.get("/api/health").json()["model_version"].startswith("chester-onnx:")
