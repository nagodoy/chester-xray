"""STOW-RS ingestion, authentication and the OsiriX compatibility paths."""

from __future__ import annotations

import base64

import pytest

from chester.models import Instance, Study
from chester.security.roles import ROLE_TECHNICIAN

TOKEN = "test-dicom-ingest-token"


@pytest.fixture
def owner(make_user):
    return make_user("ingest-owner@example.com", ROLE_TECHNICIAN)


@pytest.fixture
def stow(client, make_stow_body, owner):
    def _post(parts, *, path="/dicomweb/studies", headers=None, boundary="STOW-BOUNDARY-001"):
        body, content_type = make_stow_body(parts, boundary=boundary)
        request_headers = {
            "Content-Type": content_type,
            "X-DICOM-Ingest-Key": TOKEN,
            "X-Worklist-Owner": owner.email,
        }
        if headers is not None:
            request_headers.update(headers)
            request_headers = {k: v for k, v in request_headers.items() if v is not None}
        return client.post(path, content=body, headers=request_headers)

    return _post


class TestAuthentication:
    def test_a_valid_token_is_accepted(self, stow, make_dicom):
        assert stow([make_dicom()]).status_code in (200, 202)

    def test_a_wrong_token_is_rejected(self, stow, make_dicom):
        response = stow([make_dicom()], headers={"X-DICOM-Ingest-Key": "wrong"})
        assert response.status_code == 401

    def test_a_missing_token_is_rejected(self, stow, make_dicom):
        response = stow([make_dicom()], headers={"X-DICOM-Ingest-Key": None})
        assert response.status_code == 401

    def test_a_bearer_token_is_accepted(self, stow, make_dicom):
        response = stow(
            [make_dicom()],
            headers={"X-DICOM-Ingest-Key": None, "Authorization": f"Bearer {TOKEN}"},
        )
        assert response.status_code in (200, 202)

    def test_basic_auth_is_accepted_for_osirix(self, stow, make_dicom):
        """OsiriX sends HTTP Basic credentials, with the token as the password."""
        credentials = base64.b64encode(f"dicom:{TOKEN}".encode()).decode()
        response = stow(
            [make_dicom()],
            headers={"X-DICOM-Ingest-Key": None, "Authorization": f"Basic {credentials}"},
        )
        assert response.status_code in (200, 202)

    def test_basic_auth_with_a_wrong_password_is_rejected(self, stow, make_dicom):
        credentials = base64.b64encode(b"dicom:nope").decode()
        response = stow(
            [make_dicom()],
            headers={"X-DICOM-Ingest-Key": None, "Authorization": f"Basic {credentials}"},
        )
        assert response.status_code == 401


class TestIngestion:
    def test_a_stored_instance_is_referenced_in_the_response(self, stow, make_dicom, session):
        response = stow([make_dicom()])

        assert response.status_code in (200, 202)
        body = response.json()
        assert "00081199" in body  # ReferencedSOPSequence
        assert session.query(Study).count() == 1

    def test_multiple_instances_in_one_request(self, stow, make_dicom, session):
        response = stow([make_dicom(), make_dicom()])

        assert response.status_code in (200, 202)
        assert session.query(Instance).count() == 2

    def test_a_duplicate_is_reported_as_a_conflict(self, stow, make_dicom):
        data = make_dicom()
        assert stow([data]).status_code in (200, 202)

        duplicate = stow([data])

        assert duplicate.status_code == 409
        assert "00081198" in duplicate.json()  # FailedSOPSequence

    def test_non_dicom_content_fails(self, stow):
        response = stow([b"this is not a DICOM file"])

        assert response.status_code == 400
        assert "00081198" in response.json()

    def test_the_wrong_content_type_is_refused(self, client, owner, make_dicom):
        response = client.post(
            "/dicomweb/studies",
            content=make_dicom(),
            headers={
                "Content-Type": "application/dicom",
                "X-DICOM-Ingest-Key": TOKEN,
                "X-Worklist-Owner": owner.email,
            },
        )
        assert response.status_code == 400

    def test_an_empty_body_is_refused(self, client, owner):
        response = client.post(
            "/dicomweb/studies",
            content=b"",
            headers={
                "Content-Type": "multipart/related; boundary=x",
                "X-DICOM-Ingest-Key": TOKEN,
                "X-Worklist-Owner": owner.email,
            },
        )
        assert response.status_code == 400

    def test_a_payload_containing_the_boundary_is_not_torn_apart(self, stow, make_dicom, session):
        """The reason the hand-rolled parser was replaced.

        Splitting the raw body on the boundary bytes wherever they occur corrupts
        any DICOM whose pixel data happens to contain that sequence. A conformant
        parser only honours a delimiter at the start of a line.
        """
        import numpy as np

        boundary = "STOW-BOUNDARY-001"
        marker = f"--{boundary}".encode()
        pixels = np.zeros(64 * 64, dtype=np.uint8)
        pixels[: len(marker)] = np.frombuffer(marker, dtype=np.uint8)
        data = make_dicom(bits_allocated=8, pixels=pixels.reshape(64, 64))
        assert marker in data, "fixture did not embed the boundary sequence"

        response = stow([data], boundary=boundary)

        assert response.status_code in (200, 202), response.text
        assert session.query(Instance).count() == 1


