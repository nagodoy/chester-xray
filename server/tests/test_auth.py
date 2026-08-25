"""Email one-time-code sign-in and session lifecycle.

Ported from the previous suite, which is the behavioural specification, and
extended where that suite left a property untested.
"""

from __future__ import annotations

import pytest

from chester.models import AuthChallenge, User
from chester.security.roles import ROLE_ADMIN, ROLE_TECHNICIAN


@pytest.fixture
def authorized_user(make_user):
    return make_user("reader@example.com", ROLE_TECHNICIAN)


def test_code_sign_in_creates_a_session_and_logout_revokes_it(client, signed_in, authorized_user):
    headers, access = signed_in("reader@example.com")

    assert access["email"] == "reader@example.com"
    assert access["is_admin"] is False
    assert len(headers["X-Session-Token"]) > 32

    assert client.get("/api/auth/validate-session", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/validate-session", headers=headers).status_code == 401


def test_the_code_is_never_returned_in_the_response(client, capture_otp, authorized_user):
    response = client.post("/api/auth/request-code", json={"email": "reader@example.com"})

    assert response.status_code == 200
    assert capture_otp
    assert capture_otp[-1][1] not in response.text


def test_a_code_can_only_be_used_once(client, capture_otp, authorized_user):
    client.post("/api/auth/request-code", json={"email": "reader@example.com"})
    code = capture_otp[-1][1]

    body = {"email": "reader@example.com", "code": code}
    assert client.post("/api/auth/verify-code", json=body).status_code == 200
    assert client.post("/api/auth/verify-code", json=body).status_code == 400


def test_requesting_again_is_rate_limited(client, capture_otp, authorized_user, monkeypatch):
    from chester.config import settings

    monkeypatch.setattr(settings, "auth_otp_cooldown_seconds", 120)
    client.post("/api/auth/request-code", json={"email": "reader@example.com"})

    throttled = client.post("/api/auth/request-code", json={"email": "reader@example.com"})

    assert throttled.status_code == 429
    assert throttled.headers["Retry-After"]


def test_unauthorized_addresses_get_the_same_response_as_authorized_ones(
    client, capture_otp, authorized_user
):
    """No enumeration oracle.

    The previous implementation answered 403 for an unknown address and 200 for a
    known one, which let anyone map the allowlist.
    """
    known = client.post("/api/auth/request-code", json={"email": "reader@example.com"})
    unknown = client.post("/api/auth/request-code", json={"email": "nobody@elsewhere.test"})

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    # The difference is only in what actually happened.
    assert [recipient for recipient, _ in capture_otp] == ["reader@example.com"]


def test_a_wrong_code_spends_an_attempt(client, capture_otp, authorized_user, session):
    client.post("/api/auth/request-code", json={"email": "reader@example.com"})

    rejected = client.post(
        "/api/auth/verify-code", json={"email": "reader@example.com", "code": "000000"}
    )

    assert rejected.status_code == 400
    challenge = session.query(AuthChallenge).filter_by(email="reader@example.com").one()
    assert challenge.attempts == 1


def test_attempts_are_exhausted_and_stay_exhausted(
    client, capture_otp, authorized_user, session, monkeypatch
):
    from chester.config import settings

    monkeypatch.setattr(settings, "auth_otp_attempts", 2)
    client.post("/api/auth/request-code", json={"email": "reader@example.com"})
    real_code = capture_otp[-1][1]

    for _ in range(2):
        client.post("/api/auth/verify-code", json={"email": "reader@example.com", "code": "000000"})

    # Even the correct code is refused once the budget is spent.
    exhausted = client.post(
        "/api/auth/verify-code", json={"email": "reader@example.com", "code": real_code}
    )
    assert exhausted.status_code == 400


def test_requesting_a_new_code_retires_the_previous_one(
    client, capture_otp, authorized_user, monkeypatch
):
    """Otherwise the attempt limit can be reset simply by asking again."""
    from chester.config import settings

    monkeypatch.setattr(settings, "auth_otp_cooldown_seconds", 0)
    client.post("/api/auth/request-code", json={"email": "reader@example.com"})
    first_code = capture_otp[-1][1]

    client.post("/api/auth/request-code", json={"email": "reader@example.com"})
    second_code = capture_otp[-1][1]

    assert first_code != second_code
    stale = client.post(
        "/api/auth/verify-code", json={"email": "reader@example.com", "code": first_code}
    )
    assert stale.status_code == 400
    fresh = client.post(
        "/api/auth/verify-code", json={"email": "reader@example.com", "code": second_code}
    )
    assert fresh.status_code == 200


def test_no_session_token_is_rejected(client):
    assert client.get("/api/auth/validate-session").status_code == 401


def test_a_forged_session_token_is_rejected(client):
    response = client.get(
        "/api/auth/validate-session", headers={"X-Session-Token": "not-a-real-token"}
    )
    assert response.status_code == 401


def test_deactivating_a_user_ends_their_live_session(client, signed_in, authorized_user, session):
    """Access withdrawn mid-session must not survive until the token expires."""
    headers, _ = signed_in("reader@example.com")
    assert client.get("/api/auth/validate-session", headers=headers).status_code == 200

    authorized_user.active = False
    session.flush()

    assert client.get("/api/auth/validate-session", headers=headers).status_code == 403


def test_environment_admin_signs_in_as_admin(client, signed_in, session, monkeypatch):
    from chester.config import settings
    from chester.security.access import bootstrap_env_admins

    monkeypatch.setattr(settings, "admin_users", "boss@example.com")
    monkeypatch.setattr(
        type(settings), "admin_emails", property(lambda _self: ("boss@example.com",))
    )
    bootstrap_env_admins(session)

    _, access = signed_in("boss@example.com")

    assert access["is_admin"] is True
    assert access["role"] == ROLE_ADMIN
    assert access["source"] == "environment"
    assert session.query(User).filter_by(email="boss@example.com").one().is_env_admin


def test_broken_email_delivery_does_not_reveal_authorization(
    client, capture_otp, authorized_user, monkeypatch
):
    """A delivery failure must look the same for known and unknown addresses.

    Found by running the stack end to end: with SMTP unconfigured, an authorized
    address returned 503 while an unknown one returned 200, which reopened the
    enumeration hole the generic response exists to close.
    """
    from chester.api import auth

    def _explode(_recipient, _code):
        raise RuntimeError("smtp is down")

    monkeypatch.setattr(auth, "send_otp_email", _explode)

    known = client.post("/api/auth/request-code", json={"email": "reader@example.com"})
    unknown = client.post("/api/auth/request-code", json={"email": "nobody@elsewhere.test"})

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_unconfigured_email_is_refused_before_the_address_is_resolved(
    client, capture_otp, authorized_user, monkeypatch
):
    from chester.api import auth

    monkeypatch.setattr(auth, "email_delivery_configured", lambda: False)

    known = client.post("/api/auth/request-code", json={"email": "reader@example.com"})
    unknown = client.post("/api/auth/request-code", json={"email": "nobody@elsewhere.test"})

    assert known.status_code == unknown.status_code == 503
