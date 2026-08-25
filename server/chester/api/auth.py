"""Email one-time-code sign-in and database-backed sessions."""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from chester.api.deps import SESSION_HEADER, get_current_access
from chester.config import settings
from chester.db import get_session
from chester.emailer import EmailNotConfigured, send_otp_email
from chester.models import AuthChallenge, AuthSession, utcnow
from chester.security.access import AccessContext, materialize_user, resolve_grant
from chester.security.roles import normalize_email
from chester.security.tokens import (
    hash_otp_code,
    hash_session_token,
    new_otp_code,
    new_session_token,
    tokens_equal,
    unmatchable_code_hash,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

GENERIC_CODE_SENT = "Se este email estiver autorizado, enviaremos um código."
INVALID_CODE = "Código inválido ou expirado."


class RequestCodeBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class VerifyCodeBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else None)


def _access_payload(access: AccessContext) -> dict:
    return {
        "email": access.email,
        "role": access.role,
        "is_admin": access.is_admin,
        "allowed_pages": access.allowed_pages,
        "organization_id": str(access.organization_id),
        "source": access.source,
    }


def _cooldown_remaining(db: Session, email: str) -> int:
    """Seconds left before this address may request another code.

    Keyed on the address alone. Throttling by source IP as well was tempting, but
    it punishes everyone behind one NAT for a single user's request and it is a
    full table scan without a bounded window. Per-IP and per-network limiting
    belongs at the edge, where it can see the whole request stream.
    """
    latest = (
        db.query(AuthChallenge)
        .filter(AuthChallenge.email == email)
        .order_by(desc(AuthChallenge.requested_at))
        .first()
    )
    if latest is None:
        return 0
    elapsed = (utcnow() - latest.requested_at).total_seconds()
    return max(0, settings.auth_otp_cooldown_seconds - int(elapsed))


@router.post("/request-code")
async def request_code(
    body: RequestCodeBody,
    request: Request,
    db: Session = Depends(get_session),
):
    """Send a one-time code to an authorized address.

    Every branch below returns the same status and body. The previous
    implementation answered 403 for an unknown address and 200 for a known one,
    which let anyone enumerate the allowlist.

    Closing that oracle takes more than making the happy path uniform: a challenge
    row is recorded for unauthorized addresses too, otherwise only authorized ones
    would ever be rate limited and a second probe would separate 429 from 200. For
    an unauthorized address the stored hash is random, so no code can ever match
    it and no mail is sent.
    """
    email = normalize_email(body.email)
    request_ip = _client_ip(request)

    if _cooldown_remaining(db, email):
        raise HTTPException(
            status_code=429,
            detail="Aguarde antes de solicitar outro código.",
            headers={"Retry-After": str(settings.auth_otp_cooldown_seconds)},
        )

    grant = resolve_grant(db, email)
    if grant is None:
        logger.info("Access code requested for an unauthorized address")
        code_hash = unmatchable_code_hash()
    else:
        code = new_otp_code()
        try:
            await run_in_threadpool(send_otp_email, email, code)
        except EmailNotConfigured:
            logger.error("Access code requested but SMTP is not configured")
            raise HTTPException(
                status_code=503, detail="Envio de email não está configurado."
            ) from None
        code_hash = hash_otp_code(email, code)

    # Retire any live challenge for this address. Without this, requesting a new
    # code leaves the previous one usable and hands the caller a fresh attempt
    # budget, which undoes the per-challenge attempt limit.
    now = utcnow()
    db.query(AuthChallenge).filter(
        AuthChallenge.email == email, AuthChallenge.consumed_at.is_(None)
    ).update({AuthChallenge.consumed_at: now}, synchronize_session=False)

    db.add(
        AuthChallenge(
            email=email,
            code_hash=code_hash,
            expires_at=now + timedelta(minutes=settings.auth_otp_minutes),
            max_attempts=settings.auth_otp_attempts,
            request_ip=request_ip,
        )
    )
    db.commit()
    return {"ok": True, "message": GENERIC_CODE_SENT}


@router.post("/verify-code")
def verify_code(
    body: VerifyCodeBody,
    request: Request,
    db: Session = Depends(get_session),
):
    """Exchange a valid code for a session token."""
    email = normalize_email(body.email)
    now = utcnow()

    challenge = (
        db.query(AuthChallenge)
        .filter(AuthChallenge.email == email, AuthChallenge.consumed_at.is_(None))
        .order_by(desc(AuthChallenge.requested_at))
        .first()
    )
    if (
        challenge is None
        or challenge.expires_at <= now
        or challenge.attempts >= challenge.max_attempts
    ):
        raise HTTPException(status_code=400, detail=INVALID_CODE)

    if not tokens_equal(hash_otp_code(email, body.code), challenge.code_hash):
        _spend_attempt(db, challenge, now)
        db.commit()
        raise HTTPException(status_code=400, detail=INVALID_CODE)

    grant = resolve_grant(db, email)
    if grant is None:
        # Authorization was withdrawn between requesting and verifying.
        raise HTTPException(
            status_code=403, detail="Este email não está autorizado para este ambiente."
        )

    consumed = _spend_attempt(db, challenge, now, consume=True)
    if consumed != 1:
        # Another request consumed this challenge first; treat it as spent.
        db.rollback()
        raise HTTPException(status_code=400, detail=INVALID_CODE)

    user = materialize_user(db, grant)
    token = new_session_token()
    db.add(
        AuthSession(
            token_hash=hash_session_token(token),
            user_id=user.id,
            expires_at=now + timedelta(hours=settings.auth_session_hours),
            last_seen_at=now,
            user_agent=request.headers.get("user-agent"),
            request_ip=_client_ip(request),
        )
    )
    db.commit()
    return {"session_token": token, "access": _access_payload(AccessContext.from_user(user))}


def _spend_attempt(db: Session, challenge: AuthChallenge, now, *, consume: bool = False) -> int:
    """Increment the attempt counter, optionally consuming the challenge.

    Guarded by the same predicates that selected the challenge so two concurrent
    verifications cannot both succeed.
    """
    values: dict = {AuthChallenge.attempts: AuthChallenge.attempts + 1}
    if consume:
        values[AuthChallenge.consumed_at] = now
    return (
        db.query(AuthChallenge)
        .filter(
            AuthChallenge.id == challenge.id,
            AuthChallenge.consumed_at.is_(None),
            AuthChallenge.expires_at > now,
            AuthChallenge.attempts < AuthChallenge.max_attempts,
        )
        .update(values, synchronize_session=False)
    )


@router.get("/validate-session")
def validate_session(access: AccessContext = Depends(get_current_access)):
    return {"authenticated": True, "access": _access_payload(access)}


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_session)):
    """Revoke the presented session. Always reports success."""
    token = request.headers.get(SESSION_HEADER, "").strip()
    if token:
        auth_session = (
            db.query(AuthSession)
            .filter(AuthSession.token_hash == hash_session_token(token))
            .first()
        )
        if auth_session is not None and auth_session.revoked_at is None:
            auth_session.revoked_at = utcnow()
            db.commit()
    return {"ok": True}