class TestOwnerResolution:
    def test_an_unknown_owner_is_refused(self, stow, make_dicom):
        response = stow([make_dicom()], headers={"X-Worklist-Owner": "stranger@example.com"})

        assert response.status_code == 400
        assert "authorized" in response.json()["detail"].lower()

    def test_a_malformed_owner_is_refused(self, stow, make_dicom):
        response = stow([make_dicom()], headers={"X-Worklist-Owner": "not a valid owner!"})
        assert response.status_code == 400

    def test_the_configured_owner_is_used_when_no_header_is_sent(
        self, stow, make_dicom, owner, monkeypatch
    ):
        from chester.config import settings

        monkeypatch.setattr(settings, "dicom_ingest_owner_email", owner.email)

        response = stow([make_dicom()], headers={"X-Worklist-Owner": None})

        assert response.status_code in (200, 202)

    def test_studies_land_in_the_owners_organization(self, stow, make_dicom, session, owner):
        stow([make_dicom()])

        study = session.query(Study).one()
        assert study.owner_user_id == owner.id
        assert study.organization_id == owner.organization_id


class TestCompatibilityPaths:
    def test_the_wado_alias_reaches_the_same_handler(self, stow, make_dicom):
        assert stow([make_dicom()], path="/wado/studies").status_code in (200, 202)

    def test_the_duplicated_wado_path_is_accepted(self, stow, make_dicom):
        """Some OsiriX configurations append 'studies' to an already suffixed base."""
        response = stow([make_dicom()], path="/wado/studies/studies")
        assert response.status_code in (200, 202)

    def test_the_wado_alias_still_requires_a_credential_by_default(self, stow, make_dicom):
        response = stow([make_dicom()], path="/wado/studies", headers={"X-DICOM-Ingest-Key": None})
        assert response.status_code == 401

    def test_anonymous_wado_when_explicitly_enabled(self, stow, make_dicom, monkeypatch):
        from chester.config import settings

        monkeypatch.setattr(settings, "dicom_wado_anonymous_ingest", True)

        response = stow([make_dicom()], path="/wado/studies", headers={"X-DICOM-Ingest-Key": None})
        assert response.status_code in (200, 202)

    def test_anonymous_mode_never_opens_the_canonical_endpoint(self, stow, make_dicom, monkeypatch):
        """The relaxation is scoped to the compatibility aliases only."""
        from chester.config import settings

        monkeypatch.setattr(settings, "dicom_wado_anonymous_ingest", True)

        response = stow([make_dicom()], headers={"X-DICOM-Ingest-Key": None})
        assert response.status_code == 401


class TestLimits:
    def test_an_oversized_body_is_refused(self, stow, make_dicom, monkeypatch):
        """Anonymous WADO makes an unbounded read a denial-of-service primitive."""
        from chester.config import settings

        monkeypatch.setattr(settings, "dicom_max_upload_bytes", 512)

        response = stow([make_dicom()])

        assert response.status_code == 413


class TestConnectivityProbe:
    """A GET on an upload path answers a probe instead of 405.

    Modality workstations verify a node before they will send to it, so a
    405 on the configured URL reads as a broken endpoint.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "/dicomweb/studies",
            "/wado/studies",
            # The duplicated path some OsiriX configurations emit.
            "/wado/studies/studies",
            "/wado/studies/1.2.840.10008.1.2.3",
        ],
    )
    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_every_upload_path_answers_a_probe(self, client, path, method):
        response = client.request(method, path)

        assert response.status_code == 200

    def test_the_probe_names_the_canonical_endpoint_and_what_it_speaks(self, client):
        body = client.get("/wado/studies").json()

        assert body["status"] == "active"
        assert body["endpoint"] == "/dicomweb/studies"
        assert "STOW-RS" in body["capabilities"]
        assert any("application/dicom" in item for item in body["supportedContentTypes"])

    def test_an_anonymous_probe_is_told_nothing_about_credentials(self, client):
        """The field only appears for a caller that presented one."""
        assert "authenticated" not in client.get("/dicomweb/studies").json()

    def test_a_probe_carrying_a_credential_is_told_whether_it_works(self, client):
        """This is what separates a wrong token from an unreachable host."""
        good = client.get("/dicomweb/studies", headers={"X-DICOM-Ingest-Key": TOKEN})
        bad = client.get("/dicomweb/studies", headers={"X-DICOM-Ingest-Key": "wrong"})

        assert good.json()["authenticated"] is True
        assert bad.json()["authenticated"] is False

    def test_osirix_basic_credentials_are_checked_by_the_probe(self, client):
        encoded = base64.b64encode(f"osirix:{TOKEN}".encode()).decode()

        body = client.get("/wado/studies", headers={"Authorization": f"Basic {encoded}"}).json()

        assert body["authenticated"] is True

    def test_the_probe_does_not_open_a_way_in(self, client, make_stow_body):
        """Answering GET must not have relaxed what POST demands."""
        body, content_type = make_stow_body([b"not-a-dicom"])

        response = client.post(
            "/dicomweb/studies", content=body, headers={"Content-Type": content_type}
        )

        assert response.status_code == 401
