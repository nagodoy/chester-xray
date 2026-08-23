"""Email OTP and database-backed session endpoints."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.auth_deps import (
    AccessContext,
    get_current_access,
    hash_session_token,
    new_session_token,
    resolve_access,
    utcnow,
)
from app.config import settings
from app.database import get_db
from app.emailer import send_otp_email
from app.models import AuthChallenge, AuthSession
from app.security.roles import normalize_email

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RequestCodeBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class VerifyCodeBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else None)


def _code_hash(email: str, code: str) -> str:
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        f"{email}:{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _access_payload(access: AccessContext) -> dict:
    return {
        "email": access.email,
        "role": access.role,
        "is_admin": access.is_admin,
        "allowed_pages": access.allowed_pages,
        "source": access.source,
    }


@router.post("/request-code")
async def request_code(
    body: RequestCodeBody,
    request: Request,
    db: Session = Depends(get_db),
):
    email = normalize_email(body.email)
    if resolve_access(db, email) is None:
        raise HTTPException(status_code=403, detail="Este email não está autorizado para este ambiente.")

    now = utcnow()
    request_ip = _client_ip(request)
    latest = (
        db.query(AuthChallenge)
        .filter(
            AuthChallenge.email == email,
            AuthChallenge.consumed_at.is_(None),
        )
        .order_by(desc(AuthChallenge.requested_at))
        .first()
    )
    latest_from_ip = (
        db.query(AuthChallenge)
        .filter(AuthChallenge.request_ip == request_ip)
        .order_by(desc(AuthChallenge.requested_at))
        .first()
        if request_ip
        else None
    )
    latest_request = max(
        (challenge for challenge in (latest, latest_from_ip) if challenge is not None),
        key=lambda challenge: challenge.requested_at,
        default=None,
    )
    if latest_request and (now - latest_request.requested_at).total_seconds() < settings.auth_otp_cooldown_seconds:
        retry_after = max(
            1,
            settings.auth_otp_cooldown_seconds - int((now - latest_request.requested_at).total_seconds()),
        )
        raise HTTPException(
            status_code=429,
            detail="Aguarde antes de solicitar outro código.",
            headers={"Retry-After": str(retry_after)},
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    await run_in_threadpool(send_otp_email, email, code)
    db.add(
        AuthChallenge(
            email=email,
            code_hash=_code_hash(email, code),
            expires_at=now + timedelta(minutes=settings.auth_otp_minutes),
            max_attempts=settings.auth_otp_attempts,
            request_ip=request_ip,
        )
    )
    db.commit()
    return {"ok": True, "message": "Código enviado. Verifique seu email."}


@router.post("/verify-code")
def verify_code(
    body: VerifyCodeBody,
    request: Request,
    db: Session = Depends(get_db),
):
    email = normalize_email(body.email)
    challenge = (
        db.query(AuthChallenge)
        .filter(
            AuthChallenge.email == email,
            AuthChallenge.consumed_at.is_(None),
        )
        .order_by(desc(AuthChallenge.requested_at))
        .first()
    )
    now = utcnow()
    if (
        challenge is None
        or challenge.expires_at <= now
        or challenge.attempts >= challenge.max_attempts
    ):
        raise HTTPException(status_code=400, detail="Código inválido ou expirado.")

    expected = _code_hash(email, body.code)
    if not hmac.compare_digest(expected, challenge.code_hash):
        db.query(AuthChallenge).filter(
            AuthChallenge.id == challenge.id,
            AuthChallenge.consumed_at.is_(None),
            AuthChallenge.expires_at > now,
            AuthChallenge.attempts < AuthChallenge.max_attempts,
        ).update({AuthChallenge.attempts: AuthChallenge.attempts + 1}, synchronize_session=False)
        db.commit()
        raise HTTPException(status_code=400, detail="Código inválido ou expirado.")

    access = resolve_access(db, email)
    if access is None:
        raise HTTPException(status_code=403, detail="Este email não está autorizado para este ambiente.")

    consumed = db.query(AuthChallenge).filter(
        AuthChallenge.id == challenge.id,
        AuthChallenge.consumed_at.is_(None),
        AuthChallenge.expires_at > now,
        AuthChallenge.attempts < AuthChallenge.max_attempts,
    ).update(
        {
            AuthChallenge.attempts: AuthChallenge.attempts + 1,
            AuthChallenge.consumed_at: now,
        },
        synchronize_session=False,
    )
    if consumed != 1:
        db.rollback()
        raise HTTPException(status_code=400, detail="Código inválido ou expirado.")
    token = new_session_token()
    db.add(
        AuthSession(
            token_hash=hash_session_token(token),
            email=email,
            expires_at=now + timedelta(hours=settings.auth_session_hours),
            last_seen_at=now,
            user_agent=request.headers.get("user-agent"),
            request_ip=_client_ip(request),
        )
    )
    db.commit()
    return {"session_token": token, "access": _access_payload(access)}


@router.get("/validate-session")
def validate_session(access: AccessContext = Depends(get_current_access)):
    return {"authenticated": True, "access": _access_payload(access)}


@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
):
    token = request.headers.get("X-Session-Token", "").strip()
    if token:
        session = (
            db.query(AuthSession)
            .filter(AuthSession.token_hash == hash_session_token(token))
            .first()
        )
        if session and session.revoked_at is None:
            session.revoked_at = utcnow()
            db.commit()
    return {"ok": True}