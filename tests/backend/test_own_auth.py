"""Tests for email OTP, database sessions and access-control resolution."""
from __future__ import annotations

from app.models import AllowedDomain, AllowedEmail, Study


def _request_code(client, monkeypatch, value=42):
    from app.api import routes_auth

    sent = []
    monkeypatch.setattr(routes_auth.secrets, "randbelow", lambda _limit: value)
    monkeypatch.setattr(routes_auth, "send_otp_email", lambda recipient, code: sent.append((recipient, code)))
    response = client.post("/api/auth/request-code", json={"email": "nelsonagodoy@gmail.com"})
    assert response.status_code == 200
    assert sent == [("nelsonagodoy@gmail.com", f"{value:06d}")]
    assert sent[0][1] not in response.text
    return f"{value:06d}"


def test_otp_creates_database_session_and_logout_revokes_it(client, monkeypatch):
    code = _request_code(client, monkeypatch)
    verified = client.post(
        "/api/auth/verify-code",
        json={"email": "nelsonagodoy@gmail.com", "code": code},
    )
    assert verified.status_code == 200
    payload = verified.json()
    assert payload["access"]["is_admin"] is True
    assert len(payload["session_token"]) > 32

    headers = {"X-Session-Token": payload["session_token"]}
    assert client.get("/api/auth/validate-session", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/validate-session", headers=headers).status_code == 401


def test_otp_is_single_use_and_has_cooldown(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_otp_cooldown_seconds", 120)
    code = _request_code(client, monkeypatch, value=7)
    assert client.post("/api/auth/request-code", json={"email": "nelsonagodoy@gmail.com"}).status_code == 429
    assert client.post(
        "/api/auth/verify-code",
        json={"email": "nelsonagodoy@gmail.com", "code": code},
    ).status_code == 200
    assert client.post(
        "/api/auth/verify-code",
        json={"email": "nelsonagodoy@gmail.com", "code": code},
    ).status_code == 400


def test_access_resolution_prefers_email_over_domain_and_normalizes_pages(db_session):
    from app.api.auth_deps import resolve_access

    db_session.add(
        AllowedDomain(
            domain="example.com",
            role="consultor",
            allowed_pages=["worklist"],
            active=True,
        )
    )
    db_session.add(
        AllowedEmail(
            email="reader@example.com",
            role="technician",
            allowed_pages=[],
            active=True,
        )
    )
    db_session.commit()

    direct = resolve_access(db_session, "READER@example.com ")
    domain = resolve_access(db_session, "other@example.com")
    assert direct.role == "technician"
    assert direct.allowed_pages is None
    assert domain.role == "consultor"
    assert domain.allowed_pages == ["worklist"]


def test_inactive_email_rule_blocks_domain_fallback(db_session):
    from app.api.auth_deps import resolve_access

    db_session.add(AllowedDomain(domain="example.com", role="consultor", active=True))
    db_session.add(AllowedEmail(email="blocked@example.com", role="admin", active=False))
    db_session.commit()

    assert resolve_access(db_session, "blocked@example.com") is None


def test_environment_admin_removed_from_configuration_is_revoked(db_session, monkeypatch):
    from app.api.auth_deps import bootstrap_env_admins
    from app.config import settings

    entry = AllowedEmail(
        email="former-environment-admin@example.test",
        role="admin",
        active=True,
        is_env_admin=True,
    )
    db_session.add(entry)
    db_session.commit()
    monkeypatch.setattr(settings, "admin_users", "")

    bootstrap_env_admins(db_session)
    db_session.flush()
    assert entry.is_env_admin is False
    assert entry.active is False


def test_review_filter_requires_review_page_permission(auth_client):
    from app.api.auth_deps import AccessContext, get_current_access
    from app.main import app

    original_override = app.dependency_overrides[get_current_access]
    app.dependency_overrides[get_current_access] = lambda: AccessContext(
        email="test-user-123",
        role="technician",
        allowed_pages=["worklist"],
    )
    try:
        assert auth_client.get("/api/studies?status=needs_review").status_code == 403
    finally:
        app.dependency_overrides[get_current_access] = original_override


def test_admin_can_migrate_clerk_owned_study_to_authorized_email(auth_client, db_session):
    from app.api.auth_deps import AccessContext, get_current_access
    from app.main import app

    study = Study(owner_id="user_Legacy123", status="completed", description="Legacy study")
    db_session.add(study)
    db_session.commit()

    migrated = auth_client.post(
        "/api/legacy-study-owners/migrate",
        json={"legacy_owner_id": "user_Legacy123", "email": "nelsonagodoy@gmail.com"},
    )
    assert migrated.status_code == 200
    assert migrated.json()["study_count"] == 1
    assert db_session.get(Study, study.id).owner_id == "nelsonagodoy@gmail.com"

    original_override = app.dependency_overrides[get_current_access]
    app.dependency_overrides[get_current_access] = lambda: AccessContext(
        email="nelsonagodoy@gmail.com",
        role="admin",
        allowed_pages=None,
        is_admin=True,
    )
    try:
        listed = auth_client.get("/api/studies")
        assert listed.status_code == 200
        assert study.id in {item["id"] for item in listed.json()["items"]}
    finally:
        app.dependency_overrides[get_current_access] = original_override


def test_legacy_migration_requires_admin_session(client):
    assert client.post(
        "/api/legacy-study-owners/migrate",
        json={"legacy_owner_id": "user_Legacy123", "email": "nelsonagodoy@gmail.com"},
    ).status_code == 401


def test_legacy_migration_rejects_email_owned_studies(auth_client, db_session):
    email_owned = Study(owner_id="other.authorized@example.com", status="completed")
    db_session.add(email_owned)
    db_session.commit()

    denied = auth_client.post(
        "/api/legacy-study-owners/migrate",
        json={"legacy_owner_id": "other.authorized@example.com", "email": "nelsonagodoy@gmail.com"},
    )
    assert denied.status_code == 400
    assert db_session.get(Study, email_owned.id).owner_id == "other.authorized@example.com"


def test_legacy_owner_alias_translates_only_explicitly_mapped_subject(db_session):
    from app.models import LegacyOwnerAlias
    from app.security.ownership import resolve_ingest_owner

    db_session.add(
        LegacyOwnerAlias(
            legacy_owner_id="user_Legacy123",
            email="nelsonagodoy@gmail.com",
            created_by="nelsonagodoy@gmail.com",
        )
    )
    db_session.commit()
    assert resolve_ingest_owner(db_session, "user_Legacy123") == "nelsonagodoy@gmail.com"
    assert resolve_ingest_owner(db_session, "user_Unmapped123") == "user_Unmapped123"


def test_access_admin_cannot_remove_last_admin(auth_client, db_session):
    from app.models import AllowedEmail

    db_session.query(AllowedEmail).delete()
    entry = AllowedEmail(email="only-admin@example.test", role="admin", active=True)
    db_session.add(entry)
    db_session.commit()

    response = auth_client.patch(f"/api/allowed-emails/{entry.id}", json={"active": False})
    assert response.status_code == 409